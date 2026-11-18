#!/usr/bin/env bash
# scripts/audit-images.sh — Docker image freshness audit (HLD U26.11).
#
# Compares every container image reference in the repo (Dockerfile FROM /
# COPY --from lines and compose `image:` entries) against the container
# registries and reports drift. Run it as part of every release (see
# CONTRIBUTING.md → Releases) and weekly via .github/workflows/image-audit.yml.
#
# Modes (per policy-table entry):
#   fail   — a newer tag matching the pattern is a release blocker (OUTDATED)
#   report — a newer tag is reported but does not fail the audit (deliberate
#            migrations, e.g. database major versions)
#   info   — floating ref: resolve and print it (no comparison possible)
#
# Digest pins (repo@sha256:...) are resolved to their tag by comparing
# manifest-list digests of the newest candidate tags, then version-compared
# like a tag pin.
#
# Fail-early: any ref found in the repo but missing from the policy table
# below fails the audit — new images must be registered here, so the table
# can never silently drift from reality.
#
# Exit codes: 0 current, 1 outdated/broken/unknown refs, 2 environment error.
#
# Requirements: bash, curl, jq, GNU sort (-V). No docker needed.
set -uo pipefail

# --- policy table ------------------------------------------------------------
# Format: repo|pinned-ref|tag-pattern|mode[|even]
#   pinned-ref  : tag, or @sha256:<digest> for digest pins
#   tag-pattern : extended regex selecting this repo's stable release tags
#   even        : (optional) among X.Y candidates keep even Y — nginx numbers
#                 stable branches with even minors (1.30, 1.32, ...)
POLICY=(
  "python|3.15-slim|^[0-9]+\\.[0-9]+-slim$|fail"
  "debian|trixie-slim|^trixie-slim$|info"
  "alpine|3.24|^[0-9]+\\.[0-9]+$|fail"
  "busybox|musl|^musl$|info"
  "dovecot/dovecot|2.4.5|^2\\.[0-9]+\\.[0-9]+$|fail"
  "coturn/coturn|4.19|^[0-9]+\\.[0-9]+$|fail"
  "rspamd/rspamd|4.3|^[0-9]+\\.[0-9]+$|fail"
  "matrixdotorg/synapse|v1.165.0|^v[0-9]+\\.[0-9]+\\.[0-9]+$|fail"
  "collabora/code|@sha256:95f29f1c0a1778999125f53f058d968fe0afb37c95e32ebfb9f3444140faebd2|^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+(\\.[0-9]+)?$|fail"
  "collabora/code|26.04.6.1.1|^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+(\\.[0-9]+)?$|fail"
  "nginx|1.30-alpine|^[0-9]+\\.[0-9]+-alpine$|fail|even"
  "postgres|18-alpine|^[0-9]+-alpine$|report"
  "redis|8-alpine|^[0-9]+-alpine$|report"
  "tomsquest/docker-radicale|latest|^latest$|info"
  "ghcr.io/element-hq/matrix-authentication-service|1.29.0|^[0-9]+\\.[0-9]+\\.[0-9]+$|fail"
)

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$ROOT/docker-compose.dev.yml" ] || { echo "AUDIT ERROR: $ROOT is not the repo root (docker-compose.dev.yml missing)" >&2; exit 2; }
failures=0

die_env() { echo "AUDIT ERROR: $*" >&2; exit 2; }
command -v curl >/dev/null || die_env "curl is required"
command -v jq >/dev/null || die_env "jq is required"

# --- registry access ---------------------------------------------------------

