from typing import List

from maestro._maestro._maestro import JobParameters, JobStep, TriggerRule
from maestro.parser import load_lute_dag_str

SIMPLE_DAG: str = """
!LUTE_DAG
task_name: "Tester"
slurm_params: ""
next:
- task_name: "SocketTester"
  slurm_params: ""
  next: []
"""

SIMPLE_PARALLEL_DAG: str = """
!LUTE_DAG
- task_name: "Tester"
  slurm_params: ""
  next:
  - task_name: "SocketTester"
    slurm_params: ""
    next: []
- task_name: "Tester"
  slurm_params: ""
  next:
  - task_name: "SocketTester"
    slurm_params: ""
    next: []
"""


SIMPLE_TRIGGER_DAG: str = """
!LUTE_DAG
- !ALWAYS
  task_name: "Tester"
  slurm_params: ""
  next:
  - !ALL_FAILED
    task_name: "BinaryErrTester"
    slurm_params: ""
    next: []
  - task_name: "SocketTester"
    slurm_params: ""
    next: []
  - !ALWAYS
    task_name: "WriteTester"
    slurm_params: ""
    next:
    - task_name: "ReadTester"
      slurm_params: ""
      next: []
"""


SIMPLE_BRANCH_DAG: str = """
!LUTE_DAG
- task_name: "Tester"
  slurm_params: ""
  next:
  - !ALL_FAILED
    task_name: "SocketTester"
    slurm_params: ""
    next: []
- task_name: "Tester"
  slurm_params: ""
  next:
  - !branch_daq2
    daq2:
      task_name: "SocketTester"
      slurm_params: ""
      next: []
    daq1:
      task_name: "WriteTester"
      slurm_params: ""
      next:
      - task_name: "ReadTester"
        slurm_params: ""
        next: []
"""

PARAM_GENERATION_DAG = f"""
!LUTE_DAG
task_name: "Tester"
next:
- !param_sweep
  task_name: SocketTester
  param_matrix:
    num_arrays: [5, 10, 15]
  next: []
"""


def print_wf(wf: List[JobStep]):
    for job in wf:
        print(f"{job.managed_task_name} triggers on {job.trigger_rule}")
        print(f"\t{job.managed_task_name} uses {job.parameters.config_file}")
        print_wf(job.next)


def is_equal(steps_0: List[JobStep], steps_1: List[JobStep]) -> bool:
    if len(steps_0) != len(steps_1):
        return False
    else:
        all_equal: bool = True
        for i in range(len(steps_0)):
            if steps_0[i].managed_task_name != steps_1[i].managed_task_name:
                all_equal = False
                break
            if steps_0[i].trigger_rule != steps_1[i].trigger_rule:
                all_equal = False
                break
            if (
                steps_0[i].parameters.lute_location
                != steps_1[i].parameters.lute_location
            ):
                all_equal = False
                break
            if steps_0[i].parameters.config_file != steps_1[i].parameters.config_file:
                all_equal = False
                break
            if steps_0[i].parameters.debug != steps_1[i].parameters.debug:
                all_equal = False
                break
            if steps_0[i].extra_parameters != steps_1[i].extra_parameters:
                all_equal = False
                break
            all_equal = is_equal(steps_0[i].next, steps_1[i].next)
        return all_equal


