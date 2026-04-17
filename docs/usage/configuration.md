# Task Configuration
## Preparing a Configuration YAML
All `Task`s are parameterized through a single configuration YAML file - even third party code which requires its own configuration files is managed through this YAML file. The basic structure is split into two documents, a brief header section which contains information that is applicable across all `Task`s, such as the experiment name, run numbers and the working directory, followed by per `Task` parameters:
```yaml
%YAML 1.3
---
title: "Some title."
experiment: "MYEXP123"
# run: 12 # Does not need to be provided
date: "2024/05/01"
lute_version: 0.2.0
task_timeout: 600
work_dir: "/sdf/scratch/users/d/dorlhiac"
...
---
TaskOne:
  param_a: 123
  param_b: 456
  param_c:
    sub_var: 3
    sub_var2: 4

TaskTwo:
  new_param1: 3
  new_param2: 4

# ...
...
```

In the first document, the header, it is important that the `work_dir` is properly specified. This is the root directory from which `Task` outputs will be written, and the LUTE database will be stored. It may also be desirable to modify the `task_timeout` parameter which defines the time limit for individual `Task` jobs. By default it is set to 10 minutes, although this may not be sufficient for long running jobs. This value will be applied to **all** `Task`s so should account for the longest running job you expect.

The actual analysis parameters are defined in the second document. As these vary from `Task` to `Task`, a full description will not be provided here. An actual template with real `Task` parameters is available in `config/test.yaml`. Your analysis POC can also help you set up and choose the correct `Task`s to include as a starting point. The template YAML file has further descriptions of what each parameter does and how to fill it out. You can also refer to the `lute_help` program described under the following sub-heading.

**Some things to consider and possible points of confusion:**

- While we will be submitting **managed** `Task`s, the parameters are defined at the `Task` level. I.e. the **managed** `Task` and `Task` itself have different names, and the names in the YAML refer to the latter. This is because a single `Task` can be run using different `Executor` configurations, but using the same parameters. The list of **managed** `Task`s is in `lute/managed_tasks.py`. A table is also provided below for some routines of interest..


| **Managed** `Task`       | The `Task` it Runs       | `Task` Description                                             |
|:------------------------:|:------------------------:|:--------------------------------------------------------------:|
| `SmallDataProducer`      | `SubmitSMD`              | Smalldata production                                           |
| `CrystFELIndexer`        | `IndexCrystFEL`          | Crystallographic indexing                                      |
| `PartialatorMerger`      | `MergePartialator`       | Crystallographic merging                                       |
| `HKLComparer`            | `CompareHKL`             | Crystallographic figures of merit                              |
| `HKLManipulator`         | `ManipulateHKL`          | Crystallographic format conversions                            |
| `DimpleSolver`           | `DimpleSolve`            | Crystallographic structure solution with molecular replacement |
| `PeakFinderPyAlgos`      | `FindPeaksPyAlgos`       | Peak finding with PyAlgos algorithm.                           |
| `PeakFinderPsocake`      | `FindPeaksPsocake`       | Peak finding with psocake algorithm.                           |
| `StreamFileConcatenator` | `ConcatenateStreamFiles` | Stream file concatenation.                                     |


### How do I know what parameters are available, and what they do?
A summary of `Task` parameters is available through the `lute_help` program.

```bash
> utilities/lute_help -t [TaskName]
```

Note, some parameters may say "Unknown description" - this either means they are using an old-style defintion that does not include parameter help, or they may have some internal use. In particular you will see this for `lute_config` on every `Task`, this parameter is filled in automatically and should be ignored. E.g. as an example:

```bash
> utilities/lute_help -t IndexCrystFEL
INFO:__main__:Fetching parameter information for IndexCrystFEL.
IndexCrystFEL
-------------
Parameters for CrystFEL's `indexamajig`.

There are many parameters, and many combinations. For more information on
usage, please refer to the CrystFEL documentation, here:
https://www.desy.de/~twhite/crystfel/manual-indexamajig.html


Required Parameters:
--------------------
[...]

All Parameters:
-------------
[...]

highres (number)
	Mark all pixels greater than `x` has bad.

profile (boolean) - Default: False
	Display timing data to monitor performance.

temp_dir (string)
	Specify a path for the temp files folder.

wait_for_file (integer) - Default: 0
	Wait at most `x` seconds for a file to be created. A value of -1 means wait forever.

no_image_data (boolean) - Default: False
	Load only the metadata, no iamges. Can check indexability without high data requirements.

[...]
```

