"""Workflow parsing utilities"""

__all__ = ["load_lute_dag"]
__author__ = "Gabriel Dorlhiac"

import collections
import logging
import os
import warnings
from typing import Any, Dict, List, Optional, Tuple, Type, Union, cast

import yaml

from maestro._maestro._maestro import JobParameters, JobStep, TriggerRule
from maestro.config_generator import expand_config_for_sweep
from maestro.parser_params import (
    expand_param_matrix,
    format_slurm_params,
    validate_param_matrix,
    get_sweep_metadata,
)

if __debug__:
    warnings.simplefilter("default")
    os.environ["PYTHONWARNINGS"] = "default"
    logging.basicConfig(level=logging.DEBUG)
    logging.captureWarnings(True)
else:
    warnings.simplefilter("ignore")
    logging.basicConfig(level=logging.INFO)

logger: logging.Logger = logging.Logger(__name__)


class DagParseError(Exception): ...


RULES_STR_MAP: Dict[str, TriggerRule] = {
    "ALL_SUCCESS": TriggerRule.ALL_SUCCESS,
    "ANY_SUCESS": TriggerRule.ANY_SUCCESS,
    "ALL_COMPLETED": TriggerRule.ALL_COMPLETED,
    "ALL_FAILED": TriggerRule.ALL_FAILED,
    "ANY_FAILED": TriggerRule.ANY_FAILED,
    "ALWAYS": TriggerRule.ALWAYS,
}


def job_step_representer(
    dumper: yaml.SafeDumper, job_step: JobStep
) -> yaml.nodes.MappingNode:
    job_step_dict = {
        "task_name": job_step.managed_task_name,
        "slurm_params": job_step.extra_parameters,
        "next": job_step.next,
    }
    return dumper.represent_mapping(JobStep, job_step_dict)


def get_trigger_rule(
    node: yaml.nodes.MappingNode,
) -> Tuple[yaml.nodes.MappingNode, Optional[TriggerRule]]:
    rule: Optional[TriggerRule] = None
    if node.tag[1:] in RULES_STR_MAP.keys():
        rule = RULES_STR_MAP[node.tag[1:]]
    return node, rule


def get_branch(
    loader: yaml.SafeLoader,
    node: yaml.nodes.MappingNode,
    branch_conditions: Dict[str, bool],
) -> yaml.nodes.MappingNode:
    if "!branch" in node.tag:
        branch_key: str = node.tag[8:]
        if branch_key in branch_conditions.keys():
            sub_node: Tuple[yaml.nodes.ScalarNode, yaml.nodes.MappingNode]
            if len(node.value) > 2:
                # The current branching mechanism only supports a single conditional
                # branch. For each (key, condition) in `branch_conditions`:
                #    - Take `key` path if `condition` is True
                #    - Take other path if `condition` is False
                # If we have more than 1 key remaining it means there were more
                # branches provided (or potentially the parsing logic is wrong)
                # Warn in this case, but we'll still select one of the other branches
                warnings.warn(
                    message=(
                        "Only binary branching is currently supported but "
                        f"{len(node.value)} branches were encountered.\n"
                        "The DAG definition should be checked."
                    ),
                    category=RuntimeWarning,
                )
            # Keep track of which branch corresponds to the True condition.
            # E.g. if the tag is !branch_daq2, look for the daq2 branch.
            key_true_sub_node_idx: int = -1
            # This is the index of the chosen branch. The current node will
            # be replaced with the one at this index.
            chosen_sub_node_idx: int = -1
            for idx, sub_node in enumerate(node.value):
                name: str = cast(str, loader.construct_scalar(sub_node[0]))
                if name == branch_key:
                    key_true_sub_node_idx = idx
                    if branch_conditions[branch_key]:
                        chosen_sub_node_idx = idx
                else:
                    if not branch_conditions[branch_key]:
                        chosen_sub_node_idx = idx
            if key_true_sub_node_idx < 0:
                warnings.warn(
                    f"For {node.tag} branching, we never encountered a {branch_key} "
                    "branch. Selected branch may be incorrect. Check the DAG definition.",
                    category=RuntimeWarning,
                )
            if chosen_sub_node_idx < 0:
                warnings.warn(
                    "Unable to correctly determine the branch to take. Selecting the "
                    "first one. The DAG definition should be checked. This may also "
                    "be a bug.",
                    category=RuntimeWarning,
                )
                node.value = node.value[0][1].value
            else:
                node.value = node.value[chosen_sub_node_idx][1].value
    return node


