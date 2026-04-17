bash_scripts = [
    'activate_installation'
]

if get_option('install_utilities')
    install_data(
        bash_scripts,
        install_dir: get_option('bindir'),
    )
endif

py_sources = [
    '__init__.py',
]

py3.install_sources(
    py_sources,
    pure: false,
    subdir: 'lute_utilities',
)


subdir('dbview')
subdir('help')
subdir('setup')
