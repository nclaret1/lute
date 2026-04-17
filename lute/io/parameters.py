"""Module for LUTE parameter objects.

This module contains objects that define LUTE TaskParameters. It is separate
from the pydantic model definitions included in `lute.io.models`. This allows
LUTE first-party code to run without pydantic validation. Validation is still
required to have occurred at some point to enter correct values into the database.
"""

from typing import List

__all__: List[str] = []
__author__ = "Gabriel Dorlhiac"

import enum
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set, Tuple, TypedDict, Union

LUTE_PARAMETER_CONFIG_KEYS: Dict[str, Any] = {
    # All Tasks
    "run_directory": {
        "title": "Run Directory",
        "description": "If set, the directory a `Task` will run from.",
        "anyOf": [{"type": "null"}, {"type": "string"}],
    },  # Optional[str]
    "set_result": {
        "title": "Set Result",
        "description": (
            "If true, the TaskParameters object has information about setting the TaskResult "
            "directly from the information in its parameters."
        ),
        "type": "boolean",
    },  # bool
    "result_from_params": {
        "title": "Result From Parameters",
        "description": "If not None, contains a result defined using a validator.",
        "anyOf": [{"type": "null"}, {"type": "string"}],
    },  # Optional[str]
    "result_summary": {
        "title": "Result Summary",
        "description": "If not None, defines a summary from the set of parameters.",
        "anyOf": [{"type": "null"}, {"type": "string"}],
    },  # Optional[str]
    "impl_schemas": {
        "title": "Implements Schemas",
        "description": "Specifies the set of schemas the Task result adheres to.",
        "anyOf": [{"type": "null"}, {"type": "string"}],
    },  # Optional[str]
    # Third-party only
    "short_flags_use_eq": {
        "title": "Short Flags Use Equals",
        "description": "If True, `short` flags (-x) use `=` on command line. (E.g. `-x=1`).",
        "type": "boolean",
    },  # bool
    "long_flags_use_eq": {
        "title": "Long Flags Use Equals",
        "description": "If True, `long` flags (--xyz) use `=` on command line. (E.g. `--xyz=1`).",
        "type": "boolean",
    },  # bool
}


LUTE_PARAMETER_FIELD_ATTRS: Set[str] = {
    "flag_type",
    "rename_param",
    "description",
    "is_result",
}


def handle_field_attrs(self, *args, **kwargs):
    """"""
    for param_name, param_val in kwargs.items():
        setattr(self, param_name, param_val)
        self._dict[param_name] = param_val


class RowIds(TypedDict):
    task_id: int
    parameter_type_id: int
    config_id: int
    parameter_ids: List[int]


class ContainerBase:
    def __repr__(self) -> str:
        str_repr: str = f"{self.__class__.__name__}("
        for v in vars(self):
            if v in ("__annotations__", "_schema"):
                continue
            str_repr += f"{v}={getattr(self,v)}, "
        str_repr = str_repr[:-2]
        str_repr = f"{str_repr})"
        return str_repr


class AnalysisHeader(ContainerBase):
    """Header information for LUTE analysis runs."""

    _schema: Dict[str, Any] = {}
    _dict: Dict[str, Any] = {}

    title: str
    experiment: str
    run: Union[str, int]
    date: str
    lute_version: Union[float, str]
    task_timeout: int
    work_dir: str

    def __init__(self, schema: Dict[str, Any], *args, **kwargs):
        self._dict = {}
        self._schema = schema
        handle_field_attrs(self, *args, **kwargs)


class ParameterConfig(ContainerBase):
    """Configuration for parameters model.

    The Config class holds Pydantic configuration. A number of LUTE-specific
    configuration has also been placed here.

    Attributes:
        run_directory (Optional[str]): None. If set, it should be a valid
            path. The `Task` will be run from this directory. This may be
            useful for some `Task`s which rely on searching the working
            directory.

        set_result (bool). False. If True, the model has information about
            setting the TaskResult object from the parameters it contains.
            E.g. it has an `output` parameter which is marked as the result.
            The result can be set with a field value of `is_result=True` on
            a specific parameter, or using `result_from_params` and a
            validator.

        result_from_params (Optional[str]): None. Optionally used to define
            results from information available in the model using a custom
            validator. E.g. use a `outdir` and `filename` field to set
            `result_from_params=f"{outdir}/{filename}`, etc. Only used if
            `set_result==True`

        result_summary (Optional[str]): None. Defines a result summary that
            can be known after processing the Pydantic model. Use of summary
            depends on the Executor running the Task. All summaries are
            stored in the database, however. Only used if `set_result==True`

        impl_schemas (Optional[str]). Specifies a the schemas the
            output/results conform to. Only used if `set_result==True`.

        -----------------------
        ThirdPartyTask-specific:

        short_flags_use_eq (bool): False. If True, "short" command-line args
            are passed as `-x=arg`. ThirdPartyTask-specific.

        long_flags_use_eq (bool): False. If True, "long" command-line args
            are passed as `--long=arg`. ThirdPartyTask-specific.
    """

    def __init__(self, *args, **kwargs) -> None:
        for k, v in kwargs.items():
            if k in LUTE_PARAMETER_CONFIG_KEYS:
                setattr(self, k, v)

    # All Tasks
    run_directory: Optional[str] = None
    set_result: Optional[bool] = None
    result_from_params: Optional[str] = None
    result_summary: Optional[str] = None
    impl_schemas: Optional[str] = None
    # Third-party Only
    # short_flags_use_eq: Optional[bool]
    # long_flags_use_eq: Optional[bool]


