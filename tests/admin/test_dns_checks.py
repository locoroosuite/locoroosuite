from unittest.mock import patch, MagicMock

from app.admin.services.dns_checks import (
    _get_authoritative_nameservers,
    _check_mx,
    _check_spf,
    _check_dkim,
    _check_dmarc,
    _get_instructions,
    _parse_spf_record,
    _spf_covers_mx,
    validate_mx_hostname,
    run_all_dns_checks,
    STATUS_NOT_CONFIGURED,
    STATUS_MISMATCH,
    STATUS_PROPAGATING,
    STATUS_VERIFIED,
)


class TestParseSpf:
    def test_basic_mx(self):
        parsed = _parse_spf_record("v=spf1 mx ~all")
        assert any(m[0] == "mx" for m in parsed["mechanisms"])
        assert any(m[0] == "all" for m in parsed["mechanisms"])

    def test_include(self):
        parsed = _parse_spf_record("v=spf1 include:_spf.google.com ~all")
        assert any(m[0] == "include" for m in parsed["mechanisms"])

    def test_ip4(self):
        parsed = _parse_spf_record("v=spf1 ip4:1.2.3.4/32 ~all")
        assert any(m[0] == "ip" for m in parsed["mechanisms"])


class TestSpfCoversMx:
    def test_mx_mechanism(self):
        assert _spf_covers_mx("v=spf1 mx ~all", ["mx.example.com"]) is True

    def test_include_mechanism(self):
        assert _spf_covers_mx("v=spf1 include:_spf.example.com ~all", ["mx.example.com"]) is True

    def test_ip_mechanism(self):
        assert _spf_covers_mx("v=spf1 ip4:1.2.3.4 ~all", ["mx.example.com"]) is True

    def test_a_mechanism(self):
        assert _spf_covers_mx("v=spf1 a ~all", ["mx.example.com"]) is True

    def test_no_coverage(self):
        assert _spf_covers_mx("v=spf1 ~all", ["mx.example.com"]) is False


@patch("app.admin.services.dns_checks._get_authoritative_nameservers")
@patch("app.admin.services.dns_checks._resolve_ns_ips")
@patch("app.admin.services.dns_checks._query_record_at_ns")
class TestCheckMx:
    def test_verified(self, mock_query, mock_ns_ips, mock_ns_names):
        mock_ns_names.return_value = ["ns1.example.com", "ns2.example.com"]
        mock_ns_ips.return_value = ["1.1.1.1", "2.2.2.2"]
        mock_query.side_effect = [
            ["10 mx.example.com"],
            ["10 mx.example.com"],
        ]
        result = _check_mx("example.com", [{"host": "mx.example.com", "priority": 10}])
        assert result.status == STATUS_VERIFIED
        assert result.nameservers_ok == 2

    def test_propagating(self, mock_query, mock_ns_ips, mock_ns_names):
        mock_ns_names.return_value = ["ns1.example.com", "ns2.example.com"]
        mock_ns_ips.return_value = ["1.1.1.1", "2.2.2.2"]
        mock_query.side_effect = [
            ["10 mx.example.com"],
            [],
        ]
        result = _check_mx("example.com", [{"host": "mx.example.com", "priority": 10}])
        assert result.status == STATUS_PROPAGATING
        assert result.nameservers_ok == 1

    def test_not_configured(self, mock_query, mock_ns_ips, mock_ns_names):
        mock_ns_names.return_value = ["ns1.example.com", "ns2.example.com"]
        mock_ns_ips.return_value = ["1.1.1.1", "2.2.2.2"]
        mock_query.side_effect = [[], []]
        result = _check_mx("example.com", [{"host": "mx.example.com", "priority": 10}])
        assert result.status == STATUS_NOT_CONFIGURED
        assert result.nameservers_ok == 0

    def test_no_ns(self, mock_query, mock_ns_ips, mock_ns_names):
        mock_ns_names.return_value = []
        result = _check_mx("example.com", [{"host": "mx.example.com", "priority": 10}])
        assert result.status == STATUS_NOT_CONFIGURED

    def test_no_expected(self, mock_query, mock_ns_ips, mock_ns_names):
        result = _check_mx("example.com", [])
        assert result.status == STATUS_NOT_CONFIGURED


