import uuid
from datetime import datetime, timezone, timedelta, date


def parse_icalendar(text):
    if not text:
        return {}
    lines = _unfold_lines(text.strip().splitlines())
    method = _extract_method(lines)
    vevent_lines = _extract_vevent_lines(lines)
    if vevent_lines is None:
        return {}
    result = _parse_vevent(vevent_lines)
    if method and "method" not in result:
        result["method"] = method
    return result


def _extract_method(lines):
    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("METHOD:"):
            return stripped[7:].strip().upper()
        if upper == "BEGIN:VEVENT":
            break
    return ""


def _extract_vevent_lines(lines):
    in_vevent = False
    vevent_lines = []
    for line in lines:
        upper = line.strip().upper()
        if upper == "BEGIN:VEVENT":
            in_vevent = True
            continue
        if upper == "END:VEVENT":
            if in_vevent:
                return vevent_lines
        if in_vevent:
            vevent_lines.append(line)
    return vevent_lines if in_vevent else None


def _parse_vevent(lines):
    props = {}
    alarms = []
    current_alarm = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if upper.startswith("BEGIN:") and not upper.startswith("BEGIN:VALARM"):
            continue
        if upper.startswith("END:") and not upper.startswith("END:VALARM"):
            continue
        if upper.startswith("BEGIN:VALARM"):
            current_alarm = {}
            continue
        if upper.startswith("END:VALARM"):
            if current_alarm is not None:
                alarms.append(current_alarm)
            current_alarm = None
            continue
        if current_alarm is not None:
            _parse_alarm_prop(current_alarm, stripped)
            continue
        name, params, value = _parse_property(stripped)
        key = name.upper()
        if key == "SUMMARY":
            props.setdefault("summary", value)
        elif key == "DESCRIPTION":
            props.setdefault("description", value)
        elif key == "LOCATION":
            props.setdefault("location", value)
        elif key == "UID":
            props.setdefault("uid", value)
        elif key == "DTSTART":
            dt, is_date = _parse_datetime(params, value)
            props["dtstart"] = dt
            props["all_day"] = is_date
            if "TZID" in params and "timezone" not in props:
                props["timezone"] = params["TZID"]
        elif key == "DTEND":
            dt, is_date = _parse_datetime(params, value)
            props["dtend"] = dt
        elif key == "DURATION":
            props.setdefault("duration", value)
        elif key == "RRULE":
            props.setdefault("rrule", value)
        elif key == "EXDATE":
            props.setdefault("exdates", [])
            props["exdates"].extend(_parse_date_list(params, value))
        elif key == "RDATE":
            props.setdefault("rdates", [])
            props["rdates"].extend(_parse_date_list(params, value))
        elif key == "RECURRENCE-ID":
            dt, _ = _parse_datetime(params, value)
            props["recurrence_id"] = dt
        elif key == "ORGANIZER":
            props["organizer"] = _parse_organizer(value, params)
        elif key == "ATTENDEE":
            props.setdefault("attendees", [])
            props["attendees"].append(_parse_attendee(value, params))
        elif key == "STATUS":
            props.setdefault("status", value.upper())
        elif key == "CATEGORIES":
            props.setdefault("categories", [c.strip() for c in value.split(",")])
        elif key == "CLASS":
            props.setdefault("class_", value.upper())
        elif key == "URL":
            props.setdefault("url", value)
        elif key == "SEQUENCE":
            props.setdefault("sequence", int(value))
        elif key == "CREATED":
            props.setdefault("created_at", _parse_datetime(params, value)[0])
        elif key == "LAST-MODIFIED":
            props.setdefault("last_modified", _parse_datetime(params, value)[0])
        elif key == "METHOD":
            props.setdefault("method", value.upper())

    props.setdefault("alarms", alarms)
    props.setdefault("summary", "")
    props.setdefault("uid", "")
    props.setdefault("status", "CONFIRMED")
    return props


def _parse_alarm_prop(alarm, line):
    name, params, value = _parse_property(line)
    key = name.upper()
    if key == "TRIGGER":
        alarm["trigger"] = value
    elif key == "ACTION":
        alarm["action"] = value.upper()
    elif key == "DESCRIPTION":
        alarm["description"] = value


def _parse_organizer(value, params):
    email = value.replace("mailto:", "").strip()
    cn = params.get("CN", email)
    return {"cn": cn, "email": email}


