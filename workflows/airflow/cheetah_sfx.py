"""Run SFX processing using Cheetah.

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

from airflow import DAG

from lute.operators.jidoperators import JIDSlurmOperator

dag_id: str = f"lute_{os.path.splitext(os.path.basename(__file__))[0]}"
description: str = "Run Cheetah SFX."

dag: DAG = DAG(
    dag_id=dag_id,
    start_date=datetime(2000, 4, 10),
    schedule_interval=None,
    description=description,
    is_paused_upon_creation=False,
)

peak_finder: JIDSlurmOperator = JIDSlurmOperator(
    task_id="CheetahRunner", dag=dag, trigger_rule="none_failed"
)

indexer: JIDSlurmOperator = JIDSlurmOperator(
    max_cores=120,
    max_nodes=1,
    task_id="CrystFELIndexer",
    dag=dag,
    trigger_rule="none_failed",
)

concatenator: JIDSlurmOperator = JIDSlurmOperator(
    max_cores=2, task_id="StreamFileConcatenator", dag=dag, trigger_rule="none_failed"
)

# Merge
merger: JIDSlurmOperator = JIDSlurmOperator(
    max_cores=120,
    max_nodes=1,
    task_id="PartialatorMerger",
    dag=dag,
    trigger_rule="none_failed",
)

# Figures of merit
hkl_comparer: JIDSlurmOperator = JIDSlurmOperator(
    max_cores=8, max_nodes=1, task_id="HKLComparer", dag=dag, trigger_rule="none_failed"
)

# HKL conversions
hkl_manipulator: JIDSlurmOperator = JIDSlurmOperator(
    max_cores=8,
    max_nodes=1,
    task_id="HKLManipulator",
    dag=dag,
    trigger_rule="none_failed",
)

# CCP4
dimple_runner: JIDSlurmOperator = JIDSlurmOperator(task_id="DimpleSolver", dag=dag)

# Branch Workflow depending on available psana version
peak_finder >> indexer >> concatenator >> merger >> hkl_manipulator >> dimple_runner
merger >> hkl_comparer
