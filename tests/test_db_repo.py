import os
import tempfile
from tvwatch.core.db import DuckRepo


def test_add_list_disable_flow():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "test.duckdb")
        with DuckRepo(path) as repo:
            repo.add_or_reactivate_show("https://ceskatelevize.cz/porady/s1/")
            assert repo.list_shows(active=True) == [
                ("https://ceskatelevize.cz/porady/s1/", True)
            ]
            assert repo.disable_show("https://ceskatelevize.cz/porady/s1/") is True
            assert repo.list_shows(None) == [
                ("https://ceskatelevize.cz/porady/s1/", False)
            ]
