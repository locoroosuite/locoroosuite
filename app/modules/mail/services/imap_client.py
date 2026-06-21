import email
import imaplib
import re
import socket
import ssl
import time
from email.utils import parsedate_to_datetime


def connect_imap(host, port, use_tls=True, timeout=10, ssl_context=None):
    if use_tls:
        ctx = ssl_context
        if ctx is None:
            import os
            if os.environ.get("APP_ENV", "development") == "development":
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
        return imaplib.IMAP4_SSL(host, port, timeout=timeout, ssl_context=ctx)
    return imaplib.IMAP4(host, port, timeout=timeout)


def login_imap(client, username, password=None):
    client.login(username, password)


_LIST_RE = re.compile(r'\((?P<flags>[^)]*)\)\s+"(?P<delim>[^"]*)"\s+(?P<name>.+)$')
_STATUS_RE = re.compile(r"\((?P<items>[^)]*)\)")
_FLAGS_RE = re.compile(r"FLAGS \((?P<flags>[^)]*)\)")
_UID_RE = re.compile(r"UID (?P<uid>\d+)")
_INTERNALDATE_RE = re.compile(r'INTERNALDATE\s+"(?P<date>[^"]+)"')


def _parse_list_entry(line):
    """Parse an IMAP LIST response line into ``(flags, name)``.

    ``flags`` is a set of lower-cased IMAP mailbox flags (e.g.
    ``{'\\noselect', '\\haschildren'}``); empty when the server's response does
    not conform to the standard shape. ``name`` is the decoded mailbox name, or
    ``None`` if nothing could be extracted.
    """
    try:
        decoded = line.decode()
    except Exception:
        decoded = str(line)
    match = _LIST_RE.match(decoded)
    if match:
        flags = {f.strip().lower() for f in match.group("flags").split() if f.strip()}
        name = match.group("name").strip()
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1]
        return flags, name
    # Fallback parsing for non-conforming servers: flags unknown, best-effort name.
    if ' "' in decoded:
        name = decoded.split(' "')[-1].strip().strip('"')
    elif ")" in decoded:
        name = decoded.split(")", 1)[1].strip().strip('"')
    else:
        name = decoded.strip().strip('"')
    return set(), (name or None)


def list_folders(client):
    status, data = client.list()
    folders = []
    if status != "OK":
        return folders
    for line in data:
        if not line:
            continue
        flags, folder = _parse_list_entry(line)
        if not folder:
            continue
        # Hide virtual/non-selectable mailboxes (e.g. synthesized parents like
        # Dovecot's "dovecot" produced when the Sieve active-script symlink lands
        # inside the maildir). Such mailboxes cannot be SELECTed and would only
        # cause perpetual sync errors.
        if "\\noselect" in flags or "\\nonexistent" in flags:
            continue
        folders.append(folder)
    return folders


def select_folder(client, folder):
    typ, dat = client.select(client._quote(folder))
    if typ != "OK":
        raise imaplib.IMAP4.error(f"SELECT failed for {folder}: {dat}")
    return typ, dat


def fetch_message_uids(client, criteria="ALL"):
    status, data = client.uid("SEARCH", None, criteria)
    if status != "OK" or not data:
        return []
    return data[0].split()


def _parse_fetch_item(item):
    if isinstance(item, tuple):
        meta = item[0]
        raw = item[1]
    else:
        meta = item
        raw = None
    try:
        meta_text = meta.decode() if isinstance(meta, (bytes, bytearray)) else str(meta)
    except Exception:
        meta_text = str(meta)
    flags = []
    uid = None
    internal_date = None
    flags_match = _FLAGS_RE.search(meta_text)
    if flags_match:
        flags = [f for f in flags_match.group("flags").split() if f]
    uid_match = _UID_RE.search(meta_text)
    if uid_match:
        uid = uid_match.group("uid")
    idate_match = _INTERNALDATE_RE.search(meta_text)
    if idate_match:
        try:
            internal_date = parsedate_to_datetime(idate_match.group("date"))
        except (TypeError, ValueError, IndexError):
            pass
    return uid, flags, raw, internal_date


def fetch_message(client, uid):
    status, data = client.uid("FETCH", uid, "(BODY.PEEK[] FLAGS)")
    if status != "OK" or not data:
        return None
    for item in data:
        _uid, _flags, raw, _ = _parse_fetch_item(item)
        if raw:
            return email.message_from_bytes(raw)
    return None


def fetch_message_with_flags(client, uid):
    status, data = client.uid("FETCH", uid, "(BODY.PEEK[] FLAGS INTERNALDATE)")
    if status != "OK" or not data:
        return None, [], None
    for item in data:
        _uid, flags, raw, internal_date = _parse_fetch_item(item)
        if raw:
            return email.message_from_bytes(raw), flags, internal_date
    return None, [], None


def fetch_raw_message(client, uid):
    status, data = client.uid("FETCH", uid, "(BODY.PEEK[])")
    if status != "OK" or not data:
        return None
    for item in data:
        _uid, _flags, raw, _ = _parse_fetch_item(item)
        if raw:
            return raw
    return None


def append_message(client, folder, raw_bytes, flags=None, date_time=None):
    append_flags = None
    if flags:
        append_flags = f"({' '.join(flags)})"
    append_date = imaplib.Time2Internaldate(date_time) if date_time else None
    status, data = client.append(folder, append_flags, append_date, raw_bytes)
    if status != "OK":
        raise imaplib.IMAP4.error(f"IMAP APPEND failed for {folder}")
    return status, data


def parse_append_uid(data):
    if not data:
        return None
    for item in data:
        raw = item if isinstance(item, bytes) else str(item).encode()
        match = re.search(rb"APPENDUID\s+\d+\s+(\d+)", raw)
        if match:
            return match.group(1).decode()
    return None


