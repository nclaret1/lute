# Quick Start
`lute`, or `LUTE`, is the LCLS Unified Task Executor - an automated workflow package for running analysis pipelines at SLAC's LCLS. This project is the next iteration of `btx`.

This package is used to run arbitrary analysis code (first-party or third-party) in the form of individual analysis `Task`s. `Task`s can be linked together to form complete end-to-end analysis pipelines or workflows. For workflow management, the package interfaces with Airflow running on S3DF.

## Setup
### Getting LUTE
#### On S3DF for experiment analysis
You, or your analysis point-of-contact should run the provided `setup_lute` script located at `/sdf/group/lcls/ds/tools/lute/dev/lute/utilities/setup_lute`

Usage: `setup_lute [-h] [-d] -e EXPERIMENT [-f] [--test] [-v VERSION] [-w WORKFLOW] [SLURM_ARGS ...]`

Parameters:

- `-e EXPERIMENT, --experiment EXPERIMENT`: The experiment to perform setup for.
- `-f, --fresh_install`: Install a new version of LUTE in the experiment folder. This allows for local modifications of code. Otherwise, the central installation will be used (which cannot be modified).
- `--test`: Use test Airflow instance. Only needed for bleeding-edge workflows.
- `-v VERSION, --version VERSION`: Version of LUTE to use. Corresponds to release tag or `dev`. Defaults to `dev`.
- `-W WORKFLOW, --workflow WORKFLOW`: Which analysis workflow to run. Defaults to `smd`.
- `-d, --debug`: Turn on verbose logging.
- `[SLURM ARGS]`: This is any number of SLURM arguments you want to run your workflow with. You will likely want to provide `--ntasks` at a minimum.

The only required argument is `-e <EXPERIMENT>`. This should be the experiment you are setting up for. You will likely also want to provide the `-W <WORKFLOW>` argument. These are written in YAML, and will be copied in from the `workflows/common` directory (see description below). Because these are YAML, it is recommended (and highly likely) that you will modify the definition slightly.

The example starting points are:

- `bayfai` : Perform geometry optimization using BayFAI.
- `sfx` : Perform end-to-end SFX analysis with molecular replacement.
- `smd` : Run managed `smalldata_tools` and downstream analysis/summaries.

Providing SLURM arguments is not required, but **highly recommended**. The setup script will try to set some default values for `--partition`, `--account`, and `--ntasks`, depending on the experiment and workflow you are running. If these three arguments are not provided, it will prompt you for each one and tell you the default it has selected. Press enter (or any key) to accept. Otherwise, press `Ctrl-C` to exit the setup, and pass the arguments manually.

