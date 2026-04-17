"""BayFAI Optimization Airflow Workflow.

Run BayFAI optimization after producing a powder image with SmallData.

Note:
    The task_id MUST match the managed task name when defining DAGs - it is used
    by the operator to properly launch it.

    dag_id names must be unique, and they are not namespaced via folder
    hierarchy. I.e. all DAGs on an Airflow instance must have unique ids. The
    Airflow instance used by LUTE is currently shared by other software - DAG
    IDs should always be prefixed with `lute_`. LUTE scripts should append this
    internally, so a DAG "lute_test" can be triggered by asking for "test"
"""

import os
from datetime import datetime
from typing import Any, Dict

from airflow import DAG
from airflow.decorators import task

from lute.operators.jidoperators import JIDSlurmOperator

dag_id: str = f"lute_{os.path.splitext(os.path.basename(__file__))[0]}"
description: str = "Optimize detector geometry given a produced powder image."

dag: DAG = DAG(
    dag_id=dag_id,
    start_date=datetime(2000, 4, 10),
    schedule_interval=None,
    description=description,
    is_paused_upon_creation=False,
)

@task.branch(task_id="Psana1v2Brancher")
def psana1v2_branch_func(**context) -> str:
    if "dag_run" in context:
        conf: Dict[str, Any] = context["dag_run"].conf
        if "is_daq2" in conf:
            if conf["is_daq2"]:
                return "SmallDataProducer2"
            elif conf["is_daq2"] is None:
                raise ValueError("Could not determine psana version: Unknown DAQ state")
    return "SmallDataProducer"

psana1v2_brancher = psana1v2_branch_func()

smd_producer: JIDSlurmOperator = JIDSlurmOperator(task_id="SmallDataProducer", dag=dag)

smd_producer2: JIDSlurmOperator = JIDSlurmOperator(task_id="SmallDataProducer2", dag=dag)

bayfai_optimizer: JIDSlurmOperator = JIDSlurmOperator(max_cores=120, task_id="BayFAIOptimizer", dag=dag)

bayfai_optimizer2: JIDSlurmOperator = JIDSlurmOperator(max_cores=120, task_id="BayFAIOptimizer2", dag=dag)

# Branch Workflow depending on available psana version
psana1v2_brancher >> [smd_producer, smd_producer2]

smd_producer >> bayfai_optimizer

smd_producer2 >> bayfai_optimizer2