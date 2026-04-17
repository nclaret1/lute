# Converting XTC1 to XTC2 Files - `ConvertXtc1to2`
## Command-line help

As a reminder, the `lute_help` command-line utility may be used to inspect the full set of command-line arguments. After sourcing the activation script upon building LUTE you will have the utility in your PATH.

```bash
# Assume the current working directory is the top of the LUTE repo, and the build
# script was run
> source install/bin/activate_installation
> lute_help -T <task>
```

For help on the conversion `Task`, you can run:

```bash
> lute_help -T ConvertXtc1to2
# ...

ConvertXtc1to2
--------------
Parameters for the xtc conversion Task.


Required Parameters:
--------------------
lute_config (AnalysisHeader)
	Unknown description.

geometry (string)
	Geometry file

output_file (string)
	Where to write the output XTC2 file.

xtc1_access_pattern (object)
	Provides information for how to access the data in XTC1. The top level keys will be used as the detector names in the XTC2 data.

# ...
```

## Managed `Task`s
There is currently only 1 **managed** `Task` that runs this specific `Task`: `Xtc1to2Converter`. It runs in either the psana1 or psana2 base environment since it will be launch and manage the psana1 and psana2 environments separately to control the reading and writing for the conversion process.

## Configuration
A starting YAML for the conversion process may look like:

```yaml
ConvertXtc1to2:
  node_id: "1"
  eventfile: ""
  nevents: 120
  output_file: "{{ XTC2_FILE_PATH }}/{{ XTC2_FILE_NAME }}"
  xtc1_access_pattern:
    epix10k2M: # Name of the detector in the converted XTC2
    # You can have a list of attributes you will convert that will be stored in
    # this detector
      - xtc2_attr_name: "calib"        # Name of this attribute in xtc2
        object_name: "epix10k2M"       # Name of the detector in psana1
        object_type: "psana.Detector"  # Name of the object type in psana1
        object_field_name: "calib"     # Name of the per-event method to use in psana1
    EBeam:
      - xtc2_attr_name: "photon_energy"
        object_name: "EBeam"
        object_type: "psana.Detector"
        object_field_name: ["get","ebeamPhotonEnergy"]
```

For the most part, `node_id` can be left as is. We will cover the various other parameters in detail below. The most important being the `xtc1_access_pattern` covered at the end.

The documentation here assumes familiarity with both psana1 and psana2.

### Implicit parameters

Like many other `Task`s, this conversion `Task` will select data based on the global `experiment` and `run` provided. This are either provided explicitly in the header document of the YAML file, or are retrieved by environment variable when running from the ARP/eLog. Alternatively, they are passed on the command-line using `-e` and `-r` command-line arguments.

### Selecting events: `eventfile` and `nevents`
The default behaviour for the `Task` is to convert all the events in the XTC1 file(s) for the LCLS1 experiment to XTC2. You can optionally select a portion of these events using `eventfile` or `nevents`.

- `eventfile`: Is the path to a file that contains a list of **indices** for the events. This file should be a **CSV** file (comma-separated file).
- `nevents`: As an alternative, a specific number of events can instead be provided. This should be a single integer.

If both `eventfile` and `nevents` are provided, `eventfile` takes precedence.

### Output location: `output_file`

A single large XTC2 file will be written by this `Task`, and this parameter determines where that file will be written.

**NOTE:** There are restrictions on the format of this path!

1. The filename must have a structure like: `<exp>-r<run:04d>-s000-c000.xtc2`
2. The path must include the standard psana2 search structure, namely the final portion must be: `<hutch>/<experiment>/xtc/<filename>`.

  - For example, a MFX experiment MFX12345, running on run 42, should have a filename and output path that looks like: `/rest_of_path/mfx/mfx12345/xtc/mfx12345-r0042-s000-c000.xtc2` . The rest of the path at the beginning is free to choose (as long as it is a valid path).

### Specifying what to convert and how: `xtc1_access_pattern`

The main portion of the configuration is under this parameter. To understand how it works we will work from the provided example above:

```yaml
  xtc1_access_pattern:
    epix10k2M: # Name of the detector in the converted XTC2
    # You can have a list of attributes you will convert that will be stored in
    # this detector
      - xtc2_attr_name: "calib"        # Name of this attribute in xtc2
        object_name: "epix10k2M"       # Name of the detector in psana1
        object_type: "psana.Detector"  # Name of the object type in psana1
        object_field_name: "calib"     # Name of the per-event method to use in psana1
    EBeam:
      - xtc2_attr_name: "photon_energy"
        object_name: "EBeam"
        object_type: "psana.Detector"
        object_field_name: ["get","ebeamPhotonEnergy"]
```

The top level keys, `epix10k2M` and `EBeam`, describe what you want the names of the detectors to be in the new converted XTC2 files. I.e. you will be able to access the data from psana2 using `run.Detector("epix10k2M")` and `run.Detector("EBeam")`.

Under each detector you define a **list of dictionaries**. Each **dictionary** in the list describes one component from the XTC1 data that you will want to translate into XTC2, and the manner in which you will do the translation. The keys of the dictionary are:

1. `xtc2_attr_name`: This will be the name of the method used to access the data from **psana2**.
2. `object_name`: This is the name of the object in **psana1**
3. `object_type`: This is the type of object in **psana1**
4. `object_field_name`: This is the method (or methods) used to access the data in **psana1**.

So, taking the example for `epix10k2M` above, we are translating a single method from XTC1 to XTC2. The specification says that we will take the `calib` method from a psana1 object `psana.Detector("epix10k2M")` and attach it to a `calib` method for a psana2 object.

Alternatively, in pseudo-Python we have:

```python
# Take this psana1 data
object_type("object_name").object_field_name

# And translate it to:
epix10k2M.xtc1dump.calib
```

**NOTE:** The converted data is always provided on a special "algorithm" called `xtc1dump`. So for the example above, you are creating the `calib` method for that algorithm. I.e.:

```python
# psana1 access of the form
det = psana.Detector("epix10k2M")
data = det.calib(evt)

# is translated into psana2 access of the form
det = run.Detector("epix10k2M")
data = det.xtc1dump.calib(evt)
```

The example for `EBeam` is analogous, however it showcases a more involved case for psana1 access patterns. In this case, the data will be accessed from psana1 as follows:

```python

det = psana.Detector("epix10k2M")
data = det.get(evt).ebeamPhotonEnergy()
```

That is, the first item in the list of `object_field_name` is the method that takes the psana1 event object. The second object in the list is called without argument.
