"""Script submitted by Automated Run Processor (ARP) to trigger a SLURM-job workflow.

This script is submitted by the ARP to the batch nodes. It runs a batch job which itself
submits the individual workflow job steps.
"""

__author__ = "Gabriel Dorlhiac"

import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
import yaml
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Literal, Optional, Tuple, Type, Union, overload
from typing_extensions import TypedDict

from lute.execution.launch import (
    get_base_launch_parser,
    setup_launch_env,
    retrieve_run_info,
)

logger: logging.Logger = logging.getLogger("Launch_SLURM_Workflow")
handler: logging.Handler = logging.StreamHandler()
formatter: logging.Formatter = logging.Formatter(logging.BASIC_FORMAT)
handler.setFormatter(formatter)
logger.addHandler(handler)

if __debug__:
    logger.setLevel(logging.DEBUG)
else:
    logger.setLevel(logging.INFO)

BUFFER_LOGS: bool = True
MANAGED_TASK_STATUS: Dict[str, str] = {}
MANAGED_TASK_LOGS: Optional[Dict[str, List[str]]] = None
STATUS_LOCK: threading.Lock = threading.Lock()


class JobStatusHandler(BaseHTTPRequestHandler):

    def _handle_route(self, route: str, data: Dict[str, Any]) -> None:
        global BUFFER_LOGS
        global MANAGED_TASK_LOGS
        global MANAGED_TASK_STATUS
        global STATUS_LOCK

        if route == "/status":
            managed_task: str = data["managed_task"]
            status: str = data["status"]
            date_time: str = self.date_time_string()
            print(f"{date_time} [{managed_task}] Status = {status}.", flush=True)
            self.send_response(200)
            self.end_headers()
            with STATUS_LOCK:
                MANAGED_TASK_STATUS[managed_task] = status

        elif route == "/log":
            managed_task = data["managed_task"]
            message: str = data["message"]
            date_time = self.date_time_string()
            self.send_response(200)
            self.end_headers()

            formatted_msg: str = f"{date_time} [{managed_task}] {message}"
            if BUFFER_LOGS:
                assert MANAGED_TASK_LOGS is not None
                with STATUS_LOCK:
                    if managed_task in MANAGED_TASK_LOGS:
                        MANAGED_TASK_LOGS[managed_task].append(formatted_msg)
                    else:
                        MANAGED_TASK_LOGS[managed_task] = [formatted_msg]
            else:
                print(formatted_msg)
        else:
            print(f"Invalid route: {route}", flush=True)
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any):
        pass

    def do_POST(self) -> None:
        content_length: int = int(self.headers.get("Content-Length", 0))
        body: bytes = self.rfile.read(content_length)
        try:
            data: Dict[str, Any] = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return None
        self._handle_route(route=self.path, data=data)


class LuteParams(TypedDict):
    config_file: str
    debug: bool


class MissingParametersException(Exception): ...


@overload
def _run_subprocess_log(cmd: List[str], return_output: Literal[True]) -> str: ...


@overload
def _run_subprocess_log(cmd: List[str], return_output: Literal[False]) -> None: ...


@overload
def _run_subprocess_log(cmd: List[str]) -> None: ...


def _run_subprocess_log(cmd: List[str], return_output: bool = False) -> Optional[str]:
    """Run a subprocess with logging."""
    global logger

    out: str
    err: str
    out, err = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True
    ).communicate()
    # if out:
    #    logger.info(out)
    # if err:
    #    logger.info(err)

    if return_output:
        return out
    return None


def get_slurm_logfile_path(jobid: str) -> str:
    # scontrol show job <JobID> | grep StdOut | cut -d "=" -f2
    # may only work for job that is currently running
    # Would maybe run this first command and save the log file path
    scontrol_cmd: List[str] = [
        "scontrol",
        "show",
        "job",
        jobid,
    ]
    scontrol_out: str = _run_subprocess_log(scontrol_cmd, return_output=True)
    pattern: str = r"StdOut=.*"
    logfile_path: str = (
        re.findall(pattern, scontrol_out)[0].replace("%J", jobid).split("=")[1]
    )
    return logfile_path


