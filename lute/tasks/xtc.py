"""
Task for converting xtc1 files to xtc2 format using zmq-based communication
between psana1 and psana2 environments.

Classes:
    - ConvertXtc1to2(Task): Convert xtc1 files to xtc2 format.

Based on Mona's converter:
    https://github.com/monarin/xtc1to2
"""

import json
import logging
import os
import signal
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import zmq

from lute.execution.logging import get_logger
from lute.io.models.xtc import ConvertXtc1to2Parameters
from lute.tasks.dataclasses import TaskStatus
from lute.tasks.task import Task

logger: logging.Logger = get_logger(__name__)


class ConvertXtc1to2(Task):
    """
    A task that launches two zmq subprocesses in two different conda environments
    (old psana and new psana) to convert (old) xtc1 files to (new) xtc2 files.

    This involves:
        - Process 1: Opening the xtc1 data using psana1 environment.
        - Process 2: Receives and writes xtc2 data using psana2 environment.
        - ZeroMQ used for communication between the processes.

    Args:
        params (ConvertXtc1to2Parameters): Configuration for the conversion task.
    """

    def __init__(self, *, params: ConvertXtc1to2Parameters) -> None:
        self._task_parameters: ConvertXtc1to2Parameters
        super().__init__(params=params)

    def _run(self) -> None:
        par: ConvertXtc1to2Parameters = self._task_parameters
        exp: str = par.lute_config.experiment
        run: Union[int, str] = par.lute_config.run
        logger.debug("Starting [XTC1 Sender] in psana 1")
        data_spec: Dict[str, Any] = {}
        detnames: List[str] = []
        for detname, specs in par.xtc1_access_pattern.items():
            det_specs: List[Any] = []
            for spec in specs:
                det_specs.append(spec.dict())
            data_spec[detname] = det_specs
            detnames.append(detname)

        # Temporarily create a socket to find a free port
        context: zmq.Context = zmq.Context()
        data_socket: zmq.sugar.socket.Socket = context.socket(zmq.PULL)
        new_port: int = data_socket.bind_to_random_port("tcp://*")
        if new_port is None:
            # Failed to find a port to bind
            logger.error("Could not find a port to bind!")
            self._result.task_status = TaskStatus.FAILED
            return
        data_socket.close()
        context.term()

        detname_csv: str = ",".join(detnames)
        json_access_pattern: str = json.dumps(data_spec)
        lute_location: str = os.getenv("LUTE_PATH", "")
        assert lute_location
        zmq_process1_cmd: str = (
            f"source /sdf/group/lcls/ds/ana/sw/conda1/manage/bin/psconda.sh && "
            f"python3 {lute_location}/lute/tasks/util/xtc_push.py "
            f"-a '{json_access_pattern}' -e {exp} -p {new_port} "
            f"-r {par.lute_config.run} "
        )
        if par.eventfile != "":
            zmq_process1_cmd += f"-f {par.eventfile} "
        elif par.nevents is not None:
            zmq_process1_cmd += f"-n {par.nevents}"

        push_proc: subprocess.Popen = self._start_zmq_proc(
            zmq_process1_cmd, "[XTC1 Sender]"
        )

        time.sleep(1)

        logger.debug("Starting [XTC2 Writer] in psana 2")
        zmq_process2_cmd: str = (
            f"source /sdf/group/lcls/ds/ana/sw/conda2/manage/bin/psconda.sh && "
            f"python3 {lute_location}/lute/tasks/util/xtc_pull.py "
            f"-d {detname_csv} -e {exp} "
            f"-f {par.output_file} -n {par.node_id} -p {new_port} -r {run} "
        )
        pull_proc: subprocess.Popen = self._start_zmq_proc(
            zmq_process2_cmd, "[XTC2 Writer]"
        )

        while True:
            push_running: bool = self._is_running(push_proc)
            pull_running: bool = self._is_running(pull_proc)

            if not push_running and not pull_running:
                break

            if not push_running:
                if pull_running and push_proc.returncode:
                    self._result.task_status = TaskStatus.FAILED
                    os.kill(pull_proc.pid, signal.SIGINT)
            elif not pull_running:
                if push_running and pull_proc.returncode:
                    self._result.task_status = TaskStatus.FAILED
                    os.kill(push_proc.pid, signal.SIGINT)

            push_stdout, push_stderr = self._read_stdout_stderr(push_proc)
            pull_stdout, pull_stderr = self._read_stdout_stderr(pull_proc)
            if push_stdout is not None:
                print(f"[XTC1 Sender] {push_stdout}", flush=True)
            if push_stderr is not None:
                print(f"[XTC1 Sender] {push_stderr}", flush=True)

            if pull_stdout is not None:
                print(f"[XTC2 Writer] {pull_stdout}", flush=True)
            if pull_stderr is not None:
                print(f"[XTC2 Writer] {pull_stderr}", flush=True)

            time.sleep(0.005)

        if pull_proc.returncode or push_proc.returncode:
            self._result.task_status = TaskStatus.FAILED
            logger.error("Error during the conversion process.")
        else:
            self._result.task_status = TaskStatus.COMPLETED
            logger.info("Conversion completed.")

    def _start_zmq_proc(self, cmd: str, name: str) -> subprocess.Popen:
        """Helper function to source the correct conda env and spawn a subprocess."""

        process: subprocess.Popen = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, text=True
        )
        logger.debug(f"{name} started")
        logger.debug(f"Command: {cmd}")

        return process

    def _is_running(self, proc: subprocess.Popen) -> bool:
        return proc.poll() is None

    def _read_stdout_stderr(
        self, proc: subprocess.Popen
    ) -> Tuple[Optional[str], Optional[str]]:
        stdout: Optional[str] = proc.stdout.read() if proc.stdout is not None else None
        stderr: Optional[str] = proc.stderr.read() if proc.stderr is not None else None

        return stdout, stderr