class TaskParameters(ContainerBase):
    Config = ParameterConfig()
    lute_config: AnalysisHeader

    _schema: Dict[str, Any] = {}
    _dict: Dict[str, Any] = {}

    def __init__(self, schema: Dict[str, Any], *args, **kwargs):
        self._schema = schema
        self._dict = {}
        handle_field_attrs(self, *args, **kwargs)

    def schema(self):
        return self._schema

    def dict(self):
        return self._dict


class ThirdPartyParameters(TaskParameters):
    _unknown_template_params: Dict[str, Any]


@dataclass
class TemplateParameters:
    """Class for representing parameters for third party configuration files.

    These parameters can represent arbitrary data types and are used in
    conjunction with templates for modifying third party configuration files
    from the single LUTE YAML. Due to the storage of arbitrary data types, and
    the use of a template file, a single instance of this class can hold from a
    single template variable to an entire configuration file. The data parsing
    is done by jinja using the complementary template.
    All data is stored in the single model variable `params.`

    The pydantic "dataclass" is used over the BaseModel/Settings to allow
    positional argument instantiation of the `params` Field.
    """

    params: Any


class TemplateConfig:
    """Parameters used for templating of third party configuration files.

    Attributes:
        template_name (str): The name of the template to use. This template must
            live in `config/templates`.

        output_path (str): The FULL path, including filename to write the
            rendered template to.
    """

    _schema: Dict[str, Any] = {}

    def __init__(self, schema: Dict[str, Any], *args, **kwargs):
        self._schema = schema
        self._dict: Dict[str, Any] = {}
        handle_field_attrs(self, *args, **kwargs)


class validator:
    def __init__(self, *args, **kwargs): ...
    def __call__(self, *args, **kwargs): ...


# Everything else is object and has a definition
BASE_SCHEMA_TYPE_MAP: Dict[str, type] = {
    "boolean": bool,
    "integer": int,
    "number": float,
    "string": str,
    "enum": enum.Enum,
}


