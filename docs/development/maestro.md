# Maestro Architecture and Documentation

As the `maestro` workflow manager is written as part of the LUTE project, some additional documentation on the architecture and features is warranted. Documentation for the other workflow backends (Airflow, Prefect) can be found from their respective developers.

This page is a work in progress.

## Architecture and Components

The `maestro` workflow manager is built from a number of basic components:

- A `Server` class which implements an HTTP server.

  - There are some additional classes and components for implementing the HTTP protocol (Requests, Responses, Methods, etc.).
  - In general, the only additional component which will be relevant for most developers is the `Handler` class - this implements a functor which is used as a callback run for specific REST API endpoints.

- A `Launcher` class which provides an abstraction for job step submission. Sub-classes implement different ways of submitting a step in a workflow (e.g. via running a SLURM submission script to launch the step as a batch job).

  - These server the same role that `Operators` did for Airflow, for example.

- A `ThreadPool` class which manages a number of threads for running job steps, and a queue to run them in the correct order.
- A `Manager` class which links all the above components together.

The workflow manager, composed of the components above, runs workflows which are defined using a `JobStep` class. This class fully describes all information required to run a single step in a workflow. A workflow is just a vector of these objects. A vector defines steps which can execute in parallel. The serial dependencies of the workflow (or DAG - directed acyclic graph) are defined by the `JobStep` class itself. The implementation is reminiscent of a linked-list, with downstream steps linked to the upstream steps via a `next` member. See below for more details on the class.

## REST API

The workflow manager may make use of the HTTP server for communication with the various steps in the workflow. The use of this server is dependent on the `Launcher` implementation - the `Launcher` subclass must provide the various callback functors (via subclasses of a `Handler` class) in order to use the HTTP server. Currently, the two implemented `Launcher` types use the HTTP server. The following is the currently used REST API. All calls are intended to be initiated by the `Executor` to the `maestro` workflow manager.

Currently, the `launch_maestro.py` script (refer to the [creating workflows with maestro](development/creating_workflows_maestro.md) documentation) sets the `LUTE_MANAGER_URL` environment variable with the `hostname:port` pair that can be used to communicate with the workflow manager. It will be passed to each step of the workflow. The `Executor` can use this environment variable to determine where to send API requests.

**Important:** HTTPS is **NOT** implemented - this communication is not intended to run over anything but a trusted local network (such as within the batch cluster).

### Endpoints
#### `/status`
##### Methods

- `GET`

##### Query Parameters

None

##### Request Body
```json
    {
        "managed_task": "...",
        "status": "...",
        # additional keys to be available to other running managed Tasks
    }
```

- Additional keys may be provided beyond the two above (which are required). This allows **managed** `Task`s to attach additional metadata to their entry in the `maestro` tracker. This metadata will be available to other **managed** `Task`s that run in parallel - it therefore provides a mechanism for IPC between processes without dealing with host/port discovery.

##### Response
HTTP OK

##### Example

```py3
import requests

json_data: Dict[str, str] = {"managed_task": "SmallDataProducer", "status": "STARTED"}
resp: requests.models.Response = requests.post(
    "http://MANAGER_URL/status", json=json_data
)
```

#### `/log`
##### Methods

- `POST`

##### Query Parameters

None

##### Request Body
```json
    {
        "managed_task": "...",
        "message": "..."
    }
```

##### Response
HTTP OK

##### Example

```py3
import requests

json_data: Dict[str, str] = {
    "managed_task": "SmallDataProducer", "message": "Test message"
}
resp: requests.models.Response = requests.post(
    "http://MANAGER_URL/status", json=json_data
)
```

#### `/tasks`
##### Methods

- `GET`

##### Query Parameters

None

##### Request Body

None

##### Response

```json
{
    "managed_tasks": [
        { "name": "Task1Manager", "status": "RUNNING", /* other keys */ },
        { "name": "Task2Manager", "status": "RUNNING", /* other keys */ }
    ]
}
```

