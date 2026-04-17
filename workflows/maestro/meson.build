pybind11_dep = dependency('pybind11', required: true)

cmake = import('cmake')
spdlog_opts = cmake.subproject_options()
spdlog_opts.add_cmake_defines(
    {'SPDLOG_BUILD_ALL':'ON', 'CMAKE_POSITION_INDEPENDENT_CODE':'ON'}
)
spdlog = cmake.subproject('spdlog', options: spdlog_opts)

libspdlog = spdlog.get_variable('spdlog')
spdlog_inc = spdlog.get_variable('spdlog_inc')
spdlog_dep = declare_dependency(
    link_with: libspdlog,
    include_directories: spdlog_inc,
)

# RapidJSON for JSON parsing
rapidjson_proj = subproject('rapidjson')
rapidjson_dep = rapidjson_proj.get_variable('rapidjson_dep')
rapidjson_inc = rapidjson_proj.get_variable('rapidjson_inc')

liblwmhttp = shared_library(
    'lwmhttp',
    [
        'src/server/handler.cc',
        'src/server/http.cc',
        'src/server/server.cc'
    ],
    dependencies: [spdlog_dep, rapidjson_dep],
    include_directories: spdlog_inc,
    install: true,
    install_dir: py3.get_install_dir() / 'maestro/_maestro',
    version: '1',
    soversion: '1',
    link_args: ['-lpthread'],
)

lwm_http_dep = declare_dependency(
    link_with: [libspdlog, liblwmhttp],
    dependencies: [spdlog_dep],
)

liblwm = shared_library(
    'lwm',
    [
        'src/lwm/job.cc',
        'src/lwm/launcher.cc',
        'src/lwm/manager.cc',
        'src/parallel/threadpool.cc'
    ],
    install: true,
    install_dir: py3.get_install_dir() / 'maestro/_maestro',
    dependencies: [spdlog_dep, lwm_http_dep, rapidjson_dep],
    include_directories: spdlog_inc,
    version: '1',
    soversion: '1',
    link_args: ['-lpthread'],
)

lwm_dep = declare_dependency(
    link_with: [liblwm, liblwmhttp],
    dependencies: [lwm_http_dep],
)

py_mod = py3.extension_module('_maestro',
    ['src/_maestro/maestro.cc'],
    install: true,
    dependencies : [pybind11_dep, lwm_dep, spdlog_dep],
    subdir: 'maestro/_maestro',
    install_rpath: '$ORIGIN',
)

# Testing stuff
###############

gtest_dep = dependency('gtest', main: true, static: true, required: false)

if not gtest_dep.found()
    gtest_proj = subproject('gtest', default_options: ['default_library=static'])
    gtest_dep = gtest_proj.get_variable('gtest_main_dep')
endif

test_http = executable(
    'test_http',
    [
        'tests/cpp/test_http.cc',
        'src/server/http.cc'
    ],
    dependencies: [
        gtest_dep,
        spdlog_dep,
        rapidjson_dep,
    ],
    install: get_option('install_tests'),
    install_rpath: '$ORIGIN',
    include_directories: ['src/server'],
)
test('HTTP parsing tests', test_http)

test_server = executable(
    'test_server',
    [
        'tests/cpp/test_server.cc',
        'src/server/http.cc',
        'src/server/server.cc',
        'src/server/handler.cc'
    ],
    dependencies: [
        gtest_dep,
        spdlog_dep,
        rapidjson_dep,
    ],
    install: get_option('install_tests'),
    install_rpath: '$ORIGIN',
    include_directories: ['src/server'],
    link_args: ['-lpthread'],
)
test('Server logic tests', test_server)

test_maestro_api = executable(
    'test_maestro_api',
    [
        'tests/cpp/test_maestro_api.cc',
        'src/server/http.cc',
        'src/server/handler.cc',
        'src/lwm/launcher.cc',
        'src/lwm/job.cc',
        'src/parallel/threadpool.cc'
    ],
    dependencies: [
        gtest_dep,
        spdlog_dep,
        rapidjson_dep,
    ],
    install: get_option('install_tests'),
    install_rpath: '$ORIGIN',
    include_directories: ['src', 'src/server', 'src/lwm', 'src/parallel'],
    link_args: ['-lpthread'],
)
test('Maestro REST API tests', test_maestro_api)


# Install the Python modules
# Could do this "properly" with subdir and iterative meson.build files but its
# only a handful of modules
py_sources = [
    'src/maestro/__init__.py',
    'src/maestro/parser.py',
    'src/maestro/parser_params.py',
    'src/maestro/config_generator.py',
]
py3.install_sources(
    py_sources,
    subdir: 'maestro',
)

py_launch_sources = [
    'src/maestro/launch_maestro.py',
]
py3.install_sources(
    py_launch_sources,
    subdir: 'launch_scripts',
)
