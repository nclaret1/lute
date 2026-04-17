"""Airflow Operators for running LUTE tasks via Airflow.

Operators submit managed tasks to run and monitor task status. Status is
reported to Airflow which manages the execution order of a directed acyclic
graph (DAG) to determine which managed task to submit and when.

Classes:
    JIDSlurmOperator: Submits a managed task to run on S3DF batch nodes via the
        job interface daemon (JID). Airflow itself has no access to data or the
        file system mounted on the batch node so submission and monitoring is
        done exclusively via the JID API.
"""

__all__ = ["JIDSlurmOperator", "RequestOnlyOperator"]
__author__ = "Fred Poitevin, Murali Shankar, Gabriel Dorlhiac"

import sys
import uuid
import getpass
import time
import logging
import re
from typing import Dict, Any, Union, List, Optional

import requests

from airflow.models import BaseOperator
from airflow.exceptions import AirflowException
from airflow.plugins_manager import AirflowPlugin
from airflow.utils.decorators import apply_defaults

if __debug__:
    logging.basicConfig(level=logging.DEBUG)
else:
    logging.basicConfig(level=logging.INFO)

logger: logging.Logger = logging.getLogger(__name__)


class RequestOnlyOperator(BaseOperator):
    """This Operator makes a JID request and exits."""

    @apply_defaults
    def __init__(
        self,
        user: str = getpass.getuser(),
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)  # Initializes self.task_id
        self.user: str = user

    def execute(self, context: Dict[str, Any]) -> None:
        """Method called by Airflow which submits SLURM Job via JID.

        Args:
            context (Dict[str, Any]): Airflow dictionary object.
                https://airflow.apache.org/docs/apache-airflow/stable/templates-ref.html
                contains a list of available variables and their description.
        """
        # logger.info(f"Attempting to run at {self.get_location(context)}...")
        logger.info("Attempting to run at S3DF.")
        dagrun_config: Dict[str, Union[str, Dict[str, Union[str, int, List[str]]]]] = (
            context.get("dag_run").conf
        )
        jid_job_definition: Dict[str, str] = {
            "_id": str(uuid.uuid4()),
            "name": self.task_id,
            "executable": "myexecutable.sh",
            "trigger": "MANUAL",
            "location": dagrun_config.get("ARP_LOCATION", "S3DF"),
            "parameters": "--partition=milano --account=lcls:data",
            "run_as_user": self.user,
        }

        control_doc: Dict[str, Union[str, Dict[str, str]]] = {
            "_id": str(uuid.uuid4()),
            "arp_root_job_id": dagrun_config.get("ARP_ROOT_JOB_ID"),
            "experiment": dagrun_config.get("experiment"),
            "run_num": dagrun_config.get("run_id"),
            "user": dagrun_config.get("user"),
            "status": "",
            "tool_id": "",
            "def_id": str(uuid.uuid4()),
            "def": jid_job_definition,
        }

        uri: str = (
            "https://psdm.slac.stanford.edu/arps3dfjid/jid/ws/{experiment}/start_job"
        )
        # Endpoints have the string "{experiment}" in them
        uri = uri.format(experiment=dagrun_config.get("experiment"))
        auth: Any = dagrun_config.get("Authorization")
        logger.info(f"Calling {uri} with {control_doc}...")

        logger.info(requests.__file__)
        resp: requests.models.Response = requests.post(
            uri, json=control_doc, headers={"Authorization": auth}
        )
        logger.info(f"Status code: {resp.status_code}")
        logger.info(requests.__file__)


