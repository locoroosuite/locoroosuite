import re

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def is_valid_email(email: str) -> bool:
    if not email or not isinstance(email, str):
        return False
    cleaned = email.strip()
    if not _EMAIL_RE.match(cleaned):
        return False
    local, domain = cleaned.rsplit("@", 1)
    if ".." in domain:
        return False
    return True