- The `/tasks` API will return a set of keys that can change. In addition to just the name and status which is always provided, the **managed** `Task`s themselves may give information to `maestro` which will then be returned to other running processes that request it. This is provided via `/status` updates, which can include arbitrary keys in addition to the status update.

    - In general, this will usually include the name of the `Task` (in addition to **managed** `Task`) and host names for the processes.

##### Example

```py3
import requests

json_data: Dict[str, str] = {
    "managed_task": "SmallDataProducer", "message": "Test message"
}
resp: requests.models.Response = requests.get("http://MANAGER_URL/status")

resp_json: Dict[str, Any] = resp.json()
# resp_json should look like:
# {
#     "managed_tasks": [
#         { "name": "...", "status": "...", "task": "...", OTHER KEYS MAYBE },
#         { "name": "...", "status": "...", "task": "...", OTHER KEYS MAYBE },
#     ]
# }
```

### `/rpc`

The `rpc` endpoint consists of a `GET` and `POST` mechanism which allow **managed** `Task`s to request procedures be done by or information returned from other running **managed** `Task`s.

For requesting a procedure, it involves:

- **managed** `Task` 1 will use `POST` to request something of a target `Task` 2.
- **managed** `Task` 2 will use `GET` to see if there is some pending work. It then runs the requested procedure.

While not the explicit purpose of the endpoint, clever crafting of messages also enables it be used for general IPC.

- **managed** `Task` 1 will use `POST` to request something of **managed** `Task` 2.
- **managed** `Task` 2 will use `GET` to see if any requests are waiting for it.
- **managed** `Task` 2 will use `POST` to put up a response.
- **managed** `Task` 1 will use `GET` to read the response.

`maestro` maintains a queue system to allow work to be scheduled and acknowledged by various processes running in parallel without those processes having to deal with the routing of messages.

NOTE: This endpoint will be updated to bring terminology inline with the rest of the project. Currently `Task` is misused here, referring to a **managed** `Task`. Use caution.

##### Methods

- `GET`
- `POST`

##### Query Parameters

###### `GET`

- `?task=XYZ` : The **managed** `Task` for which the message is directed. This information may alternatively be provided in the request body.

###### `POST`

None

##### Request Body

###### `GET`

- `{ "task": "..." }`

###### `POST`

```json
{
    "target": "...",
    "message": "..."
}
```

- This will queue a message to be processed by the **managed** `Task` defined in `target`. The meaning of `message` is defined by the **managed** `Task`.

##### Response

###### `GET`


```json
{
    "message": "output."
}
```


###### `POST`

```json
"Message queued."
```

##### Example
**Example 1 - The intended purpose of RPC**
```python3
import requests

# From managed Task 1

json_data: Dict[str, str] = {
    "target": "Task2Manager", "message": "run_cleanup_function"
}
resp: requests.models.Response = requests.post("http://MANAGER_URL/rpc", json=json_data)
```
```python3
# From managed Task 2
## Check for messages

resp: requests.models.Response = requests.get(
    "http://MANAGER_URL/rpc?task=Task2Manager", json=json_data
)

## Would generally do error handling and so on, but ignore for now.
if resp.json()["message"] == "run_cleanup_function":
    run_cleanup_function()
```

**Example 2 - Adapted for more general IPC**
```python3
## Use structured messages to allow responses
## requester includes name in message so answer can be routed back

# From managed Task 1
json_data: Dict[str, str] = {
    "target": "Task2Manager", "message": "Task1Manager: What is the answer?"
}
resp: requests.models.Response = requests.post(
    "http://MANAGER_URL/rpc", json=json_data
)
```
```python3
# From managed Task 2
## Check for messages
resp: requests.models.Response = requests.get(
    "http://MANAGER_URL/rpc?task=Task2Manager"
)

## Would generally do error handling and so on, but ignore for now.
requester_and_request: str = resp.json()["message"]

requester, request = [item.strip() for item in requester_and_request.split(":")]

answer: str = "I don't know"
if request == "What is the answer":
    answer = "42 is the answer"

### Send response back
json_data: Dict[str, str] = {
    "target": requester, "message": answer
}
```
```python3
# Finally back to managed Task 1
resp: requests.models.Response = requests.get(
    "http://MANAGER_URL/rpc?task=Task1Manager"
)

## Would generally do error handling and so on, but ignore for now.
answer: str = resp.json()["message"]
print(answer)
```

