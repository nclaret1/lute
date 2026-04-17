"""Custom loggers for Tasks.

Classes:
    SocketCommunicatorHandler(logging.Handler): Logging handler which passes
        messages via LUTE SocketCommunicator objects.

Functions:
    get_logger(name: str) -> logging.Logger: Grab a standard LUTE logger and
        reduce logging from libraries which output many messages.
"""

import logging
from typing import Optional, Dict, Literal

from lute.execution.ipc import SocketCommunicator, Message

__all__ = ["get_logger"]
__author__ = "Gabriel Dorlhiac"

STD_PYTHON_LOG_FORMAT: str = "%(levelname)s:%(name)s:%(message)s"
"""Default Python logging formatter specification."""

LUTE_TASK_LOG_FORMAT: str = "TASK_LOG -- %(levelname)s:%(name)s: %(message)s"
"""Format specification for the formatter used by the standard LUTE logger."""


class SocketCommunicatorHandler(logging.Handler):
    """Logging handler which passes messages via SocketCommunicator objects."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._communicator: SocketCommunicator = SocketCommunicator()
        self._communicator.delayed_setup()

    def emit(self, record: logging.LogRecord):
        formatted_message: str = self.format(record)
        msg: Message = Message(contents=formatted_message, signal="TASK_LOG")
        self._communicator.write(msg)


class ColorFormatter(logging.Formatter):
    """Provide color text formatting for a logger."""

    WARNING_COLOR_FMT: str = "\x1b[33;20m"
    ERROR_COLOR_FMT: str = "\x1b[31;20m"
    CRITICAL_COLOR_FMT: str = "\x1b[31;1m"
    RESET_COLOR_FMT: str = "\x1b[0m"

    def __init__(
        self,
        fmt: Optional[str] = None,
        datefmt: Optional[str] = None,
        style: Literal["%", "{", "$"] = "%",
        validate: bool = True,
        *,
        is_task: bool = True,
    ) -> None:
        super().__init__(fmt, datefmt, style, validate)
        log_format: str
        if is_task:
            log_format = LUTE_TASK_LOG_FORMAT
        else:
            log_format = STD_PYTHON_LOG_FORMAT
        self.level_formats: Dict[int, str] = {
            logging.DEBUG: f"{log_format}",
            logging.INFO: f"{log_format}",
            logging.WARNING: f"{self.WARNING_COLOR_FMT}{log_format}{self.RESET_COLOR_FMT}",
            logging.ERROR: f"{self.ERROR_COLOR_FMT}{log_format}{self.RESET_COLOR_FMT}",
            logging.CRITICAL: f"{self.CRITICAL_COLOR_FMT}{log_format}{self.RESET_COLOR_FMT}",
        }

    def format(self, record) -> str:
        log_fmt: str = self.level_formats[record.levelno]
        formatter: logging.Formatter = logging.Formatter(log_fmt)
        return formatter.format(record)


def get_logger(name: str, is_task: bool = True) -> logging.Logger:
    """Retrieve a logger with LUTE handler and set log levels of other loggers.

    This function returns a logger with correct log level set by the debug flag.
    In addition, it silences (or reduces) logs from commonly imported libraries
    which produce many messages that make log files hard to parse. This is
    particularly useful when running in debug mode.

    Args:
        name (str): Name of the logger.

    Returns:
        logger (logging.Logger): Custom logger.
    """
    for other_name, other_logger in logging.root.manager.loggerDict.items():
        if "matplotlib" in other_name and not isinstance(
            other_logger, logging.PlaceHolder
        ):
            other_logger.disabled = True
        elif "pyFAI" in other_name and not isinstance(
            other_logger, logging.PlaceHolder
        ):
            other_logger.disabled = True
        elif "numpy" in other_name and not isinstance(
            other_logger, logging.PlaceHolder
        ):
            other_logger.setLevel(logging.CRITICAL)
    logger: logging.Logger = logging.getLogger(name)
    logger.propagate = False
    handler: logging.Handler
    if is_task:
        handler = SocketCommunicatorHandler()
    else:
        handler = logging.StreamHandler()
    formatter: ColorFormatter = ColorFormatter(is_task=is_task)
    handler.setFormatter(formatter)
    logger.handlers = []
    logger.addHandler(handler)
    if __debug__:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    return logger
