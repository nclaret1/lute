# Shamelessly lifted from skimage
# NumPy include directory
incdir_numpy = meson.get_external_property('numpy-include-dir', 'not-given')
if incdir_numpy == 'not-given'
  # If not specified, try to query NumPy from the build python
  incdir_numpy = run_command(py3,
    [
      '-c',
      'import os; os.chdir(".."); import numpy; print(numpy.get_include())'
    ],
    check: true
  ).stdout().strip()
endif

numpy_nodepr_api = '-DNPY_NO_DEPRECATED_API=NPY_1_24_API_VERSION'

np_dep = declare_dependency(
  include_directories: incdir_numpy,
  compile_args: numpy_nodepr_api
)

py3.extension_module(
    '_peakfinders_ext',
    [
        'peakfinder8.cpp',
	'peakfinder8_v2.cpp',
	'peakfinders.cpp',
    ],
    install: true,
    subdir: 'lute/tasks/algorithms',
    dependencies: [np_dep],
)