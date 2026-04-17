bash_scripts = [
    'submit_launch_airflow.sh',
    'submit_launch_prefect.sh',
    'submit_launch_slurm.sh',
    # 'submit_slurm.sh',
]

install_data(
    bash_scripts,
    install_dir: get_option('bindir'),
)

py_sources = [
    '__init__.py',
    'launch_airflow.py',
    'launch_prefect.py',
    'launch_slurm_v1.py',
    'submit_slurm.py',
]

py3.install_sources(
    py_sources,
    pure: false,
    subdir: 'launch_scripts',
)

