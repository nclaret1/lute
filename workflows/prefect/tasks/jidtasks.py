"""Prefect tasks for running LUTE Managed tasks via JID.

Prefect tasks submit LUTE managed tasks to run and monitor task status via JID.
Status is monitored by the Prefect instance which manages the execution order
of a `flow` (workflow) to determine which managed task to submit and when.

Classes:
    run_managed_task: Submits a managed task to run on S3DF batch nodes via the
        job interface daemon (JID). Prefect itself has no access to data or the
        file system mounted on the batch node so submission and monitoring is
        done exclusively via the JID API.
"""

__all__ = ["run_managed_task"]
__author__ = "Gabriel Dorlhiac"

import uuid
import time
import logging
import re
from typing import Dict, Any, Union, List, Optional

import requests
from prefect import task
from prefect.states import Completed, Failed, State

from flow_dataclasses import FlowConf, LuteParams

logger: logging.Logger = logging.getLogger(__name__)

JID_API_LOCATION: str = "https://psdm.slac.stanford.edu/arps3dfjid/jid/ws"
"""S3DF JID API location."""

JID_API_ENDPOINTS: Dict[str, str] = {
    "start_job": "{experiment}/start_job",
    "job_statuses": "job_statuses",
    "job_log_file": "{experiment}/job_log_file",
}


class JIDException(Exception): ...


class MissingParametersException(Exception): ...


def sub_overridable_arguments(
    slurm_param_str: str,
    max_cores: Optional[int] = None,
    max_nodes: Optional[int] = None,
    require_partition: Optional[str] = None,
) -> str:
    """Overrides certain SLURM arguments given instance options.

    Since the same SLURM arguments are used by default for the entire DAG,
    individual Operator instances can override some important ones if they
    are passed at instantiation.

    ASSUMES `=` is used with SLURM arguments! E.g. --ntasks=12, --nodes=0-4

    Args:
        slurm_param_str (str): Constructed string of DAG SLURM arguments
            without modification

        max_cores (Optional[int]): Optionally override number of cores for SLURM
            job submission.

        max_nodes (Optional[int]): Optionally override number of nodes for SLURM
            job submission.

        require_partition (Optional[str]): Optionally require a specific job by run
            on a specific SLURM partition.

    Returns:
        slurm_param_str (str): Modified SLURM argument string.
    """
    # Cap max cores used by a managed Task if that is requested
    # Only search for part after `=` since this will usually be passed
    if max_cores is not None:
        pattern: str = r"(?<=\bntasks=)\d+"
        ntasks: int
        try:
            ntasks = int(re.findall(pattern, slurm_param_str)[0])
            if ntasks > max_cores:
                slurm_param_str = re.sub(pattern, f"{max_cores}", slurm_param_str)
        except IndexError:  # If `ntasks` not passed - 1 is default
            ntasks = 1
            slurm_param_str = f"{slurm_param_str} --ntasks={ntasks}"

    # Cap max nodes. Unlike above search for everything, if not present, add it.
    if max_nodes is not None:
        pattern = r"nodes=\S+"
        try:
            _ = re.findall(pattern, slurm_param_str)[0]
            # Check if present with above. Below does nothing but does not
            # throw error if pattern not present.
            slurm_param_str = re.sub(pattern, f"nodes=0-{max_nodes}", slurm_param_str)
        except IndexError:  # `--nodes` not present
            slurm_param_str = f"{slurm_param_str} --nodes=0-{max_nodes}"

    # Force use of a specific partition
    if require_partition is not None:
        pattern = r"partition=\S+"
        try:
            _ = re.findall(pattern, slurm_param_str)[0]
            # Check if present with above. Below does nothing but does not
            # throw error if pattern not present.
            slurm_param_str = re.sub(
                pattern, f"partition={require_partition}", slurm_param_str
            )
        except IndexError:  # --partition not present. This shouldn't happen
            slurm_param_str = f"{slurm_param_str} --partition={require_partition}"

    return slurm_param_str


