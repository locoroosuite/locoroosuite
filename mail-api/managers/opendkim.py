from __future__ import annotations

import os
import re
import subprocess
from typing import Any


class OpenDKIMManager:
    def __init__(
        self,
        keys_dir: str = "/etc/opendkim/keys",
        key_table_path: str = "/etc/opendkim/key-table",
        signing_table_path: str = "/etc/opendkim/signing-table",
        selector: str = "default",
    ):
        self.keys_dir = keys_dir
        self.key_table_path = key_table_path
        self.signing_table_path = signing_table_path
        self.default_selector = selector

    def _resolve_selector(self, selector: str | None) -> str:
        return selector if selector else self.default_selector

    def _private_key_path(self, domain: str, selector: str | None = None) -> str:
        s = self._resolve_selector(selector)
        return os.path.join(self.keys_dir, f"{s}.{domain}.private")

    def _public_key_path(self, domain: str, selector: str | None = None) -> str:
        s = self._resolve_selector(selector)
        return os.path.join(self.keys_dir, f"{s}.{domain}.txt")

    def _key_table_entry(self, domain: str, selector: str | None = None) -> str:
        s = self._resolve_selector(selector)
        return f"{s}._domainkey.{domain} {domain}:{s}:{self._private_key_path(domain, s)}"

    def _signing_table_entry(self, domain: str, selector: str | None = None) -> str:
        s = self._resolve_selector(selector)
        return f"*@{domain} {s}._domainkey.{domain}"

    def _read_table(self, path: str) -> set[str]:
        if not os.path.exists(path):
            return set()
        with open(path, "r") as f:
            return {line.strip() for line in f if line.strip() and not line.startswith("#")}

    def _write_table(self, path: str, entries: set[str]) -> None:
        with open(path, "w") as f:
            for entry in sorted(entries):
                f.write(entry + "\n")

    def _remove_matching_entries(self, path: str, prefix: str) -> None:
        entries = self._read_table(path)
        filtered = {e for e in entries if not e.startswith(prefix)}
        self._write_table(path, filtered)

    def _extract_public_key(self, domain: str, selector: str | None = None) -> str:
        pub_path = self._public_key_path(domain, selector)
        if not os.path.exists(pub_path):
            raise FileNotFoundError(f"No public key found for {domain}")
        with open(pub_path, "r") as f:
            content = f.read()
        match = re.search(r'p=([A-Za-z0-9+/=]+)', content)
        if match:
            return match.group(1)
        pem_match = re.search(r'-----BEGIN PUBLIC KEY-----(.+?)-----END PUBLIC KEY-----', content, re.DOTALL)
        if pem_match:
            return pem_match.group(1).replace('\n', '').strip()
        raise ValueError(f"Could not parse public key from {pub_path}")

    def _reload_opendkim(self) -> None:
        try:
            subprocess.run(
                ["pkill", "-HUP", "opendkim"],
                check=False,
                timeout=5,
                capture_output=True,
            )
        except FileNotFoundError:
            pass

    def domain_has_key(self, domain: str, selector: str | None = None) -> bool:
        return os.path.exists(self._private_key_path(domain, selector))

    def generate_key(self, domain: str, key_size: int = 2048, selector: str | None = None) -> dict[str, Any]:
        domain = domain.strip().lower()
        s = self._resolve_selector(selector)
        priv_path = self._private_key_path(domain, s)
        pub_path = self._public_key_path(domain, s)

        os.makedirs(self.keys_dir, exist_ok=True)

        subprocess.run(
            ["openssl", "genrsa", "-out", priv_path, str(key_size)],
            check=True,
            capture_output=True,
            timeout=30,
        )
        subprocess.run(
            ["openssl", "rsa", "-in", priv_path, "-pubout", "-out", pub_path],
            check=True,
            capture_output=True,
            timeout=10,
        )

        self._remove_matching_entries(self.key_table_path, f"{s}._domainkey.{domain} ")
        self._remove_matching_entries(self.signing_table_path, f"*@{domain}")

        key_table = self._read_table(self.key_table_path)
        signing_table = self._read_table(self.signing_table_path)

        key_table.add(self._key_table_entry(domain, s))
        signing_table.add(self._signing_table_entry(domain, s))

        self._write_table(self.key_table_path, key_table)
        self._write_table(self.signing_table_path, signing_table)

        self._reload_opendkim()

        public_key = self._extract_public_key(domain, s)
        txt_record = f"v=DKIM1; k=rsa; p={public_key}"

        return {
            "domain": domain,
            "selector": s,
            "public_key": public_key,
            "txt_record": txt_record,
        }

    def get_key(self, domain: str, selector: str | None = None) -> dict[str, Any]:
        domain = domain.strip().lower()
        s = self._resolve_selector(selector)
        if not self.domain_has_key(domain, s):
            raise FileNotFoundError(f"No DKIM key found for {domain}")
        public_key = self._extract_public_key(domain, s)
        txt_record = f"v=DKIM1; k=rsa; p={public_key}"
        return {
            "domain": domain,
            "selector": s,
            "public_key": public_key,
            "txt_record": txt_record,
        }

    def remove_key(self, domain: str, selector: str | None = None) -> None:
        domain = domain.strip().lower()
        s = self._resolve_selector(selector)
        priv_path = self._private_key_path(domain, s)
        pub_path = self._public_key_path(domain, s)

        for path in (priv_path, pub_path):
            if os.path.exists(path):
                os.remove(path)

        self._remove_matching_entries(self.key_table_path, f"{s}._domainkey.{domain} ")
        self._remove_matching_entries(self.signing_table_path, f"*@{domain}")

        self._reload_opendkim()