def delete_message_by_uid(client, uid):
    client.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
    client.expunge()


def create_folder(client, folder):
    return client.create(folder)


def rename_folder(client, old_name, new_name):
    return client.rename(old_name, new_name)


def delete_folder(client, folder):
    return client.delete(folder)


def get_folder_delimiter(client):
    status, data = client.list()
    if status != "OK" or not data:
        return "/"
    for line in data:
        if not line:
            continue
        try:
            decoded = line.decode() if isinstance(line, (bytes, bytearray)) else str(line)
        except Exception:
            decoded = str(line)
        match = _LIST_RE.match(decoded)
        if match and match.group("delim"):
            return match.group("delim")
    return "/"


def encode_mailbox_name(name):
    """Encode a mailbox name using modified UTF-7 (RFC 3501 Section 5.1.3).

    ASCII printable names pass through unchanged (the literal ``&`` becomes
    ``&-``); non-ASCII segments are encoded in modified BASE64 of UTF-16BE,
    wrapped in ``&...-`` with ``/`` replaced by ``,`` and padding stripped.
    Returns the wire-format string.
    """
    if name is None:
        return name
    out = []
    buf = []
    for ch in name:
        code = ord(ch)
        if 0x20 <= code <= 0x7E:
            if buf:
                out.append(_modified_utf7_encode("".join(buf)))
                buf = []
            out.append("&-" if ch == "&" else ch)
        else:
            buf.append(ch)
    if buf:
        out.append(_modified_utf7_encode("".join(buf)))
    return "".join(out)


def _modified_utf7_encode(text):
    import base64
    data = text.encode("utf-16-be")
    encoded = base64.b64encode(data).decode("ascii")
    return "&" + encoded.rstrip("=").replace("/", ",") + "-"


def ensure_folder_and_append(client, folder, raw_bytes, flags=None, date_time=None):
    try:
        return append_message(client, folder, raw_bytes, flags=flags, date_time=date_time)
    except imaplib.IMAP4.error:
        pass
    from app.modules.mail.services.folder_aliases import resolve_folder_name
    available = list_folders(client)
    resolved = resolve_folder_name(available, folder)
    try:
        return append_message(client, resolved, raw_bytes, flags=flags, date_time=date_time)
    except imaplib.IMAP4.error:
        pass
    create_folder(client, resolved)
    return append_message(client, resolved, raw_bytes, flags=flags, date_time=date_time)


def move_message(client, uid, destination):
    client.uid("COPY", uid, destination)
    client.uid("STORE", uid, "+FLAGS", "(\\Deleted)")


def set_flag(client, uid, flag, add=True):
    op = "+FLAGS" if add else "-FLAGS"
    client.uid("STORE", uid, op, f"({flag})")


def search_headers(client, query):
    return fetch_message_uids(client, f"OR SUBJECT \"{query}\" FROM \"{query}\"")


def search_full_text(client, query):
    return fetch_message_uids(client, f"TEXT \"{query}\"")


def search_header(client, header, value):
    status, data = client.uid("SEARCH", None, "HEADER", header, value)
    if status != "OK" or not data:
        return []
    return data[0].split()


def folder_status(client, folder):
    target = client._quote(folder) if hasattr(client, "_quote") else folder
    status, data = client.status(target, "(UIDVALIDITY UIDNEXT UNSEEN HIGHESTMODSEQ)")
    if status != "OK" or not data:
        return {}
    raw = data[0]
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
    except Exception:
        decoded = str(raw)
    match = _STATUS_RE.search(decoded)
    if not match:
        return {}
    items = match.group("items").split()
    info = {}
    for idx in range(0, len(items), 2):
        if idx + 1 >= len(items):
            break
        key = items[idx].upper()
        value = items[idx + 1]
        info[key] = value
    return info


def fetch_flags(client, uid_set):
    status, data = client.uid("FETCH", uid_set, "(FLAGS)")
    if status != "OK" or not data:
        return {}
    results = {}
    for item in data:
        uid, flags, _raw, _ = _parse_fetch_item(item)
        if uid:
            results[uid] = flags
    return results


def idle_wait(client, timeout=60):
    tag = client._new_tag()
    try:
        if getattr(client, "sock", None):
            client.sock.settimeout(None)
    except Exception:
        pass
    client.send(f"{tag} IDLE\r\n".encode())
    line = b""
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                if getattr(client, "sock", None):
                    client.sock.settimeout(2)
                candidate = client._get_line()
            except OSError as exc:
                if "timed out" in str(exc).lower():
                    continue
                raise
            if candidate.startswith(b"+"):
                line = candidate
                break
            if not line:
                line = candidate
    except OSError as exc:
        if "timed out" in str(exc).lower():
            return False, None
        raise
    if not line.startswith(b"+"):
        return False, line
    client.sock.settimeout(timeout)
    response = None
    try:
        response = client._get_line()
    except socket.timeout:
        response = None
    except OSError as exc:
        if "timed out" in str(exc).lower():
            response = None
        else:
            raise
    finally:
        try:
            client.send(b"DONE\r\n")
            client.sock.settimeout(5)
            while tag not in client.tagged_commands:
                client._get_response()
            del client.tagged_commands[tag]
        except Exception:
            pass
        try:
            client.sock.settimeout(None)
        except Exception:
            pass
    return True, response


def safe_logout(client, timeout=3):
    try:
        if getattr(client, "sock", None):
            client.sock.settimeout(timeout)
        client.logout()
        return True
    except socket.timeout:
        pass
    except Exception:
        pass
    try:
        client.shutdown()
    except Exception:
        pass
    return False