def _parse_attendee(value, params):
    email = value.replace("mailto:", "").strip()
    cn = params.get("CN", email)
    role = params.get("ROLE", "REQ-PARTICIPANT")
    partstat = params.get("PARTSTAT", "NEEDS-ACTION")
    rsvp = params.get("RSVP", "TRUE").upper() == "TRUE"
    return {"cn": cn, "email": email, "role": role, "partstat": partstat, "rsvp": rsvp}


def _parse_datetime(params, value):
    tzid = params.get("TZID")
    value = value.strip()
    is_date = params.get("VALUE") == "DATE" or len(value) == 8
    if is_date:
        try:
            return date(int(value[:4]), int(value[4:6]), int(value[6:8])).isoformat(), True
        except (ValueError, IndexError):
            return value, True
    if value.endswith("Z"):
        value = value[:-1]
        try:
            dt = datetime.strptime(value, "%Y%m%dT%H%M%S")
            return dt.replace(tzinfo=timezone.utc).isoformat(), False
        except ValueError:
            return value, False
    try:
        dt = datetime.strptime(value, "%Y%m%dT%H%M%S")
        if tzid:
            return dt.isoformat(), False
        return dt.replace(tzinfo=timezone.utc).isoformat(), False
    except ValueError:
        return value, False


def _parse_date_list(params, value):
    dates = []
    for v in value.split(","):
        v = v.strip()
        if v:
            dt, _ = _parse_datetime(params, v)
            dates.append(dt)
    return dates


def _generate_vtimezone_lines(tzid):
    try:
        from zoneinfo import ZoneInfo as _ZI
        tz = _ZI(tzid)
    except Exception:
        return []

    year = datetime.now().year

    def _offset_at(month):
        dt = datetime(year, month, 15, 12, 0, tzinfo=tz)
        return dt.utcoffset(), dt.dst() or timedelta(0)

    jan_off, jan_dst = _offset_at(1)
    jul_off, jul_dst = _offset_at(7)

    def fmt(td):
        total = int(td.total_seconds())
        sign = "+" if total >= 0 else "-"
        total = abs(total)
        h, m = divmod(total, 3600)
        return f"{sign}{h:02d}{m:02d}"

    lines = ["BEGIN:VTIMEZONE", f"TZID:{tzid}"]

    if jan_off == jul_off:
        lines += [
            "BEGIN:STANDARD",
            "DTSTART:19700101T000000",
            f"TZOFFSETFROM:{fmt(jan_off)}",
            f"TZOFFSETTO:{fmt(jan_off)}",
            "END:STANDARD",
        ]
    else:
        std_off = jan_off if jan_dst == timedelta(0) else jul_off
        dst_off = jul_off if jan_dst == timedelta(0) else jan_off
        lines += [
            "BEGIN:STANDARD",
            "DTSTART:19700401T030000",
            f"TZOFFSETFROM:{fmt(dst_off)}",
            f"TZOFFSETTO:{fmt(std_off)}",
            "END:STANDARD",
            "BEGIN:DAYLIGHT",
            "DTSTART:19701001T020000",
            f"TZOFFSETFROM:{fmt(std_off)}",
            f"TZOFFSETTO:{fmt(dst_off)}",
            "END:DAYLIGHT",
        ]

    lines.append("END:VTIMEZONE")
    return lines


