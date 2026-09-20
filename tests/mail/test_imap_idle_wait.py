"""Tests for IMAP IDLE handling in imap_client.idle_wait (U4.3/U4.4).

U4.4: Dovecot's "* OK Still here" keepalive carries no mailbox change and must
not be surfaced as a response (it would trigger a pointless no-op sync).
"""

import socket

from app.modules.mail.services.imap_client import idle_wait


class _FakeSock:
    def __init__(self):
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)


class FakeIdleClient:
    """Minimal imaplib.IMAP4 stand-in for idle_wait.

    ``lines`` are yielded by ``_get_line`` in order; Exception instances are
    raised instead of returned.
    """

    def __init__(self, lines):
        self._lines = iter(lines)
        self.sock = _FakeSock()
        self.tagged_commands = {}
        self.sent = []
        self._tagcount = 0

    def _new_tag(self):
        self._tagcount += 1
        tag = f"TAG{self._tagcount}".encode()
        self.tagged_commands[tag] = None
        return tag

    def send(self, data):
        self.sent.append(data if isinstance(data, bytes) else bytes(data))

    def _get_line(self):
        try:
            item = next(self._lines)
        except StopIteration:
            raise socket.timeout from None
        if isinstance(item, Exception):
            raise item
        return item

    def _get_response(self):
        return None


class TestIdleWaitHandshake:
    def test_rejected_idle_command_returns_unsupported(self):
        client = FakeIdleClient([b"TAG1 BAD Error in IDLE command"])
        supported, response = idle_wait(client, timeout=1)
        assert supported is False
        assert response == b"TAG1 BAD Error in IDLE command"

    def test_plus_continuation_is_supported(self):
        client = FakeIdleClient([b"+ idling", socket.timeout()])
        supported, _response = idle_wait(client, timeout=1)
        assert supported is True


class TestIdleWaitKeepaliveFilter:
    def test_keepalive_only_yields_no_response(self):
        client = FakeIdleClient([b"+ idling", b"* OK Still here", socket.timeout()])
        supported, response = idle_wait(client, timeout=1)
        assert supported is True
        assert response is None

    def test_keepalive_then_exists_yields_exists(self):
        client = FakeIdleClient(
            [b"+ idling", b"* OK Still here", b"* OK Still here", b"* 5 EXISTS"]
        )
        supported, response = idle_wait(client, timeout=1)
        assert supported is True
        assert response == b"* 5 EXISTS"

    def test_expunge_is_a_change(self):
        client = FakeIdleClient([b"+ idling", b"* 3 EXPUNGE"])
        supported, response = idle_wait(client, timeout=1)
        assert supported is True
        assert response == b"* 3 EXPUNGE"

    def test_bye_is_surfaced_not_swallowed(self):
        client = FakeIdleClient([b"+ idling", b"* BYE Autologout; idle disconnected"])
        supported, response = idle_wait(client, timeout=1)
        assert supported is True
        assert response == b"* BYE Autologout; idle disconnected"

    def test_done_sent_after_change(self):
        client = FakeIdleClient([b"+ idling", b"* 5 EXISTS"])
        idle_wait(client, timeout=1)
        assert b"DONE\r\n" in client.sent
