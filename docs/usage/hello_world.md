# "Running the `Test` `Task`
This simple example will walk you through running the `Test` `Task`, which simply counts to 10 and then succeed or fails depending on the configuration.
Please note that this should be run on psana in S3DF, as for now it relies on the specific path of the `psconda` environment.

Clone the Lute repository and run the installation script:
```bash
> ./build.sh
```

Start by creating the following `yaml` configuration file for the `Task`.
Note: this file can created anywhere, it does not need (and probably should not) be under the LUTE repository.
```yaml
%YAML 1.3
---
title: "LUTE Simple test"
experiment: "dummy"
lute_version: 0.2.0
task_timeout: 6000
work_dir: "/sdf/scratch/users/e/espov/lute_tests"  # <-- Replace with your preferred location. This is where the LUTE database will be created
...
---
Test:
  compound_var:  # <-- Mandatory variable for this Task
    Int: 10
    Dict:
      abc: 'def'
  float_var: 1.1  # <-- Optional integer variable
  str_var: "test_var"  # <-- Optional string variable
  throw_error: false  # <-- Will make the job succeed / fail on purpose
...
```

You are now ready to run the task.

Start by sourcing the relevant environments:
```bash
> source /sdf/group/lcls/ds/ana/sw/conda1/manage/bin/psconda.sh
```
and
```bash
> source <PATH_TO_LUTE>/install/bin/activate_installation
```

Now, simply call
```bash
> run_task -t Tester -c <PATH_TO_YOUR_YAML_FILE>
```

You should get the following ouput:
```bash
DEBUG:lute.execution.ipc:Preference of ZMQ usage not specified. Defaulting to using ZMQ.
INFO:lute.execution.ipc:Will use Unix sockets (ZMQ).
DEBUG:lute.execution.ipc:SocketCommunicator defines socket_path: /lscratch/espov/tmp/lute_670bd61d51354924ac493aad5885e3bb.sock
DEBUG:lute.execution.executor:Absolute path to subprocess_task.py not found.
INFO:lute.execution.executor:(DEBUG:lute.execution.ipc:Preference of ZMQ usage not specified. Defaulting to using ZMQ.
)
DEBUG:lute.execution.executor:Cannot set result from TaskParameters. `set_result` not specified!
INFO:lute.execution.executor:Executor: Test started
ERROR:lute.io.elog:eLog Update Failed! JID_UPDATE_COUNTERS is not defined!
INFO:lute.execution.executor:lute_config=AnalysisHeader(title='LUTE Simple test', experiment='dummy', run='', date='1970/01/01', lute_version=0.2.0, task_timeout=6000, work_dir='/sdf/scratch/users/e/espov/lute_tests') float_var=1.1 str_var='test_var' compound_var=CompoundVar(int_var=1, dict_var={'a': 'b'}) throw_error=False
INFO:lute.execution.executor:Test message 0
INFO:lute.execution.executor:Test message 1
INFO:lute.execution.executor:Test message 2
INFO:lute.execution.executor:Test message 3
INFO:lute.execution.executor:Test message 4
INFO:lute.execution.executor:Test message 5
INFO:lute.execution.executor:Test message 6
INFO:lute.execution.executor:Test message 7
INFO:lute.execution.executor:Test message 8
INFO:lute.execution.executor:Test message 9
INFO:lute.execution.executor:Test Finished.
INFO:lute.execution.executor:TaskStatus.COMPLETED
ERROR:lute.io.elog:eLog Update Failed! JID_UPDATE_COUNTERS is not defined!
DEBUG:lute.io._db.v1._sqlite:_make_task_table[CREATE]: CREATE TABLE IF NOT EXISTS Test(id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, gen_cfg_id INTEGER, exec_cfg_id INTEGER, "float_var" REAL, "str_var" TEXT, "compound_var.int_var" INTEGER, "compound_var.dict_var.a" TEXT, "throw_error" INTEGER, "result.task_status" TEXT, "result.summary" TEXT, "result.payload" BLOB, "result.impl_schemas" TEXT, valid_flag INTEGER)
DEBUG:root:_gen_cfg_table_entry: Rows matching title LIKE 'LUTE Simple test' AND experiment LIKE 'dummy' AND run LIKE '' AND date LIKE '1970/01/01' AND lute_version LIKE '0.1' AND task_timeout LIKE '6000': [(3,)]
DEBUG:root:_exec_cfg_table_entry: No matching rows - adding new row: 6
DEBUG:lute.io._db.v1._sqlite:_add_task_entry: ['"gen_cfg_id"', '"exec_cfg_id"', '"float_var"', '"str_var"', '"compound_var.int_var"', '"compound_var.dict_var.a"', '"throw_error"', '"result.task_status"', '"result.summary"', '"result.payload"', '"result.impl_schemas"', '"valid_flag"']
                [3, 6, 1.1, 'test_var', 1, 'b', False, 'COMPLETED', 'Test Finished.', '', None, 1]
DEBUG:lute.execution.ipc:Stopping socket reader thread.
DEBUG:lute.execution.ipc:Closed reading thread.
INFO:lute.execution.executor:Exiting after Task completion.
```

When you first run a LUTE `Task` or workflow, the lute.db file is created in the `work_dir` and contains all the information about the task you just ran. Subsequent runs will be added to the database. You can take a look at it using the dbview utility:
```bash
> dbview -p <work_dir>/lute.db
```
You can navigate the dabase using the arrow keys and exit with `q` when done. And this concludes this example.