`lute_help` can also be used to retrieve a list of all currently available `Task`s. (Truncated for brevity)

```bash
> ./lute/utilities/lute_help -l
INFO:__main__:Fetching Task list.
Task List
---------

- AnalyzeSmallDataXAS
  Current Managed Tasks: SmallDataXASAnalyzer,
	TaskParameter model for AnalyzeSmallDataXAS Task.

	This Task does basic analysis of XAS data based on a SmallData HDF5 output
	file. It calculates difference absorption and signal binned by various
	scanned motors.

- AnalyzeSmallDataXES
  Current Managed Tasks: SmallDataXESAnalyzer,
	TaskParameter model for AnalyzeSmallDataXES Task.

	This Task does basic analysis of XES data based on a SmallData HDF5 output
	file. It calculates difference emission and signal binned by various
	scanned motors.
```

**NOTE:** Associated **managed** `Task`s are provided under each `Task` name. These will be the names passed to submission scripts.

## Variable Substitution in YAML Files
Using `validator`s, it is possible to define (generally, default) model parameters for a `Task` in terms of other parameters. It is also possible to use validated Pydantic model parameters to substitute values into a configuration file required to run a third party `Task` (e.g. some `Task`s may require their own JSON, TOML files, etc. to run properly). For more information on these types of substitutions, refer to the `Creating a new Task` documentation on `Task` creation (in the Developer Documentation).

These types of substitutions, however, have a limitation in that they are not easily adapted at run time. They therefore address only a small number of the possible combinations in the dependencies between different input parameters. In order to support more complex relationships between parameters, variable substitutions can also be used in the configuration YAML itself. Using a syntax similar to `Jinja` templates, you can define values for YAML parameters in terms of other parameters or environment variables. The values are substituted before Pydantic attempts to validate the configuration.

It is perhaps easiest to illustrate with an example. A test case is provided in `config/test_var_subs.yaml` and is reproduced here:

```yaml
%YAML 1.3
---
title: "Configuration to Test YAML Substitution"
experiment: "TestYAMLSubs"
run: 12
date: "2024/05/01"
lute_version: 0.2.0
task_timeout: 600
work_dir: "/sdf/scratch/users/d/dorlhiac"
...
---
OtherTask:
  useful_other_var: "USE ME!"

NonExistentTask:
  test_sub: "/path/to/{{ experiment }}/file_r{{ run:04d }}.input"         # Substitute `experiment` and `run` from header above
  test_env_sub: "/path/to/{{ $EXPERIMENT }}/file.input"                   # Substitute from the environment variable $EXPERIMENT
  test_nested:
    a: "outfile_{{ run }}_one.out"                                        # Substitute `run` from header above
    b:
      c: "outfile_{{ run }}_two.out"                                      # Also substitute `run` from header above
      d: "{{ OtherTask.useful_other_var }}"                               # Substitute `useful_other_var` from `OtherTask`
  test_fmt: "{{ run:04d }}"                                               # Subsitute `run` and format as 0012
  test_env_fmt: "{{ $RUN:04d }}"                                          # Substitute environment variable $RUN and pad to 4 w/ zeros
...
```

Input parameters in the config YAML can be substituted with either other input parameters or environment variables, with or without limited string formatting. All substitutions occur between double curly brackets: `{{ VARIABLE_TO_SUBSTITUTE }}`. Environment variables are indicated by `$` in front of the variable name. Parameters from the header, i.e. the first YAML document (top section) containing the `run`, `experiment`, version fields, etc. can be substituted without any qualification. If you want to use the `run` parameter, you can substitute it using `{{ run }}`. All other parameters, i.e. from other `Task`s or within `Task`s, must use a qualified name. Nested levels are delimited using a `.`. E.g. consider a structure like:

```yaml
Task:
  param_set:
    a: 1
    b: 2
    c: 3
```
In order to use parameter `c`, you would use `{{ Task.param_set.c }}` as the substitution.

Take care when using substitutions! This process will not try to guess for you. When a substitution is not available, e.g. due to misspelling, one of two things will happen:

- If it was an environment variable that does not exist, no substitution will be performed, although a message will be printed. I.e. you will be left with `param: /my/failed/{{ $SUBSTITUTION }}` as your parameter. This may or may not fail the model validation step, but is likely not what you intended.
- If it was an attempt at substituting another YAML parameter which does not exist, an exception will be thrown and the program will exit.

