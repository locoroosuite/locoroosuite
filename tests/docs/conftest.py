import shutil
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _isolated_docs_dir(app):
    docs_dir = tempfile.mkdtemp(prefix="docs_test_")
    app.config["DOCS_DIR"] = docs_dir
    ctx = app.app_context()
    ctx.push()
    yield
    ctx.pop()
    shutil.rmtree(docs_dir, ignore_errors=True)
