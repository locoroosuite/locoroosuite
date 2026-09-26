import os
import re
import threading
from pathlib import Path

import pytest
from managers.dovecot import DovecotManager
from passlib.hash import sha256_crypt

_HASH_RE = re.compile(r"^\{SHA256-CRYPT\}\$5\$[A-Za-z0-9./]+\$[A-Za-z0-9./]+$")


def _make_manager(tmp_path: Path) -> DovecotManager:
    return DovecotManager(
        users_path=str(tmp_path / "passwd"),
        mail_root=str(tmp_path / "vhosts"),
    )


def _hash_line(email: str, users_path: Path) -> str:
    for line in users_path.read_text().splitlines():
        if line.startswith(f"{email}:"):
            return line
    raise AssertionError(f"No passwd entry for {email}")


class TestUserCrud:
    def test_add_user_creates_entry_and_maildir(self, tmp_path: Path):
        m = _make_manager(tmp_path)
        m.add_user("alice@test.localhost", "Secret123!")

        assert m.user_exists("alice@test.localhost")
        line = _hash_line("alice@test.localhost", tmp_path / "passwd")
        prefix, hash_part, *extra = line.split(":")
        assert prefix == "alice@test.localhost"
        assert _HASH_RE.match(hash_part), f"unexpected hash format: {hash_part}"
        assert sha256_crypt.verify("Secret123!", hash_part.removeprefix("{SHA256-CRYPT}"))
        assert extra[:2] == ["5000", "5000"]
        for sub in ("", "cur", "new", "tmp"):
            assert (tmp_path / "vhosts" / "test.localhost" / "alice" / sub).is_dir()

    def test_add_user_duplicate_raises(self, tmp_path: Path):
        m = _make_manager(tmp_path)
        m.add_user("alice@test.localhost", "Secret123!")
        with pytest.raises(FileExistsError):
            m.add_user("alice@test.localhost", "Other456!")

    def test_remove_user(self, tmp_path: Path):
        m = _make_manager(tmp_path)
        m.add_user("alice@test.localhost", "Secret123!")
        m.add_user("bob@test.localhost", "Secret123!")
        m.remove_user("alice@test.localhost")

        assert not m.user_exists("alice@test.localhost")
        assert m.user_exists("bob@test.localhost")

    def test_remove_missing_user_raises(self, tmp_path: Path):
        m = _make_manager(tmp_path)
        with pytest.raises(FileNotFoundError):
            m.remove_user("ghost@test.localhost")

    def test_set_password_rotates_hash(self, tmp_path: Path):
        m = _make_manager(tmp_path)
        m.add_user("alice@test.localhost", "Secret123!")
        old = _hash_line("alice@test.localhost", tmp_path / "passwd")

        m.set_password("alice@test.localhost", "NewPass456!")

        new = _hash_line("alice@test.localhost", tmp_path / "passwd")
        assert new != old
        new_hash = new.split(":")[1].removeprefix("{SHA256-CRYPT}")
        assert sha256_crypt.verify("NewPass456!", new_hash)
        assert not sha256_crypt.verify("Secret123!", new_hash)

    def test_set_password_missing_user_raises(self, tmp_path: Path):
        m = _make_manager(tmp_path)
        with pytest.raises(FileNotFoundError):
            m.set_password("ghost@test.localhost", "whatever")

    def test_set_quota_keeps_other_fields(self, tmp_path: Path):
        m = _make_manager(tmp_path)
        m.add_user("alice@test.localhost", "Secret123!")
        m.set_quota("alice@test.localhost", 1024)

        line = _hash_line("alice@test.localhost", tmp_path / "passwd")
        hash_part = line.split(":")[1]
        assert sha256_crypt.verify("Secret123!", hash_part.removeprefix("{SHA256-CRYPT}"))

    def test_list_users_filters_by_domain(self, tmp_path: Path):
        m = _make_manager(tmp_path)
        m.add_user("alice@test.localhost", "Secret123!")
        m.add_user("bob@other.example", "Secret123!")

        assert [u["email"] for u in m.list_users("test.localhost")] == ["alice@test.localhost"]
        assert len(m.list_users()) == 2