# hub_tags <repo> — print "name<TAB>date" for every tag, newest first.
hub_tags() {
  local repo="$1" url page
  # Official images live under the library/ namespace in the Hub API.
  [[ "$repo" != */* ]] && repo="library/$repo"
  url="https://hub.docker.com/v2/repositories/${repo}/tags?page_size=100"
  while [ -n "$url" ]; do
    page=$(curl -sS -m 30 "$url") || die_env "cannot reach hub.docker.com for $repo"
    jq -r '.results[]? | [.name, (.tag_last_pushed // "" | split("T")[0])] | @tsv' <<<"$page"
    url=$(jq -r '.next // empty' <<<"$page")
  done
}

# ghcr_tags <repo-without-registry-prefix> — same shape as hub_tags.
ghcr_tags() {
  local repo="$1" token last batch next
  token=$(curl -sS -m 30 "https://ghcr.io/token?scope=repository:${repo}:pull" | jq -r '.token // empty')
  [ -n "$token" ] || die_env "cannot get ghcr.io token for $repo"
  last=""
  while :; do
    if [ -z "$last" ]; then
      batch=$(curl -sS -m 30 -H "Authorization: Bearer $token" \
        "https://ghcr.io/v2/${repo}/tags/list?n=1000")
    else
      batch=$(curl -sS -m 30 -H "Authorization: Bearer $token" \
        "https://ghcr.io/v2/${repo}/tags/list?n=1000&last=${last}")
    fi
    next=$(jq -r '.tags | length' <<<"$batch" 2>/dev/null)
    [ "$next" -gt 0 ] 2>/dev/null || break
    # ghcr tags carry no push timestamps; print empty dates.
    jq -r '.tags[] | "\t\(.)"' <<<"$batch" | awk -F'\t' '{print $2 "\t" ""}'
    last=$(jq -r '.tags | last' <<<"$batch")
    [ "$next" -lt 1000 ] && break
  done
}

# manifest_digest <full-repo-with-registry> <tag> — manifest-list digest.
manifest_digest() {
  local repo="$1" tag="$2" auth reg accept hdrs
  if [[ "$repo" == ghcr.io/* ]]; then
    repo="${repo#ghcr.io/}"
    auth=$(curl -sS -m 30 "https://ghcr.io/token?scope=repository:${repo}:pull" | jq -r '.token // empty')
    reg="https://ghcr.io"
  else
    auth=$(curl -sS -m 30 "https://auth.docker.io/token?service=registry.docker.io&scope=repository:${repo}:pull" | jq -r '.token // empty')
    reg="https://registry-1.docker.io"
  fi
  [ -n "$auth" ] || die_env "cannot get registry token for $repo"
  accept="application/vnd.docker.distribution.manifest.list.v2+json"
  hdrs=$(curl -sS -m 30 -o /dev/null --head -D - \
    -H "Authorization: Bearer $auth" -H "Accept: ${accept}" \
    "$reg/v2/${repo}/manifests/${tag}") || die_env "cannot reach registry for $repo:$tag"
  awk 'tolower($1) == "docker-content-digest:" {gsub("\r",""); print $2}' <<<"$hdrs" | head -1
}

# --- helpers -----------------------------------------------------------------

# normalize <tag> — strip leading v and -alpine/-slim suffixes for -V sorting.
normalize() { sed -e 's/^v//' -e 's/-alpine$//' -e 's/-slim$//' -e 's/-musl$//' <<<"$1"; }

declare -A TAGCACHE
repo_tags() { # repo-with-registry -> "name<TAB>date" lines (cached)
  local key="$1"
  [ -n "${TAGCACHE[$key]:-}" ] && { printf '%s\n' "${TAGCACHE[$key]}"; return; }
  local out
  if [[ "$key" == ghcr.io/* ]]; then
    out=$(ghcr_tags "${key#ghcr.io/}")
  else
    out=$(hub_tags "$key")
  fi
  TAGCACHE[$key]="$out"
  printf '%s\n' "$out"
}

# newest_matching <lines> <pattern> [even] — newest tag by version sort.
newest_matching() {
  local lines="$1" pattern="$2" even="${3:-}"
  local tag v minor
  {
    while IFS=$'\t' read -r tag _; do
      [ -n "$tag" ] || continue
      [[ "$tag" =~ $pattern ]] || continue
      v=$(normalize "$tag")
      if [ "$even" = "even" ]; then
        minor=$(cut -d. -f2 <<<"$v")
        [ $((minor % 2)) -eq 0 ] || continue
      fi
      printf '%s\n' "$v"
    done <<<"$lines"
  } | sort -V | tail -1
}

# --- discover repo refs ------------------------------------------------------

mapfile -t found_refs < <(
  {
    find "$ROOT" -name 'Dockerfile*' -not -path '*/node_modules/*' -not -path '*/venv/*' -not -path '*/.git/*' -type f -print0 2>/dev/null |
      xargs -0 -r grep -hE '^[[:space:]]*(FROM|COPY[[:space:]]+--from=)' 2>/dev/null |
      sed -nE \
        -e 's/^[[:space:]]*FROM[[:space:]].*$/&/p' \
        -e 's/^[[:space:]]*COPY[[:space:]]+--from=([^ ]+).*$/COPYFROM \1/p' |
      sed -E \
        -e 's/^[[:space:]]*FROM[[:space:]]+(--[^ ]+[[:space:]]+)*([^ ]+).*/FROMREF \2/' \
        -e 's/^COPYFROM (.*)/\1/' |
      awk '
        /^FROMREF/ { print $2; next }
        # Image refs contain ":" or "@"; named build stages do not.
        /[:@]/ { print }'
    find "$ROOT" -name 'docker-compose*.yml' -not -path '*/node_modules/*' -not -path '*/venv/*' -not -path '*/.git/*' -type f -print0 2>/dev/null |
      xargs -0 -r grep -hE '^[[:space:]]*image:' 2>/dev/null |
      sed -e 's/^[[:space:]]*image:[[:space:]]*//' -e 's/^["'\'']//' -e 's/["'\'']$//'
  } | sort -u
)
[ "${#found_refs[@]}" -gt 0 ] || die_env "no image refs discovered — check the find patterns"

