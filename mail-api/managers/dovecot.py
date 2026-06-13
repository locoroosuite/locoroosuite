from __future__ import annotations

import os
from typing import Any

from passlib.hash import sha256_crypt


class DovecotManager:
    def __init__(self, users_path: str = "/etc/dovecot/users", mail_root: str = "/var/mail/vhosts"):
        self.users_path = users_path
        self.mail_root = mail_root

    def _parse_users(self) -> dict[str, dict[str, Any]]:
        users: dict[str, dict[str, Any]] = {}
        if not os.path.exists(self.users_path):
            return users
        with open(self.users_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(":", 5)
                if len(parts) >= 2:
                    email = parts[0]
                    users[email] = {
                        "email": email,
                        "password_hash": parts[1],
                        "extra": parts[2:] if len(parts) > 2 else [],
                    }
        return users

    def _build_extra_fields(self, quota_bytes: int | None = None) -> list[str]:
        fields = [
            "5000",
            "5000",
            "/var/mail/vhosts/%d/%n",
        ]
        return fields

    def _write_users(self, users: dict[str, dict[str, Any]]) -> None:
        with open(self.users_path, "w") as f:
            for email in sorted(users.keys()):
                user = users[email]
                parts = [user["email"], user["password_hash"]] + user.get("extra", [])
                f.write(":".join(parts) + "\n")
        os.chmod(self.users_path, 0o644)

    def _hash_password(self, password: str) -> str:
        hashed = sha256_crypt.using(rounds=5000).hash(password)
        return f"{{SHA256-CRYPT}}{hashed}"

    def _maildir_path(self, email: str) -> str:
        local, domain = email.split("@", 1)
        return os.path.join(self.mail_root, domain, local)

    def list_users(self, domain: str = "") -> list[dict[str, Any]]:
        users = self._parse_users()
        result = []
        for email, data in sorted(users.items()):
            if domain and not email.endswith(f"@{domain}"):
                continue
            result.append({"email": email})
        return result

    def user_exists(self, email: str) -> bool:
        users = self._parse_users()
        return email in users

    def add_user(self, email: str, password: str, quota_bytes: int | None = None) -> None:
        users = self._parse_users()
        if email in users:
            raise FileExistsError(f"User {email} already exists")
        hashed = self._hash_password(password)
        users[email] = {
            "email": email,
            "password_hash": hashed,
            "extra": self._build_extra_fields(quota_bytes),
        }
        self._write_users(users)
        maildir = self._maildir_path(email)
        for sub in ("", "cur", "new", "tmp"):
            path = os.path.join(maildir, sub)
            os.makedirs(path, exist_ok=True)
            os.chown(path, 5000, 5000)

    def remove_user(self, email: str) -> None:
        users = self._parse_users()
        if email not in users:
            raise FileNotFoundError(f"User {email} not found")
        del users[email]
        self._write_users(users)

    def set_password(self, email: str, password: str) -> None:
        users = self._parse_users()
        if email not in users:
            raise FileNotFoundError(f"User {email} not found")
        users[email]["password_hash"] = self._hash_password(password)
        self._write_users(users)

    def set_quota(self, email: str, quota_bytes: int) -> None:
        users = self._parse_users()
        if email not in users:
            raise FileNotFoundError(f"User {email} not found")
        users[email]["extra"] = self._build_extra_fields(quota_bytes)
        self._write_users(users)
