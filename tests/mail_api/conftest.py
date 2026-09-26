import os
import sys
from pathlib import Path

import pytest

_MAIL_API_DIR = Path(__file__).resolve().parents[2] / "mail-api"
if str(_MAIL_API_DIR) not in sys.path:
    sys.path.insert(0, str(_MAIL_API_DIR))


@pytest.fixture(autouse=True)
def _no_chown(monkeypatch: pytest.MonkeyPatch):
    """DovecotManager.add_user chowns maildirs to uid/gid 5000, which only
    root may do; neutralize it so the suite can run as a regular user."""
    monkeypatch.setattr(os, "chown", lambda path, uid, gid: None)