class JIDSlurmOperator(BaseOperator):
    """Airflow Operator which submits SLURM jobs through the JID."""

    ui_color: str = "#006699"

    jid_api_location: str = "https://psdm.slac.stanford.edu/arps3dfjid/jid/ws"
    """S3DF JID API location."""

    jid_api_endpoints: Dict[str, str] = {
        "start_job": "{experiment}/start_job",
        "job_statuses": "job_statuses",
        "job_log_file": "{experiment}/job_log_file",
    }

    @apply_defaults
    def __init__(
        self,
        user: str = getpass.getuser(),
        poke_interval: float = 5.0,
        max_cores: Optional[int] = None,
        max_nodes: Optional[int] = None,
        require_partition: Optional[str] = None,
        custom_slurm_params: str = "",
        *args,
        **kwargs,
    ) -> None:
        """Runs a LUTE managed Task on the batch nodes.

        Args:
            user (str): User to run the SLURM job as.
            poke_interval (float): How frequently to ping the JID for status
                updates.
            max_cores (Optional[int]): The maximum number of cores to allow
                for this job. If more cores are requested in the Airflow context
                setting this parameter will make sure the job request is capped.
            max_nodes (Optional[int]): The maximum number of nodes to allow
                this job to run across. If more nodes are requested, or no node
                specification is provided this parameter will cap the requested
                node count. This can be used, e.g. to prevent non-MPI jobs from
                running on multiple nodes.
            require_partition (Optional[str]): Force the job to run on a specific
                partition. Will override the passed partition if it is different.
            custom_slurm_params (str): If a non-empty string this will replace
                ALL the SLURM arguments that are passed via Airflow context. If
                used it therefore MUST contain every needed argument e.g.:
                     "--partition=<...> --account=<...> --ntasks=<...>"
        """
        super().__init__(*args, **kwargs)  # Initializes self.task_id
        self.lute_location: str = ""
        self.user: str = user
        self.poke_interval: float = poke_interval
        self.max_cores: Optional[int] = max_cores
        self.max_nodes: Optional[int] = max_nodes
        self.require_partition: Optional[str] = require_partition
        self.lute_task_id: str = kwargs.get("task_id", "")
        if "." in self.lute_task_id:
            # In a task_group the group id is prepended to task_id
            # We want to remove this and only keep the last portion
            self.lute_task_id = self.lute_task_id.split(".")[-1]
        self.custom_slurm_params: str = custom_slurm_params

    def _sub_overridable_arguments(self, slurm_param_str: str) -> str:
        """Overrides certain SLURM arguments given instance options.

        Since the same SLURM arguments are used by default for the entire DAG,
        individual Operator instances can override some important ones if they
        are passed at instantiation.

        ASSUMES `=` is used with SLURM arguments! E.g. --ntasks=12, --nodes=0-4

        Args:
            slurm_param_str (str): Constructed string of DAG SLURM arguments
                without modification
        Returns:
            slurm_param_str (str): Modified SLURM argument string.
        """
        # Cap max cores used by a managed Task if that is requested
        # Only search for part after `=` since this will usually be passed
        if self.max_cores is not None:
            pattern: str = r"(?<=\bntasks=)\d+"
            ntasks: int
            try:
                ntasks = int(re.findall(pattern, slurm_param_str)[0])
                if ntasks > self.max_cores:
                    slurm_param_str = re.sub(
                        pattern, f"{self.max_cores}", slurm_param_str
                    )
            except IndexError:  # If `ntasks` not passed - 1 is default
                ntasks = 1
                slurm_param_str = f"{slurm_param_str} --ntasks={ntasks}"

        # Cap max nodes. Unlike above search for everything, if not present, add it.
        if self.max_nodes is not None:
            pattern = r"nodes=\S+"
            try:
                _ = re.findall(pattern, slurm_param_str)[0]
                # Check if present with above. Below does nothing but does not
                # throw error if pattern not present.
                slurm_param_str = re.sub(
                    pattern, f"nodes=0-{self.max_nodes}", slurm_param_str
                )
            except IndexError:  # `--nodes` not present
                slurm_param_str = f"{slurm_param_str} --nodes=0-{self.max_nodes}"

        # Force use of a specific partition
        if self.require_partition is not None:
            pattern = r"partition=\S+"
            try:
                _ = re.findall(pattern, slurm_param_str)[0]
                # Check if present with above. Below does nothing but does not
                # throw error if pattern not present.
                slurm_param_str = re.sub(
                    pattern, f"partition={self.require_partition}", slurm_param_str
                )
            except IndexError:  # --partition not present. This shouldn't happen
                slurm_param_str = (
                    f"{slurm_param_str} --partition={self.require_partition}"
                )

        return slurm_param_str

    def create_control_doc(
        self, context: Dict[str, Any]
    ) -> Dict[str, Union[str, Dict[str, str]]]:
        """Prepare the control document for job submission via the JID.

        Translates and Airflow dictionary to the representation needed by the
        JID.

        Args:
            context (Dict[str, Any]): Airflow dictionary object.
                https://airflow.apache.org/docs/apache-airflow/stable/templates-ref.html
                contains a list of available variables and their description.

        Returns:
            control_doc (Dict[str, Union[str, Dict[str, str]]]): JID job control
                dictionary.
        """

        dagrun_config: Dict[str, Union[str, Dict[str, Union[str, int, List[str]]]]] = (
            context.get("dag_run").conf
        )

        self.lute_location = dagrun_config.get(
            "lute_location", "/sdf/group/lcls/ds/tools/lute/latest"
        )
        lute_params: Dict[str, str] = dagrun_config.get("lute_params", {})

        config_path: str = lute_params["config_file"]
        # Note that task_id is from the parent class.
        # When defining the Operator instances the id is assumed to match a
        # managed task!
        lute_param_str: str
        if lute_params["debug"]:
            lute_param_str = (
                f"--taskname {self.lute_task_id} --config {config_path} --debug"
            )
        else:
            lute_param_str = f"--taskname {self.lute_task_id} --config {config_path}"

        if "is_daq2" in dagrun_config:
            if dagrun_config["is_daq2"]:
                lute_param_str = f"{lute_param_str} --psana2"

        kerb_file: Optional[str] = dagrun_config.get("kerb_file")
        if kerb_file is not None:
            lute_param_str = f"{lute_param_str} -K {kerb_file}"

        slurm_param_str: str
        if self.custom_slurm_params:  # SLURM params != ""
            slurm_param_str = self.custom_slurm_params
        else:
            # slurm_params holds a List[str]
            slurm_param_str = " ".join(dagrun_config.get("slurm_params"))

            # Make any requested SLURM argument substitutions
            slurm_param_str = self._sub_overridable_arguments(slurm_param_str)

        parameter_str: str = f"{lute_param_str} {slurm_param_str}"

        executable_subdir: str = dagrun_config.get(
            "executable_subdir", "launch_scripts"
        )

        jid_job_definition: Dict[str, str] = {
            "_id": str(uuid.uuid4()),
            "name": self.lute_task_id,
            "executable": f"{self.lute_location}/{executable_subdir}/submit_slurm",
            "trigger": "MANUAL",
            "location": dagrun_config.get("ARP_LOCATION", "S3DF"),
            "parameters": parameter_str,
            "run_as_user": self.user,
        }

        control_doc: Dict[str, Union[str, Dict[str, str]]] = {
            "_id": str(uuid.uuid4()),
            "arp_root_job_id": dagrun_config.get("ARP_ROOT_JOB_ID"),
            "experiment": dagrun_config.get("experiment"),
            "run_num": dagrun_config.get("run_id"),
            "user": dagrun_config.get("user"),
            "status": "",
            "tool_id": "",
            "def_id": str(uuid.uuid4()),
            "def": jid_job_definition,
        }

        return control_doc

    def parse_response(
        self, resp: requests.models.Response, check_for_error: List[str]
    ) -> Dict[str, Any]:
        """Parse a JID HTTP response.

        Args:
            resp (requests.models.Response): The response object from a JID
                HTTP request.
            check_for_error (List[str]): A list of strings/patterns to search
                for in response. Exception is raised if there are any matches.

        Returns:
            value (Dict[str, Any]): Dictionary containing HTTP response value.

        Raises:
            AirflowException: Raised to translate multiple errors into object
                properly handled by the Airflow server.
        """
        logger.debug(f"{resp.status_code}: {resp.content}")
        if resp.status_code not in (200,):
            raise AirflowException(f"Bad response from JID {resp}: {resp.content}")
        try:
            json: Dict[str, Union[str, int]] = resp.json()
            if json.get("success", "") not in (True,):
                raise AirflowException(f"Error from JID {resp}: {resp.content}")
            value: Dict[str, Any] = json.get("value")

            for pattern in check_for_error:
                if pattern in value:
                    raise AirflowException(
                        f"Response failed due to string match {pattern} against response {value}"
                    )
            return value
        except Exception as err:
            raise AirflowException(
                f"Response from JID not parseable, unknown error: {err}"
            )

    def rpc(
        self,
        endpoint: str,
        control_doc: Union[
            List[Dict[str, Union[str, Dict[str, str]]]],
            Dict[str, Union[str, Dict[str, str]]],
        ],
        context: Dict[str, Any],
        check_for_error: List[str] = [],
    ) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
        """Submit job via JID and retrieve responses.

        Remote Procedure Call (RPC).

        Args:
            endpoint (str): Which API endpoint to use.

            control_doc (Dict[str, Union[str, Dict[str, str]]]): Dictionary for
                JID call.

            context (Dict[str, Any]): Airflow dictionary object.
                https://airflow.apache.org/docs/apache-airflow/stable/templates-ref.html
                contains a list of available variables and their description.

            check_for_error (List[str]): A list of keywords to search for in a
                response to indicate error conditions. Default [].

        Returns:
            value (Dict[str, Any]): Dictionary containing HTTP response value.
        """
        # if not self.get_location(context) in self.locations:
        #     raise AirflowException(f"JID location {self.get_location(context)} is not configured")
        dagrun_config: Dict[str, Union[str, Dict[str, Union[str, int, List[str]]]]] = (
            context.get("dag_run").conf
        )
        experiment: str = dagrun_config.get("experiment")
        auth: Any = dagrun_config.get("Authorization")

        uri: str = f"{self.jid_api_location}/{self.jid_api_endpoints[endpoint]}"
        # Endpoints have the string "{experiment}" in them
        uri = uri.format(experiment=experiment)

        logger.debug(f"Calling {uri} with {control_doc}...")

        resp: requests.models.Response = requests.post(
            uri, json=control_doc, headers={"Authorization": auth}
        )
        logger.debug(f" + {resp.status_code}: {resp.content.decode('utf-8')}")

        value: Dict[str, Any] = self.parse_response(resp, check_for_error)

        return value

    def execute(self, context: Dict[str, Any]) -> None:
        """Method called by Airflow which submits SLURM Job via JID.

        Args:
            context (Dict[str, Any]): Airflow dictionary object.
                https://airflow.apache.org/docs/apache-airflow/stable/templates-ref.html
                contains a list of available variables and their description.
        """
        # logger.info(f"Attempting to run at {self.get_location(context)}...")
        logger.info("Attempting to run at S3DF.")
        control_doc = self.create_control_doc(context)
        logger.info(control_doc)
        logger.info(f"{self.jid_api_location}/{self.jid_api_endpoints['start_job']}")
        # start_job requires a dictionary
        msg: Dict[str, Any] = self.rpc(
            endpoint="start_job", control_doc=control_doc, context=context
        )
        logger.info(f"JobID {msg['tool_id']} successfully submitted!")

        jobs: List[Dict[str, Any]] = [msg]
        logger.info("Checking for job completion.")
        while jobs[0].get("status") in ("RUNNING", "SUBMITTED"):
            jobs = self.rpc(
                endpoint="job_statuses",
                control_doc=jobs,  # job_statuses requires a list
                context=context,
                check_for_error=[" error: ", "Traceback"],
            )
            time.sleep(self.poke_interval)

        # Logs out to xcom
        out = self.rpc("job_log_file", jobs[0], context)
        context["task_instance"].xcom_push(key="log", value=out)
        final_status: str = jobs[0].get("status")
        logger.info(f"Final status: {final_status}")
        if final_status in ("FAILED", "EXITED"):
            # Only DONE indicates success. EXITED may be cancelled or SLURM err
            logger.error(f"`Task` job marked as {final_status}!")
            sys.exit(-1)

        failure_messages: List[str] = [
            "INFO:lute.execution.executor:Task failed with return code:",
            "INFO:lute.execution.executor:Exiting after Task failure.",
        ]
        for msg in failure_messages:
            if msg in out:
                logger.error("Logs indicate `Task` failed!")
                sys.exit(-1)


class JIDPlugins(AirflowPlugin):
    name = "jid_plugins"
    operators = [JIDSlurmOperator]
