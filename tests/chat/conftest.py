import contextlib
import os
import tempfile

import pytest

from app.modules.chat.services import cache_db


@pytest.fixture()
def chat_cache():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = cache_db.open_cache(path, "0" * 64)
    yield conn
    conn.close()
    with contextlib.suppress(OSError):
        os.unlink(path)