The `setup_lute` script will create the eLog job for your selected workflow. Results will be presented back to you in the eLog. The script will also produce a configuration file which will live at `/sdf/data/lcls/ds/<hutch>/<experiment>/results/lute_output/<hutch>.yaml`. You will want to modify this configuration prior to running. See [Basic Usage](/#basic-usage) below for more information.

#### Locally or otherwise
LUTE is publically available on [GitHub](https://github.com/slac-lcls/lute). In order to run it, the first step is to clone the repository:

```bash
# Navigate to the directory of your choice.
git clone@github.com:slac-lcls/lute
```
The repository directory structure is as follows:

```
lute
  |--- config             # Configuration YAML files (see below) and templates for third party config
  |--- docs               # Documentation (including this page)
  |--- extensions         # Python extensions (written in C and C++), includes algorithms like Peakfinder8
  |--- launch_scripts     # Entry points for using SLURM and communicating with Airflow
  |--- lute               # Code
        |--- run_task.py  # Script to run an individual managed Task
        |--- ...
  |--- subprojects        # Vendored dependencies (should not be relevant for most users)
  |--- tests              # As you'd expect
  |--- utilities          # Help utility programs
  |--- workflows          # This directory contains workflow definitions. It is synced elsewhere and not used directly.
        |--- airflow      # DAGs and components written for the Airflow backend
        |--- common       # Examples (only examples!) for YAML DAGs
        |--- maestro      # *** Contains the source code for the `maestro` workflow manager
        |--- prefect      # DAGs and components written for the Prefect backend
```

In general, most interactions with the software will be through scripts located in the `launch_scripts` directory. Some users (for certain use-cases) may also choose to run the `run_task.py` script directly - it's location has been highlighted within hierarchy. To begin with you will need a YAML file, templates for which are available in the `config` directory. The structure of the YAML file and how to use the various launch scripts are described in more detail in the Usage and Developer documentation on this site.

## Basic Usage
### Overview
LUTE runs code as `Task`s that are managed by an `Executor`. The `Executor` provides modifications to the environment the `Task` runs in, as well as controls details of inter-process communication, reporting results to the eLog, etc. Combinations of specific `Executor`s and `Task`s are already provided, and are referred to as **managed** `Task`s.

**Managed** `Task`s are submitted as a single unit. They can be run individually, or a series of independent steps can be submitted all at once in the form of a workflow, or **directed acyclic graph** (**DAG**). This latter option makes use of [maestro](/development/maestro), a workflow manager built for this project, to manage the individual execution steps. Additional backends, such as Airflow and Prefect, are also supported, but are not the default.

Running analysis with LUTE is the process of submitting one or more **managed** `Task`s. This is generally a two step process.

1. First, a configuration YAML file is prepared. This contains the parameterizations of all the `Task`s which you may run.
2. Individual **managed** `Task` submission, or workflow (**DAG**) submission.

**Note:** You configure `Task`s, but submit **managed** `Task`s. If you run the help utility described below, and in the usage manual, you can find which **managed** `Task`s correspond to which `Task`s. In general, `Task`s are verbs and the `Executor`s that run them (i.e. **managed** `Task`s) are nouns. E.g. the `FindPeaksSFX` `Task`, is run by submitting the `PeakFinderSFX` **managed** `Task`. There may be many **managed** `Task`s which submit the same underlying `Task`, but in different environments.

### Config YAML
If you ran the `setup_lute` script you will already have a `<hutch>.yaml` file located in your experiment results folder. This YAML file is commented by `Task`. You will **need** to modify a few of these parameters for some of the `Task`s. E.g. a partial example of the config file may look like this:

```yaml
%YAML 1.3
---
title: "Doing Peakfinding" # Include experiment description if desired
experiment: "{{ $EXPERIMENT }}"
#run: "{{ $RUN }}"
date: "2023/10/25"
lute_version: 0.2.0      # Do not be change unless need to force older version
task_timeout: 6000
work_dir: ""
...
---
###########
# SFX
###########

# You must provide det_name and pv_camera_length
# pv_camera_length is the detector distance
# It can be a PV, or alternative a float for a fixed offset
# Example PVs are commented below, ask beamline staff for relevant PV
# Change outdir to an appropriate directory for CXI files.
FindPeaksSFX:
  algorithm: "Peakfinder8"
  outdir: "/path/to/cxi_out"
  det_name: "epix10k2M"
  event_receiver: "evr0"
  tag: "lyso"
  event_logic: false
  psana_mask: false
  mask_file: null
  min_peaks: 10
  max_peaks: 2048
  npix_min: 2
  npix_max: 30
  amax_thr: 40
  atot_thr: 180
  son_min: 10.0
  peak_rank: 3
  r0: 3.0
  dr: 2.0
  nsigm: 10.0
  #pv_camera_length: "MFX:ROB:CONT:POS:Z"  # MFX epix10k2M
  make_powder_plots: true
  geometry_file: /path/to/my/crystfel.geom
```

If you are running from the eLog you do not need to modify the first section at all, unless you want to provide a title for your experiment. You may want to adjust the `task_timeout` if you believe some of your `Task`s are long running. It is in units of seconds. You can change the `work_dir` parameter if you want to write output to some other location, when running the setup script it will be automatically set to your experiment results folder.

The provided comments and `Task` name should be enough to get you started. If there are any questions, first try running the utilities described below, then refer to the more in depth usage manual on `Task` configuration located in this documentation. Contact the maintainers if anything is still unclear.

If you are attempting to create a configuration file from scratch refer first to the usage manual.

### A note on utilties
In the `utilities` directory (in the main `lute` directory) there are two useful programs to provide assistance with using the software:

- `utilities/dbview`: LUTE stores all parameters for every analysis routine it runs (as well as results) in a database. This database is stored in the `work_dir` defined in the YAML file. The `dbview` utility is a TUI application (Text-based user interface) which runs in the terminal. It allows you to navigate a LUTE database using the arrow keys, etc. Usage is: `utilities/dbview -p <path/to/lute.db>`. You will only have a database after your first **managed** `Task` completes (whether it succeeds or not). (**NOTE:** If using the new database specification v0.2, you can additionally pass the `--summarize` option to `utilities/dbview`. This will pivot and reorganize the data since the table structure is harder to parse at a glance in the new specification.)
- `utilities/help`: Source code here builds the `lute_help` utility. This utility provides help and usage information for running LUTE software. It provides access to parameter descriptions to assist in properly filling out a configuration YAML. It's usage is described in slightly more detail in the `Usage Manual`. Briefly you can run `lute_help -t <TaskName>` to retrieve parameters for a single `Task` (to put in your configuration YAML), or `lute_help -l` to list all `Task`s (and their associated **managed** `Task`s).
- `utilities/setup`: Source code for building the setup script described at the top.
