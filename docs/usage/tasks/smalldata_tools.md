# Running smalldata_tools - `SubmitSMD`
## Command-line help

As a reminder, the `lute_help` command-line utility may be used to inspect the full set of command-line arguments. After sourcing the activation script upon building LUTE you will have the utility in your PATH.

```bash
# Assume the current working directory is the top of the LUTE repo, and the build
# script was run
> source install/bin/activate_installation
> lute_help -T <task>
```

For help on smalldata_tools, you can run:

```bash
> lute_help -T SubmitSMD
# ...

ROIParams:
ROI (array)
	Definition of ROIs, can define multiple.

writeArea (boolean) - Default: False
	Whether to write out the area image of the ROI.

# ...
```

## Managed `Task`s
smalldata_tools (via the `Task` `SubmitSMD`) can be run in various environments using different **managed** `Task`s:

- `SmallDataProducer`: Run the psana1 smalldata_tools production
- `SmallDataProducer2`: Run the psana2 smalldata_tools production
- `SmallDataProducerSpack`: Run the psana2 smalldata_tools production, **in the spack environment**. This is required for compression features.

## Configuration

The starting YAML for smalldata_tools may look like:

```yaml
SubmitSMD:
  # Command line arguments
  #map_by: "core"   # MPI resource mapping - take care with changing unless familiar
  #bind_to: "core"  # MPI resource binding - take care with changing unless familiar
  #np: 5
  #producer: "/path/to/producer"
  #run: "{{ run }}"
  #experiment: "{{ experiment }}"
  #stn: 0
  #nevents: 5
  #interactive: false
  #directory: "/output/hdf5/directory"
  #psdm_dir: "/special/sit_psdm_data/directory"
  #config: "mfx_cctbx"
  #gather_interval: 25
  #norecorder: False
  #url: "https://pswww.slac.stanford.edu"
  #epicsAll: False
  #full: False
  #fullSum: False
  #default: true
  #image: False
  #tiff: False
  #centerpix: False
  #postRuntable: False
  #wait: False
  #xtcav: False
  #noarch: False
  # Producer variables. These are substituted into the producer to run specific
  # data reduction algorithms. Uncomment and modify as needed.
  # If you prefer to modify the producer file directly, leave commented.
  # Beginning with `getROIs`, you will need to modify the first entry to be a
  # detector. This detector MUST MATCH one of the detectors in `detnames`.
  # In the future this will be automated. If you have multiple detectors you can
  # add them with their own set of parameters.
  #detnames: ["epix10k2M"]
  #epicsPV: []
  #ttCalib: []
  #getPressioCompression:
  #  epix10k2M:
  #    compressor_id: "sz3"
  #    # Specific arguments vary depending on compressor_id
  #    compressor_args:
  #      abs_error_bound: 10
  # Provide detector image sum algorithms.
  # If detSumAlgos is not defined, it will default to calib, calib_dropped and
  # calib_dropped_square for every detector. You can add processing algorithms for
  # single detectors (e.g. epix10k2M or Rayonix) as below, or add algorithms which
  # will apply to every detector defined in detnames by placing them under "all"
  #detSumAlgos:
  #  all:
  #    - "calib"
  #    - "calib_dropped"
  #    - "calib_dropped_square"
  #    - "calib_thresADU1"
  #  epix10k2M:
  #    - "calib_thresADU5"
  #    - "calib_max"
  #  Rayonix:
  #    - "calib_skipFirst_thresADU1"
  #    - "calib_skipFirst_max"
  #getROIs:
  #  jungfrau1M:   # Change to detector name
  #    - ROI: [[[1, 2], [157, 487], [294, 598]]]
  #      #name: "abcd" # Providing a name is only required if you are creating multiple ROIs
  #      writeArea: True   # Whether to save ROI, if False, save sum but not img.
  #      thresADU: None
  #      calcPars: True
  #getAzIntParams:
  #  jungfrau:
  #    eBeam: 18
  #    center: [87526.79161840, 92773.3296889500]
  #    dis_to_sam: 80.0
  #    tx: 0
  #    ty: 0
  #    ADU_per_Photon: 1.0
  #    phiBins: 1
  #    qbin: 5e-3
  #    thresRms: null
  #    thresADUhigh: null
  #    geomCorr: true
  #    polCorr: true
  #    userMask: "/path/to/numpy_array.
  #getAzIntPyFAIParams:
  #  Rayonix:
  #    pix_size: 176e-6
  #    ai_kwargs:
  #      dist: 1
  #      poni1: 960 * 1.76e-4
  #      poni2: 960 * 1.76e-4
  #    npts: 512
  #    int_units: "2th_deg"
  #    return2d: False
  #getPhotonsParams:
  #  jungfrau1M:
  #    ADU_per_photon: 9.5
  #    thresADU: 0.8
  #getDropletParams:
  #  epix_1:
  #    threshold: 5
  #    thresholdLow: 5
  #    thresADU: 60
  #    useRms: True
  #    nData: 1e5
  #getDroplet2Photons:
  #  epix_alc1:
  #    droplet:
  #      threshold: 10
  #      thresholdLow: 3
  #      thresADU: 10
  #      useRms: True
  #    d2p:
  #      aduspphot: 162
  #      mask: np.load('path_to_mask.npy')
  #      cputime: True
  #    nData: 3e4
  #getSvdParams:
  #  acq_0:
  #    basis_file: None
  #    n_pulse: 1
  #    delay: None
  #    return_reconstructed: True
  #getAutocorrParams:
  #  epix_2:
  #    mask: "/sdf/home/e/example/dataAna/mask_epix.npy"
  #    thresAdu: [72.0, 1.0e6]
  #    save_range: [70, 50]
  #    save_lineout: True
```