## `JobStep` class

The `JobStep` class is defined in `workflows/maestro/src/lwm/job.hh`. It provides all information required to run a single step of a workflow. It is reproduced here for convenience.

```c++
  class JobParameters {
  public:
    std::string lute_location;
    std::string executable_subdir;
    std::string config_file;
    bool debug;
  };

  class JobStep {
  public:
    JobStep(std::string _task_name,
            TriggerRule _trigger_rule,
            JobParameters _parameters,
            std::string _extra_parameters,
            std::vector<JobStep> _next)
      : managed_task_name(_task_name)
      , trigger_rule(_trigger_rule)
      , parameters(_parameters)
      , extra_parameters(_extra_parameters)
      , next(_next)
    {}

    std::string managed_task_name;
    TriggerRule trigger_rule;
    JobParameters parameters;
    std::string extra_parameters;
    std::vector<JobStep> next;
  };
```

The following table provides an overview of the uses of the various member fields.
| Member              | Used                                                                                            |
|:-------------------:|:------------------------------------------------------------------------------------------------|
| `managed_task_name` | The name of the **managed** `Task` to run.                                                      |
| `trigger_rule`      | The trigger rule determining the dependence of this step on those upstreams (see below).        |
| `parameters`        | A set of general parameters determining the LUTE installation/clone to use and the config file. |
| `extra_parameters`  | A `Launcher` specific set of parameters. Currently used by the `SlurmLauncher` for SLURM args.  |
| `next`              | A vector of `JobStep`s to run after this one completes.                                         |


### `TriggerRule` - Defining when a step should run

The following enum class defines a number of trigger rules that are currently implemented.

```c++
  enum class TriggerRule {
    ALL_SUCCESS=0,
    ANY_SUCCESS=1,
    ALL_COMPLETED=2,

    ALL_FAILED=3,
    ANY_FAILED=4,

    ALWAYS=5
  };
```

In order, these mean:

- `ALL_SUCCESS`: This job step will only be submitted if all previous steps in its branch succeed (This is the default)
- `ANY_SUCCESS`: This job step will be submitted as soon as any previous step in its branch succeed.
- `ALL_COMPLETED`: This job step will be submitted as soon as all previous steps complete, whether they succeed or fail.
- `ALL_FAILED`: This job step will be submitted only if all previous steps have failed.
- `ANY_FAILED`: This job step will be submitted as soon as any previous step fails.
- `ALWAYS`: This job step will always run as soon as it is reached in the DAG.


## `Launcher` classes

There are currently two `Launcher` implementations. They are selected via the `LauncherType` enum passed to the `Manager`.

```c++
  enum class LauncherType {
    PythonLauncherType = 0,
    SlurmLauncherType = 1
  };
```

- The `PythonLauncher` runs the job steps directly calling the `python` interpreter - this is a blocking call.
- The `SlurmLauncher` submits the `submit_slurm` batch script to launch the `JobStep` as a batch job.

### `Handler` implementations

Side-by-side with the actual `Launcher` implementations a set of `Handler`s have been defined that implement the REST API. These are used by both the `PythonLauncher` and `SlurmLauncher`.

- `JsonStatusHandler`: Implements the callback for status updates from the `Executor`.
- `JsonLogHandler`: Implements the callback for log updates from the `Executor`. If the unbuffered logs option is passed to the launch script then this handler will print log messages as they come in.

As both `Launcher` implementations use (and expect) the HTTP server, the `m_expects_server` bool is set to `true` for the implementations. Setting this boolean allows the `Manager` to register the callback handlers with the HTTP server.

