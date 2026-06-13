from __future__ import annotations

import logging
import re
import socket
from dataclasses import dataclass
from typing import Any

import dns.resolver
import dns.rdatatype

logger = logging.getLogger(__name__)

DNS_TIMEOUT = 5

STATUS_NOT_CONFIGURED = "not_configured"
STATUS_MISMATCH = "mismatch"
STATUS_PROPAGATING = "propagating"
STATUS_VERIFIED = "verified"

DNS_INSTRUCTIONS: dict[str, dict[str, str]] = {
    "mx": {
        STATUS_NOT_CONFIGURED: "Add an MX record to your domain's DNS matching the value in the <strong>Expected value</strong> box. If the record already exists, verify the hostname and priority match exactly.",
        STATUS_PROPAGATING: "DNS propagation in progress — some nameservers have the correct record. This usually resolves within a few hours. No action needed.",
    },
    "spf": {
        STATUS_NOT_CONFIGURED: "Add a TXT record at your domain root with the value in the <strong>Expected value</strong> box. This authorizes the platform mail server to send email on behalf of your domain.",
        STATUS_MISMATCH: "An SPF record exists but does not authorize the platform mail servers. Update the TXT record at your domain root to match the value in the <strong>Expected value</strong> box.",
        STATUS_PROPAGATING: "DNS propagation in progress — some nameservers have the correct record. This usually resolves within a few hours. No action needed.",
    },
    "dkim": {
        STATUS_NOT_CONFIGURED: "",
        STATUS_MISMATCH: "",
        STATUS_PROPAGATING: "DNS propagation in progress — some nameservers have the correct record. This usually resolves within a few hours. No action needed.",
    },
    "dmarc": {
        STATUS_NOT_CONFIGURED: "",
        STATUS_MISMATCH: "",
        STATUS_PROPAGATING: "DNS propagation in progress — some nameservers have the correct record. This usually resolves within a few hours. No action needed.",
    },
}


def _get_instructions(record_type: str, status: str, details: str = "", *, domain_name: str = "", dkim_selector: str = "") -> str:
    if record_type == "dkim":
        dns_name = f"{dkim_selector}._domainkey.{domain_name}" if dkim_selector and domain_name else "&lt;selector&gt;._domainkey.&lt;your-domain&gt;"
        if status == STATUS_NOT_CONFIGURED:
            if "No DKIM public key" in details:
                return f'No DKIM signing key exists yet. Generate one in the <a href="#dkim-settings" class="underline font-medium">DKIM signing key</a> section above, then add the resulting TXT record to your DNS.'
            return f'Add a TXT record at <code class="bg-slate-100 px-1 rounded">{dns_name}</code> with the value from the <a href="#dkim-settings" class="underline font-medium">DKIM signing key</a> section above. This signs outgoing mail and improves deliverability.'
        if status == STATUS_MISMATCH:
            return f'A DKIM TXT record exists but the public key does not match. Copy the correct value from the <a href="#dkim-settings" class="underline font-medium">DKIM signing key</a> section above and update the record at <code class="bg-slate-100 px-1 rounded">{dns_name}</code>.'
    if record_type == "dmarc" and domain_name:
        dmarc_name = f"_dmarc.{domain_name}"
        if status == STATUS_NOT_CONFIGURED:
            return f'Add a TXT record at <code class="bg-slate-100 px-1 rounded">{dmarc_name}</code> with the value in the <strong>Expected value</strong> box. DMARC tells receiving servers what to do with email that fails SPF or DKIM checks.'
        if status == STATUS_MISMATCH:
            return f'A DMARC record exists but the policy or report email is incorrect. Update the TXT record at <code class="bg-slate-100 px-1 rounded">{dmarc_name}</code> to match the value in the <strong>Expected value</strong> box.'
    return DNS_INSTRUCTIONS.get(record_type, {}).get(status, "")


@dataclass
class DnsCheckResult:
    record_type: str
    status: str
    expected: str
    found: list[str] | None = None
    nameservers_checked: int = 0
    nameservers_ok: int = 0
    details: str = ""


def _get_authoritative_nameservers(domain: str) -> list[str]:
    resolver = dns.resolver.Resolver()
    resolver.lifetime = DNS_TIMEOUT

    try:
        ns_answers = resolver.resolve(domain, "NS")
        return sorted({str(rdata.target).rstrip(".") for rdata in ns_answers})
    except Exception:
        pass

    parts = domain.split(".")
    for i in range(1, len(parts)):
        parent = ".".join(parts[i:])
        try:
            ns_answers = resolver.resolve(parent, "NS")
            return sorted({str(rdata.target).rstrip(".") for rdata in ns_answers})
        except Exception:
            continue

    return []


