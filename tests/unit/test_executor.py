import os

import pytest
from unittest.mock import MagicMock

from lute.execution.executor import Executor, BaseExecutor  # noqa: F401
from lute.tasks.dataclasses import (
    TaskResult,
    TaskStatus,
    DescribedAnalysis,
)  # noqa: F401
from lute.io.parameters import TaskParameters  # noqa: F401


@pytest.fixture
def mock_communicator():
    comm = MagicMock()
    comm.__str__.return_value = "MockCommunicator"
    return comm


@pytest.fixture
def executor(mock_communicator):
    # BaseExecutor is ABC, Executor is concrete
    return Executor(task_name="TestTask", communicators=[mock_communicator])


def test_executor_init(executor):
    assert executor._analysis_desc.task_result.task_name == "TestTask"
    assert executor._analysis_desc.task_result.task_status == TaskStatus.PENDING
    assert len(executor._communicators) == 1


def test_executor_add_tasklet(executor):
    def my_tasklet(x):
        return x * 2

    executor.add_tasklet(my_tasklet, args=[10], when="before")

    assert executor._tasklets["before"] is not None
    assert len(executor._tasklets["before"]) == 1
    tasklet_tuple = executor._tasklets["before"][0]
    assert tasklet_tuple[0] == my_tasklet
    assert tasklet_tuple[1] == [10]


def test_sub_tasklet_parameters(executor):
    mock_params = MagicMock()
    mock_params.input_file = "data.txt"
    mock_params.lute_config = MagicMock()
    mock_params.lute_config.run = 123

    executor._analysis_desc.task_parameters = mock_params

    args = ["{{ input_file }}", "run_{{ lute_config.run }}", "plain_string"]
    new_args = executor._sub_tasklet_parameters(args)

    assert new_args[0] == "data.txt"
    assert new_args[1] == "run_123"
    assert new_args[2] == "plain_string"


def test_update_environment(executor):
    executor.update_environment({"NEW_VAR": "value"})
    assert executor._delayed_update_env_args == ({"NEW_VAR": "value"}, "prepend")

    # Manually trigger internal update to test logic
    executor._update_environment({"ANOTHER": "val"})
    assert executor._analysis_desc.task_env["ANOTHER"] == "val"


def test_update_environment_path(executor):
    executor._analysis_desc.task_env["PATH"] = "/usr/bin"
    executor._update_environment({"PATH": "/my/bin"}, update_path="prepend")
    assert (
        executor._analysis_desc.task_env["PATH"] == "/my/bin" + os.pathsep + "/usr/bin"
    )

    executor._analysis_desc.task_env["PATH"] = "/usr/bin"
    executor._update_environment({"PATH": "/my/bin"}, update_path="append")
    assert (
        executor._analysis_desc.task_env["PATH"] == "/usr/bin" + os.pathsep + "/my/bin"
    )


def test_run_tasklets(executor):
    mock_tasklet = MagicMock(return_value="Output")
    executor.add_tasklet(
        mock_tasklet, args=["{{ val }}"], when="after", set_result=True
    )

    mock_params = MagicMock()
    mock_params.val = "input"
    executor._analysis_desc.task_parameters = mock_params

    executor._run_tasklets(when="after")

    mock_tasklet.assert_called_once_with("input")
    assert executor._analysis_desc.task_result.payload == "Output"