def get_slurm_log(logfile_path: str) -> str:
    read_logfile_cmd: List[str] = ["cat", logfile_path]
    logfile: str = _run_subprocess_log(read_logfile_cmd, return_output=True)

    return logfile


def get_slurm_job_status(jobid: str) -> str:
    status_cmd: List[str] = [
        "sacct",
        "-j",
        jobid,
        "-o",
        "State",
    ]
    status_cmd_out: str = _run_subprocess_log(status_cmd, return_output=True)
    status: str = status_cmd_out.split("\n")[2].strip().replace("+", "")
    return status


def prepare_launch_command(
    task_name: str,
    lute_params: LuteParams,
    slurm_params: str,
    kerb_file: Optional[str] = None,
) -> str:
    if lute_params == {}:
        logger.critical("Empty LUTE parameter dictionary! Need configuration YAML!")
        raise MissingParametersException

    config_path: str = lute_params["config_file"]
    lute_param_str: str
    if lute_params["debug"]:
        lute_param_str = f"--taskname {task_name} --config {config_path} --debug"
    else:
        lute_param_str = f"--taskname {task_name} --config {config_path}"

    if kerb_file is not None:
        lute_param_str = f"{lute_param_str} -K {kerb_file}"

    parameter_str: str = f"{lute_param_str} {slurm_params}"
    return parameter_str


def launch_lute_task(
    lute_location: str,
    task_name: str,
    lute_params: LuteParams,
    slurm_params: str,
    kerb_file: Optional[str] = None,
    wait_for: Optional[Future] = None,
) -> Tuple[str, str, str]:
    global BUFFER_LOGS
    global MANAGED_TASK_LOGS
    global MANAGED_TASK_STATUS
    global STATUS_LOCK

    if wait_for is not None:
        while not wait_for.done():
            ...

        prev_task: str
        prev_status: str
        prev_task, prev_status, _ = wait_for.result()
        if prev_status in ("FAILED", "UPSTREAM_FAILED", "CANCELLED"):
            log: str = (
                f"---- UPSTREAM {prev_task} FAILED, NOT LAUNCHING {task_name} ----"
            )
            status: str = "UPSTREAM_FAILED"
            return task_name, status, log

    parameter_str: str = prepare_launch_command(
        task_name=task_name,
        lute_params=lute_params,
        slurm_params=slurm_params,
        kerb_file=kerb_file,
    )
    executable: str = f"{lute_location}/launch_scripts/submit_slurm"
    launch_cmd: List[str] = [executable]
    launch_cmd.extend(parameter_str.split())

    logger.info(f"Submitting {task_name}")
    out: str = _run_subprocess_log(launch_cmd, return_output=True)

    # grab jobid from out
    pattern: str = r"Submitted batch job [0-9]{0,100}"
    jobid_full_str: str = re.findall(pattern, out)[0]

    jobid: str = jobid_full_str.split()[-1]

    # logfile_path: str = get_slurm_logfile_path(jobid=jobid)
    # Loop on task here?
    logfile: str = ""

    while True:
        with STATUS_LOCK:
            if task_name not in MANAGED_TASK_STATUS:
                status = "PENDING"
            else:
                status = MANAGED_TASK_STATUS[task_name]

        if BUFFER_LOGS:
            assert MANAGED_TASK_LOGS is not None
            with STATUS_LOCK:
                if task_name in MANAGED_TASK_LOGS and MANAGED_TASK_LOGS[task_name]:
                    for _ in range(len(MANAGED_TASK_LOGS[task_name])):
                        logfile += f"{MANAGED_TASK_LOGS[task_name].pop(0)}\n"
        # If running in unbuffered mode, the HTTPServer will just flush the log
        # immediately as it comes in. This loop doesn't have to do anything

        if status in ("FAILED", "COMPLETED", "CANCELLED", "TIMEDOUT"):
            break
        time.sleep(5)

    if not logfile:
        logfile = f"---- NO LOGS FOR JOB {jobid} RUNNING {task_name} ----"

    return task_name, status, logfile