def _resolve_ns_ips(ns_names: list[str]) -> list[str]:
    resolver = dns.resolver.Resolver()
    resolver.lifetime = DNS_TIMEOUT
    ips = []
    for name in ns_names:
        try:
            for rdata in resolver.resolve(name, "A"):
                ips.append(str(rdata))
        except Exception:
            pass
    return ips


def _query_record_at_ns(domain: str, rtype: str, ns_ip: str) -> list[str]:
    resolver = dns.resolver.Resolver()
    resolver.lifetime = DNS_TIMEOUT
    resolver.nameservers = [ns_ip]

    try:
        answers = resolver.resolve(domain, rtype)
        results = []
        for rdata in answers:
            if rtype == "MX":
                results.append(f"{rdata.preference} {str(rdata.exchange).rstrip('.')}")
            elif rtype == "TXT":
                txt = rdata.strings
                if isinstance(txt, (list, tuple)):
                    txt = b"".join(txt)
                if isinstance(txt, bytes):
                    txt = txt.decode("utf-8", errors="replace")
                results.append(str(txt))
            else:
                results.append(str(rdata))
        return results
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        return []
    except Exception:
        return []


def _check_mx(domain: str, expected_hosts: list[dict[str, Any]]) -> DnsCheckResult:
    if not expected_hosts:
        return DnsCheckResult(
            record_type="MX",
            status=STATUS_NOT_CONFIGURED,
            expected="",
            details="No MX servers configured in platform settings.",
        )

    expected_entries = sorted(
        [f"{e.get('priority', 10)} {e['host'].rstrip('.')}" for e in expected_hosts],
    )
    expected_str = "\n".join(
        [f"@  IN  MX  {e.get('priority', 10)}  {e['host']}." for e in expected_hosts],
    )

    ns_names = _get_authoritative_nameservers(domain)
    if not ns_names:
        return DnsCheckResult(
            record_type="MX",
            status=STATUS_NOT_CONFIGURED,
            expected=expected_str,
            details="Could not resolve authoritative nameservers.",
        )

    ns_ips = _resolve_ns_ips(ns_names)
    if not ns_ips:
        return DnsCheckResult(
            record_type="MX",
            status=STATUS_NOT_CONFIGURED,
            expected=expected_str,
            details="Could not resolve nameserver IP addresses.",
        )

    ns_ok = 0
    all_found: list[list[str]] = []

    for ip in ns_ips:
        records = _query_record_at_ns(domain, "MX", ip)
        all_found.append(records)
        found_entries = sorted(records)
        if found_entries == expected_entries:
            ns_ok += 1

    total = len(ns_ips)
    if ns_ok == total:
        status = STATUS_VERIFIED
    elif ns_ok > 0:
        status = STATUS_PROPAGATING
    else:
        status = STATUS_NOT_CONFIGURED

    return DnsCheckResult(
        record_type="MX",
        status=status,
        expected=expected_str,
        found=all_found[0] if all_found else None,
        nameservers_checked=total,
        nameservers_ok=ns_ok,
        details=f"{ns_ok}/{total} nameservers have correct records.",
    )


def _parse_spf_record(txt: str) -> dict[str, Any]:
    parts = txt.split()
    mechanisms = []
    for part in parts[1:]:
        if part in ("+all", "-all", "~all", "?all"):
            mechanisms.append(("all", part[0] if len(part) == 4 else "+"))
        elif part.startswith("mx"):
            mechanisms.append(("mx", part))
        elif part.startswith("ip4:") or part.startswith("ip6:"):
            mechanisms.append(("ip", part))
        elif part.startswith("include:"):
            mechanisms.append(("include", part))
        elif part.startswith("a"):
            mechanisms.append(("a", part))
        elif part == "v=spf1":
            continue
        else:
            mechanisms.append(("other", part))
    return {"raw": txt, "mechanisms": mechanisms}


def _spf_covers_mx(spf_txt: str, mx_hosts: list[str]) -> bool:
    parsed = _parse_spf_record(spf_txt)
    for mech_type, mech_val in parsed["mechanisms"]:
        if mech_type == "mx":
            return True
        if mech_type == "ip":
            return True
        if mech_type == "include":
            return True
        if mech_type == "a":
            return True
    return False


