#!/usr/bin/env bash
# Enforce the "strictly cleaner" pyright ratchet on touched Python files.
#
# For every .py file that is modified or added relative to BASE_REF (default:
# master), compare the pyright error+warning count of the file's base version
# against its current working-tree version. The count must STRICTLY DECREASE
# (or stay at 0 if it was already 0). New files must be 0.
#
# This is the machine-checkable backstop for the policy in
# AGENTS.md -> "Static Checks (last step)". It exists so the "that error was
# pre-existing" loophole is falsifiable: the base count is measured, not vibes.
#
# Usage:
#   ./scripts/typecheck_ratchet.sh              # BASE_REF defaults to master
#   ./scripts/typecheck_ratchet.sh origin/main  # positional arg
#   BASE_REF=HEAD make typecheck-ratchet        # env var (Make sets it)
#
# BASE_REF resolution: positional arg > env var > "master".
#
# Escape hatch: if a file cannot be made cleaner without a cross-module change,
# a signature change, or a schema migration, flag it to the user instead of
# silently expanding scope. This script will report the violation; the human
# decides whether to accept the escape hatch for that file.
#
# Safety: to measure the base version with the same project context (imports,
# pyrightconfig.json) as the working version, this script temporarily swaps the
# working file for its base version in place, counts, then restores it. The
# EXIT trap restores every swapped file from its backup before cleaning up, so
# interruption (Ctrl-C, SIGTERM, timeout) cannot lose working-tree data.

set -uo pipefail

BASE_REF="${1:-${BASE_REF:-master}}"
PYRIGHT="./venv/bin/pyright"

if [ ! -x "$PYRIGHT" ]; then
    echo "typecheck-ratchet: $PYRIGHT not found (run from repo root)" >&2
    exit 2
fi

if ! git cat-file -e "$BASE_REF" 2>/dev/null; then
    echo "typecheck-ratchet: BASE_REF '$BASE_REF' is not a valid git ref" >&2
    exit 2
fi

TMP="$(mktemp -d)"
RESTORE="$TMP/restore.tsv"   # backup<TAB>orig, one per swapped file
: > "$RESTORE"

cleanup() {
    local rc=$?
    if [ -s "$RESTORE" ]; then
        while IFS=$'\t' read -r bk orig; do
            [ -f "$bk" ] && cp "$bk" "$orig" 2>/dev/null || true
        done < "$RESTORE"
    fi
    rm -rf "$TMP"
    return $rc
}
trap cleanup EXIT

note_interrupt() {
    echo "" >&2
    echo "typecheck-ratchet: interrupted — restoring working tree from backups." >&2
}
trap note_interrupt INT TERM

# Collect touched .py files: modified/added vs BASE_REF, plus untracked.
# Skip deletes (the file must exist in the working tree to be checked).
mapfile -t FILES < <(
    {
        git diff --name-only --diff-filter=AM "$BASE_REF" -- '*.py' 2>/dev/null || true
        git ls-files --others --exclude-standard -- '*.py' 2>/dev/null || true
    } | sort -u | while IFS= read -r f; do
        [ -f "$f" ] && printf '%s\n' "$f"
    done
)

if [ "${#FILES[@]}" -eq 0 ]; then
    echo "typecheck-ratchet: no touched .py files vs $BASE_REF"
    exit 0
fi

# pyright error+warning count for a file at its real path (keeps project context).
count() {
    local summary e w
    summary="$("$PYRIGHT" "$1" 2>/dev/null | tail -n1 || true)"
    if [ -z "$summary" ]; then
        echo "ERR"
        return
    fi
    # Summary line looks like: "N errors, M warnings, K informations"
    e="$(printf '%s' "$summary" | grep -oE '^[0-9]+' || echo 0)"
    w="$(printf '%s' "$summary" | grep -oE '[0-9]+ warnings' | grep -oE '[0-9]+' || echo 0)"
    echo $(( e + w ))
}

fail=0
printf "typecheck-ratchet (base=%s)\n" "$BASE_REF"
printf -- "------------------------------------------------------------ %s\n" "------------------"
printf "%-55s %s\n" "file" "before -> after"
printf -- "------------------------------------------------------------ %s\n" "------------------"
for f in "${FILES[@]}"; do
    if git cat-file -e "$BASE_REF:$f" 2>/dev/null; then
        # Swap in the base version at the real path, count, then restore inline.
        # Backup is also registered for the EXIT trap so interruption is safe.
        bk="$(mktemp "$TMP/cur.XXXXXX")"
        cp "$f" "$bk"
        printf '%s\t%s\n' "$bk" "$f" >> "$RESTORE"
        git show "$BASE_REF:$f" > "$f"
        before="$(count "$f")"
        cp "$bk" "$f"
    else
        before=0   # new file: must be clean
    fi
    after="$(count "$f")"

    status="ok"
    if [ "$after" = "ERR" ] || [ "$before" = "ERR" ]; then
        status="ERROR running pyright"; fail=1
    elif [ "$before" -eq 0 ] && [ "$after" -ne 0 ]; then
        status="FAIL (was 0, must stay 0)"; fail=1
    elif [ "$before" -gt 0 ] && [ "$after" -ge "$before" ]; then
        status="FAIL (must be strictly less than $before)"; fail=1
    elif [ "$before" -gt 0 ] && [ "$after" -lt "$before" ]; then
        status="improved"
    fi
    printf "%-55s %4d -> %-4d  %s\n" "$f" "$before" "$after" "$status"
done

echo
if [ "$fail" -ne 0 ]; then
    echo "FAIL: ratchet violated. See AGENTS.md -> \"Static Checks (last step)\"."
    echo "Escape hatch: if a fix needs cross-module / signature / migration work,"
    echo "flag it to the user with the error text and file:line rather than"
    echo "silently expanding the diff."
    exit 1
fi
echo "OK: every touched file is cleaner (or still clean)."
