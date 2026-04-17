# Installing `LUTE`
## Source Code

The source code for `LUTE` is available on GitHub at [slac-lcls/lute](https://github.com/slac-lcls/lute).

It can therefore be retrieved on the command-line using:
```bash
> git clone git@github.com:slac-lcls/lute
# or git clone https://github.com/slac-lcls/lute.git for https
```

## Building the Package

At the top level of the directory is a script `build.sh` for building the code on S3DF.

You can run it using:
```bash
> ./build.sh -e # in top level of the repository you just cloned
```

This will create an `install` directory inside the top-level of `LUTE`, and build and install all components there.

The `build.sh` script will create a build environment and cache it in your home directory (under `~/.cache/lute_build_env_XXXX_{PYVER}`). The full path is determined from a hash of the installation location. Caching this build environment speeds up subsequent re-builds significantly; however, it can be deleted by passing `-c` to the `build.sh` script when it is run. The full set of options for this script are:

```bash
> ./build.sh -h
build.sh:
    Build an installation of LUTE.

    This build script will create an isolated build environment. It is cached in
    your home directory under ~/.cache/lute_build_env_XXXX where the final portion
    is created from a hash of base installation directory.

    Subsequent runs of the build will not need to re-create the build environment,
    speeding up the process significantly. You can of course delete the build environment
    from the specified folder at any time, and it will be recreated next time the script
    is run. You can pass a parameter to this script to do cleanup as well.

    Options:
        -c|--clean
          Clean up the build environment
        -h|--help
          Display this message.

    Options that apply on subsequent runs of the build script:
        -e|--entry_points
          Re-run the pip install command. This is only needed if pyproject.toml is
          modified.
        -r|--reconfigure
          Re-run the meson setup. This is only required if meson.build files have been
          modified, or meson options/the install prefix have changed since the last
          time it was run.
        -s|--source_pyenv
          Use an additional source environment. Can be used to build multiple versions
          of the extensions. E.g. to have 3.9 and 3.11 Python extension libraries.
```

The installation can be "activated" after being built. This will put all the relevant scripts and binaries into your path.

```bash
> source install/bin/activate_installation
```

This activation script is sourced automatically by submission scripts to make the LUTE code available to various job steps.


### Important Notes

- You should run the script the first time using the `-e` flag - this will create the Python entry points, i.e. the executables you will use.
- You may want to provide the `--source_pyenv` flag and build a **second** time. This allows you to have multiple versions of LUTE for different Python versions. LUTE is capable of submitting Tasks across Python versions; however, any C-extensions require that a version be compiled for the target `Task` Python. See example below.

### Running for Different Python Versions
You may find that you have a first-party `Task` that requires an environment that uses a different Python version than the base-environment that LUTE uses. In this case, you can build LUTE for both the base environment, and the target `Task` environment.

An example of this sort of setup is when runnning the `FindPeaksSFXXpp` `Task`s. The base environment is Python 3.9, but the `..._xpp.sh` environments are Python 3.11. The peak finding algorithms are C-extensions so need to be compiled against that interpreter version.

To deal with this situation, you will build twice.

```bash
# First time pass `-e` to get the entry-points/executables
> ./build.sh -e
# ... building ...
> ./build.sh --source_pyenv /sdf/group/lcls/ds/ana/sw/conda2/manage/bin/xpp_drp_cpu.sh
# ... building ...
```

After running twice, you will have two `_buildXYZ` directories. In `install/lib` you will see a `python3.9` and `python3.11` installation.