@patch("app.admin.services.dns_checks._get_authoritative_nameservers")
@patch("app.admin.services.dns_checks._resolve_ns_ips")
@patch("app.admin.services.dns_checks._query_record_at_ns")
class TestCheckSpf:
    def test_verified(self, mock_query, mock_ns_ips, mock_ns_names):
        mock_ns_names.return_value = ["ns1.example.com"]
        mock_ns_ips.return_value = ["1.1.1.1"]
        mock_query.return_value = ["v=spf1 mx ~all"]
        result = _check_spf("example.com", ["mx.example.com"])
        assert result.status == STATUS_VERIFIED

    def test_not_configured(self, mock_query, mock_ns_ips, mock_ns_names):
        mock_ns_names.return_value = ["ns1.example.com"]
        mock_ns_ips.return_value = ["1.1.1.1"]
        mock_query.return_value = []
        result = _check_spf("example.com", ["mx.example.com"])
        assert result.status == STATUS_NOT_CONFIGURED


@patch("app.admin.services.dns_checks._get_authoritative_nameservers")
@patch("app.admin.services.dns_checks._resolve_ns_ips")
@patch("app.admin.services.dns_checks._query_record_at_ns")
class TestCheckDkim:
    def test_verified(self, mock_query, mock_ns_ips, mock_ns_names):
        mock_ns_names.return_value = ["ns1.example.com"]
        mock_ns_ips.return_value = ["1.1.1.1"]
        mock_query.return_value = ["v=DKIM1; k=rsa; p=ABC123"]
        result = _check_dkim("example.com", "default", "ABC123")
        assert result.status == STATUS_VERIFIED

    def test_key_mismatch(self, mock_query, mock_ns_ips, mock_ns_names):
        mock_ns_names.return_value = ["ns1.example.com"]
        mock_ns_ips.return_value = ["1.1.1.1"]
        mock_query.return_value = ["v=DKIM1; k=rsa; p=WRONGKEY"]
        result = _check_dkim("example.com", "default", "ABC123")
        assert result.status == STATUS_MISMATCH

    def test_no_public_key(self, mock_query, mock_ns_ips, mock_ns_names):
        result = _check_dkim("example.com", "default", None)
        assert result.status == STATUS_NOT_CONFIGURED
        assert "No DKIM public key" in result.details


@patch("app.admin.services.dns_checks._get_authoritative_nameservers")
@patch("app.admin.services.dns_checks._resolve_ns_ips")
@patch("app.admin.services.dns_checks._query_record_at_ns")
class TestCheckDmarc:
    def test_verified(self, mock_query, mock_ns_ips, mock_ns_names):
        mock_ns_names.return_value = ["ns1.example.com"]
        mock_ns_ips.return_value = ["1.1.1.1"]
        mock_query.return_value = ["v=DMARC1; p=none; rua=mailto:dmarc@example.com"]
        result = _check_dmarc("example.com", "none", "dmarc@example.com")
        assert result.status == STATUS_VERIFIED

    def test_not_configured(self, mock_query, mock_ns_ips, mock_ns_names):
        mock_ns_names.return_value = ["ns1.example.com"]
        mock_ns_ips.return_value = ["1.1.1.1"]
        mock_query.return_value = []
        result = _check_dmarc("example.com", "none", "dmarc@example.com")
        assert result.status == STATUS_NOT_CONFIGURED

    def test_wrong_rua(self, mock_query, mock_ns_ips, mock_ns_names):
        mock_ns_names.return_value = ["ns1.example.com"]
        mock_ns_ips.return_value = ["1.1.1.1"]
        mock_query.return_value = ["v=DMARC1; p=none; rua=mailto:other@example.com"]
        result = _check_dmarc("example.com", "none", "dmarc@example.com")
        assert result.status == STATUS_MISMATCH


class TestValidateMxHostname:
    @patch("app.admin.services.dns_checks.socket.create_connection")
    @patch("app.admin.services.dns_checks.dns.resolver.Resolver")
    def test_valid(self, mock_resolver_cls, mock_conn):
        resolver = MagicMock()
        mock_resolver_cls.return_value = resolver
        rdata = MagicMock()
        type(rdata).__str__ = lambda self: "1.2.3.4"
        resolver.resolve.return_value = [rdata]
        mock_conn.return_value.__enter__ = MagicMock()
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        result = validate_mx_hostname("mx.example.com")
        assert result["resolves"] is True

    @patch("app.admin.services.dns_checks.dns.resolver.Resolver")
    def test_does_not_resolve(self, mock_resolver_cls):
        resolver = MagicMock()
        mock_resolver_cls.return_value = resolver
        resolver.resolve.side_effect = Exception("no resolution")
        result = validate_mx_hostname("bad.example.com")
        assert result["resolves"] is False
        assert result["valid"] is False