def generate_icalendar(data, uid=None):
    if not uid:
        uid = data.get("uid") or str(uuid.uuid4())
    tzid = data.get("timezone")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//LocoRooSuite//Calendar//EN",
    ]
    if tzid:
        vtz = _generate_vtimezone_lines(tzid)
        if vtz:
            lines.extend(vtz)
    method = data.get("method")
    if method:
        lines.append(f"METHOD:{method}")
    lines.append("BEGIN:VEVENT")
    lines.append(f"UID:{uid}")
    lines.append(f"DTSTAMP:{_format_utc_now()}")
    summary = data.get("summary", "").strip()
    if summary:
        lines.append(f"SUMMARY:{summary}")
    description = (data.get("description") or "").strip()
    if description:
        lines.append(f"DESCRIPTION:{description}")
    location = (data.get("location") or "").strip()
    if location:
        lines.append(f"LOCATION:{location}")
    tzid = data.get("timezone")
    dtstart = data.get("dtstart")
    if dtstart:
        all_day = data.get("all_day", False)
        if all_day:
            lines.append(f"DTSTART;VALUE=DATE:{_format_dt(dtstart, True)}")
        elif tzid:
            lines.append(f"DTSTART;TZID={tzid}:{_format_dt(dtstart, False, utc=False)}")
        else:
            lines.append(f"DTSTART:{_format_dt(dtstart, False)}")
    dtend = data.get("dtend")
    if dtend:
        all_day = data.get("all_day", False)
        if all_day:
            lines.append(f"DTEND;VALUE=DATE:{_format_dt(dtend, True)}")
        elif tzid:
            lines.append(f"DTEND;TZID={tzid}:{_format_dt(dtend, False, utc=False)}")
        else:
            lines.append(f"DTEND:{_format_dt(dtend, False)}")
    duration = data.get("duration")
    if duration and not dtend:
        lines.append(f"DURATION:{duration}")
    rrule = data.get("rrule")
    if rrule:
        lines.append(f"RRULE:{rrule}")
    exdates = data.get("exdates") or []
    if exdates:
        lines.append(f"EXDATE:{','.join(exdates)}")
    rdates = data.get("rdates") or []
    if rdates:
        lines.append(f"RDATE:{','.join(rdates)}")
    organizer = data.get("organizer")
    if organizer:
        cn = organizer.get("cn", "")
        email = organizer.get("email", "")
        lines.append(f"ORGANIZER;CN={cn}:mailto:{email}")
    attendees = data.get("attendees") or []
    for att in attendees:
        parts = [f"CN={att.get('cn', '')}"]
        parts.append(f"ROLE={att.get('role', 'REQ-PARTICIPANT')}")
        parts.append(f"PARTSTAT={att.get('partstat', 'NEEDS-ACTION')}")
        parts.append(f"RSVP={att.get('rsvp', 'TRUE')}")
        lines.append(f"ATTENDEE;{';'.join(parts)}:mailto:{att.get('email', '')}")
    status = data.get("status")
    if status:
        lines.append(f"STATUS:{status}")
    categories = data.get("categories")
    if categories:
        lines.append(f"CATEGORIES:{','.join(categories)}")
    class_ = data.get("class_")
    if class_:
        lines.append(f"CLASS:{class_}")
    url = data.get("url")
    if url:
        lines.append(f"URL:{url}")
    sequence = data.get("sequence", 0)
    lines.append(f"SEQUENCE:{sequence}")
    alarms = data.get("alarms") or []
    for alarm in alarms:
        lines.append("BEGIN:VALARM")
        trigger = alarm.get("trigger", "-PT15M")
        action = alarm.get("action", "DISPLAY")
        lines.append(f"TRIGGER:{trigger}")
        lines.append(f"ACTION:{action}")
        alarm_desc = alarm.get("description", summary or "Reminder")
        lines.append(f"DESCRIPTION:{alarm_desc}")
        lines.append("END:VALARM")
    lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


def extract_uid(ical_text):
    for line in ical_text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("UID:"):
            return stripped[4:].strip()
    return None


def _format_dt(dt_str, all_day=False, utc=True):
    if all_day:
        return dt_str[:10].replace("-", "")
    try:
        dt = datetime.fromisoformat(dt_str)
        if utc and dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        fmt = "%Y%m%dT%H%M%SZ" if utc else "%Y%m%dT%H%M%S"
        return dt.strftime(fmt)
    except (ValueError, TypeError):
        return dt_str


def _format_utc_now():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _unfold_lines(raw_lines):
    result = []
    for line in raw_lines:
        if line.startswith((" ", "\t")) and result:
            result[-1] += line[1:]
        else:
            result.append(line)
    return result


def _split_components(lines):
    components = []
    current_type = None
    current_lines = []
    depth = 0
    for line in lines:
        upper = line.strip().upper()
        if upper.startswith("BEGIN:"):
            comp_type = upper[6:]
            if depth == 0:
                current_type = comp_type
                current_lines = []
            else:
                current_lines.append(line)
            depth += 1
        elif upper.startswith("END:"):
            depth -= 1
            if depth == 0 and current_type:
                components.append((current_type, current_lines))
                current_type = None
                current_lines = []
            else:
                current_lines.append(line)
        elif depth > 0:
            current_lines.append(line)
    return components


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
            params[pk.upper()] = pv
        else:
            params.setdefault("TYPE", [])
            if isinstance(params["TYPE"], list):
                params["TYPE"].append(p.upper())
            else:
                params["TYPE"] = [params["TYPE"], p.upper()]
    return name, params, value