def _check_spf(domain: str, mx_hosts: list[str]) -> DnsCheckResult:
    expected_str = "v=spf1 mx ~all"

    ns_names = _get_authoritative_nameservers(domain)
    if not ns_names:
        return DnsCheckResult(
            record_type="SPF",
            status=STATUS_NOT_CONFIGURED,
            expected=expected_str,
            details="Could not resolve authoritative nameservers.",
        )

    ns_ips = _resolve_ns_ips(ns_names)
    if not ns_ips:
        return DnsCheckResult(
            record_type="SPF",
            status=STATUS_NOT_CONFIGURED,
            expected=expected_str,
            details="Could not resolve nameserver IP addresses.",
        )

    ns_ok = 0
    found_records: list[str] = []

    for ip in ns_ips:
        records = _query_record_at_ns(domain, "TXT", ip)
        spf_records = [r for r in records if r.startswith("v=spf1")]
        if spf_records:
            found_records = spf_records
            if _spf_covers_mx(spf_records[0], mx_hosts):
                ns_ok += 1

    total = len(ns_ips)
    if ns_ok == total:
        status = STATUS_VERIFIED
    elif ns_ok > 0:
        status = STATUS_PROPAGATING
    elif found_records:
        status = STATUS_MISMATCH
    else:
        status = STATUS_NOT_CONFIGURED

    detail = f"{ns_ok}/{total} nameservers have valid SPF records."
    if found_records and status == STATUS_MISMATCH:
        detail = f"SPF record found but does not cover our mail servers. {ns_ok}/{total} nameservers OK."

    return DnsCheckResult(
        record_type="SPF",
        status=status,
        expected=expected_str,
        found=found_records or None,
        nameservers_checked=total,
        nameservers_ok=ns_ok,
        details=detail,
    )


def _check_dkim(domain: str, selector: str, expected_public_key: str | None) -> DnsCheckResult:
    if not expected_public_key:
        return DnsCheckResult(
            record_type="DKIM",
            status=STATUS_NOT_CONFIGURED,
            expected="",
            details="No DKIM public key available. Generate a key via the mail API first.",
        )

    dkim_domain = f"{selector}._domainkey.{domain}"
    expected_str = f"v=DKIM1; k=rsa; p={expected_public_key}"

    ns_names = _get_authoritative_nameservers(domain)
    if not ns_names:
        return DnsCheckResult(
            record_type="DKIM",
            status=STATUS_NOT_CONFIGURED,
            expected=expected_str,
            details="Could not resolve authoritative nameservers.",
        )

    ns_ips = _resolve_ns_ips(ns_names)
    if not ns_ips:
        return DnsCheckResult(
            record_type="DKIM",
            status=STATUS_NOT_CONFIGURED,
            expected=expected_str,
            details="Could not resolve nameserver IP addresses.",
        )

    ns_ok = 0
    found_records: list[str] = []

    for ip in ns_ips:
        records = _query_record_at_ns(dkim_domain, "TXT", ip)
        dkim_records = [r for r in records if "v=DKIM1" in r or "v=dkim" in r.lower()]
        if dkim_records:
            found_records = dkim_records
            for rec in dkim_records:
                p_match = re.search(r'p=([A-Za-z0-9+/=]+)', rec)
                if p_match and p_match.group(1) == expected_public_key:
                    ns_ok += 1
                    break

    total = len(ns_ips)
    if ns_ok == total:
        status = STATUS_VERIFIED
    elif ns_ok > 0:
        status = STATUS_PROPAGATING
    elif found_records:
        status = STATUS_MISMATCH
    else:
        status = STATUS_NOT_CONFIGURED

    detail = f"{ns_ok}/{total} nameservers have matching DKIM records."
    if found_records and status == STATUS_MISMATCH:
        detail = f"DKIM record found but public key does not match. {ns_ok}/{total} nameservers OK."

    return DnsCheckResult(
        record_type="DKIM",
        status=status,
        expected=expected_str,
        found=found_records or None,
        nameservers_checked=total,
        nameservers_ok=ns_ok,
        details=detail,
    )


