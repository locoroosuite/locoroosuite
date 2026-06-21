import pytest

from app.modules.docs.services import folders as f


class TestValidateFolderName:
    def test_strips_whitespace(self):
        assert f.validate_folder_name("  Work  ") == "Work"

    def test_rejects_empty(self):
        with pytest.raises(f.FolderError):
            f.validate_folder_name("")
        with pytest.raises(f.FolderError):
            f.validate_folder_name("   ")

    def test_rejects_slash_and_backslash(self):
        for bad in ["a/b", "a\\b", "a\x00b"]:
            with pytest.raises(f.FolderError):
                f.validate_folder_name(bad)

    def test_rejects_too_long(self):
        with pytest.raises(f.FolderError):
            f.validate_folder_name("x" * (f.MAX_NAME_LEN + 1))


class TestPathHelpers:
    def test_normalize_path_root(self):
        assert f.normalize_path("", "Work") == "Work"
        assert f.normalize_path(None, "Work") == "Work"

    def test_normalize_path_nested(self):
        assert f.normalize_path("Work", "Projects") == "Work/Projects"
        assert f.normalize_path("Work/Dept", "Q1") == "Work/Dept/Q1"

    def test_normalize_path_strips_leading_trailing_slash(self):
        assert f.normalize_path("/Work/", "Projects") == "Work/Projects"

    def test_parent_and_leaf(self):
        assert f.parent_path("Work/Projects/Alpha") == "Work/Projects"
        assert f.parent_path("Work") == ""
        assert f.parent_path("") == ""
        assert f.leaf_name("Work/Projects/Alpha") == "Alpha"
        assert f.leaf_name("Work") == "Work"

    def test_depth_and_ancestors(self):
        assert f.depth("") == 0
        assert f.depth("Work") == 1
        assert f.depth("Work/Projects/Alpha") == 3
        assert f.ancestors("Work/Projects/Alpha") == ["Work", "Work/Projects"]
        assert f.ancestors("Work") == []
        assert f.ancestors("") == []

    def test_assert_depth_rejects_too_deep(self):
        deep = "/".join(f"l{i}" for i in range(f.MAX_DEPTH + 1))
        with pytest.raises(f.FolderError):
            f.assert_depth(deep)


class TestBuildTree:
    def test_empty(self):
        assert f.build_tree([], []) == []

    def test_includes_empty_folders_and_inferred_paths(self):
        rows = [
            {"path": "Work", "name": "Work"},
            {"path": "Work/Projects", "name": "Projects"},
            {"path": "Empty", "name": "Empty"},
        ]
        doc_paths = ["Work", "Work", "Work/Projects/Alpha", "Other"]
        tree = f.build_tree(rows, doc_paths)

        by_name = {n["name"]: n for n in tree}
        assert by_name["Empty"]["count"] == 0
        assert by_name["Other"]["count"] == 1
        assert by_name["Work"]["count"] == 2
        work = by_name["Work"]
        projects = work["children"][0]
        assert projects["name"] == "Projects"
        assert projects["count"] == 0
        assert projects["children"][0]["name"] == "Alpha"
        assert projects["children"][0]["count"] == 1
        assert projects["children"][0]["children"] == []

    def test_list_flat(self):
        rows = [{"path": "Work", "name": "Work"}, {"path": "Work/Sub", "name": "Sub"}]
        doc_paths = ["Work", "Work/Sub", "Work/Sub", "Lonely"]
        flat = f.build_tree(rows, doc_paths)
        # build_tree nests; flat API is separate, sanity check structure only here
        assert flat  # tree built without error


class TestEnsureFolderPath:
    def test_creates_ancestors(self, cache_conn_factory):
        conn = cache_conn_factory()
        f.ensure_folder_path(conn, 1, "A/B/C")
        from app.modules.docs.services import cache_db
        assert cache_db.folder_exists(conn, 1, "A")
        assert cache_db.folder_exists(conn, 1, "A/B")
        assert cache_db.folder_exists(conn, 1, "A/B/C")

    def test_idempotent(self, cache_conn_factory):
        conn = cache_conn_factory()
        f.ensure_folder_path(conn, 1, "A/B")
        f.ensure_folder_path(conn, 1, "A/B")  # no error
        from app.modules.docs.services import cache_db
        rows = [r["path"] for r in cache_db.list_folders(conn, 1)]
        assert rows.count("A/B") == 1


@pytest.fixture
def cache_conn_factory():
    import os
    import tempfile
    from app.modules.docs.services.cache_db import open_cache

    paths = []

    def _make():
        fh = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        path = fh.name
        fh.close()
        paths.append(path)
        return open_cache(path, "0" * 64)

    yield _make

    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass
