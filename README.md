# LUTE
[![Docs](https://img.shields.io/badge/Docs-GH_Pages-blue)](https://slac-lcls.github.io/lute/dev)
[![Release build](https://github.com/slac-lcls/lute/actions/workflows/build.yml/badge.svg)](https://github.com/slac-lcls/lute/actions/workflows/release.yml)

## Description
`lute`, or `LUTE`, is the LCLS Unified Task Executor - an automated workflow package for running analysis pipelines at SLAC's LCLS. This project is the next iteration of [btx](https://github.com/lcls-users/btx).

This package is used to run arbitrary analysis code (first-party or third-party) in the form of individual analysis `Task`s. `Task`s can be linked together to form complete end-to-end analysis pipelines or workflows. For workflow management, the package interfaces with Airflow running on S3DF.

## Installation

## Usage

## Roadmap
A timeline is available in ...

## Contributing
All contributions most proceed through pull (merge) requests (PRs). Please fork the repository and open a PR when ready to merge your contribution. There is a specific PR template (coming soon...) which should be used to describe the nature of the contribution and how it was implemented. Before beginning, please read through the following guidelines regarding code style, naming conventions and general practices.

### Docstrings and Documentation
Modules, classes, and functions should be documented with docstrings. The [Google style](https://google.github.io/styleguide/pyguide.html) should be followed, with a minor addition - namely, module level docstrings should include a list of public classes, functions, etc. Example usage sections are optional but recommended for clarity. Refer to the link above for the full specification, or below for an abbreviated example.
```py
"""This is a module level docstring.

Classes:
    Class1: Short description

Functions:
    function1(prototype): Short description

Exceptions:
    MyException: When/why it is raised.

Example usage:
    How one might use the classes and functions. Test modules may include how
        to run them.
"""

class Class1:
    """Class descriptions can go here or in __init__.

    Attributes:
        public_attr (type): Description
        # properties should be documented separately in their getter.
    """
    def __init__(self, arg1: type) -> None:
        """Description of class can go here.

        Args:
            param1 (type): Description
        """
        ...

    @property
    def my_property(self) -> type:
        """type: Property attributes are documented here."""
        ...

def function1(arg1: type) -> type:
    """Function description.

    Args:
        arg1 (type):  Description

    Returns: # Or Yields for generators
        name (type): Description # Alternatively, can just be `type: Description`

    Raises:
        MyException: Why?
    """
    ...
```

For documentation generation, [MkDocs](https://www.mkdocs.org/) is used. Docstrings written using the above style guidelines should be parsed correctly for automatically generated documentation.

### Branch Conventions
For development of new features on your personal forks of the repository, please try to follow the following naming convention for your branches: `{ACRONYM}/{description}`. Please see below, under commit messages, for the relevant acronyms. For example, a PR implementing a new feature which produces summaries in the eLog may be called: `ENH/elog_summaries`.

As new features are implemented in personal forks, this official repository maintains only two branches, which are used to indicate the state of the project in terms of feature-list and stability.
* The `main` branch contains the latest stable release.
* The `dev` branch contains the most recent features and changes. This branch should **not** be used for untested or partially implemented features; however, any and all new additions versus the `main` branch are subject to change. When opening a PR for a new feature, it should be merged first to the `dev` branch. This branch is periodically merged into `main` following the development timeline and list of milestones. Once merged into `main`, the commit is tagged, and a new release is made.

### Code Style
* Type hints should be used throughout the code base. For the time being, support is still provided for Python 3.9, so certain more recent typing constructions are not available. E.g.
```py
my_var: str | int = get_str_or_int()
```
while valid in Python 3.10+, is unavailable in Python 3.9. Instead please use the `typing` module:
```py
from typing import Union
my_var: Union[str, int] = get_str_or_int()
```
The `typing` module contains many other useful features for type hint support.

Note that it is possible to use `from __future__ import annotations` to enable the use of these features. It is a breaking change and is used on a module-by-module basis, please investigate if it applies to your use case.


### Commit Messages
Inspired by `pcdshub` repositories, in turn following [NumPy conventions](https://numpy.org/doc/stable/dev/development_workflow.html#writing-the-commit-message), all commit messages should ideally be prefixed by a three letter acronym. Each of these acronyms has a specific meaning, making it easy to discern at a glance what the intended purpose of the commit is (bug fix, new feature, etc.). Pull (merge) request titles, and origin branches, should use the same acronyms. The following acronyms are in use:
| Acronym | Meaning                                                                  |
|:-------:|:-------------------------------------------------------------------------|
| BUG     | Bug fix                                                                  |
| DEP     | Deprecate a feature                                                      |
| DOC     | Documentation (either source code, or ADRs, design docs, etc.)           |
| ENH     | Enhancement - new feature.                                               |
| MNT     | Maintenance (refactoring, typos, name changes, code style, linting, etc) |
| SKL     | Skeleton. This should be used to outline what will later be an ENH.      |
| TST     | Related to tests.                                                        |
| UTL     | Utilities. This can be any new tool or changes to CI/CD, etc.            |

### Class and Object Naming Conventions

### Style, Formatting, Linting
This repository uses [Black](https://black.readthedocs.io/en/stable/) for formatting of Python code. [Ruff](https://docs.astral.sh/ruff/) is used for linting with flake8 rules, and [mypy](https://mypy-lang.org/) for type checking.

Github actions implement checks for compliance. Formatting changes are auto-committed.

### Debugging Code
Temporary debugging code should **not** be commited to the repository. E.g., extraneous `print` statements, etc, which are added when fixing a bug. Nonetheless, a selection of permanent debugging options may be included in the code provided they can be disabled when not running in debug mode. For standard operation, this package should be run using the `-O` flag which disables `assert` statements and sets the constant `__debug__ = False`. Without that flag, the package is considered to be running in "debug mode". As such, to include debug related code, please use a construction similar to the following:
```py
if __debug__:
    # We are in debugging mode!
    # Here is my debug code
    ...
else: # optional clause if needed
    # Python was run like: `python -O ...`
    ...
```

Debug logging code (i.e. `logger.debug("my message")`) need not be placed inside the above `if` statement. However, the configuration statement for logging **level** should be. Ideally this is placed at the top of each module.

## Authors and acknowledgment

## License
This project's license is available in `LICENSE.md`.

## Project status
Early active development - the public API is still subject to change, but some degree of stability can be expected at this point. The codebase is in active usage in production.
