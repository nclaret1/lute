"""Configuration file expansion for parameter sweeps.

This module handles expanding a base config.yaml with parameter-specific
sections for each task instance created by parameter sweeps.
"""

__all__ = ["expand_config_for_sweep", "load_base_config", "write_expanded_config"]
__author__ = "Gabriel Dorlhiac"

import os
import tempfile
import uuid
from typing import Any, Dict, List, Optional

import yaml


def load_base_config(config_path: str) -> List[Dict[str, Any]]:
    """Load the base configuration YAML file.

    This is the config.yaml provided by the user running the workflow.

    Args:
        config_path: Path to the base config.yaml file.

    Returns:
        docs (List[Dict[str, Any]]): List of AnalysisHeader and TaskParameters
            YAML documents.
    """
    with open(config_path, "r") as f:
        # Load all YAML documents - AnalysisHeader then the TaskParameters
        configs: List[Dict[str, Any]] = list(yaml.safe_load_all(f))

    return configs


def expand_task_config(
    task_name: str,
    base_config: Dict[str, Any],
    param_combinations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Create configuration sections for each parameter combination.

    Args:
        task_name: The base task name (e.g., "Test"). This is the true Task name
            NOT the managed Task name (Executor+Task in lute.managed_tasks)

        base_config: The base configuration for this task (if it exists). Some Tasks
            may not have entries in the config.yaml since they have defaults for all
            parameters.

        param_combinations: List of dictionaries, each containing parameter
            values for one combination.

    Returns:
        configs (Dict[str, Any]): Dictionary providing parameter sets for each
            (expanded) Task name (i.e. containing a suffix, like Test_0).
    """
    expanded_configs = {}

    for idx, params in enumerate(param_combinations):
        expanded_task_name: str = f"{task_name}_{idx}"

        # Start with base config if it exists
        task_config: Dict[str, Any] = {}
        if base_config:
            task_config = base_config.copy()

        # Merge in the parameter-specific values
        task_config.update(params)
        expanded_configs[expanded_task_name] = task_config

    return expanded_configs


def merge_into_existing_config(
    base_configs: List[Dict[str, Any]],
    expanded_sections: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Merge expanded Task configurations into the existing config structure.

    Args:
        base_configs (List[Dict[str, Any]]): Config dictionaries from the starting
            config.yaml file.

        expanded_configs (Dict[str, Any]): Dictionaries for the swept Task's that
            have suffixed names.

    Returns:
        updated_configs (List[Dict[str, Any]]): Updated config documents with
            the expanded parameter sets included.
    """
    # At least currently, final document is the TaskParameters
    # ... shouldn't be more than 2
    if len(base_configs) > 1:
        params_doc = base_configs[-1]
        params_doc.update(expanded_sections)
    elif len(base_configs) == 1:
        # Shouldn't happen... But I guess case for it...
        base_configs[0].update(expanded_sections)
    else:
        # Empty config? Create a new one
        base_configs = [expanded_sections]

    return base_configs


def write_expanded_config(
    expanded_configs: List[Dict[str, Any]],
    output_dir: Optional[str] = None,
) -> str:
    """Write the expanded configuration to a file.

    If no output directory is provided, a temporary file will be created instead.

    Args:
        expanded_configs: List of config documents to write.

        output_dir: Optional directory to write to. If None, creates a temp file.
            The full filename/path is generated randomly.

    Returns:
        output_path (str): Path to the written config file.
    """
    filename_prefix: str = f"config_expanded_{uuid.uuid4().hex[:8]}"
    filename_suffix: str = ".yaml"
    output_path: str
    if output_dir is None:
        # Create a temporary file
        fd: int
        fd, output_path = tempfile.mkstemp(
            prefix=filename_prefix,
            suffix=filename_suffix,
            dir="/tmp",
        )
        os.close(fd)  # Close the file descriptor, we'll write with yaml
    else:
        output_path = f"{output_dir}/{filename_prefix}{filename_suffix}"

    with open(output_path, "w") as f:
        # Write all documents
        # NOTE: flow_style is whether to write out as param: {key: val, key2: val2}...
        #       setting to False means it is written as a block like
        #       param:
        #         key: val
        yaml.dump_all(expanded_configs, f, default_flow_style=False)

    return output_path


def expand_config_for_sweep(
    config_path: str,
    task_name: str,
    param_combinations: List[Dict[str, Any]],
    output_dir: Optional[str] = None,
) -> str:
    """For a given Task name, add parameter combos into config YAML.

    Read the config YAML, extracting any current parameters, create a new set
    of dictionaries based on these parameters, and then add in the parameter
    combinations provided. A new expanded config YAML will be written out, with
    a series of suffixed Task names indicating the various sets of parameter
    combinations.

    Args:
        config_path (str): Path to the original config.yaml.

        task_name (str): Task name to have parameter combos generated for.

        param_combinations (List[Dict[str, Any]]): List of parameter dictionaries.
            One dictionary for each parameter set.

        output_dir: Optional output directory. If None, the generated file will
            be written to temp.

    Returns:
        output_path (str): Path to the expanded config file.
    """
    # Load base config - have two docs AnalysisHeader followed by TaskParameters
    base_configs: List[Dict[str, Any]] = load_base_config(config_path)

    # Find the base task config if it exists
    base_task_config: Dict[str, Any] = {}
    if len(base_configs) > 1:
        # At least currently, final document is the TaskParameters
        # ... shouldn't be more than 2
        params_doc = base_configs[-1]
        base_task_config = params_doc.get(task_name, {})
    elif len(base_configs) == 1:
        # Shouldn't happen... But I guess case for it...
        base_task_config = base_configs[0].get(task_name, {})

    # Expand the Task config for all parameter combinations
    expanded_sections: Dict[str, Any] = expand_task_config(
        task_name, base_task_config, param_combinations
    )

    # Merge into existing config
    updated_configs: List[Dict[str, Any]] = merge_into_existing_config(
        base_configs, expanded_sections
    )

    # Write to file and return the output path
    return write_expanded_config(updated_configs, output_dir)
