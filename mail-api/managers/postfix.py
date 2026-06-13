from __future__ import annotations

import os
import subprocess
from typing import Any


class PostfixManager:
    def __init__(
        self,
        domains_path: str = "/etc/postfix/virtual_domains",
        aliases_path: str = "/etc/postfix/virtual",
        aliases_db_path: str = "/etc/postfix/virtual.db",
    ):
        self.domains_path = domains_path
        self.aliases_path = aliases_path
        self.aliases_db_path = aliases_db_path

    def _read_domains(self) -> list[str]:
        if not os.path.exists(self.domains_path):
            return []
        with open(self.domains_path, "r") as f:
            return [
                line.split()[0].strip().lower()
                for line in f
                if line.strip() and not line.startswith("#")
            ]

    def _write_domains(self, domains: list[str]) -> None:
        with open(self.domains_path, "w") as f:
            for domain in sorted(set(domains)):
                f.write(f"{domain}\tOK\n")

    def _postmap(self) -> None:
        try:
            subprocess.run(
                ["postmap", self.aliases_path],
                capture_output=True, text=True, timeout=10,
            )
        except FileNotFoundError:
            pass

    def list_domains(self) -> list[dict[str, Any]]:
        domains = self._read_domains()
        return [{"domain": d} for d in sorted(domains)]

    def add_domain(self, domain: str) -> None:
        domains = self._read_domains()
        if domain not in domains:
            domains.append(domain)
            self._write_domains(domains)

    def remove_domain(self, domain: str) -> None:
        domains = self._read_domains()
        if domain in domains:
            domains.remove(domain)
            self._write_domains(domains)
