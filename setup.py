"""
setup.py for LUTE
"""

from setuptools import Extension, find_packages, setup
from typing import List

import numpy as np

peakfinders_ext: Extension = Extension(
    name="lute.tasks.algorithms._peakfinders_ext",
    include_dirs=[np.get_include()],
    libraries=["stdc++"],
    sources=(
        [
            "extensions/algorithms/peakfinder8.cpp",
            "extensions/algorithms/peakfinder8_v2.cpp",
            "extensions/algorithms/peakfinders.cpp",
        ]
    ),
    language="c++",
)
extensions: List[Extension] = [peakfinders_ext]
USE_MYPYC: bool
try:
    from mypyc.build import mypycify

    USE_MYPYC = True
except ModuleNotFoundError:
    USE_MYPYC = False

if USE_MYPYC:
    extensions.extend(mypycify(["lute/execution","lute/tasks"]))

version_fh = open("lute/__init__.py", "r")
version = version_fh.readlines()[-1].split("=")[1].strip().split('"')[1]
version_fh.close()
setup(
    name="lute",
    version=version,
    url="https://slac-lcls.github.io/lute/dev/",
    description="LCLS Unified Task Executor.",
    packages=find_packages(where="."),
    package_data={"config": ["*.yaml", "templates/*.*"]},
    ext_modules=extensions,
    platforms="any",
)