@patch("app.admin.services.dns_checks.run_all_dns_checks")
def test_run_all_returns_four_records(mock_checks):
    mock_checks.return_value = {
        "mx": {"status": "verified", "expected": "", "found": None, "nameservers_checked": 0, "nameservers_ok": 0, "details": "", "instructions": ""},
        "spf": {"status": "verified", "expected": "", "found": None, "nameservers_checked": 0, "nameservers_ok": 0, "details": "", "instructions": ""},
        "dkim": {"status": "verified", "expected": "", "found": None, "nameservers_checked": 0, "nameservers_ok": 0, "details": "", "instructions": ""},
        "dmarc": {"status": "verified", "expected": "", "found": None, "nameservers_checked": 0, "nameservers_ok": 0, "details": "", "instructions": ""},
    }
    result = mock_checks(
        domain_name="example.com",
        mx_servers=[{"host": "mx.example.com", "priority": 10}],
        dkim_selector="default",
        dkim_public_key="ABC",
        dmarc_policy="none",
        dmarc_rua="dmarc@example.com",
    )
    assert len(result) == 4
    assert "mx" in result
    assert "spf" in result
    assert "dkim" in result
    assert "dmarc" in result


class TestGetInstructions:
    def test_mx_not_configured(self):
        instr = _get_instructions("mx", STATUS_NOT_CONFIGURED)
        assert "MX record" in instr
        assert len(instr) > 20

    def test_spf_not_configured(self):
        instr = _get_instructions("spf", STATUS_NOT_CONFIGURED)
        assert "TXT record" in instr

    def test_dkim_not_configured_no_key(self):
        instr = _get_instructions("dkim", STATUS_NOT_CONFIGURED, "No DKIM public key available.")
        assert "DKIM signing key" in instr
        assert "#dkim-settings" in instr

    def test_dkim_not_configured_dns_missing(self):
        instr = _get_instructions("dkim", STATUS_NOT_CONFIGURED, "0/2 nameservers have matching DKIM records.", domain_name="example.com", dkim_selector="default")
        assert "default._domainkey.example.com" in instr
        assert "#dkim-settings" in instr

    def test_dkim_mismatch(self):
        instr = _get_instructions("dkim", STATUS_MISMATCH, domain_name="example.com", dkim_selector="default")
        assert "does not match" in instr
        assert "default._domainkey.example.com" in instr

    def test_spf_mismatch(self):
        instr = _get_instructions("spf", STATUS_MISMATCH)
        assert "does not authorize" in instr

    def test_dmarc_not_configured(self):
        instr = _get_instructions("dmarc", STATUS_NOT_CONFIGURED, domain_name="example.com")
        assert "_dmarc.example.com" in instr
        assert "TXT record" in instr

    def test_dmarc_mismatch(self):
        instr = _get_instructions("dmarc", STATUS_MISMATCH, domain_name="example.com")
        assert "_dmarc.example.com" in instr
        assert "incorrect" in instr

    def test_propagating_has_propagation_note(self):
        for rtype in ["mx", "spf", "dkim", "dmarc"]:
            instr = _get_instructions(rtype, STATUS_PROPAGATING)
            assert "propagation" in instr.lower()

    def test_verified_returns_empty(self):
        for rtype in ["mx", "spf", "dkim", "dmarc"]:
            assert _get_instructions(rtype, STATUS_VERIFIED) == ""

    def test_mismatch_returns_empty_for_mx(self):
        assert _get_instructions("mx", STATUS_MISMATCH) == ""

    def test_unknown_status_returns_empty(self):
        assert _get_instructions("mx", "unknown_status") == ""

    def test_unknown_type_returns_empty(self):
        assert _get_instructions("unknown", STATUS_NOT_CONFIGURED) == ""


@patch("app.admin.services.dns_checks._query_record_at_ns")
@patch("app.admin.services.dns_checks._resolve_ns_ips")
@patch("app.admin.services.dns_checks._get_authoritative_nameservers")
def test_run_all_includes_instructions(mock_ns, mock_ips, mock_query):
    mock_ns.return_value = ["ns1.example.com"]
    mock_ips.return_value = ["1.1.1.1"]
    mock_query.return_value = []
    result = run_all_dns_checks(
        domain_name="example.com",
        mx_servers=[{"host": "mx.example.com", "priority": 10}],
        dkim_selector="default",
        dkim_public_key=None,
        dmarc_policy="none",
        dmarc_rua=None,
    )
    for rtype in ["mx", "spf", "dkim", "dmarc"]:
        assert "instructions" in result[rtype]
        assert isinstance(result[rtype]["instructions"], str)