## `ManagerParameters` and `Manager` creation

A number of constructors are provided for the `Manager` class; however, the most complete (and the one accessible via Python bindings) is the one that defines a set of `ManagerParameters`. This allows for full configuration of the workflow manager. The parameters object is reproduced below.

```c++
  class ManagerParameters {
  public:
    /**
     * Number of threads for the manager. This should probably match the number
     * of Managed Task's that will run concurrently.
     */
    unsigned num_manager_threads{2};
    /**
     * Number of threads for the server process. This probably doesn't need to be
     * very high unless you expect a lot of HTTP traffic.
     */
    unsigned num_server_threads{2};
    /**
     * Whether to print logs immediately or only at the end of each JobStep by
     * pulling them from the log file.
     */
    bool unbuffered_logs{false};

    /**
     * HTTP server host IP (usually 0.0.0.0)
     */
    std::string host{"0.0.0.0"};
    /**
     * HTTP server port.
     */
    std::uint16_t port{8080};
    /**
     * What kind of job launching to employ (SLURM, Python, etc.)
     */
    LauncherType launch_type{LauncherType::PythonLauncherType};
    /**
     * Whether the experiment/workflow is LCLS2. This affects the base environment used.
     */
    bool is_daq2{false};
    /**
     * What the run type is for this experiment run.
     */
    std::string run_type{""};
  };
```

The construct then takes an instance of this class:

```c++
Manager::Manager(const ManagerParameters& params);
```

## Running workflows

After creating a `Manager` instance, there are two steps to run a workflow.

- First, the `queue_workflow` member function is called (`Manager::queue_workflow(const WfDefinition& wf_defn)`)

  - `WfDefinition` is a type alias for `std::vector<JobStep>`

- After queueing a workflow, the `run_workflow` function is called.

### Rough mechanism

When the `Manager` was created, it created a `ThreadPool` instance in line with the set of parameters passed to the constructor. A `Launcher` type was also selected. As it recurses through the workflow, it will queue the `JobStep` objects to the `ThreadPool` queue to be run using the `Launcher::launch_task` function.

- Specifically, a lambda is used to to queue the selected `Launcher::launch_task` into the `ThreadPool`'s work queue.

The main function for doing this is `Manager::recurse_workflow` (in `workflows/maestro/src/lwm/manager.cc`). It is reproduced below for convenience:

```c++
  void Manager::recurse_workflow(const WfDefinition& wf, MaybeJobFutures_t wait_for=std::nullopt) {
    auto launch_func = [&](const JobStep &job,
                           bool is_daq2,
                           MaybeJobFutures_t wait_for = std::nullopt) -> JobReturn {
      return m_launcher->launch_task(job, is_daq2, wait_for);
    };

    for (const auto& step : wf) {
      auto next_wait_for = std::shared_future<JobReturn>(m_job_pool.enqueue(launch_func,
                                                                            step,
                                                                            m_params.is_daq2,
                                                                            wait_for));
      m_all_futures->push_back(next_wait_for);
      if (!step.next.empty()) {
        recurse_workflow(step.next, std::optional<decltype(next_wait_for)>(next_wait_for));
      }
    }
  }
```

After queueing the work to the `ThreadPool` queue, the `Manager` receives a future it can check for job completion. Once all `JobStep`s are queued, the rest of the `run_workflow` function consists of a loop checking the set of collected futures for completion.

### Python bindings

A number of objects are exposed to Python using the `pybind11` library. The top-level API provides only a single function which creates the workflow manager and runs a workflow:

```c++
std::string run_workflow(LWM::WfDefinition wf_defn,
                         LWM::ManagerParameters manager_params) {
  LWM::Manager manager = LWM::Manager(manager_params);
  manager.queue_workflow(wf_defn);

  return manager.run_workflow();
}
```

How to call this function (and create the manager parameters) from Python can be seen in `workflows/maestro/src/maestro/launch_maestro.py`.
