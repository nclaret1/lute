"""Basic test Tasks for testing functionality that requires MPI.

Classes:
    TestMultiNodeCommunication(Task): Test Task which verifies that the
        SocketCommunicator can write back to the Executor on a different node.
"""

__all__ = ["TestMultiNodeCommunication"]
__author__ = "Gabriel Dorlhiac"

import os
import time
from typing import cast

import numpy as np
import numpy.typing as npt
import matplotlib.pyplot as plt  # type: ignore
from mpi4py import MPI

from lute.tasks.task import Task
from lute.tasks.dataclasses import TaskStatus
from lute.io.models.mpi_tests import TestMultiNodeCommunicationParameters
from lute.execution.ipc import Message


class TestMultiNodeCommunication(Task):
    """Task to test multi-node communication.

    This test only tests the desired functionality if run on a cluster with
    multiple machines and MPI. E.g. submission via SLURM on S3DF.

    This Task uses MPI and should be submitted with at least 2 ranks (but
    probably not too many in the interest of not wasting resources). It must be
    submitted with (SLURM) arguments that ensure the Task is run on at least two
    nodes.
    """

    def __init__(
        self, *, params: TestMultiNodeCommunicationParameters, use_mpi: bool = True
    ) -> None:
        super().__init__(params=params, use_mpi=use_mpi)
        self._comm: MPI.Intracomm = MPI.COMM_WORLD
        self._rank: int = self._comm.Get_rank()
        self._world_size: int = self._comm.Get_size()

    def _run(self) -> None:
        self._task_parameters = cast(
            TestMultiNodeCommunicationParameters, self._task_parameters
        )
        time.sleep(self._rank)
        msg: Message = Message(
            f"Rank {self._rank} of {self._world_size} sending message."
            f"  From {MPI.Get_processor_name()} to {os.getenv('LUTE_EXECUTOR_HOST')}."
        )
        self._report_to_executor(msg)
        if self._task_parameters.send_obj == "array":
            arr_size: int
            if self._task_parameters.arr_size is not None:
                arr_size = self._task_parameters.arr_size
            else:
                arr_size = 512
            msg = Message(contents=np.random.rand(arr_size))
            self._report_to_executor(msg)
        elif self._task_parameters.send_obj == "plot":
            x: npt.NDArray[np.float64] = np.linspace(0, 49, 50)
            y: npt.NDArray[np.float64] = np.random.rand(50)
            fig, ax = plt.subplots(1, 1)
            ax.plot(x, y, label="Test")
            ax.set_title("Multi-Node Communication Test")
            msg = Message(contents=fig)
            self._report_to_executor(msg)

    def _post_run(self) -> None:
        if self._result.task_status != TaskStatus.FAILED:
            self._result.summary = "Test Finished."
            self._result.task_status = TaskStatus.COMPLETED
        time.sleep(0.1)
