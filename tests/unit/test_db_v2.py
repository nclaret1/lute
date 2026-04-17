import sqlite3
import pytest
import os

# import json
# from unittest.mock import MagicMock
# from lute.io._db.v2.api import (
#     record_analysis_db,
#     record_parameters_db,
#     update_analysis_db,
# )
from lute.io._db.v2._sqlite import create_tables

# from lute.tasks.dataclasses import DescribedAnalysis, TaskResult, TaskStatus
# from lute.io.parameters import TaskParameters, RowIds


# Complicated to setup TaskParameters... Do some basic mocking for now...
class MockLuteConfig:
    def __init__(self, work_dir):
        self.work_dir = work_dir


class MockTaskParameters:
    def __init__(self, work_dir):
        self.lute_config = MockLuteConfig(work_dir)

    def dict(self):
        return {"param1": "val1"}

    def schema(self):
        return {"properties": {"param1": {"type": "string"}}}


@pytest.fixture
def temp_db_dir(tmp_path):
    d = tmp_path / "db"
    d.mkdir()
    return str(d)


def test_create_tables_v2(temp_db_dir):
    db_path = os.path.join(temp_db_dir, "lute.db")
    con = sqlite3.connect(db_path)
    create_tables(con)

    # Check if key tables exist
    res = con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in res.fetchall()]
    assert "executions" in tables
    assert "tasks" in tables
    assert "parameters" in tables
    assert "results" in tables
    con.close()