This set of parameters can be split into two sections, the command-line options, and the producer parameters. These can be discussed separately.

### Command-line arguments

The following parameters control the behaviour of the job submission:

```yaml
  # Command line arguments
  #map_by: "core"   # MPI resource mapping - take care with changing unless familiar
  #bind_to: "core"  # MPI resource binding - take care with changing unless familiar
  #np: 5
  #producer: "/path/to/producer"
  #run: "{{ run }}"
  #experiment: "{{ experiment }}"
  #stn: 0
  #directory: "/output/hdf5/directory"
  #psdm_dir: "/special/sit_psdm_data/directory"
  #config: "mfx_cctbx"
  #gather_interval: 25
  #norecorder: False
  #url: "https://pswww.slac.stanford.edu"
  #epicsAll: False
  #full: False
  #fullSum: False
  #default: true
  #image: False
  #tiff: False
  #centerpix: False
  #postRuntable: False
  #wait: False
  #xtcav: False
  #noarch: False
```

For normal workflow operation, you probably do not need to modify any of these. However, you may be interested in setting some of them for testing. In particular:

- `directory`: You can provide a separate output directory for the produced HDF5 files.
- `nevents`: Provide a maximum number of events to process. Set this to be between 5 and 10 to run interactively.
- `interactive`: Can set this to true to run "interactively" (i.e., if not submitting SLURM jobs)
- `np`: The number of ranks/processes to use.

As mentioned, in normal operation (in the context of a workflow, or SLURM submissions) you likely do not need to modify these. Sensible defaults exist, and where appropriate the values will also be computed as necessary.

You may also want to specify `config` if you have multiple possible configuration files to use.

The options for MPI resource mappings may be useful but care should be taken when modifying them:

- `map_by` selects how MPI maps the ranks to various resources (cores, sockets).
- `bind_to` changes how MPI then **restricts** (i.e. binds) the ranks to those resources.

### Production parameters

#### ROI Selection: `getROIs`

One or more ROIs can be defined on a per detector basis using the parameters defined in this block:

```yaml
  getROIs:
    jungfrau1M:   # Change to detector name
      - ROI: [[[1, 2], [157, 487], [294, 598]]]
        #name: "abcd" # Providing a name is only required if you are creating multiple ROIs
        writeArea: True   # Whether to save ROI, if False, save sum but not img.
        thresADU: None
        calcPars: True
```

Note that under each detector there is a **list** of dictionaries because multiple ROIs can be defined. For each ROI, the parameters are as follows:

- `ROI`: This defines the ROI. The format is a list of lists, where the inner lists define a set of indices for each dimension of the array of the data that the ROI will span. E.g. `[[1,2],[157,487],[294,598]]` defines an ROI spanning 1-2 (so the index 1) of the first dimension, 157-487 of the second dimension and 294-598 of the third dimension.

  - **Note:** You can alternatively set this option to `null` (as in, `ROI: null`). Instead of defining an ROI based on indices, `smalldata_tools` will take this to mean that you want to save the full detector image. As always, think if you really need to do this before choosing to do so. Things will take longer to process, will use more memory, and the data will double in size when saved to disk.

- `name`: Provides a name for the ROI. There will be a default name; however, if you are defining multiple ROIs you should provide a unique name for each of them, otherwise they will overwrite each other.
- `thresADU`: A threshold to add to the ROI.
- `calcPars`: Whether to output some summary statistics (mean, max, etc.) along with the ROI.
- `writeArea`: A boolean indicating whether to write out the area, or just the sum of the ROI.

#### Azimuthal integration: `getAzIntParams` and `getAzIntPyFAIParams`

Two algorithms for azimuthal integration are provided, the home grown implementation via `getAzIntParams` and the PyFAI implementation using `getAzIntPyFAIParams`. The two can be run simultaneously if really needed.

```yaml
  #getAzIntParams:
  #  jungfrau:
  #    eBeam: 18
  #    center: [87526.79161840, 92773.3296889500]
  #    dis_to_sam: 80.0
  #    tx: 0
  #    ty: 0
  #    ADU_per_Photon: 1.0
  #    phiBins: 1
  #    qbin: 5e-3
  #    thresRms: null
  #    thresADUhigh: null
  #    geomCorr: true
  #    polCorr: true
  #    userMask: "/path/to/numpy_array.
  #getAzIntPyFAIParams:
  #  Rayonix:
  #    pix_size: 176e-6
  #    ai_kwargs:
  #      dist: 1
  #      poni1: 960 * 1.76e-4
  #      poni2: 960 * 1.76e-4
  #    npts: 512
  #    int_units: "2th_deg"
  #    return2d: False
```

#### Compression Verification

Via libpressio (if using the `SmallDataProducerSpack` **managed** `Task` with the spack environment) a simple compression/decompression operation is provided. This will compress and immediately decompress the data before performing any other operations. The purpose of this is to provide a method for verifying whether the compression has any effect on the resultant output data. The configuration is as follows:

```yaml
  getPressioCompression:
    epix10k2M:
      compressor_id: "sz3"
      # Specific arguments vary depending on compressor_id
      compressor_args:
        abs_error_bound: 10
```

These options are again provided per detector. The first option is the `compressor_id`. This is currently only `sz3`, however additional compressors may be supported in the future.

For the specific compressor selected a set of arguments may be provided under `compressor_args`.

- For `sz3` the following arguments are supported:

  - `abs_error_bound`: Provides the absolute bound on the error for the lossy compression.
