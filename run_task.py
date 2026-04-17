import argparse
import logging
import os
import re
import socket
import subprocess
import sys
from typing import List, Optional

from lute.execution.executor import BaseExecutor
from lute import managed_tasks

if __debug__:
    logging.basicConfig(level=logging.DEBUG)
else:
    logging.basicConfig(level=logging.INFO)

logger: logging.Logger = logging.getLogger(__name__)


def setup_mpi_hostfile() -> None:
    """Prepare a hostfile for MPI if running in a SLURM job.

    This function creates a hostfile and sets an environment variable to the path
    where it was created. The hostfile can be used by Tasks that use MPI to determine
    available resources in a SLURM allocation. Depending on environment configuration
    and/or MPI version, the automated MPI mechanism may not work.
    """
    nodelist: str = os.getenv("SLURM_JOB_NODELIST", "")
    if nodelist:
        result: subprocess.CompletedProcess = subprocess.run(
            ["scontrol", "show", "hostnames", nodelist],
            capture_output=True,
            text=True,
            check=True,
        )
        nodes: List[str] = result.stdout.splitlines()
        tpn_str: str = os.getenv("SLURM_TASKS_PER_NODE", "")
        tpn_list: List[int] = []
        for part in tpn_str.split(","):
            m: Optional[re.Match] = re.match(r"(\d+)\(x(\d+)\)", part)
            if m:
                tasks: int = int(m.group(1))
                count: int = int(m.group(2))
                tpn_list.extend([tasks] * count)
            else:
                try:
                    tpn_list.append(int(part))
                except ValueError:
                    pass
        job_id: Optional[str] = os.getenv("SLURM_JOB_ID")
        assert isinstance(job_id, str)
        hostfile_path: str = os.path.abspath(f"lute_hostfile_{job_id}.hosts")
        executor_host: str = socket.gethostname()

        # Setup MPI core affinity. The Task tries to do this as well, but MPI
        # ignores (sometimes). The `Executor` will pin later (in `execute_task`)
        # since the core counts may be used by various calculations.
        mpi_cpuset: str = ""
        try:
            available_cores: List[int] = sorted(list(os.sched_getaffinity(0)))
            if len(available_cores) > 1:
                mpi_cpuset = ",".join(map(str, available_cores[1:]))
                logger.debug(f"MPI cpuset for {executor_host}: {mpi_cpuset}")
            else:
                logger.warning(
                    f"Only one core available on {executor_host} - the Executor "
                    "and Task will share it."
                )
        except Exception as e:
            logger.warning(f"Could not determine CPU affinity: {e}")

        with open(hostfile_path, "w") as f:
            node: str
            tpn: int
            for node, tpn in zip(nodes, tpn_list):
                if node == executor_host and mpi_cpuset:
                    # Explicitly assign the reserved core list to this node
                    n_slots = tpn - 1
                    f.write(f"{node} slots={n_slots} cpuset={mpi_cpuset}\n")
                else:
                    n_slots = tpn
                    f.write(f"{node} slots={n_slots}\n")

        # Task layer will look for this environment variable
        os.environ["LUTE_MPI_HOSTFILE_PATH"] = hostfile_path


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog="run_managed_task",
        description="Run a LUTE managed task.",
        epilog="Refer to https://github.com/slac-lcls/lute for more information.",
    )
    parser.add_argument(
        "-c", "--config", type=str, help="Path to config file with Task parameters."
    )
    parser.add_argument(
        "-t",
        "--taskname",
        type=str,
        help="Name of the Managed Task to run.",
        default="test",
    )

    args: argparse.Namespace = parser.parse_args()
    config: str = args.config
    task_name: str = args.taskname

    # Environment variables need to be set before importing Executors
    os.environ["LUTE_CONFIGPATH"] = config

    # Prepare hostfile in case using MPI
    setup_mpi_hostfile()

    # Try to find the *managed* Task - In case of generated parameter combos, there
    # may be a suffix in the name, e.g. Tester_0, Tester_1, so check for this
    # if names don't match
    managed_task: BaseExecutor
    if hasattr(managed_tasks, task_name):
        managed_task = getattr(managed_tasks, task_name)
    else:
        # Check if this is a generated Task name with suffix (e.g., TaskName_0)
        # Try stripping the numeric suffix
        base_task_name: str = re.sub(r"_\d+$", "", task_name)
        suffix_match: Optional[re.Match[str]] = re.search(r"(_\d+)$", task_name)
        suffix: str = suffix_match.group(1) if suffix_match else ""

        if base_task_name != task_name and hasattr(managed_tasks, base_task_name):
            # Found the underlying managed Task
            logger.info(
                f"Parameter sweep managed Task detected: '{task_name}' -> "
                f"base task '{base_task_name}'"
            )
            managed_task = getattr(managed_tasks, base_task_name)

            # IMPORTANT: For config lookup, we need Task name + suffix
            # Full process is:
            #   1. Find the *managed* Task: TestRunner_0 -> TestRunner
            #   2. For this instance of the *managed* Task, change the name
            #      of the Task it runs so that config look up works later.
            #      TestRunner.task_name = Test -> TestRunner.task_name = Test_0
            if hasattr(managed_task, "task_name"):
                suffixed_task_name: str = f"{managed_task.task_name}{suffix}"
                logger.info(f"Resolved config section name: '{suffixed_task_name}'")
                managed_task.task_name = suffixed_task_name
        else:
            # Task not found even after stripping suffix
            import difflib

            logger.error(f"{task_name} unrecognized!")
            valid_names: List[str] = [
                name
                for name in dir(managed_tasks)
                if isinstance(getattr(managed_tasks, name), BaseExecutor)
            ]
            # List below may be empty...
            possible_options: List[str] = difflib.get_close_matches(
                task_name, valid_names, n=2, cutoff=0.1
            )
            if possible_options:
                logger.info(f"Perhaps you meant: {possible_options}?")
                logger.info(f"All possible options are: {valid_names}")
            else:
                logger.info(
                    f"Could not infer a close match for the managed Task name. Possible options are: {valid_names}"
                )
            sys.exit(-1)

    # Set the name of the *managed* Task (Executor uses this for DB calls)
    managed_task._m_task_name = task_name
    managed_task.execute_task()


if __name__ == "__main__":
    main()