def get_lute_dag_loader(
    gen_params: JobParameters,
    default_trigger_rule: TriggerRule = TriggerRule.ALL_SUCCESS,
    default_slurm_params: str = "",
    branch_conditions: Dict[str, bool] = {"daq2": True},
) -> Type[yaml.SafeLoader]:
    """Create a loader that handles the special tags used by LUTE's YAML files.

    Args:
        gen_params (JobParameters): A JobParameter object that contains the LUTE
            path, among other general configuration. This is appened to each JobStep.

        default_trigger_rule (TriggerRule): The default TriggerRule to apply to a
            JobStep if None has been specified.

        default_slurm_params (str): The default SLURM parameters that will be applied
            to all JobSteps if they don't define any themselves.

        branch_conditions (Dict[str, bool]): A mapping of branch keys to the
            evaluated conditions. E.g. `daq2` : True means a `!branch_daq2`
            tag will be evaluated, and that the `daq2` branch under that tag
            will be taken. The mapping should be constructed from run time conditions.
            E.g. if it is determined that this is not an LCLS2 experiment, the
            dictionary should look like {"daq2": False} by the time it reaches this
            function.

    Returns:
        loader (Type[yaml.SafeLoader]): A loader class type that has had the extra
            constructors added to it to handle the special tags.
    """

    loader: Type[yaml.SafeLoader] = yaml.SafeLoader

    def dag_constructor(
        loader: yaml.SafeLoader,
        node: Union[yaml.nodes.MappingNode, yaml.nodes.SequenceNode],
    ) -> List[JobStep]:
        """Constructor to recursively build JobStep objects under a !LUTE_DAG tag."""
        job_step_dicts: List[
            Tuple[
                Union[Dict[collections.abc.Hashable, Any], JobStep],
                TriggerRule,
            ]
        ]
        job_step_dict: Union[Dict[collections.abc.Hashable, Any], JobStep] = {}
        if isinstance(node, yaml.nodes.SequenceNode):
            job_step_dicts = []
            sub_node: yaml.nodes.MappingNode
            for sub_node in node.value:
                # Let YAML dispatcher handle tags like !param_sweep
                # loader.construct_object will call the appropriate constructor
                if sub_node.tag == "!param_sweep":
                    constructed_obj = loader.construct_object(sub_node, deep=False)
                    if isinstance(constructed_obj, list):
                        for step in constructed_obj:
                            if isinstance(step, JobStep):
                                job_step_dicts.append((step, step.trigger_rule))
                            else:
                                # Shouldn't happen, but handle gracefully
                                job_step_dicts.append((step, default_trigger_rule))
                    else:
                        # Single JobStep or dict, call dag_constructor to process it
                        returned_job_steps: List[JobStep] = dag_constructor(
                            loader, sub_node
                        )
                        for step in returned_job_steps:
                            job_step_dicts.append((step, step.trigger_rule))
                else:
                    returned_job_steps = dag_constructor(loader, sub_node)
                    for step in returned_job_steps:
                        job_step_dicts.append((step, step.trigger_rule))
        elif isinstance(node, yaml.nodes.MappingNode):
            node = get_branch(
                loader=loader, node=node, branch_conditions=branch_conditions
            )
            rule: Optional[TriggerRule] = None
            node, rule = get_trigger_rule(node)
            sub_node_desc: Tuple[
                yaml.nodes.ScalarNode,
                Union[yaml.nodes.ScalarNode, yaml.nodes.SequenceNode],
            ]
            for sub_node_desc in node.value:
                key_name: str = cast(str, loader.construct_scalar(sub_node_desc[0]))
                val_node: Union[yaml.nodes.ScalarNode, yaml.nodes.SequenceNode] = (
                    sub_node_desc[1]
                )
                val: Union[str, List[JobStep]]
                if isinstance(val_node, yaml.nodes.ScalarNode):
                    val = cast(str, loader.construct_scalar(val_node))
                else:
                    val = dag_constructor(loader, val_node)
                job_step_dict[key_name] = val
            job_step_dicts = [(job_step_dict, rule)]
        else:
            raise RuntimeError(f"Unsupported node type for LUTE DAG: {type(node)}")

        job_steps: List[JobStep] = []
        task_name: str
        slurm_params: str
        next_job_steps: List[JobStep]
        trig_rule: TriggerRule
        for job_step_dict, trig_rule in job_step_dicts:
            # This is clunky... and someone should ideally spend time to make it better
            # But depending on the route through the conditions (how many lists) you
            # may have a list[dict] or a list[JobStep] by the time you get here...
            # So we just ignore that this is not good and append the job steps.
            if isinstance(job_step_dict, dict):
                task_name = job_step_dict.get("task_name")
                slurm_params = job_step_dict.get("slurm_params")
                if not slurm_params:
                    slurm_params = default_slurm_params
                next_job_steps = job_step_dict.get("next")
                if trig_rule is None:
                    trig_rule = default_trigger_rule
                job_steps.append(
                    JobStep(
                        task_name,
                        trig_rule,
                        gen_params,
                        slurm_params,
                        next_job_steps,
                    )
                )
            else:
                job_steps.append(job_step_dict)
        return job_steps

    def param_sweep_constructor(
        loader: yaml.SafeLoader,
        node: yaml.nodes.MappingNode,
    ) -> List[JobStep]:
        """Constructor for !param_sweep tag - expands parameter matrices."""
        # Parse the node into a dictionary
        sweep_dict: Dict[str, Any] = {}
        for key_node, val_node in node.value:
            key = loader.construct_scalar(key_node)
            if key == "next":
                # Recursively construct next steps
                if isinstance(val_node, yaml.nodes.SequenceNode):
                    sweep_dict[key] = dag_constructor(loader, val_node)
                else:
                    sweep_dict[key] = []
            elif key == "param_matrix":
                # Construct param_matrix as a dictionary
                sweep_dict[key] = loader.construct_mapping(val_node, deep=True)
            else:
                # Simple scalar
                sweep_dict[key] = loader.construct_scalar(val_node)

        # Extract required fields
        managed_task_name: str = sweep_dict.get("task_name", "")
        param_matrix: Dict[str, Any] = sweep_dict.get("param_matrix", {})
        slurm_template: str = sweep_dict.get("slurm_params", "")
        next_steps: List[Dict[str, Any]] = sweep_dict.get("next", [])

        if not managed_task_name:
            raise DagParseError("!param_sweep requires 'task_name' field")

        # Resolve true Task name from managed Task name
        task_name: str = managed_task_name
        try:
            # Non-ideal doing this... but don't have other access to underlying
            # name...
            from lute import managed_tasks
            from lute.execution.executor import BaseExecutor

            if hasattr(managed_tasks, managed_task_name):
                executor: BaseExecutor = getattr(managed_tasks, managed_task_name)
                # Executor.task_name has the actual Task name
                if hasattr(executor, "task_name"):
                    task_name = executor.task_name
                    logger.info(
                        f"Resolved '{managed_task_name}' as running Task '{task_name}'",
                    )
        except ImportError:
            # If we can't import managed_tasks, use managed_task_name as fallback
            logger.warn(
                "Could not import managed_tasks to determine Task name. "
                f"Using '{managed_task_name}' as-is...",
            )

        # Validate and expand parameter matrix
        validate_param_matrix(param_matrix)
        param_combinations: List[Dict[str, Any]] = expand_param_matrix(param_matrix)

        metadata: Dict[str, Any] = get_sweep_metadata(param_combinations)
        logger.debug(
            f"Parameter sweep for '{managed_task_name}': "
            f"Generating {metadata['total_tasks']} task instances with "
            f"parameters {metadata['parameters']}",
        )

        # Expand config file with all parameter combinations
        original_config: str = gen_params.config_file
        expanded_config_dir: str = os.path.dirname(original_config)
        expanded_config_path: str = expand_config_for_sweep(
            config_path=original_config,
            task_name=task_name,  # Use Task name, not managed Task name!
            param_combinations=param_combinations,
            output_dir=expanded_config_dir,
        )

        # Create a new JobParameters with the expanded config
        expanded_gen_params: JobParameters = JobParameters(
            gen_params.lute_location,
            gen_params.executable_subdir,
            expanded_config_path,
            gen_params.debug,
        )

        # Generate JobStep for each parameter combination
        job_steps: List[JobStep] = []
        for idx, params in enumerate(param_combinations):
            expanded_task_name: str = f"{managed_task_name}_{idx}"

            # Format SLURM params with parameter values
            slurm_params: str
            if slurm_template:
                try:
                    slurm_params = format_slurm_params(slurm_template, params)
                except ValueError:
                    # If formatting fails, use template as-is
                    slurm_params = slurm_template
            else:
                slurm_params: str = default_slurm_params

            # Create JobStep with expanded config
            job_step: JobStep = JobStep(
                expanded_task_name,
                default_trigger_rule,
                expanded_gen_params,
                slurm_params,
                next_steps,  # All instances share same next steps
            )
            job_steps.append(job_step)

        return job_steps

    # Add the full DAG constructor which builds JobStep objects
    loader.add_constructor("!LUTE_DAG", dag_constructor)
    loader.add_constructor("!param_sweep", param_sweep_constructor)

    return loader