def _check_dmarc(domain: str, expected_policy: str, expected_rua: str | None) -> DnsCheckResult:
    dmarc_domain = f"_dmarc.{domain}"
    rua_tag = f"rua=mailto:{expected_rua}" if expected_rua else ""
    expected_str = f"v=DMARC1; p={expected_policy}; {rua_tag}".rstrip("; ")

    ns_names = _get_authoritative_nameservers(domain)
    if not ns_names:
        return DnsCheckResult(
            record_type="DMARC",
            status=STATUS_NOT_CONFIGURED,
            expected=expected_str,
            details="Could not resolve authoritative nameservers.",
        )

    ns_ips = _resolve_ns_ips(ns_names)
    if not ns_ips:
        return DnsCheckResult(
            record_type="DMARC",
            status=STATUS_NOT_CONFIGURED,
            expected=expected_str,
            details="Could not resolve nameserver IP addresses.",
        )

    ns_ok = 0
    found_records: list[str] = []

    for ip in ns_ips:
        records = _query_record_at_ns(dmarc_domain, "TXT", ip)
        dmarc_records = [r for r in records if "v=DMARC1" in r or "v=dmarc" in r.lower()]
        if dmarc_records:
            found_records = dmarc_records
            rec = dmarc_records[0]
            has_valid_policy = False
            has_valid_rua = True
            p_match = re.search(r'p\s*=\s*(none|quarantine|reject)', rec, re.IGNORECASE)
            if p_match:
                has_valid_policy = True
            if expected_rua:
                rua_match = re.search(r'rua\s*=\s*mailto:' + re.escape(expected_rua), rec, re.IGNORECASE)
                has_valid_rua = bool(rua_match)
            if has_valid_policy and has_valid_rua:
                ns_ok += 1

    total = len(ns_ips)
    if ns_ok == total:
        status = STATUS_VERIFIED
    elif ns_ok > 0:
        status = STATUS_PROPAGATING
    elif found_records:
        status = STATUS_MISMATCH
    else:
        status = STATUS_NOT_CONFIGURED

    detail = f"{ns_ok}/{total} nameservers have valid DMARC records."
    if found_records and status == STATUS_MISMATCH:
        detail = f"DMARC record found but policy or report email does not match expected values. {ns_ok}/{total} nameservers OK."

    return DnsCheckResult(
        record_type="DMARC",
        status=status,
        expected=expected_str,
        found=found_records or None,
        nameservers_checked=total,
        nameservers_ok=ns_ok,
        details=detail,
    )


def validate_mx_hostname(hostname: str) -> dict[str, Any]:
    resolver = dns.resolver.Resolver()
    resolver.lifetime = DNS_TIMEOUT

    resolved = False
    ips: list[str] = []
    try:
        for rdata in resolver.resolve(hostname, "A"):
            ips.append(str(rdata))
            resolved = True
    except Exception:
        pass
    try:
        for rdata in resolver.resolve(hostname, "AAAA"):
            ips.append(str(rdata))
            resolved = True
    except Exception:
        pass

    port_ok = False
    if resolved:
        try:
            with socket.create_connection((hostname, 25), timeout=DNS_TIMEOUT):
                port_ok = True
        except (OSError, socket.timeout):
            pass

    return {
        "hostname": hostname,
        "resolves": resolved,
        "ips": ips,
        "port_25_reachable": port_ok,
        "valid": resolved and port_ok,
    }


def run_all_dns_checks(
    domain_name: str,
    mx_servers: list[dict[str, Any]],
    dkim_selector: str,
    dkim_public_key: str | None,
    dmarc_policy: str,
    dmarc_rua: str | None,
) -> dict[str, Any]:
    mx_hosts = [s["host"] for s in mx_servers]

    mx_result = _check_mx(domain_name, mx_servers)
    spf_result = _check_spf(domain_name, mx_hosts)
    dkim_result = _check_dkim(domain_name, dkim_selector, dkim_public_key)
    dmarc_result = _check_dmarc(domain_name, dmarc_policy, dmarc_rua)

    return {
        "mx": {
            "status": mx_result.status,
            "expected": mx_result.expected,
            "found": mx_result.found,
            "nameservers_checked": mx_result.nameservers_checked,
            "nameservers_ok": mx_result.nameservers_ok,
            "details": mx_result.details,
            "instructions": _get_instructions("mx", mx_result.status),
        },
        "spf": {
            "status": spf_result.status,
            "expected": spf_result.expected,
            "found": spf_result.found,
            "nameservers_checked": spf_result.nameservers_checked,
            "nameservers_ok": spf_result.nameservers_ok,
            "details": spf_result.details,
            "instructions": _get_instructions("spf", spf_result.status),
        },
        "dkim": {
            "status": dkim_result.status,
            "expected": dkim_result.expected,
            "found": dkim_result.found,
            "nameservers_checked": dkim_result.nameservers_checked,
            "nameservers_ok": dkim_result.nameservers_ok,
            "details": dkim_result.details,
            "instructions": _get_instructions("dkim", dkim_result.status, dkim_result.details, domain_name=domain_name, dkim_selector=dkim_selector),
        },
        "dmarc": {
            "status": dmarc_result.status,
            "expected": dmarc_result.expected,
            "found": dmarc_result.found,
            "nameservers_checked": dmarc_result.nameservers_checked,
            "nameservers_ok": dmarc_result.nameservers_ok,
            "details": dmarc_result.details,
            "instructions": _get_instructions("dmarc", dmarc_result.status, domain_name=domain_name),
        },
    }