def create_control_doc(
    lute_task_id: str,
    conf: FlowConf,
    custom_slurm_params: str = "",
    max_cores: Optional[int] = None,
    max_nodes: Optional[int] = None,
    require_partition: Optional[str] = None,
) -> Dict[str, Union[str, Dict[str, str]]]:
    """Prepare the control document for job submission via the JID.

    Translates and Airflow dictionary to the representation needed by the
    JID.

    Args:
        lute_task_id (str): Name of the LUTE Managed Task to run.

        conf (FlowConf): Dictionary containing information for LUTE submission.

        custom_slurm_params (str): Optionally override all the SLURM options which are
            present in the `conf` object.

        max_cores (Optional[int]): Optionally override number of cores for SLURM
            job submission.

        max_nodes (Optional[int]): Optionally override number of nodes for SLURM
            job submission.

        require_partition (Optional[str]): Optionally require a specific job by run
            on a specific SLURM partition.

    Returns:
        control_doc (Dict[str, Union[str, Dict[str, str]]]): JID job control
            dictionary.
    """

    lute_location: str = conf.get(
        "lute_location", "/sdf/group/lcls/ds/tools/lute/latest"
    )
    executable_subdir: str = conf.get("executable_subdir","launch_scripts")
    lute_params: LuteParams = conf.get("lute_params", {})

    if lute_params == {}:
        logger.critical("Empty LUTE parameter dictionary! Need configuration YAML!")
        raise MissingParametersException
    config_path: str = lute_params["config_file"]
    # Note that task_id is from the parent class.
    # When defining the Operator instances the id is assumed to match a
    # managed task!
    lute_param_str: str
    if lute_params["debug"]:
        lute_param_str = f"--taskname {lute_task_id} --config {config_path} --debug"
    else:
        lute_param_str = f"--taskname {lute_task_id} --config {config_path}"

    is_daq2: Optional[bool] = conf.get("is_daq2", False)
    if is_daq2:
        lute_param_str = f"{lute_param_str} --psana2"

    kerb_file: Optional[str] = conf.get("kerb_file")
    if kerb_file is not None:
        lute_param_str = f"{lute_param_str} -K {kerb_file}"

    slurm_param_str: str
    if custom_slurm_params:  # SLURM params != ""
        slurm_param_str = custom_slurm_params
    else:
        # slurm_params holds a List[str]
        slurm_param_str = " ".join(conf.get("slurm_params"))

        # Make any requested SLURM argument substitutions
        slurm_param_str = sub_overridable_arguments(
            slurm_param_str, max_cores, max_nodes, require_partition
        )

    parameter_str: str = f"{lute_param_str} {slurm_param_str}"
    jid_job_definition: Dict[str, str] = {
        "_id": str(uuid.uuid4()),
        "name": lute_task_id,
        "executable": f"{lute_location}/{executable_subdir}/submit_slurm",
        "trigger": "MANUAL",
        "location": conf.get("ARP_LOCATION", "S3DF"),
        "parameters": parameter_str,
        "run_as_user": conf.get("user"),
    }

    control_doc: Dict[str, Union[str, Dict[str, str]]] = {
        "_id": str(uuid.uuid4()),
        "arp_root_job_id": conf.get("ARP_ROOT_JOB_ID"),
        "experiment": conf.get("experiment"),
        "run_num": conf.get("run_id"),
        "user": conf.get("user"),
        "status": "",
        "tool_id": "",
        "def_id": str(uuid.uuid4()),
        "def": jid_job_definition,
    }

    return control_doc


def parse_response(
    resp: requests.models.Response, check_for_error: List[str]
) -> Dict[str, Any]:
    """Parse a JID HTTP response.

    Args:
        resp (requests.models.Response): The response object from a JID
            HTTP request.

        check_for_error (List[str]): A list of strings/patterns to search
            for in response. Exception is raised if there are any matches.

    Returns:
        value (Dict[str, Any]): Dictionary containing HTTP response value.
    """
    logger.debug(f"{resp.status_code}: {resp.content}")
    if resp.status_code not in (200,):
        raise JIDException(f"Bad response from JID {resp}: {resp.content}")
    try:
        json: Dict[str, Union[str, int]] = resp.json()
        if json.get("success", "") not in (True,):
            raise Exception(f"Error from JID {resp}: {resp.content}")
        value: Dict[str, Any] = json.get("value")

        for pattern in check_for_error:
            if pattern in value:
                raise Exception(
                    f"Response failed due to string match {pattern} against response {value}"
                )
        return value
    except Exception as err:
        raise JIDException(f"Response from JID not parseable, unknown error: {err}")


