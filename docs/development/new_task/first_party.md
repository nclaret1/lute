# Creating a "First-Party" `Task`
The process for creating a "First-Party" `Task` is very similar to that for a "Third-Party" `Task`, with the difference being that you must also write the analysis code. The steps for integration are:

1. Write the `TaskParameters` model.
2. Write the `Task` class. There are a few rules that need to be adhered to. Pay special attention if you require either:

    1. The use of MPI. (See [here](#using-mpi-for-your-task))
    2. Your `Task` to run in a specific environment that the `Executor` does NOT run in. (See [here](#running-your-task-in-a-different-environment))

3. Make your `Task` available by modifying the import function.
4. Specify an `Executor`

## Specifying a `TaskParameters` Model for your `Task`
Parameter models have a format that must be followed for "Third-Party" `Task`s, but "First-Party" `Task`s have a little more liberty in how parameters are dealt with, since the `Task` will do all the parsing itself.

To create a model, the basic steps are:

1. If necessary, create a new module (e.g. `new_task_category.py`) under `lute.io.models`, or find an appropriate pre-existing module in that directory.
    - An `import` statement must be added to `lute.io.models._init_` if a new module is created, so it can be found.
    - If defining the model in a pre-existing module, make sure to modify the `__all__` statement to include it.
2. Create a new model that inherits from `TaskParameters`. You can look at `lute.models.io.tests.TestReadOutputParameters` for an example. **The model must be named** `<YourTaskName>Parameters`

    - You should include **all** relevant parameters here, including input file, output file, and any potentially adjustable parameters. These parameters **must** be included even if there are some implicit dependencies between `Task`s and it would make sense for the parameter to be auto-populated based on some other output. Creating this dependency is done with validators (see step 3.). All parameters should be overridable, and all `Task`s should be fully-independently configurable, based solely on their model and the configuration YAML.
    - To follow the preferred format, parameters should be defined as: `param_name: type = Field([default value], description="This parameter does X.")`

3. Use validators to do more complex things for your parameters, including populating default values dynamically:

    - E.g. create default values that depend on other parameters in the model - see for example: [SubmitSMDParameters](https://github.com/slac-lcls/lute/blob/14d485f931cf9201d3abe0693b2959b35111579f/lute/io/models/smd.py#L461).
    - E.g. create default values that depend on other `Task`s by reading from the database - see for example: [TestReadOutputParameters](https://github.com/slac-lcls/lute/blob/14d485f931cf9201d3abe0693b2959b35111579f/lute/io/models/tests.py#L102).

4. The model will have access to some general configuration values by inheriting from `TaskParameters`. These parameters are all stored in `lute_config` which is an instance of `AnalysisHeader` ([defined here](https://github.com/slac-lcls/lute/blob/14d485f931cf9201d3abe0693b2959b35111579f/lute/io/models/base.py#L44)).

    - For example, the experiment and run number can be obtained from this object and a validator could use these values to define the default input file for the `Task`.

A number of configuration options and **Field** attributes are also available for "First-Party" `Task` models. These are identical to those used for the `ThirdPartyTask`s, although there is a smaller selection. These options are reproduced below for convenience.

**Config settings and options**

Under the class definition for `Config` in the model, we can modify global options for all the parameters. In addition, there are a number of configuration options related to specifying what the outputs/results from the associated `Task` are, and a number of options to modify runtime behaviour. Currently, the available configuration options are:

| **Config Parameter** | **Meaning**                                                                                                  | **Default Value**     | **ThirdPartyTask-specific?**             |
|:--------------------:|:------------------------------------------------------------------------------------------------------------:|:---------------------:|:----------------------------------------:|
| `run_directory`      | If provided, can be used to specify the directory from which a `Task` is run.                                | `None` (not provided) | **NO**                                   |
| `set_result`         | `bool`. If `True` search the model definition for a parameter that indicates what the result is.             | `False`               | **NO**                                   |
| `result_from_params` | If `set_result` is `True` can define a result using this option and a validator. See also `is_result` below. | `None` (not provided) | **NO**                                   |
| `short_flags_use_eq` | Use equals sign instead of space for arguments of `-` parameters.                                            | `False`               | **YES** - Only affects `ThirdPartyTask`s |
| `long_flags_use_eq`  | Use equals sign instead of space for arguments of `-` parameters.                                            | `False`               | **YES** - Only affects `ThirdPartyTask`s |


These configuration options modify how the parameter models are parsed and passed along on the command-line, as well as what we consider results and where a `Task` can run. The default behaviour is that parameters are assumed to be passed as `-p arg` and `--param arg`, the `Task` will be run in the current working directory (or scratch if submitted with the ARP), and we have no information about `Task` results . Setting the above options can modify this behaviour.

- By setting `short_flags_use_eq` and/or `long_flags_use_eq` to `True` parameters are instead passed as `-p=arg` and `--param=arg`.
- By setting `run_directory` to a valid path, we can force a `Task` to be run in a specific directory. By default the `Task` will be run from the directory you submit the job in, or from your scratch folder (`/sdf/scratch/...`) if you submit from the eLog. Some `ThirdPartyTask`s rely on searching the correct working directory in order run properly.
- By setting `set_result` to `True` we indicate that the `TaskParameters` model will provide information on what the `TaskResult` is. This setting must be used with one of two options, either the `result_from_params` `Config` option, described below, or the **Field** attribute `is_result` described in the next sub-section (**Field Attributes**).
- `result_from_params` is a Config option that can be used when `set_result==True`. In conjunction with a **validator** (described a sections down) we can use this option to specify a result from all the information contained in the model. E.g. if you have a `Task` that has parameters for an `output_directory` and a `output_filename`, you can set `result_from_params==f"{output_directory}/{output_filename}"`.


**Field attributes**

In addition to the global configuration options there are a couple of ways to specify individual parameters. The following `Field` attributes are used when parsing the model:

| **Field Attribute** | **Meaning**                                                                                            | **Default Value** | **Example**                                       |
|:-------------------:|:------------------------------------------------------------------------------------------------------:|:-----------------:|:-------------------------------------------------:|
| `description`       | Documentation of the parameter's usage or purpose.                                                     | N/A               | `arg = Field(..., description="Argument for...")` |
| `is_result`         | `bool`. If the `set_result` `Config` option is `True`, we can set this to `True` to indicate a result. | N/A               | `output_result = Field(..., is_result=true)`      |


## Writing the `Task`
You can write your analysis code (or whatever code to be executed) as long as it adheres to the limited rules below. You can create a new module for your `Task` in `lute.tasks` or add it to any existing module, if it makes sense for it to belong there. The `Task` itself is a single class constructed as:

1. Your analysis `Task` is a class named in a way that matches its Pydantic model. E.g. `RunTask` is the `Task`, and `RunTaskParameters` is the Pydantic model.
2. The class must inherit from the `Task` class (see template below). **If you intend to use MPI see the following section.**
3. You must provide an implementation of a `_run` method. This is the method that will be executed when the `Task` is run. You can in addition write as many methods as you need. For fine-grained execution control you can also provide `_pre_run()` and `_post_run()` methods, but this is optional.
4. For all communication (including print statements) you should use the `_report_to_executor(msg: Message)` method. Since the `Task` is run as a subprocess this method will pass information to the controlling `Executor`. You can pass **any** type of object using this method, strings, plots, arrays, etc.
5. If you did not use the `set_result` configuration option in your parameters model, make sure to provide a result when finished. This is done by setting `self._result.payload = ...`. You can set the result to be any object. If you have written the result to a file, for example, please provide a path.

A minimal template is provided below.

```py
"""Standard docstring..."""

__all__ = ["RunTask"]
__author__ = "" # Please include so we know who the SME is

# Include any imports you need here

from lute.execution.ipc import Message                    # Message for communication
from lute.io.models.my_task import RunTaskParameters      # For TaskParameters
from lute.tasks.task import *                             # For Task

class RunTask(Task): # Inherit from Task
    """Task description goes here, or in __init__"""

    def __init__(self, *, params: RunTaskParameters) -> None:
        super().__init__(params=params) # Sets up Task, parameters, etc.
        # Parameters will be available through:
          # self._task_parameters
          # You access with . operator: self._task_parameters.param1, etc.
        # Your result object is availble through:
          # self._result
            # self._result.payload <- Main result
            # self._result.summary <- Short summary
            # self._result.task_status <- Semi-automatic, but can be set manually

    def _run(self) -> None:
        # THIS METHOD MUST BE PROVIDED
        self.do_my_analysis()

    def do_my_analysis(self) -> None:
        # Send a message, proper way to print:
        msg: Message(contents="My message contents", signal="")
        self._report_to_executor(msg)

        # When done, set result - assume we wrote a file, e.g.
        self._result.payload = "/path/to/output_file.h5"
        # Optionally also set status - good practice but not obligatory
        self._result.task_status = TaskStatus.COMPLETED
```

### Accessing your validated parameters

The parameters specified in your `TaskParameters` model will be available from the `self._task_parameters` object. They are accessed as attributes on this object, e.g. `self._task_parameters.param1`. They can be used in any manner required by the running code.

### Using MPI for your `Task`

In the case your `Task` is written to use `MPI` a slight modification to the template above is needed. Specifically, an additional keyword argument should be passed to the base class initializer: `use_mpi=True`. This tells the base class to adjust signalling/communication behaviour appropriately for a multi-rank MPI program. Doing this prevents tricky-to-track-down problems due to ranks starting, completing and sending messages at different times. The rest of your code can, as before, be written as you see fit. The use of this keyword argument will also synchronize the start of all ranks and wait until all ranks have finished to exit.

```py
"""Task which needs to run with MPI"""

__all__ = ["RunMPITask"]
__author__ = "" # Please include so we know who the SME is

# Include any imports you need here

from lute.execution.ipc import Message # Message for communication
from lute.io.models.base import *      # For TaskParameters
from lute.tasks.task import *          # For Task

# Only the init is shown
class RunMPITask(Task): # Inherit from Task
    """Task description goes here, or in __init__"""

    # Signal the use of MPI!
    def __init__(self, *, params: RunMPITaskParameters, use_mpi: bool = True) -> None:
        super().__init__(params=params, use_mpi=use_mpi) # Sets up Task, parameters, etc.
        # That's it.
```

### Running your `Task` in a different environment

In the case your `Task` is intended to be run a different environment than the `Executor` (usually the psana1 or psana2 environments), you will need to include the following changes to the template to allow the LUTE infrastructure to support this.

```py
"""First-party Task which runs in a different environment."""

__all__ = ["RunTaskNewEnv"]
__author__ = "" # Please include so we know who the SME is

from typing import Optional, TYPE_CHECKING # This will be used to guard incompatible objects at run-time

# Include any imports you need here

from lute.execution.ipc import Message # Message for communication
# from lute.io.models.base import *    # For TaskParameters, see below
from lute.tasks.task import *          # For Task
from lute.io.parameters import RowIds  # New option needed.

if TYPE_CHECKING:
    # For type checking we will use your TaskParameters model, so import it here
    from lute.io.models.my_task_type import RunTaskNewEnvParameters
else:
    # This is the import case at RUN time. We cannot rely on `RunTaskNewEnvParameters`
    # being importable in arbitrary environments, so we construct a surrogate.
    # The following import is only to avoid errors with type hints. No other
    # code changes are needed
    from lute.io.parameters import TaskParameters
    RunTaskNewEnvParameters = TaskParameters

# Only the init is shown
class RunTaskNewEnv(Task): # Inherit from Task
    """Task description goes here, or in __init__"""

    # Provide the new `row_ids` argument
    def __init__(self, *, params: RunTaskNewEnvParameters, row_ids: Optional[RowIds] = None) -> None:
        super().__init__(params=params, row_ids=row_ids) # Sets up Task, parameters, etc.
        # That's it - rest of Task code and accessing parameters is identical to before
```

For the purposes of running first-party code in a separate environment, we reconstruct the `TaskParameters` objects into containers that require no external dependencies (and in particular pydantic). This allows the object to be constructed in any environment (with some restrictions, see [below](#known-environment-restrictions), as all the code is included in LUTE itself.

To summarize, the changes needed in order to support this mode of operation are:

1. A few minor import changes:

    1. Import the `TYPE_CHECKING` guard from Python's `typing` module.
    2. Import `RowIds` from `lute.io.parameters`
    3. `if TYPE_CHECKING` import your `RunTaskNewEnvParameters` from `lute.io.models....` (this is the pydantic model). Otherwise, import `TaskParameters` from `lute.io.parameters`, and **THEN** set `RunTaskNewEnvParameters = TaskParameters`.

2. Provide a new argument to the initializer: `row_ids: Optional[RowIds] = None`. This will then get passed to the super-class initialiazer: `super().__init__(params=params, row_ids=row_ids)`.

**Note:** You can use this mode with MPI as well, in which case you also provide the `use_mpi=True` argument.

### Message signals

Signals in `Message` objects are strings and can be one of the following:

```py
LUTE_SIGNALS: Set[str] = {
    "NO_PICKLE_MODE",
    "TASK_STARTED",
    "TASK_FAILED",
    "TASK_STOPPED",
    "TASK_DONE",
    "TASK_CANCELLED",
    "TASK_RESULT",
    "TASK_REQUEST",
    "TASK_RESPONSE",
    "TASK_METADATA",
}
```
Each of these signals is associated with a hook on the `Executor`-side. They are for the most part used by base classes; however, you can choose to make use of them manually as well.


### Advanced use case: IPC with other `Task`s

When using `maestro` as a backend workflow manager (support for the below is not yet available for other backends), it is possible to communicate with other **managed** `Task`s that are running in parallel.

Specifically, `maestro` supports a number of REST API endpoints to facilitate this:

- `GET /tasks` : Get a list of all running **managed** `Task`s and any metadata that they have published.
- `GET/POST /rpc?task=....` : Remote procedure call for the provided `task` as the query parameter.


While knowledge of these APIs is useful, from the perspective of `Task` code, direct calls to them is not needed. All `Task` communication goes via the `Executor`. A number of methods have been provided in the base `Task` class in order to facilitate communication.

- `task_request(for_task: str, request: Any, wait_for_resp: bool = True)`: Send a `request` to `for_task`. You can either block (by setting `wait_for_resp` to `True`) or check back later.
- `get_running_tasks() -> Message`: Get a current list of running `Task`s and any metadata they have published already.
    - The data will be returned in the following format (in `Message.contents`). The dictionary will have a `managed_tasks` key, which has a list of dictionaries for each running **managed** `Task`. There will always be a `name`, `status` and `task` key. Any other keys can change.

```
{
    "managed_tasks": [
        {
            "name": "managed_task_name1",
            "status": "RUNNING",
            "task": "task_name1",
            "meta_key1": "val1",
        }, # May have other keys
        {
            "name": "managed_task_name2",
            "status": "RUNNING",
            "task": "task_name2",
            "meta_key2": "val2",
        }, # May have other keys
}
```

- `publish_metadata(metadata: Dict[str, Any])`: Publish a dictionary that will be attached to the metadata stored for the running `Task`. It will then be retrievable by the other `Task`s if they call `get_running_tasks`.

Note: To use metadata as a means of communication between the various `Task`s, you must know what the other `Task` will be publishing. Currently, there are only a few standardized sets of metadata which include hostnames. Beyond this, it is all `Task`-specific.

## Making your `Task` available
Once the `Task` has been written, it needs to be made available for import. Since different `Task`s can have conflicting dependencies and environments, this is managed through an import function. When the `Task` is done, or ready for testing, a condition is added to `lute.tasks.__init__.import_task`. For example, assume the `Task` is called `RunXASAnalysis` and it's defined in a module called `xas.py`, we would add the following lines to the `import_task` function:

```py
# in lute.tasks.__init__

# ...

def import_task(task_name: str) -> Type[Task]:
    # ...
    if task_name == "RunXASAnalysis":
        from .xas import RunXASAnalysis

        return RunXASAnalysis
```

## Defining an `Executor`
The process of `Executor` definition is identical to the process as described for [`ThirdPartyTask`s here](/development/new_task/third_party#specifying-an-executor-creating-a-runnable-managed-task). The one exception is if you defined the `Task` to use MPI as described in the section [above](#using-mpi-for-your-task), you will likely consider using the `MPIExecutor`.

### Environment setup
By default, first-party `Task`s are expected to use the same environment as the `Executor`. When using the same environment, you can still use the environment update methods to insert specific environment variables, without any other additional changes (assuming these variables do not invalidate `PYTHONPATH`, touch conda-specific variables, etc.). It is also possible to use a completely different environment. Please read the template [above](#running-your-task-in-a-different-environment) carefully.

#### Known Environment Restrictions
The third-party dependencies (outside of pydantic for parameter validation) have been kept relatively slim for the core LUTE infrastructure. This allows first-party code to run under most environments, albeit with the following minor restrictions.

1. The environment must be Python 3.8+. Support can likely be extended to Python 3.7 (and potentially 3.5), if a use-case arises.
2. There is a soft-dependency on the use of ZMQ for IPC between the `Task` and `Executor` layers. This, however, can be removed by setting the environment `LUTE_USE_ZMQ=0` when launching the **Managed** `Task`. There is a fully functional implementation using the Python `socket` module.
3. There is a dependency on pickle protocol 4 (current default). If the protocol default is changed in newer Python versions (or support is added for Python 3.7 and below), code changes will be needed to coordinate protocol versions used by the `Task` and `Executor` in order to serialize and deserialize communications between them.
4. The current "environment switching" mechanism is very naive, and expects the new environment to be a conda environment. In principle, it should not be difficult to add support for other types (spack, etc.); however, this is not currently in place. Speak with the maintainers if you require this support.

## Setting `Task` Results
The `Executor` may decide to perform additional actions with specific `Task` results based on their type. In the case of a first-party `Task`, it is generally preferred to directly set the results rather than rely on `tasklets` (see [here](development/new_task/third_party/#parsing-inputs-and-outputs-tasklets)).

In general, `Task`s have a `self._result` attribute, which is an instance of `TaskResult` (`lute.tasks.dataclasses.TaskResult`). There are two fields in particular to be user-set: `summary` and `payload`. The meaning of these fields is "squishy", and should be used in a manner that makes intuitive sense for the code being written. As an example, a `payload` could be an output file, while a `summary` could be a figure generated from that file. It is not necessary to define both (or even any) of these fields.

If the `Executor` encounters the following objects in **EITHER** the `summary` or `payload` fields, it will take the corresponding action:

- `Dict[str, str]`: Post key/value pairs under the report section in the control tab of the eLog. These key/values are also posted as run parameters if possible. This return type is best used for short text summaries, e.g. indexing rate, execution time, etc. The key/value pairs are converted to a semi-colon delimited string for storage in the database. E.g. `{"Rate": 0.05, "Total": 10}` will be stored as `Rate: 0.05;Total: 10` in the database.
- `ElogSummaryPlots`: This special dataclass, defined in `lute.tasks.dataclasses` is used to create an eLog summary plot under the Summaries tab. The path to the created HTML file is stored in the database.

If you provide a list or tuple of these objects as the `summary` or `payload` each of the items will be processed independently. For datbase archiving, the various entries are stored semi-colon delimited.