def get_lute_dag_dumper() -> Type[yaml.SafeDumper]:
    dumper: Type[yaml.SafeDumper] = yaml.SafeDumper
    dumper.add_representer(JobStep, job_step_representer)
    return yaml.SafeDumper


def load_lute_dag(
    workflow_path: str,
    lute_location: str,
    executable_subdir: str,
    config_file: str,
    debug: bool,
    default_slurm_params: str = "",
    branch_conditions: Dict[str, bool] = {"daq2": True},
) -> List[JobStep]:
    """Load a LUTE DAG from a YAML file with support for LUTE's custom tags.

    Args:
        workflow_path (str): The path to the workflow to load.

        lute_location (str): The path of the LUTE installation to use.

        executable_subdir (str): The sub-directory to use for the executables.

        config_file (str): The path to the config file to use.

        debug (bool): Whether to run in debug mode.

        default_slurm_params (str): The default SLURM parameters that will be applied
            to all JobSteps if they don't define any themselves.

        branch_conditions (Dict[str, bool]): A mapping of branch keys to the
            evaluated conditions. E.g. `daq2` : True means a `!branch_daq2`
            tag will be evaluated, and that the `daq2` branch under that tag
            will be taken. The mapping should be constructed from run time conditions.
            E.g. if it is determined that this is not an LCLS2 experiment, the
            dictionary should look like {"daq2": False} by the time it reaches this
            function.

    Returns:
        wf_defn (List[JobStep]): The parsed workflow to be passed to the workflow
            manager.

    Raises:
        DagParseError: Raised if anything goes wrong in parsing. The most common
            issue would be leaving off required LUTE tags. All workflows must
            begin with a `!LUTE_DAG` tag on the top so the parser knows to create
            the appropriate objects.
    """

    gen_params: JobParameters = JobParameters(
        lute_location, executable_subdir, config_file, debug
    )

    loader: Type[yaml.SafeLoader] = get_lute_dag_loader(
        gen_params=gen_params,
        default_trigger_rule=TriggerRule.ALL_SUCCESS,
        default_slurm_params=default_slurm_params,
        branch_conditions=branch_conditions,
    )

    # The workflow passed may not contain the special keys, so we will check
    # afterwards because the parsing will have been incorrect
    wf_defn: Union[List[JobStep], List[Any], Dict[collections.abc.Hashable, Any]]
    with open(workflow_path, "r") as wf_fp:
        wf_defn = yaml.load(stream=wf_fp, Loader=loader)

    if isinstance(wf_defn, list) and isinstance(wf_defn[0], JobStep):
        # Good - JobSteps were parsed correctly
        return wf_defn
    else:
        raise DagParseError(
            "Invalid LUTE definition. The YAML was properly formatted, however "
            "it did not parse to Maestro JobStep objects. Most likely a tag was "
            "forgotten. The very first line of the workflow must look like:\n"
            "!LUTE_DAG\n"
            "...rest of workflow definition..."
        )