def rpc(
    endpoint: str,
    control_doc: Union[
        List[Dict[str, Union[str, Dict[str, str]]]],
        Dict[str, Union[str, Dict[str, str]]],
    ],
    conf: FlowConf,
    check_for_error: List[str] = [],
) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """Submit job via JID and retrieve responses.

    Remote Procedure Call (RPC).

    Args:
        endpoint (str): Which API endpoint to use.

        control_doc (Dict[str, Union[str, Dict[str, str]]]): Dictionary for
            JID call.

        conf (FlowConf): Dictionary containing information for LUTE submission.

        check_for_error (List[str]): A list of keywords to search for in a
            response to indicate error conditions. Default [].

    Returns:
        value (Dict[str, Any]): Dictionary containing HTTP response value.
    """
    # if not self.get_location(context) in self.locations:
    #     raise AirflowException(f"JID location {self.get_location(context)} is not configured")
    experiment: str = conf.get("experiment")
    auth: str = conf.get("Authorization")

    uri: str = f"{JID_API_LOCATION}/{JID_API_ENDPOINTS[endpoint]}"
    # Endpoints have the string "{experiment}" in them
    uri = uri.format(experiment=experiment)

    logger.debug(f"Calling {uri} with {control_doc}...")

    resp: requests.models.Response = requests.post(
        uri, json=control_doc, headers={"Authorization": auth}
    )
    logger.debug(f" + {resp.status_code}: {resp.content.decode('utf-8')}")

    value: Dict[str, Any] = parse_response(resp, check_for_error)

    return value


@task
def run_managed_task(
    lute_task_id: str,
    conf: FlowConf,
    poke_interval: float = 5.0,
    max_cores: Optional[int] = None,
    max_nodes: Optional[int] = None,
    require_partition: Optional[str] = None,
    custom_slurm_params: str = "",
) -> State:
    """Task which submits a LUTE Managed Task via the JID.

    Args:
        lute_task_id (str): Name of the LUTE Managed Task to run.

        conf (FlowConf): Dictionary containing information for LUTE submission.

        poke_interval (float): Status polling interval in seconds.

        max_cores (Optional[int]): Optionally override number of cores for SLURM
            job submission.

        max_nodes (Optional[int]): Optionally override number of nodes for SLURM
            job submission.

        require_partition (Optional[str]): Optionally require a specific job by run
            on a specific SLURM partition.

        custom_slurm_params (str): Optionally override all the SLURM options which are
            present in the `conf` object.
    """
    print(f"Running LUTE Managed Task: {lute_task_id}")
    logger.info("Attempting to run at S3DF.")
    try:
        control_doc = create_control_doc(
            lute_task_id=lute_task_id,
            conf=conf,
            custom_slurm_params=custom_slurm_params,
            max_cores=max_cores,
            max_nodes=max_nodes,
            require_partition=require_partition,
        )
    except MissingParametersException as err:
        return Failed(message=f"Missing conf parameters: {err}")

    logger.info(control_doc)
    logger.info(f"{JID_API_LOCATION}/{JID_API_ENDPOINTS['start_job']}")
    # start_job requires a dictionary
    try:
        msg: Dict[str, Any] = rpc(
            endpoint="start_job", control_doc=control_doc, conf=conf
        )
        logger.info(f"JobID {msg['tool_id']} successfully submitted!")

        jobs: List[Dict[str, Any]] = [msg]
        logger.info("Checking for job completion.")
        while jobs[0].get("status") in ("RUNNING", "SUBMITTED"):
            jobs = rpc(
                endpoint="job_statuses",
                control_doc=jobs,
                conf=conf,
                check_for_error=[" error: ", "Traceback"],
            )
            time.sleep(poke_interval)

        # Grab Task logs
        out = rpc("job_log_file", jobs[0], conf)
        # context["task_instance"].xcom_push(key="log", value=out)
        final_status: str = jobs[0].get("status")
        logger.info(f"Final status: {final_status}")
        if final_status in ("FAILED", "EXITED"):
            # Only DONE indicates success. EXITED may be cancelled or SLURM err
            logger.error(f"`Task` job marked as {final_status}!")
            return Failed(message=f"`Task` job marked as {final_status}")
    except JIDException as err:
        return Failed(message=f"JIDException: {err}")

    failure_messages: List[str] = [
        "INFO:lute.execution.executor:Task failed with return code:",
        "INFO:lute.execution.executor:Exiting after Task failure.",
    ]
    failure_msg: str
    for failure_msg in failure_messages:
        if failure_msg in out:
            logger.error("Logs indicate `Task` failed!")
            return Failed(message="Logs indicate `Task` failed!")

    print(out)
    return Completed(message="Task completed successfully.")
