"""Basic test Tasks for testing functionality.

Classes:
    Test(Task): Simplest test Task - runs a 10 iteration loop and returns a
        result.

    TestSocket(Task): Test Task which sends larger data to test socket IPC.

    TestWriteOutput(Task): Test Task which writes an output file.

    TestReadOutput(Task): Test Task which reads in a file. Can be used to test
        database access.
"""

__all__ = ["Test", "TestSocket", "TestWriteOutput", "TestReadOutput", "TestRequest"]
__author__ = "Gabriel Dorlhiac"

import time
from typing import List, cast

import numpy as np

from lute.tasks.task import Task
from lute.io.models.tests import (
    TestParameters,
    TestSocketParameters,
    TestWriteOutputParameters,
    TestReadOutputParameters,
)
from lute.tasks.dataclasses import TaskStatus
from lute.execution.ipc import Message


class Test(Task):
    """Simple test Task to ensure subprocess and pipe-based IPC work."""

    def __init__(self, *, params: TestParameters) -> None:
        super().__init__(params=params)

    def _run(self) -> None:
        self._task_parameters = cast(TestParameters, self._task_parameters)
        for i in range(10):
            time.sleep(1)
            msg: Message = Message(contents=f"Test message {i}")
            self._report_to_executor(msg)
        if self._task_parameters.throw_error:
            raise RuntimeError("Testing Error!")

    def _post_run(self) -> None:
        self._result.summary = "Test Finished."
        self._result.task_status = TaskStatus.COMPLETED
        time.sleep(0.1)


class TestSocket(Task):
    """Simple test Task to ensure basic IPC over Unix sockets works."""

    def __init__(self, *, params: TestSocketParameters) -> None:
        super().__init__(params=params)

    def _run(self) -> None:
        self._task_parameters = cast(TestSocketParameters, self._task_parameters)
        for i in range(self._task_parameters.num_arrays):
            msg: Message = Message(contents=f"Sending array {i}")
            self._report_to_executor(msg)
            time.sleep(0.05)
            msg = Message(contents=np.random.rand(self._task_parameters.array_size))
            self._report_to_executor(msg)

    def _post_run(self) -> None:
        super()._post_run()
        self._task_parameters = cast(TestSocketParameters, self._task_parameters)
        self._result.summary = f"Sent {self._task_parameters.num_arrays} arrays"
        self._result.payload = np.random.rand(self._task_parameters.array_size)
        self._result.task_status = TaskStatus.COMPLETED


class TestWriteOutput(Task):
    """Simple test Task to write output other Tasks depend on."""

    def __init__(self, *, params: TestWriteOutputParameters) -> None:
        super().__init__(params=params)

    def _run(self) -> None:
        self._task_parameters = cast(TestWriteOutputParameters, self._task_parameters)
        for i in range(self._task_parameters.num_vals):
            # Doing some calculations...
            time.sleep(0.05)
            if i % 10 == 0:
                msg: Message = Message(contents=f"Processed {i+1} values!")
                self._report_to_executor(msg)

    def _post_run(self) -> None:
        super()._post_run()
        self._task_parameters = cast(TestWriteOutputParameters, self._task_parameters)
        work_dir: str = self._task_parameters.lute_config.work_dir
        out_file: str = f"{work_dir}/{self._task_parameters.outfile_name}"
        array: np.ndarray = np.random.rand(self._task_parameters.num_vals)
        np.savetxt(out_file, array, delimiter=",")
        self._result.summary = "Completed task successfully."
        self._result.payload = out_file
        self._result.task_status = TaskStatus.COMPLETED


class TestReadOutput(Task):
    """Simple test Task to read in output from the test Task above.

    Its pydantic model relies on a database access to retrieve the output file.
    """

    def __init__(self, *, params: TestReadOutputParameters) -> None:
        super().__init__(params=params)

    def _run(self) -> None:
        self._task_parameters = cast(TestReadOutputParameters, self._task_parameters)
        _: np.ndarray = np.loadtxt(self._task_parameters.in_file, delimiter=",")
        self._report_to_executor(msg=Message(contents="Successfully loaded data!"))
        for i in range(5):
            time.sleep(1)

    def _post_run(self) -> None:
        super()._post_run()
        self._result.summary = "Was able to load data."
        self._result.payload = "This Task produces no output."
        self._result.task_status = TaskStatus.COMPLETED


class TestRequest(Task):
    """Simple test Task to try to send requests to and from via workflow manager."""

    def __init__(self, *, params: TestReadOutputParameters) -> None:
        super().__init__(params=params)

    def _parse_response(self, resp: Message) -> None:
        running_managed_tasks: List[str] = []
        running_tasks: List[str] = []
        if isinstance(resp.contents, dict):
            for managed_task_dict in resp.contents["managed_tasks"]:
                running_managed_tasks.append(managed_task_dict["name"])
                running_tasks.append(managed_task_dict["task"])

        n_tasks: int = len(running_managed_tasks)
        msg_str: str = f"TestRequest acknowledges that {n_tasks} Tasks are running\n"
        msg_str += "Their names are: " + ",".join(running_managed_tasks)

        self._report_to_executor(msg=Message(contents=msg_str))

    def _run(self) -> None:
        self._report_to_executor(Message(contents="Got to run"))
        time.sleep(1)

        # Format should be:
        # {
        #     "managed_tasks": [
        #         { "name": "...", "status": "...", "task": "...", OTHER KEYS MAYBE },
        #         { "name": "...", "status": "...", "task": "...", OTHER KEYS MAYBE },
        #     ]
        # }
        resp: Message = self.get_running_tasks()
        self._parse_response(resp=resp)
        time.sleep(1)

        resp = self.get_running_tasks()
        self._parse_response(resp=resp)
        time.sleep(1)

        resp = self.get_running_tasks()
        self._parse_response(resp=resp)
        time.sleep(1)

        self._report_to_executor(Message(contents="And done..."))

    def _post_run(self) -> None:
        super()._post_run()
        self._result.summary = "Was able to request via workflow manager.."
        self._result.payload = "This Task produces no output."
        self._result.task_status = TaskStatus.COMPLETED