def construct_task_parameters(schema: Dict[str, Any], values: Dict[str, Any]) -> object:
    """Construct a TaskParameters object from a schema and parameter values.

    This function will create a new `TaskParameters` object from a pydantic schema
    (usually retrieved from the database). This is a simplified container defined in
    this module, rather than the pydantic version. This allows its use in environments
    which do not have pydantic installed.

    This function will recursively construct necessary internal objects as well, e.g.,
    `AnalysisHeader`, `TemplateParameters`, etc.

    NOTE: This function assumes that the values passed in have been validated, and
          that they conform to the schema. No validation will be done.

    Args:
        schema (Dict[str, Any]): The JSON schema for the **PYDANTIC** TaskParameters
            model. Usually this will be retrieved from the database.

        values (Dict[str, Any]): The of the parameters for the TaskParameters object.

    Returns:
        new_obj (object): Usually this will be the `TaskParameters` instance (or a
            a sub-class thereof), but this method recursively constructs all objects.
    """
    # Parameter may not be here but in properties
    fields_for_params: Dict[str, Any] = {}
    class_name: str = schema["title"]
    param_config_obj_vals: Dict[str, Any] = {}
    if "definitions" in schema and "Config" in schema["definitions"]:
        config_properties: Dict[str, Any] = schema["definitions"]["Config"][
            "properties"
        ]
        for config_prop in config_properties:
            if config_prop in LUTE_PARAMETER_CONFIG_KEYS:
                # We put the value of the Config option as a `const` field in the defn
                config_prop_val: Any = config_properties[config_prop]["const"]
                param_config_obj_vals[config_prop] = config_prop_val
        schema["definitions"].pop("Config")
    for param_name in values:
        try:
            param_info: Dict[str, Any] = schema["properties"][param_name]
        except KeyError:
            for _, defn in schema["definitions"].items():
                if param_name in defn["properties"]:
                    param_info = defn["properties"][param_name]
                    break
            else:
                raise RuntimeError(f"Cannot find {param_name} in schema")
        working_value: Any = values[param_name]
        if working_value.__class__.__name__ == "TemplateParameters":
            working_value = working_value.params
        if working_value is None:
            fields_for_params[param_name] = None
            continue
        if "type" in param_info:
            type_info: str = param_info["type"]
            # new_field: Field
            new_field: Any
            cast_as: type
            if type_info == "array":
                cast_as = BASE_SCHEMA_TYPE_MAP[param_info["items"]["type"]]
                new_field = list(map(cast_as, working_value))
            elif type_info == "null":
                new_field = None
            elif type_info == "object":
                # Ideally we shouldn't get here, but it can happen if there is a
                # complex object passed as a parameter but no model defined.
                # E.g. this will happen if a dict {"a":1, "b":2} is the parameter
                # without having a separate BaseModel defined for it.
                # We cannot type check, so we just hope json deserialization worked.
                new_field = working_value
            else:
                cast_as = BASE_SCHEMA_TYPE_MAP[param_info["type"]]
                new_field = cast_as(working_value)
            fields_for_params[param_name] = new_field
        else:
            # Look here for information:
            # https://json-schema.org/draft/2020-12/json-schema-core#section-10.2
            # See also:
            # https://github.com/OAI/OpenAPI-Specification/blob/main/versions/3.0.2.md#data-types
            # Have to look up definitions
            if "oneOf" in param_info:
                # Case of Union[Obj1,Obj2,...]
                ...
            elif "allOf" in param_info:
                # Will be a str like "#/definitions/ClassName"
                sub_schema: Dict[str, Any] = schema
                ref: str = param_info["allOf"][0]["$ref"]  # .split("/")
                ref_parts: List[str] = ref.split("/")
                for part in ref_parts:
                    if part == "#":
                        continue
                    else:
                        sub_schema = sub_schema[part]
                fields_for_params[param_name] = construct_task_parameters(
                    sub_schema, working_value
                )
            elif "anyOf" in param_info:
                for possibility in param_info["anyOf"]:
                    # If we can successfully cast on the first type we will.
                    if "type" in possibility:
                        type_info = possibility["type"]
                        if type_info == "array":
                            cast_as = BASE_SCHEMA_TYPE_MAP[possibility["items"]["type"]]
                            try:
                                fields_for_params[param_name] = list(
                                    map(cast_as, working_value)
                                )
                                break
                            except ValueError:
                                # Maybe the next type will work
                                continue
                        elif type_info == "null":
                            fields_for_params[param_name] = None
                        else:
                            if isinstance(
                                working_value, BASE_SCHEMA_TYPE_MAP[possibility["type"]]
                            ):
                                fields_for_params[param_name] = working_value
                                break
                            else:
                                try:
                                    cast_as = BASE_SCHEMA_TYPE_MAP[possibility["type"]]
                                    fields_for_params[param_name] = cast_as(
                                        working_value
                                    )
                                    break
                                except ValueError:
                                    # Maybe the next type will work
                                    continue
                else:
                    raise ValueError(
                        f"Could not construct Field for parameter: {param_name}"
                    )

    obj_type: type
    if class_name == "TaskParameters":
        obj_type = TaskParameters
    elif class_name == "AnalysisHeader":
        obj_type = AnalysisHeader
    elif class_name == "TemplateConfig":
        obj_type = TemplateConfig
    elif class_name == "TemplateParameters":
        obj_type = TemplateParameters
    else:
        base_classes: Tuple[type] = (TaskParameters,)
        class_attrs: Dict[str, Any] = dict(TaskParameters.__dict__)
        # Remove bad fields
        ignore_keys: Set[str] = {"__weakref__", "__dict__"}
        safe_class_attrs: Dict[str, Any] = {}
        for key in class_attrs:
            if key in ignore_keys:
                continue
            else:
                safe_class_attrs[key] = class_attrs[key]
        obj_type = type(class_name, base_classes, safe_class_attrs)

    if param_config_obj_vals:
        # We only have a non-empty dict if this type has a Config attr
        param_config = ParameterConfig(**param_config_obj_vals)
        assert hasattr(obj_type, "Config")
        obj_type.Config = param_config

    obj: Any
    if obj_type == "TemplateParameters":
        # This is the only base class that doesn't retain a schema
        obj = obj_type(**fields_for_params)
    else:
        obj = obj_type(schema, **fields_for_params)

    if hasattr(obj, "_dict"):
        # This is messy - leftover from the object construction.
        # TODO: The above process should be cleaned up at some point.
        del obj._dict
    return obj