class TestParsing:
    # These values don't matter since we are only testing
    lute_location: str = "/path/to/lute"
    executable_subdir: str = ""  # Not running anything so irrelevant
    config_file: str = "/path/to/config"
    debug: bool = True
    gen_params: JobParameters = JobParameters(
        lute_location, executable_subdir, config_file, debug
    )

    def test_simple(self):
        job_steps: List[JobStep] = [
            JobStep(
                "Tester",
                TriggerRule.ALL_SUCCESS,
                TestParsing.gen_params,
                "",
                [
                    JobStep(
                        "SocketTester",
                        TriggerRule.ALL_SUCCESS,
                        TestParsing.gen_params,
                        "",
                        [],
                    )
                ],
            )
        ]
        wf_defn: List[JobStep] = load_lute_dag_str(
            workflow_str=SIMPLE_DAG,
            lute_location=TestParsing.lute_location,
            executable_subdir=TestParsing.executable_subdir,  # Not running anything so irrelevant
            config_file=TestParsing.config_file,
            debug=TestParsing.debug,
            branch_conditions={"daq2": True},
        )
        assert is_equal(wf_defn, job_steps)  # wf_defn == job_steps

    def test_parallel(self):
        job_steps: List[JobStep] = [
            JobStep(
                "Tester",
                TriggerRule.ALL_SUCCESS,
                TestParsing.gen_params,
                "",
                [
                    JobStep(
                        "SocketTester",
                        TriggerRule.ALL_SUCCESS,
                        TestParsing.gen_params,
                        "",
                        [],
                    )
                ],
            ),
            JobStep(
                "Tester",
                TriggerRule.ALL_SUCCESS,
                TestParsing.gen_params,
                "",
                [
                    JobStep(
                        "SocketTester",
                        TriggerRule.ALL_SUCCESS,
                        TestParsing.gen_params,
                        "",
                        [],
                    )
                ],
            ),
        ]
        wf_defn: List[JobStep] = load_lute_dag_str(
            workflow_str=SIMPLE_PARALLEL_DAG,
            lute_location=TestParsing.lute_location,
            executable_subdir=TestParsing.executable_subdir,  # Not running anything so irrelevant
            config_file=TestParsing.config_file,
            debug=TestParsing.debug,
            branch_conditions={"daq2": True},
        )
        assert is_equal(wf_defn, job_steps)

    def test_trigger(self):
        job_steps: List[JobStep] = [
            JobStep(
                "Tester",
                TriggerRule.ALWAYS,
                TestParsing.gen_params,
                "",
                [
                    JobStep(
                        "BinaryErrTester",
                        TriggerRule.ALL_FAILED,
                        TestParsing.gen_params,
                        "",
                        [],
                    ),
                    JobStep(
                        "SocketTester",
                        TriggerRule.ALL_SUCCESS,
                        TestParsing.gen_params,
                        "",
                        [],
                    ),
                    JobStep(
                        "WriteTester",
                        TriggerRule.ALWAYS,
                        TestParsing.gen_params,
                        "",
                        [
                            JobStep(
                                "ReadTester",
                                TriggerRule.ALL_SUCCESS,
                                TestParsing.gen_params,
                                "",
                                [],
                            ),
                        ],
                    ),
                ],
            ),
        ]
        wf_defn: List[JobStep] = load_lute_dag_str(
            workflow_str=SIMPLE_TRIGGER_DAG,
            lute_location=TestParsing.lute_location,
            executable_subdir=TestParsing.executable_subdir,  # Not running anything so irrelevant
            config_file=TestParsing.config_file,
            debug=TestParsing.debug,
            branch_conditions={"daq2": True},
        )
        assert is_equal(wf_defn, job_steps)

    def test_branch_daq2(self):
        job_steps: List[JobStep] = [
            JobStep(
                "Tester",
                TriggerRule.ALL_SUCCESS,
                TestParsing.gen_params,
                "",
                [
                    JobStep(
                        "SocketTester",
                        TriggerRule.ALL_FAILED,
                        TestParsing.gen_params,
                        "",
                        [],
                    ),
                ],
            ),
            JobStep(
                "Tester",
                TriggerRule.ALL_SUCCESS,
                TestParsing.gen_params,
                "",
                [
                    JobStep(
                        "SocketTester",
                        TriggerRule.ALL_SUCCESS,
                        TestParsing.gen_params,
                        "",
                        [],
                    ),
                ],
            ),
        ]
        wf_defn: List[JobStep] = load_lute_dag_str(
            workflow_str=SIMPLE_BRANCH_DAG,
            lute_location=TestParsing.lute_location,
            executable_subdir=TestParsing.executable_subdir,  # Not running anything so irrelevant
            config_file=TestParsing.config_file,
            debug=TestParsing.debug,
            branch_conditions={"daq2": True},
        )
        assert is_equal(wf_defn, job_steps)

    def test_branch_daq1(self):
        job_steps: List[JobStep] = [
            JobStep(
                "Tester",
                TriggerRule.ALL_SUCCESS,
                TestParsing.gen_params,
                "",
                [
                    JobStep(
                        "SocketTester",
                        TriggerRule.ALL_FAILED,
                        TestParsing.gen_params,
                        "",
                        [],
                    ),
                ],
            ),
            JobStep(
                "Tester",
                TriggerRule.ALL_SUCCESS,
                TestParsing.gen_params,
                "",
                [
                    JobStep(
                        "WriteTester",
                        TriggerRule.ALL_SUCCESS,
                        TestParsing.gen_params,
                        "",
                        [
                            JobStep(
                                "ReadTester",
                                TriggerRule.ALL_SUCCESS,
                                TestParsing.gen_params,
                                "",
                                [],
                            ),
                        ],
                    ),
                ],
            ),
        ]
        wf_defn: List[JobStep] = load_lute_dag_str(
            workflow_str=SIMPLE_BRANCH_DAG,
            lute_location=TestParsing.lute_location,
            executable_subdir=TestParsing.executable_subdir,  # Not running anything so irrelevant
            config_file=TestParsing.config_file,
            debug=TestParsing.debug,
            branch_conditions={"daq2": False},
        )
        assert is_equal(wf_defn, job_steps)

    def test_param_generation(self):
        import tempfile
        import yaml
        import os

        # This one actual needs a config file
        config_data = [
            {"experiment": "test", "run": 1, "work_dir": "/tmp"},
            {"SocketTester": {"num_arrays": 10}},
        ]

        fd: int
        temp_config: str
        fd, temp_config = tempfile.mkstemp(suffix=".yaml")
        expanded_config_stored: str = ""
        try:
            with os.fdopen(fd, "w") as f:
                yaml.dump_all(config_data, f)

            wf_defn: List[JobStep] = load_lute_dag_str(
                workflow_str=PARAM_GENERATION_DAG,
                lute_location=TestParsing.lute_location,
                executable_subdir=TestParsing.executable_subdir,
                config_file=temp_config,
                debug=TestParsing.debug,
                branch_conditions={"daq2": True},
            )

            expanded_config_stored = wf_defn[0].next[0].parameters.config_file

            starting_params: JobParameters = JobParameters(
                TestParsing.lute_location,
                TestParsing.executable_subdir,
                temp_config,
                TestParsing.debug,
            )
            starting_params.config_file = temp_config
            expanded_params: JobParameters = JobParameters(
                TestParsing.lute_location,
                TestParsing.executable_subdir,
                expanded_config_stored,
                TestParsing.debug,
            )

            job_steps: List[JobStep] = [
                JobStep(
                    "Tester",
                    TriggerRule.ALL_SUCCESS,
                    starting_params,
                    "",
                    [
                        JobStep(
                            "SocketTester_0",
                            TriggerRule.ALL_SUCCESS,
                            expanded_params,
                            "",
                            [],
                        ),
                        JobStep(
                            "SocketTester_1",
                            TriggerRule.ALL_SUCCESS,
                            expanded_params,
                            "",
                            [],
                        ),
                        JobStep(
                            "SocketTester_2",
                            TriggerRule.ALL_SUCCESS,
                            expanded_params,
                            "",
                            [],
                        ),
                    ],
                )
            ]

            assert is_equal(job_steps, wf_defn)

        finally:
            # Clean up temp config
            if os.path.exists(temp_config):
                os.unlink(temp_config)

            # Clean up any expanded config that was created
            if os.path.exists(expanded_config_stored):
                os.unlink(expanded_config_stored)
