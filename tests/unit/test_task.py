import pytest
from unittest.mock import MagicMock, patch

from lute.tasks.task import Task
from lute.io.models.base import TaskParameters, AnalysisHeader
from lute.tasks.dataclasses import TaskStatus


# Need a concrete subclass to test the ABC Task
class ConcreteTask(Task):
    def _run(self) -> None:
        self._result.summary = "Success"
        self._result.task_status = TaskStatus.COMPLETED


@pytest.fixture
def mock_params(tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    header = AnalysisHeader(experiment="test_exp", run=1, work_dir=str(work_dir))
    params = MagicMock(spec=TaskParameters)
    params.lute_config = header
    params.Config = MagicMock()
    params.Config.result_from_params = None
    params.Config.run_directory = str(work_dir)
    return params


def test_task_init(mock_params):
    with patch("os.chdir"):
        with patch("sys.stdin") as mock_stdin:
            # Avoid changing dir in tests
            task = ConcreteTask(params=mock_params)
            assert task.name == "ConcreteTask"
            assert task.result.task_status == TaskStatus.PENDING


def test_task_run(mock_params):
    with patch("os.chdir"):
        with patch.object(ConcreteTask, "_report_to_executor"):
            with patch("os.kill"):
                with patch("sys.stdin") as mock_stdin:
                    # Avoid sending signals in tests
                    task = ConcreteTask(params=mock_params)
                    task.run()
                    assert task.result.summary == "Success"
                    assert task.result.task_status == TaskStatus.COMPLETED


def test_task_call(mock_params):
    with patch("os.chdir"):
        with patch.object(ConcreteTask, "_report_to_executor"):
            with patch("os.kill"):
                with patch("sys.stdin") as mock_stdin:
                    task = ConcreteTask(params=mock_params)
                    task()  # Test __call__
                    assert task.result.task_status == TaskStatus.COMPLETED
