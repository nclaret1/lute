# Integrating a New `Task`

`Task`s can be broadly categorized into two types:

- "First-party" - where the analysis or executed code is maintained within this library.
- "Third-party" - where the analysis, code, or program is maintained elsewhere and is simply called by a wrapping `Task`.

Creating a new `Task` of either type generally involves the same steps, although for first-party `Task`s, the analysis code must of course also be written. Due to this difference, as well as additional considerations for parameter handling when dealing with "third-party" `Task`s, the "first-party" and "third-party" `Task` integration cases will be considered separately.

**Note:** Strictly speaking, the LUTE execution framework handles **three** cases. When dealing with first-party `Task`s, the case when the `Task` runs in the same environment as the `Executor` and in a different environment, are handled separately. From the perspective of implementing a new `Task` there aren't many differences, however, and these two cases are both covered in the first-party `Task` documentation.

## Build System

LUTE currently uses [meson](https://mesonbuild.com) for building the package, along with [meson-python](https://mesonbuild.com/meson-python/). Within each sub-directory for the Python package you will find a `meson.build` file, it will have a structure similar to the following:

```
py_sources = [
    '__init__.py',
    '...',
]

py3.install_sources(
    py_sources,
    pure: false,
    subdir: 'lute/...',
)
```

If you add a new Python module, make sure to update the list of files in `py_sources` to ensure that it gets installed.

If you add a new sub-package (i.e., you create a new directory), a couple of changes will be required.

- First, in the directory where you created the new directory you will add the following to `meson.build`:

```
# ... rest of the meson.build file ...

subdir('name_of_my_new_subpackage')
```

- Second, in the new sub-package you will create a new `meson.build` that looks like the above

```
py_sources = [
    '__init__.py',
    '...my_new_module.py...',
]

py3.install_sources(
    py_sources,
    pure: false,
    subdir: 'lute/.../name_of_my_new_subpackage',
)
```

**Note:** Make sure that when you define `py3.install_sources`, you include the `subdir` argument, and that it contains the path to your new subpackage!
