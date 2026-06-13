import uuid
import re


def parse_vcard(text):
    if not text:
        return {}
    lines = _unfold_lines(text.strip().splitlines())
    props = {}
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if upper.startswith("BEGIN:") or upper.startswith("END:") or upper.startswith("VERSION:") or upper.startswith("PRODID:"):
            continue
        name, params, value = _parse_property(stripped)
        key = name.upper()
        if key == "FN":
            props.setdefault("fn", value)
        elif key == "N":
            parts = value.split(";")
            props.setdefault("last_name", parts[0] if len(parts) > 0 else "")
            props.setdefault("first_name", parts[1] if len(parts) > 1 else "")
        elif key == "UID":
            props.setdefault("uid", value)
        elif key == "EMAIL":
            _assign_typed(props, "email", params, value, ["WORK", "HOME"])
        elif key == "TEL":
            _assign_typed(props, "tel", params, value, ["WORK", "HOME", "CELL"])
        elif key == "ORG":
            props.setdefault("org", value.rstrip(";").split(";")[0])
        elif key == "TITLE":
            props.setdefault("title", value)
        elif key == "NOTE":
            props.setdefault("note", value)
    props.setdefault("fn", "")
    props.setdefault("last_name", "")
    props.setdefault("first_name", "")
    props.setdefault("uid", "")
    return props


def generate_vcard(data, uid=None):
    if not uid:
        uid = data.get("uid") or str(uuid.uuid4())
    fn = (data.get("fn") or "").strip()
    if not fn:
        parts = [data.get("first_name") or "", data.get("last_name") or ""]
        fn = " ".join(p for p in parts if p).strip()
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"UID:{uid}",
        f"FN:{fn}",
        f"N:{data.get('last_name') or ''};{data.get('first_name') or ''};;;",
    ]
    if data.get("email_work"):
        lines.append(f"EMAIL;TYPE=WORK:{data['email_work']}")
    if data.get("email_home"):
        lines.append(f"EMAIL;TYPE=HOME:{data['email_home']}")
    if data.get("tel_work"):
        lines.append(f"TEL;TYPE=WORK:{data['tel_work']}")
    if data.get("tel_cell"):
        lines.append(f"TEL;TYPE=CELL:{data['tel_cell']}")
    if data.get("tel_home"):
        lines.append(f"TEL;TYPE=HOME:{data['tel_home']}")
    if data.get("org"):
        lines.append(f"ORG:{data['org']}")
    if data.get("title"):
        lines.append(f"TITLE:{data['title']}")
    if data.get("note"):
        lines.append(f"NOTE:{data['note']}")
    lines.append("END:VCARD")
    return "\r\n".join(lines)


def extract_uid(vcard_text):
    for line in vcard_text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("UID:"):
            return stripped[4:].strip()
    return None


def _unfold_lines(raw_lines):
    result = []
    for line in raw_lines:
        if line.startswith((" ", "\t")) and result:
            result[-1] += line[1:]
        else:
            result.append(line)
    return result


def _parse_property(line):
    colon_idx = line.find(":")
    if colon_idx == -1:
        return line.upper(), {}, ""
    left = line[:colon_idx]
    value = line[colon_idx + 1:]
    parts = left.split(";")
    name = parts[0].split(".", 1)[-1].upper()
    params = {}
    for p in parts[1:]:
        if "=" in p:
            pk, pv = p.split("=", 1)
            params[pk.upper()] = pv.upper()
        else:
            params.setdefault("TYPE", [])
            if isinstance(params["TYPE"], list):
                params["TYPE"].append(p.upper())
            else:
                params["TYPE"] = [params["TYPE"], p.upper()]
    return name, params, value


def _assign_typed(props, prefix, params, value, type_order):
    types = params.get("TYPE", [])
    if isinstance(types, str):
        types = [types]
    matched = False
    for t in type_order:
        if t in types:
            key = f"{prefix}_{t.lower()}"
            props.setdefault(key, value)
            matched = True
            break
    if not matched:
        for t in type_order:
            key = f"{prefix}_{t.lower()}"
            if key not in props:
                props[key] = value
                break