ref_repo() { # python:3.15-slim -> python ; repo@sha256:.. -> repo
  local r="${1%%@*}"
  echo "${r%:*}"
}
ref_pin() { # python:3.15-slim -> 3.15-slim ; repo@sha256:x -> @sha256:x
  local r="$1"
  if [[ "$r" == *"@"* ]]; then echo "@${r#*@}"; else echo "${r##*:}"; fi
}

printf '%-9s %-58s %s\n' "STATUS" "REF" "DETAIL"
printf '%.s-' {1..140}; echo

for entry in "${POLICY[@]}"; do
  IFS='|' read -r p_repo p_pin p_pattern p_mode p_flag <<<"$entry"

  # Skip table rows whose ref is not actually used in the repo right now
  # (stale rows would otherwise audit ghosts).
  used=0
  for r in "${found_refs[@]}"; do
    [ "$(ref_repo "$r")" = "$p_repo" ] && [ "$(ref_pin "$r")" = "$p_pin" ] && used=1
  done
  if [ "$used" -eq 0 ]; then
    printf '%-9s %-58s %s\n' "STALE" "$p_repo:$p_pin" "policy entry not found in repo — remove or fix it"
    failures=$((failures + 1))
    continue
  fi

  tags=$(repo_tags "$p_repo")
  [ -n "$tags" ] || die_env "no tags returned for $p_repo"

  newest=$(newest_matching "$tags" "$p_pattern" "$p_flag")
  [ -n "$newest" ] || die_env "no tags match '$p_pattern' for $p_repo — fix the policy pattern"

  if [[ "$p_pin" == @sha256:* ]]; then
    # Resolve the digest to a tag among the most recently pushed candidates.
    pinned_digest="${p_pin#@}"
    pinned_ver="?"
    checked=0
    while IFS=$'\t' read -r tag _; do
      [[ "$tag" =~ $p_pattern ]] || continue
      checked=$((checked + 1)); [ "$checked" -gt 10 ] && break
      d=$(manifest_digest "$p_repo" "$tag")
      if [ "$d" = "$pinned_digest" ]; then pinned_ver=$(normalize "$tag"); break; fi
    done <<<"$tags"
    if [ "$pinned_ver" = "?" ]; then
      printf '%-9s %-58s %s\n' "UNKNOWN" "$p_repo@…" "digest not among the 10 newest tags — repin deliberately"
      failures=$((failures + 1))
      continue
    fi
    pinned_disp="$p_repo@$pinned_ver"
    pin_cmp="$pinned_ver"
  else
    pin_cmp=$(normalize "$p_pin")
    pinned_disp="$p_repo:$p_pin"
    # The pinned tag must still exist upstream (exact match, tag + TAB).
    if ! grep -qF "$(printf '%s\t' "$p_pin")" <<<"$tags"; then
      printf '%-9s %-58s %s\n' "BROKEN" "$pinned_disp" "tag no longer exists upstream"
      failures=$((failures + 1))
      continue
    fi
  fi

  if [ "$pin_cmp" = "$newest" ]; then
    printf '%-9s %-58s %s\n' "OK" "$pinned_disp" "latest matching: $newest"
  elif [ "$p_mode" = "fail" ]; then
    printf '%-9s %-58s %s\n' "OUTDATED" "$pinned_disp" "newer available: $newest — bump before the next release"
    failures=$((failures + 1))
  elif [ "$p_mode" = "report" ]; then
    printf '%-9s %-58s %s\n' "REPORT" "$pinned_disp" "newer available: $newest (deliberate migration — decide, don't auto-bump)"
  else
    printf '%-9s %-58s %s\n' "INFO" "$pinned_disp" "floating ref — resolves to latest ($newest)"
  fi
done

# Unknown repo refs (discovered but not in the policy table).
for r in "${found_refs[@]}"; do
  rr=$(ref_repo "$r"); rp=$(ref_pin "$r")
  known=0
  for entry in "${POLICY[@]}"; do
    IFS='|' read -r p_repo p_pin _ <<<"$entry"
    [ "$p_repo" = "$rr" ] && [ "$p_pin" = "$rp" ] && known=1
  done
  if [ "$known" -eq 0 ]; then
    printf '%-9s %-58s %s\n' "UNKNOWN" "$r" "add to the policy table in scripts/audit-images.sh"
    failures=$((failures + 1))
  fi
done

echo
if [ "$failures" -gt 0 ]; then
  echo "AUDIT FAILED: $failures issue(s). Bump the images, rebuild (make restart), run the e2e suite."
  exit 1
fi
echo "AUDIT PASSED: all tracked images are current."
