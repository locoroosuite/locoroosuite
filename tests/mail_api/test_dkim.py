import os
import tempfile
import pytest

from managers.opendkim import OpenDKIMManager


@pytest.fixture
def tmp_dirs():
    with tempfile.TemporaryDirectory() as tmpdir:
        keys_dir = os.path.join(tmpdir, "keys")
        os.makedirs(keys_dir)
        key_table = os.path.join(tmpdir, "key-table")
        signing_table = os.path.join(tmpdir, "signing-table")
        manager = OpenDKIMManager(
            keys_dir=keys_dir,
            key_table_path=key_table,
            signing_table_path=signing_table,
            selector="default",
        )
        yield manager, keys_dir, key_table, signing_table


class TestOpenDKIMManager:
    def test_generate_key(self, tmp_dirs):
        manager, keys_dir, key_table, signing_table = tmp_dirs
        result = manager.generate_key("example.com")

        assert result["domain"] == "example.com"
        assert result["selector"] == "default"
        assert result["public_key"]
        assert "v=DKIM1; k=rsa; p=" in result["txt_record"]
        assert os.path.exists(os.path.join(keys_dir, "default.example.com.private"))
        assert os.path.exists(os.path.join(keys_dir, "default.example.com.txt"))

    def test_generate_key_writes_tables(self, tmp_dirs):
        manager, keys_dir, key_table, signing_table = tmp_dirs
        manager.generate_key("example.com")

        with open(key_table) as f:
            content = f.read()
        assert "default._domainkey.example.com" in content
        assert "example.com:default:" in content

        with open(signing_table) as f:
            content = f.read()
        assert "*@example.com" in content

    def test_get_key(self, tmp_dirs):
        manager, keys_dir, key_table, signing_table = tmp_dirs
        generated = manager.generate_key("example.com")
        fetched = manager.get_key("example.com")

        assert fetched["public_key"] == generated["public_key"]
        assert fetched["selector"] == "default"

    def test_get_key_not_found(self, tmp_dirs):
        manager, keys_dir, key_table, signing_table = tmp_dirs
        with pytest.raises(FileNotFoundError):
            manager.get_key("nonexistent.com")

    def test_domain_has_key(self, tmp_dirs):
        manager, keys_dir, key_table, signing_table = tmp_dirs
        assert manager.domain_has_key("example.com") is False
        manager.generate_key("example.com")
        assert manager.domain_has_key("example.com") is True

    def test_remove_key(self, tmp_dirs):
        manager, keys_dir, key_table, signing_table = tmp_dirs
        manager.generate_key("example.com")
        assert manager.domain_has_key("example.com") is True

        manager.remove_key("example.com")
        assert manager.domain_has_key("example.com") is False

        with open(key_table) as f:
            assert "example.com" not in f.read()
        with open(signing_table) as f:
            assert "example.com" not in f.read()

    def test_remove_key_idempotent(self, tmp_dirs):
        manager, keys_dir, key_table, signing_table = tmp_dirs
        manager.remove_key("nonexistent.com")

    def test_multiple_domains(self, tmp_dirs):
        manager, keys_dir, key_table, signing_table = tmp_dirs
        manager.generate_key("a.com")
        manager.generate_key("b.com")

        assert manager.domain_has_key("a.com")
        assert manager.domain_has_key("b.com")

        with open(key_table) as f:
            content = f.read()
        assert "a.com" in content
        assert "b.com" in content

        manager.remove_key("a.com")
        assert not manager.domain_has_key("a.com")
        assert manager.domain_has_key("b.com")

    def test_key_is_valid_rsa(self, tmp_dirs):
        manager, keys_dir, key_table, signing_table = tmp_dirs
        result = manager.generate_key("example.com")
        priv_path = os.path.join(keys_dir, "default.example.com.private")

        from cryptography.hazmat.primitives import serialization
        with open(priv_path, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        assert key.key_size == 2048


class TestOpenDKIMManagerCustomSelector:
    def test_generate_key_custom_selector(self, tmp_dirs):
        manager, keys_dir, key_table, signing_table = tmp_dirs
        result = manager.generate_key("example.com", selector="mail2026")

        assert result["selector"] == "mail2026"
        assert result["domain"] == "example.com"
        assert result["public_key"]
        assert os.path.exists(os.path.join(keys_dir, "mail2026.example.com.private"))
        assert os.path.exists(os.path.join(keys_dir, "mail2026.example.com.txt"))

        with open(key_table) as f:
            content = f.read()
        assert "mail2026._domainkey.example.com" in content
        assert "example.com:mail2026:" in content

        with open(signing_table) as f:
            content = f.read()
        assert "*@example.com mail2026._domainkey.example.com" in content

    def test_get_key_custom_selector(self, tmp_dirs):
        manager, keys_dir, key_table, signing_table = tmp_dirs
        generated = manager.generate_key("example.com", selector="mail2026")
        fetched = manager.get_key("example.com", selector="mail2026")

        assert fetched["public_key"] == generated["public_key"]
        assert fetched["selector"] == "mail2026"

        with pytest.raises(FileNotFoundError):
            manager.get_key("example.com")

    def test_generate_key_replaces_old_style_entry(self, tmp_dirs):
        manager, keys_dir, key_table, signing_table = tmp_dirs

        old_key_entry = "mail2026._domainkey.example.com example.com:mail2026:/etc/opendkim/keys/example.com/mail2026.private"
        old_signing_entry = "*@example.com mail2026._domainkey.example.com"

        with open(key_table, "w") as f:
            f.write(old_key_entry + "\n")
        with open(signing_table, "w") as f:
            f.write(old_signing_entry + "\n")

        manager.generate_key("example.com", selector="mail2026")

        with open(key_table) as f:
            key_content = f.read()
        assert "/etc/opendkim/keys/example.com/mail2026.private" not in key_content
        assert "mail2026._domainkey.example.com" in key_content
        assert "example.com:mail2026:" in key_content
        lines = [l.strip() for l in key_content.strip().splitlines() if l.strip()]
        matching = [l for l in lines if l.startswith("mail2026._domainkey.example.com ")]
        assert len(matching) == 1

    def test_remove_key_with_custom_selector(self, tmp_dirs):
        manager, keys_dir, key_table, signing_table = tmp_dirs
        manager.generate_key("example.com", selector="mail2026")

        assert manager.domain_has_key("example.com", selector="mail2026")
        assert not manager.domain_has_key("example.com")

        manager.remove_key("example.com", selector="mail2026")
        assert not manager.domain_has_key("example.com", selector="mail2026")
        assert not os.path.exists(os.path.join(keys_dir, "mail2026.example.com.private"))

        with open(key_table) as f:
            assert "example.com" not in f.read()
        with open(signing_table) as f:
            assert "example.com" not in f.read()

    def test_custom_and_default_selectors_coexist(self, tmp_dirs):
        manager, keys_dir, key_table, signing_table = tmp_dirs
        manager.generate_key("example.com", selector="default")
        manager.generate_key("example.com", selector="mail2026")

        assert manager.domain_has_key("example.com", selector="default")
        assert manager.domain_has_key("example.com", selector="mail2026")

        default_key = manager.get_key("example.com", selector="default")
        custom_key = manager.get_key("example.com", selector="mail2026")
        assert default_key["public_key"] != custom_key["public_key"]

        manager.remove_key("example.com", selector="default")
        assert not manager.domain_has_key("example.com", selector="default")
        assert manager.domain_has_key("example.com", selector="mail2026")