**Defining your own parameters**

The configuration file is **not** validated in its totality, only on a `Task`-by-`Task` basis, but it **is read** in its totality. E.g. when running `MyTask` only that portion of the configuration is validated even though the entire file has been read, and is available for substitutions. As a result, it is safe to introduce extra entries into the YAML file, as long as they are not entered under a specific `Task`'s configuration. This may be useful to create your own global substitutions, for example if there is a key variable that may be used across different `Task`s.
E.g. Consider a case where you want to create a more generic configuration file where a single variable is used by multiple `Task`s. This single variable may be changed between experiments, for instance, but is likely static for the duration of a single set of analyses. In order to avoid a mistake when changing the configuration between experiments you can define this special variable (or variables) as a separate entry in the YAML, and make use of substitutions in each `Task`'s configuration. This way the variable only needs to be changed in one place.

```yaml
# Define our substitution. This is only for substitutiosns!
MY_SPECIAL_SUB: "EXPMT_DEPENDENT_VALUE"  # Can change here once per experiment!

RunTask1:
  special_var: "{{ MY_SPECIAL_SUB }}"
  var_1: 1
  var_2: "a"
  # ...

RunTask2:
  special_var: "{{ MY_SPECIAL_SUB }}"
  var_3: "abcd"
  var_4: 123
  # ...

RunTask3:
  special_var: "{{ MY_SPECIAL_SUB }}"
  #...

# ... and so on
```

### Gotchas!
**Order matters**

While in general you can use parameters that appear later in a YAML document to substitute for values of parameters that appear earlier, the substitutions themselves will be performed in order of appearance. It is therefore **NOT possible** to correctly use a later parameter as a substitution for an earlier one, if the later one itself depends on a substitution. The YAML document, however, can be rearranged without error. The order in the YAML document has no effect on execution order which is determined purely by the workflow definition. As mentioned above, the document is not validated in its entirety so rearrangements are allowed. For example consider the following situation which produces an incorrect substitution:


```yaml
%YAML 1.3
---
title: "Configuration to Test YAML Substitution"
experiment: "TestYAMLSubs"
run: 12
date: "2024/05/01"
lute_version: 0.2.0
task_timeout: 600
work_dir: "/sdf/data/lcls/ds/exp/experiment/scratch"
...
---
RunTaskOne:
  input_dir: "{{ RunTaskTwo.path }}"  # Will incorrectly be "{{ work_dir }}/additional_path/{{ $RUN }}"
  # ...

RunTaskTwo:
  # Remember `work_dir` and `run` come from the header document and don't need to
  # be qualified
  path: "{{ work_dir }}/additional_path/{{ run }}"
...
```

This configuration can be rearranged to achieve the desired result:

```yaml
%YAML 1.3
---
title: "Configuration to Test YAML Substitution"
experiment: "TestYAMLSubs"
run: 12
date: "2024/05/01"
lute_version: 0.2.0
task_timeout: 600
work_dir: "/sdf/data/lcls/ds/exp/experiment/scratch"
...
---
RunTaskTwo:
  # Remember `work_dir` comes from the header document and doesn't need to be qualified
  path: "{{ work_dir }}/additional_path/{{ run }}"

RunTaskOne:
  input_dir: "{{ RunTaskTwo.path }}"  # Will now be /sdf/data/lcls/ds/exp/experiment/scratch/additional_path/12
  # ...
...
```

On the otherhand, relationships such as these may point to inconsistencies in the dependencies between `Task`s which may warrant a refactor.

**Found unhashable key**

To avoid YAML parsing issues when using the substitution syntax, be sure to quote your substitutions. Before substitution is performed, a dictionary is first constructed by the `pyyaml` package which parses the document - it may fail to parse the document and raise an exception if the substitutions are not quoted.
E.g.
```yaml
# USE THIS
MyTask:
  var_sub: "{{ other_var:04d }}"

# **DO NOT** USE THIS
MyTask:
  var_sub: {{ other_var:04d }}
```

During validation, Pydantic will by default cast variables if possible, because of this it is generally safe to use strings for substitutions. E.g. if your parameter is expecting an integer, and after substitution you pass `"2"`, Pydantic will cast this to the `int` `2`, and validation will succeed. As part of the substitution process limited type casting will also be handled if it is necessary for any formatting strings provided. E.g. `"{{ run:04d }}"` requires that run be an integer, so it will be treated as such in order to apply the formatting.
