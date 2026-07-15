"""LUTE Tasks

Functions:
    import_task(task_name: str) -> Type[Task]: Provides conditional import of
       Task's. This prevents import conflicts as Task's may be intended to run
       in different environments.
Exceptions:
    TaskNotFoundError: Raised if
"""

from typing import Type

from lute.tasks.task import Task


class TaskNotFoundError(Exception):
    """Exception raised if an unrecognized Task is requested.

    The Task could be invalid (e.g. misspelled, nonexistent) or it may not have
    been registered with the `import_task` function below.
    """

    pass  # fmt: pass


def import_task(task_name: str) -> Type[Task]:
    """Conditionally imports Task's to prevent environment conflicts.

    Args:
        task_name (str): The name of the Task to import.

    Returns:
        TaskType (Type[Task]): The requested Task class.

    Raises:
        TaskNotFoundError: Raised if the requested Task is unrecognized.
            If the Task exits it may not have been registered.
    """
    if task_name == "Test":
        from .test import Test

        return Test

    if task_name == "TestSocket":
        from .test import TestSocket

        return TestSocket

    if task_name == "TestReadOutput":
        from .test import TestReadOutput

        return TestReadOutput

    if task_name == "TestWriteOutput":
        from .test import TestWriteOutput

        return TestWriteOutput

    if task_name == "FindPeaksSFX":
        from .sfx_find_peaks import FindPeaksSFX

        return FindPeaksSFX

    if task_name == "FindPeaksSFXLocal":
        from .sfx_find_peaks_local import FindPeaksSFXLocal

        return FindPeaksSFXLocal
    
    if task_name == "ConcatenateStreamFiles":
        from .sfx_index import ConcatenateStreamFiles

        return ConcatenateStreamFiles

    if task_name == "AnalyzeSmallDataXSS":
        from .smalldata import AnalyzeSmallDataXSS

        return AnalyzeSmallDataXSS

    if task_name == "AnalyzeSmallDataXAS":
        from .smalldata import AnalyzeSmallDataXAS

        return AnalyzeSmallDataXAS

    if task_name == "AnalyzeSmallDataXES":
        from .smalldata import AnalyzeSmallDataXES

        return AnalyzeSmallDataXES

    if task_name == "TestMultiNodeCommunication":
        from .mpi_test import TestMultiNodeCommunication

        return TestMultiNodeCommunication

    if task_name == "BayFAI":
        from .bayfai import BayFAI

        return BayFAI

    if task_name == "OptimizeAgBhGeometryExhaustive":
        from .geometry import OptimizeAgBhGeometryExhaustive

        return OptimizeAgBhGeometryExhaustive

    if task_name == "ConvertSMDToNexus":
        from .nexus import ConvertSMDToNexus

        return ConvertSMDToNexus

    if task_name == "ConvertXtc1to2":
        from .xtc import ConvertXtc1to2

        return ConvertXtc1to2

    if task_name == "ComparePeakFindingPrecision":
        from .precision import ComparePeakFindingPrecision

        return ComparePeakFindingPrecision

    if task_name == "DrFindPeaksPyAlgos":
        from .sfx_dr_find_peaks import DrFindPeaksPyAlgos

        return DrFindPeaksPyAlgos

    if task_name == "StreamFindPeaksPyAlgos":
        from .sfx_stream_find_peaks import StreamFindPeaksPyAlgos

        return StreamFindPeaksPyAlgos

    if task_name == "SfxDrFindPeaksZmq":
        from .sfx_dr_find_peaks_zmq import SfxDrFindPeaksZmq

        return SfxDrFindPeaksZmq

    if task_name == "TestRequest":
        from .test import TestRequest

        return TestRequest
    

    raise TaskNotFoundError