def load_lute_dag_str(
    workflow_str: str,
    lute_location: str,
    executable_subdir: str,
    config_file: str,
    debug: bool,
    default_slurm_params: str = "",
    branch_conditions: Dict[str, bool] = {"daq2": True},
) -> List[JobStep]:
    """As `lute_lute_dag` but takes a string for the workflow instead of a path."""
    gen_params: JobParameters = JobParameters(
        lute_location, executable_subdir, config_file, debug
    )

    loader: Type[yaml.SafeLoader] = get_lute_dag_loader(
        gen_params=gen_params,
        default_trigger_rule=TriggerRule.ALL_SUCCESS,
        default_slurm_params=default_slurm_params,
        branch_conditions=branch_conditions,
    )

    # The workflow passed may not contain the special keys, so we will check
    # afterwards because the parsing will have been incorrect
    wf_defn: Union[List[JobStep], List[Any], Dict[collections.abc.Hashable, Any]]
    wf_defn = yaml.load(workflow_str, Loader=loader)

    if isinstance(wf_defn, list) and isinstance(wf_defn[0], JobStep):
        # Good - JobSteps were parsed correctly
        return wf_defn
    else:
        raise DagParseError(
            "Invalid LUTE definition. The YAML was properly formatted, however "
            "it did not parse to Maestro JobStep objects. Most likely a tag was "
            "forgotten. The very first line of the workflow must look like:\n"
            "!LUTE_DAG\n"
            "...rest of workflow definition..."
        )
