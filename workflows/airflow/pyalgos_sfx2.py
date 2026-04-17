# File: /.../workflows/airflow/pyalgos_sfx2.py

from datetime import datetime
import os
from airflow import DAG
from lute.operators.jidoperators import JIDSlurmOperator

dag_id: str = f"lute_{os.path.splitext(os.path.basename(__file__))[0]}"
description: str = (
    "Run SFX processing using PyAlgos peak finding."
)

dag: DAG = DAG(
    dag_id=dag_id,
    start_date=datetime(2025, 10, 15),
    schedule_interval=None,
    description=description,
)

# task_id MUST match the keys in your pyalgos_sfx2.yaml file
peak_finder: JIDSlurmOperator = JIDSlurmOperator(task_id="FindPeaksPyAlgos", dag=dag)
indexer: JIDSlurmOperator = JIDSlurmOperator(task_id="IndexCrystFEL", dag=dag)
merger: JIDSlurmOperator = JIDSlurmOperator(task_id="MergePartialator", dag=dag)

# Define the workflow order
peak_finder >> indexer 