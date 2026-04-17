# Workflows with Maestro
**Note:** As with other backends, the term **DAG**, or directed acyclic graph, may be used to refer to "worflows". This page will use the terms workflow and DAG interchangeably.

## Relevant Components
In addition to the core LUTE package, a number of components are generally involved to run a workflow.

The main interface to the `maestro` workflow manager is:

- `launch_maestro.py` (defined in source code). This is installed as `launch_slurm` when LUTE is built.
- `submit_launch_slurm.sh` A wrapper script which submits the above as a batch job so it doesn't need to be watched interactively.

The main components of the `maestro` sub-project are:

- A `Manager` which orchestrates the workflow.
- A `Server` which is used for HTTP communication.
- `Launcher` objects which define how individual steps in the workflow are launched.

Workflow definitions are all YAML documents similarly to what is described in the [dynamic workflows](dynamic_workflows.md) page; however, there are a number of small differences between that format (which is used for Airflow and Prefect) and the `maestro`. The `maestro` format also supports a number of additional features.

## Launch/Submission Scripts
### `launch_slurm` (The source file is: `launch_maestro.py`)

This script will parse a YAML workflow definition and then pass it to the `maestro` backend.

The script takes the following parameters:

```bash
usage: launch_slurm [-h] -c CONFIG -W WORKFLOW_DEFN [-e EXPERIMENT] [-r RUN] [-d] [--num_server_threads NUM_SERVER_THREADS] [--unbuffered] [$SLURM_ARGS]

A light-weight workflow manager which executes LUTE Managed Tasks.

required arguments:
  -c CONFIG, --config CONFIG
                        Path to config YAML file.
  -W WORKFLOW_DEFN, --workflow_defn WORKFLOW_DEFN
                        Path to a YAML file with workflow.

required arguments when running without the ARP:
  -e EXPERIMENT, --experiment EXPERIMENT
                        Provide an experiment if not running with ARP.
  -r RUN, --run RUN     Provide a run number if not running with ARP.

optional arguments:
  -h, --help            show this help message and exit
  -d, --debug           Run in debug mode.
  --num_server_threads NUM_SERVER_THREADS
                        Number of threads to use for the HTTP server.
  --unbuffered          Flush logs immediately. Warning: This can make output confusing when running multiple managed Tasks are running in parallel.

Refer to https://github.com/slac-lcls/lute for more information.
```

- `-c` refers to the path of the configuration YAML that contains the parameters for each **managed** `Task` in the requested workflow.
- `-W` is the path to the custom DAG defined in YAML.
- `-e | --experiment` is used to pass the experiment name. Needed if not using the ARP, i.e. running from the command-line.
- `-r | --run` is used to pass a run number. Needed if not using the ARP, i.e. running from the command-line.

- `--debug` is an optional flag to run all steps of the workflow in debug mode for verbose logging and output.
- `--num_server_threads` is an optional flag which will override the default number of threads setup by the built-in HTTP server.
- `--unbuffered` is an optional flag which will display logs immediately as the are written. By default, logs are collected and displayed at the end of each step of the workflow. This unbuffered option will print them immediately. This is probably not useful if you have multiple things running in parallel as part of your workflow, but if all the steps are running serially it could be useful for more immediate feedback.

- `SLURM_ARGS` are SLURM arguments to be passed to the `submit_slurm` script which are used for each individual **managed** `Task`. These arguments to do NOT affect the submission parameters for the job running `launch_airflow.py` (if using `submit_launch_airflow.sh` below).


#### Lifetime

This script will run for the entire duration of the **workflow (DAG)**. After it launches the DAG, it will enter a status update loop which will keep track of each individual job (each job runs one managed `Task`). At the end of each job it will collect the log file, in addition to providing a few other status updates/debugging messages, and append it to its own log (if not using the `--unbuffered` flag described above). This allows all logging for the entire workflow (DAG) to be inspected from an individual file. This is particularly useful when running via the eLog, because only a single log file is displayed.

### `submit_launch_slurm.sh`
This script wraps the `launch_slurm` script and submits it as a batch job. This is useful to not have to keep a terminal window open to watch the process interactively when submitting manually. It is, **however**, required when submitting from the eLog. The initial job submitted by the ARP can not have a duration of longer than 30 seconds, as it will then time out. As the `launch_slurm` job will live for the entire duration of the workflow, it must be submitted as a batch a batch job.

The interface is mostly indentical to `launch_slurm` itself, except the path to the actual `launch_slurm` script must be passed as the first argument.

Currently, the wrapper submits the job with minimal resources, requesting only a single core for the workflow manager. In the future, this may be made configurable.

Usage:

```bash
submit_launch_slurm.sh /path/to/launch_slurm [-h] -c CONFIG -W WORKFLOW_DEFN [-e EXPERIMENT] [-r RUN] [-d] [--num_server_threads NUM_SERVER_THREADS] [--unbuffered] [$SLURM_ARGS]
```

## Creating a new workflow
Defining a new workflow involves creating a new YAML file.

