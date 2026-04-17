USE_PYDANTIC_MODELS: bool = True

exec_script_template: str = """
import pickle
import sys
from typing import Any, Dict, Optional, Type

# Need to set this before importing anything else
import lute.execution.subprocess_utils
lute.execution.subprocess_utils.USE_PYDANTIC_MODELS = False

from lute.io.db import get_task_parameters_defn_and_params
from lute.io.parameters import construct_task_parameters, RowIds, TaskParameters
from lute.tasks.task import Task

from lute.tasks import import_task, TaskNotFoundError

try:
    TaskType: Type[Task] = import_task("{task_name}")
except TaskNotFoundError:
    logger.debug(
        (
            "Task {task_name} not found! Things to double check:"
            "\t - The spelling of the Task name."
            "\t - Has the Task been registered in lute.tasks.import_task."
        )
    )
    sys.exit(-1)

row_ids: RowIds = {row_ids}

definition: Dict[str, Any]
param_values: Dict[str, Any]
definition, param_values = get_task_parameters_defn_and_params(db_dir="{work_dir}", row_ids=row_ids)
new_params: TaskParameters = construct_task_parameters(schema=definition, values=param_values)
new_params.lute_config.work_dir = "{work_dir}"
task: Task = TaskType(params=new_params, row_ids=row_ids)
task.run()
"""