class TestAtomicWrites:
    """Regression tests: the passwd file must be replaced atomically.

    Before the fix, ``_write_users`` truncated the file in place and rewrote
    it line by line. Dovecot auth processes read the file concurrently, so a
    read landing inside that window saw empty or partial content and failed
    authentication for users that existed the whole time — the root cause of
    the flaky ``TestSendingLimit::test_sending_limit_enforced`` login step.
    """

    def test_users_file_replaced_atomically_via_rename(self, tmp_path: Path, monkeypatch):
        m = _make_manager(tmp_path)
        m.add_user("old@test.localhost", "Secret123!")
        old_content = (tmp_path / "passwd").read_text()

        calls = []
        real_replace = os.replace

        def spy_replace(src, dst):
            calls.append(
                {
                    "target_at_replace": Path(dst).read_text(),
                    "tmp_content": Path(src).read_text(),
                }
            )
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", spy_replace)

        m.add_user("new@test.localhost", "Secret123!")

        assert calls, (
            "users file was not written atomically: os.replace() was never called "
            "(in-place truncate+write would expose partial content to Dovecot)"
        )
        assert calls[0]["target_at_replace"] == old_content, (
            "target file changed before the rename completed"
        )
        final_content = (tmp_path / "passwd").read_text()
        assert calls[0]["tmp_content"] == final_content
        assert "new@test.localhost" in final_content
        assert "old@test.localhost" in final_content
        assert not list(tmp_path.glob("passwd.tmp")), "stale temp file left behind"

    def test_failed_write_leaves_original_intact(self, tmp_path: Path, monkeypatch):
        m = _make_manager(tmp_path)
        m.add_user("old@test.localhost", "Secret123!")
        original = (tmp_path / "passwd").read_text()

        def broken_replace(src, dst):
            raise OSError("simulated rename failure")

        monkeypatch.setattr(os, "replace", broken_replace)

        with pytest.raises(OSError):
            m.add_user("new@test.localhost", "Secret123!")

        assert (tmp_path / "passwd").read_text() == original
        assert not list(tmp_path.glob("passwd.tmp"))

    def test_concurrent_reader_never_sees_partial_file(self, tmp_path: Path):
        m = _make_manager(tmp_path)
        for i in range(5):
            m.add_user(f"seed{i}@test.localhost", "Secret123!")

        stop = threading.Event()
        bad_snapshots: list[str] = []

        def reader() -> None:
            while not stop.is_set():
                content = (tmp_path / "passwd").read_text()
                if not content.endswith("\n"):
                    bad_snapshots.append(repr(content[-60:]))
                    continue
                for line in content.splitlines():
                    if not re.match(r"^[^:@\s]+@[^:\s]+:\{SHA256-CRYPT\}\$5\$", line):
                        bad_snapshots.append(line)
                        break

        t = threading.Thread(target=reader)
        t.start()
        try:
            for i in range(5, 155):
                m.add_user(f"user{i}@test.localhost", "Secret123!")
        finally:
            stop.set()
            t.join()

        assert not bad_snapshots, f"reader observed non-atomic file states: {bad_snapshots[:3]}"

    def test_concurrent_mutations_do_not_lose_users(self, tmp_path: Path):
        m = _make_manager(tmp_path)
        count = 40

        def add_range(start: int, end: int) -> None:
            for i in range(start, end):
                m.add_user(f"user{i}@test.localhost", "Secret123!")

        t1 = threading.Thread(target=add_range, args=(0, count // 2))
        t2 = threading.Thread(target=add_range, args=(count // 2, count))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        listed = {u["email"] for u in m.list_users()}
        missing = [
            f"user{i}@test.localhost"
            for i in range(count)
            if f"user{i}@test.localhost" not in listed
        ]
        assert not missing, f"lost updates during concurrent writes: {missing}"