As an example we will consider the following test workflow.

```yaml
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
```

- All workflows **must** begin with a `!LUTE_DAG` tag. This tells the YAML parser that this should be parsed into the special `JobStep` structures that `maestro` recognizes as steps to be submitted.
- Next, the top level of the workflow (the first **managed** `Task` (or `Task`s)) is setup.

  - If there is only one step to launch initially, this can be a dictionary, otherwise it must be a list to define two steps to be launched in parallel.

- Each step in the workflow must contain:

  - A `task_name`, which is the name of a **managed** `Task` to be submitted.
  - A custom string of SLURM arguments in `slurm_params`. This can be used to override the arguments which are passed in the command-line, but if it is provided, it must provide **EVERY** SLURM argument. Currently, only replacing some arguments is not supported.
  - A field called `next` which is a list which sets up the **managed** `Task`s that will be submitted after this one completes. If it is left empty, then this branch of the workflow ends here.

A number of additional special tags can be used as described below in the relevant sections.

### Trigger Rules

A set of trigger rules can be provided before each step definition with an appropriate tag:

- `!ALL_SUCCESS`: This job step will only be submitted if all previous steps in its branch succeed (This is the default)
- `!ANY_SUCCESS`: This job step will be submitted as soon as any previous step in its branch succeed.
- `!ALL_COMPLETED`: This job step will be submitted as soon as all previous steps complete, whether they succeed or fail.
- `!ALL_FAILED`: This job step will be submitted only if all previous steps have failed.
- `!ANY_FAILED`: This job step will be submitted as soon as any previous step fails.
- `!ALWAYS`: This job step will always run as soon as it is reached in the DAG.

### Setting up branching

A number of branching conditions can be defined as well. Currently supported are branching based on LCLS1 vs LCLS2 DAQ, and the run type. These are defined by using the tag `!branch_daq2` (for example) and defining two dictionaries underneath it for the various cases (`daq2` or `daq1` in this example).

### Parameter generation

A not infrequent requirement is to rerun a workflow where some set of the `Task` parameters is modified each time. Parameter "sweeps" or generation can be configured from the DAG definition to simplify this process.

A `!param_sweep` tag can be prepended before the relevant step in the DAG. When adding this tag, in addition to `task_name`, `slurm_params`, and `next` keys which must be defined for every step in the DAG, a `param_matrix` key is included. Under this key, the set of `Task` parameter values should be provided.

For example, the `SocketTester` **managed** `Task`, runs `TestSocket`. This has a parameter `num_arrays` which takes an integer. If we want to run that `Task` five times, with a different value each time for this parameter, we can define the DAG as:

```yaml
!LUTE_DAG
task_name: Tester
slurm_params: "--partition=milano --account=lcls:data --ntasks=1"
next:
- !param_sweep
  task_name: SocketTester
  param_matrix:
    num_arrays: [5, 10, 15, 20, 25]
  slurm_params: "--partition=milano --account=lcls:data --ntasks=1"
  next: []
```

This will create a workflow which runs four instances of the **managed** `Task` **IN PARALLEL**.

```
          - SocketTester_0   # Has 5 for num_arrays
        /
       /  - SocketTester_1   # Has 10 for num_arrays
      / /
Tester    - SocketTester_2   # Has 15 for num_arrays
      \ \
       \  - SocketTester_2   # Has 20 for num_arrays
        \
          - SocketTester_3   # Has 25 for num_arrays
```

#### How does this actually work? Important notes for developers

The parameter generation mechanism works by creating a new temporary config YAML. The original that the user has provided is used as the starting point. When using parameter generation it is generally still required to provide a config YAML to start with.

The `maestro` parser will take the parameter matrix provided in the DAG definition and create copies for the relevant `Task` that include modified versions of the specified parameters. Note how in the example above, the **managed** `Task`s have been denoted with a `_X` suffix. This suffix is intentional, and is used for determining which set of parameters each job will use. The suffix will be eventually removed; however, it is important for the lookup process.

In particular, the process works as:

1. When `run_task.py` sees a **managed** `Task` with this suffix, it will look for the underlying **managed** `Task` that it corresponds to. E.g., using the above definition, it will see `SocketTester_0` and determine that it must run `SocketTester`.

    - I.e., `SocketTester_0` -> `SocketTester`

2. This determination is not enough, however. For this **managed** `Task` instance, it will then make sure that t modifies the name of the `Task` that it is running. So, it will take the stripped suffix and add it to the `Task` name. `TestSocket` will become `TestSocket_0`. This allows the underlying `Task` layer to lookup the information in the config YAML transparently.

    - More specifically, it will do `SocketTester.task_name = "TestSocket"` -> `SocketTester.task_name = "TestSocket_0"`

3. Both the Execution and Task layers, once the config lookup process has completed, will remove the suffix where appropriate. E.g., we don't want to store information in the database under a `Task` named `TestSocket_0`. Ultimately, all these instances are running `TestSocket`, and the database will reflect that. The suffixes are just used internally to facilitate the parameter generation process.