def create_workflow(
    executor: ThreadPoolExecutor,
    wait_for: Optional[Future],
    all_futures: List[Future],
    wf_dict: Union[Dict[str, Any], List[Dict[str, Any]]],
    lute_location: str,
    lute_params: LuteParams,
    default_slurm_params: str,
    kerb_file: Optional[str] = None,
) -> None:
    slurm_params: str
    future: Future
    if isinstance(wf_dict, list):
        for task_dict in wf_dict:
            slurm_params = task_dict.get("slurm_params", "")
            if not slurm_params:
                slurm_params = default_slurm_params
            future = executor.submit(
                launch_lute_task,
                lute_location,
                task_dict["task_name"],
                lute_params,
                slurm_params,
                kerb_file,
                wait_for,
            )
            all_futures.append(future)
            if task_dict["next"] == [] or task_dict["next"] is None:
                pass
            else:
                for task in task_dict["next"]:
                    create_workflow(
                        executor,
                        wait_for=future,
                        all_futures=all_futures,
                        wf_dict=task,
                        lute_location=lute_location,
                        lute_params=lute_params,
                        default_slurm_params=default_slurm_params,
                        kerb_file=kerb_file,
                    )
    else:
        slurm_params = wf_dict.get("slurm_params", "")
        if not slurm_params:
            slurm_params = default_slurm_params
        future = executor.submit(
            launch_lute_task,
            lute_location,
            wf_dict["task_name"],
            lute_params,
            slurm_params,
            kerb_file,
            wait_for,
        )
        all_futures.append(future)
        if wf_dict["next"] == [] or wf_dict["next"] is None:
            return None
        else:
            for task in wf_dict["next"]:
                create_workflow(
                    executor,
                    wait_for=future,
                    all_futures=all_futures,
                    wf_dict=task,
                    lute_location=lute_location,
                    lute_params=lute_params,
                    default_slurm_params=default_slurm_params,
                    kerb_file=kerb_file,
                )
    return None


def count_tasks_and_print_wf(
    wf: Union[List[Dict[str, Any]], Dict[str, Any]], wf_str: str
) -> Tuple[str, int]:
    if isinstance(wf, list):
        parallel_str: str = ""
        task_count: int = 0
        for task_dict in wf:
            task_count += 1
            task_name: str = task_dict["task_name"]
            new_str: str
            if wf_str != "":
                new_str = f"{wf_str} >> {task_name}"
            else:
                new_str = task_name

            if task_dict["next"] == [] or task_dict["next"] is None:
                parallel_str += f"{task_name}\n"
            else:
                full_branched_str: str = ""
                for task in task_dict["next"]:
                    branch_str: str
                    branch_task_count: int
                    branch_str, branch_task_count = count_tasks_and_print_wf(
                        task, new_str
                    )
                    full_branched_str += f"{branch_str}\n"
                    task_count += branch_task_count
                parallel_str += f"{full_branched_str}\n"
        return parallel_str, task_count
    else:
        task_name = wf["task_name"]
        if wf_str != "":
            new_str = f"{wf_str} >> {task_name}"
        else:
            new_str = task_name

        if wf["next"] == [] or wf["next"] is None:
            return f"{new_str}", 1
        else:
            task_count = 1
            full_branched_str = ""
            for task in wf["next"]:
                branch_str, branch_task_count = count_tasks_and_print_wf(task, new_str)
                full_branched_str += f"{branch_str}\n"
                task_count += branch_task_count

            return full_branched_str, task_count


