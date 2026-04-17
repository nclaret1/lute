"""Parameter expansion utilities for parameter sweeps.

This module provides utilities for expanding parameter matrices into
individual parameter combinations and generating task instances.
"""

__all__ = ["expand_param_matrix"]
__author__ = "Gabriel Dorlhiac"

import itertools
from typing import Any, Dict, List


def expand_param_matrix(param_matrix: Dict[str, List[Any]]) -> List[Dict[str, Any]]:
    """Expand a parameter matrix into all combinations.

    Args:
        param_matrix (Dict[str, List[Any]]): Dictionary mapping parameter names
            to lists of values.

    Returns:
        param_combos (List[Dict[str, Any]]): List of dictionaries, each representing
            one parameter combination.

    Example:
        >>> param_matrix = {"num_cores": [1, 2, 4], "batch_size": [100, 500]}
        >>> param_combos = expand_param_matrix(param_matrix)
        >>> param_combos
        [{"num_cores": 1, "batch_size": 100},
         {"num_cores": 1, "batch_size": 500},
         {"num_cores": 2, "batch_size": 100},
         {"num_cores": 2, "batch_size": 500},
         {"num_cores": 4, "batch_size": 100},
         {"num_cores": 4, "batch_size": 500}]
    """
    if not param_matrix:
        return [{}]

    # Get parameter names and their value lists
    param_names = list(param_matrix.keys())
    param_value_lists = [param_matrix[name] for name in param_names]

    # Generate all combinations using itertools.product
    combinations = []
    for values in itertools.product(*param_value_lists):
        combination = dict(zip(param_names, values))
        combinations.append(combination)

    return combinations


def format_slurm_params(
    slurm_template: str,
    param_values: Dict[str, Any],
) -> str:
    """Format SLURM parameters with parameter values.

    Supports Python f-string style templating in SLURM parameters.

    Args:
        slurm_template: Template string with placeholders.
            Example: "--ntasks={num_cores} --mem={memory}GB"
        param_values: Dictionary of parameter values to substitute.

    Returns:
        param_str (str): Formatted SLURM parameter string.

    Example:
        >>> format_slurm_params("--ntasks={num_cores}",{"num_cores": 4})
        '--ntasks=4'
    """
    try:
        return slurm_template.format(**param_values)
    except KeyError as e:
        # Parameter referenced in template but not in param_values
        raise ValueError(
            f"SLURM template references parameter {e} which is not in param_matrix"
        )


def generate_task_names(
    base_task_name: str,
    num_combinations: int,
) -> List[str]:
    """Generate unique task names for parameter set instances.

    Args:
        base_task_name (str): Base name of the task.

        num_combinations (int): Number of parameter combinations.

    Returns:
        expanded_task_names (List[int]): List of Task names with indices appended.
    """
    return [f"{base_task_name}_{idx}" for idx in range(num_combinations)]


def validate_param_matrix(param_matrix: Dict[str, Any]) -> None:
    """Validate that a parameter matrix is well-formed.

    Args:
        param_matrix (Dict[str, Any]): The parameter matrix to validate.

    Raises:
        ValueError: If the parameter matrix is invalid. E.g. no dictionary provided
            or lists aren't given for one or more parameters in the dictionary.
    """
    if not isinstance(param_matrix, dict):
        raise ValueError(f"param_matrix must be a dictionary, got {type(param_matrix)}")

    for param_name, values in param_matrix.items():
        if not isinstance(values, list):
            raise ValueError(
                f"Parameter '{param_name}' values must be a list, got {type(values)}"
            )
        if not values:
            raise ValueError(f"Parameter '{param_name}' has an empty value list")


def get_sweep_metadata(
    param_combinations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Generate metadata about a parameter sweep (to log info).

    Args:
        param_combinations (List[Dict[str, Any]]): List of parameter combinations.

    Returns:
        metadata (Dict[str, Any]): Dictionary with generated parameter metadata.
    """
    if not param_combinations:
        return {
            "num_combinations": 0,
            "parameters": [],
            "total_tasks": 0,
        }

    param_names: List[str] = list(param_combinations[0].keys())

    return {
        "num_combinations": len(param_combinations),
        "parameters": param_names,
        "total_tasks": len(param_combinations),
        "example_combination": param_combinations[0] if param_combinations else {},
    }
