import os
import sqlite3

import pytest

from lute.io._db.v1.api import record_analysis_db
from lute.io.models.base import TaskParameters, AnalysisHeader
from lute.tasks.dataclasses import TaskResult, TaskStatus, DescribedAnalysis


class SimpleTaskParams(TaskParameters):
    """A minimal subclass of TaskParameters for testing."""

    pass


@pytest.fixture
def temp_db_dir(tmp_path):
    d = tmp_path / "db"
    d.mkdir()
    return str(d)


def test_record_analysis_db_v1(temp_db_dir):
    mock_result = TaskResult(
        task_name="V1Task",
        task_status=TaskStatus.COMPLETED,
        summary="Done",
        payload="Result",
    )

    # Need to use real classes due to assertions in database code
    header = AnalysisHeader(experiment="test_exp", run=1, work_dir=temp_db_dir)

    params = SimpleTaskParams(lute_config=header)

    cfg = DescribedAnalysis(
        task_result=mock_result,
        task_parameters=params,
        task_env={},
        executor_name="TestExecutor",
        poll_interval=0.1,
        communicator_desc=[],
    )

    try:
        record_analysis_db(cfg)
    except Exception as e:
        pytest.fail(f"record_analysis_db failed with: {e}")

    db_path = os.path.join(temp_db_dir, "lute.db")
    assert os.path.exists(db_path)

    con = sqlite3.connect(db_path)
    # V1 should have gen_cfg, exec_cfg, and a task table
    res = con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in res.fetchall()]
    assert "gen_cfg" in tables
    assert "exec_cfg" in tables
    assert "V1Task" in tables
    con.close()