def main() -> None:
    """Run the SLURM Workflow Manager (SWM).

    This will launch the a series of jobs as run
    """
    global BUFFER_LOGS
    global MANAGED_TASK_LOGS

    parser = get_base_launch_parser(
        "A light-weight workflow manager which executes LUTE Managed Tasks."
    )
    parser.add_argument(
        "--unbuffered",
        help=(
            "Flush logs immediately. Warning: This can make output confusing "
            "when running multiple managed Tasks are running in parallel."
        ),
        action="store_true",
    )

    args, extra_args = parser.parse_known_args()

    launch_info = setup_launch_env(args)
    experiment = launch_info["experiment"]
    run_num = launch_info["run_num"]
    jid_authorization = launch_info["authorization"]
    cache_file = launch_info["kerb_file"]

    BUFFER_LOGS = not args.unbuffered
    if BUFFER_LOGS:
        # If buffering make sure we have a dict here.
        MANAGED_TASK_LOGS = {}

    run_type, is_daq2 = retrieve_run_info(
        experiment, run_num, jid_authorization, args.type
    )
    if is_daq2 is None:
        is_daq2 = True  # Fallback to original behavior if unknown
    wf_defn: Dict[str, Any]

    def branch_daq2_constructor(
        loader: yaml.SafeLoader, node: yaml.nodes.MappingNode
    ) -> Any:
        values = loader.construct_mapping(node)
        if is_daq2:
            return values["daq2"]
        else:
            return values["daq1"]

    loader: Type[yaml.SafeLoader] = yaml.SafeLoader
    loader.add_constructor("!branch_daq2", branch_daq2_constructor)
    with open(args.workflow_defn, "r") as f:
        wf_defn = yaml.load(stream=f, Loader=loader)

    lute_location: str = os.path.abspath(f"{os.path.dirname(__file__)}/..")

    lute_params: LuteParams = {"config_file": args.config, "debug": args.debug}
    default_slurm_params: str = " ".join(extra_args)

    wf_repr: str
    task_count: int
    wf_repr, task_count = count_tasks_and_print_wf(wf_defn, "")

    logger.info(f"Running the following workflow with {task_count} Managed Tasks:")
    print(wf_repr, flush=True)

    server: HTTPServer = HTTPServer(("0.0.0.0", 41239), JobStatusHandler)
    os.environ["LUTE_MANAGER_URL"] = f"{socket.gethostname()}:41239"
    server_thread: threading.Thread = threading.Thread(
        target=server.serve_forever, daemon=True
    )
    server_thread.start()
    with ThreadPoolExecutor(max_workers=task_count) as executor:
        all_futures: List[Future] = []
        # Recursively submit work to the ThreadPoolExecutor. The individual functions
        # submitted to the ThreadPoolExecutor wait on previous futures if
        # appropriate
        create_workflow(
            executor=executor,
            wait_for=None,
            all_futures=all_futures,
            wf_dict=wf_defn,
            lute_location=lute_location,
            lute_params=lute_params,
            default_slurm_params=default_slurm_params,
            kerb_file=cache_file,
        )

        n_failed: int = 0
        for future in as_completed(all_futures):  # as_completed from concurrent.futures
            task_name: str
            status: str
            logfile: str
            task_name, status, logfile = future.result()
            if not args.unbuffered:
                logger.info(f"Providing logs for {task_name}")
                print("-" * 50, flush=True)
                print(logfile, flush=True)
                print("-" * 50, flush=True)
            if status in ("FAILED", "UPSTREAM_FAILED"):
                # Need smarter way to determine workflow failure
                # For now count any step failing as the entire workflow failing
                n_failed += 1

        if n_failed > 0:
            logger.info("Workflow exited: FAILED")
            sys.exit(-1)
        else:
            logger.info("Workflow exited: COMPLETED")

    server.shutdown()
    server.server_close()
    server_thread.join()


if __name__ == "__main__":
    main()
