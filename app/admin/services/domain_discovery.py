from dataclasses import dataclass
from typing import List, Optional, Tuple

import dns.resolver


@dataclass(frozen=True)
class DiscoveryCandidate:
    host: str
    port: int
    label: str
    tls_mode: Optional[str] = None


def _resolve_srv(resolver: dns.resolver.Resolver, name: str) -> List[Tuple[str, int, int, int]]:
    try:
        answers = resolver.resolve(name, "SRV")
    except Exception:
        return []
    records = []
    for rdata in answers:
        target = str(rdata.target).rstrip(".")
        records.append((target, int(rdata.port), int(rdata.priority), int(rdata.weight)))
    records.sort(key=lambda item: (item[2], -item[3]))
    return records


def _has_address(resolver: dns.resolver.Resolver, hostname: str) -> bool:
    try:
        resolver.resolve(hostname, "A")
        return True
    except Exception:
        pass
    try:
        resolver.resolve(hostname, "AAAA")
        return True
    except Exception:
        return False


def discover_domain_settings(domain_name: str):
    resolver = dns.resolver.Resolver()
    resolver.lifetime = 2.5

    imap_candidates: List[DiscoveryCandidate] = []
    smtp_candidates: List[DiscoveryCandidate] = []

    for service, label, port_hint in (
        ("_imaps._tcp", "imaps", 993),
        ("_imap._tcp", "imap", 143),
    ):
        for host, port, _, _ in _resolve_srv(resolver, f"{service}.{domain_name}"):
            imap_candidates.append(DiscoveryCandidate(host=host, port=port or port_hint, label=f"SRV {label}"))

    for service, label, tls_mode, port_hint in (
        ("_submission._tcp", "submission", "starttls", 587),
        ("_smtps._tcp", "smtps", "smtps", 465),
        ("_smtp._tcp", "smtp", "starttls", 25),
    ):
        for host, port, _, _ in _resolve_srv(resolver, f"{service}.{domain_name}"):
            smtp_candidates.append(
                DiscoveryCandidate(host=host, port=port or port_hint, label=f"SRV {label}", tls_mode=tls_mode)
            )

    for hostname, port, label in (
        (f"imap.{domain_name}", 993, "A/AAAA imap"),
        (f"mail.{domain_name}", 993, "A/AAAA mail (imap)"),
    ):
        if _has_address(resolver, hostname):
            imap_candidates.append(DiscoveryCandidate(host=hostname, port=port, label=label))

    for hostname, port, label, tls_mode in (
        (f"smtp.{domain_name}", 587, "A/AAAA smtp", "starttls"),
        (f"mail.{domain_name}", 587, "A/AAAA mail (smtp)", "starttls"),
    ):
        if _has_address(resolver, hostname):
            smtp_candidates.append(DiscoveryCandidate(host=hostname, port=port, label=label, tls_mode=tls_mode))

    try:
        mx_answers = resolver.resolve(domain_name, "MX")
    except Exception:
        mx_answers = []
    for rdata in mx_answers:
        host = str(rdata.exchange).rstrip(".")
        smtp_candidates.append(
            DiscoveryCandidate(host=host, port=587, label="MX fallback", tls_mode="starttls")
        )

    imap_candidates = _dedupe_candidates(imap_candidates)
    smtp_candidates = _dedupe_candidates(smtp_candidates)

    return {
        "imap_candidates": imap_candidates,
        "smtp_candidates": smtp_candidates,
        "imap_primary": imap_candidates[0] if imap_candidates else None,
        "smtp_primary": smtp_candidates[0] if smtp_candidates else None,
    }


def _dedupe_candidates(candidates: List[DiscoveryCandidate]) -> List[DiscoveryCandidate]:
    seen = set()
    unique = []
    for candidate in candidates:
        key = (candidate.host, candidate.port, candidate.tls_mode)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique
