"""Parameter model for the ZMQ-streaming DR peak-finding task.

Classes:
    SfxDrFindPeaksZmqParameters: parameters for SfxDrFindPeaksZmq.
"""

from typing import Any, Dict, Literal, Optional

from pydantic import Field, validator

from lute.io.models.sfx_dr_find_peaks import DrFindPeaksPyAlgosParameters


class SfxDrFindPeaksZmqParameters(DrFindPeaksPyAlgosParameters):
    """Parameters for ZMQ-streaming DR peak finding.

    Identical to DrFindPeaksPyAlgosParameters, but data is streamed from a
    psana1 subprocess over ZMQ (via sfx_dr_zmq_push.py) instead of being read
    directly with MPIDataSource.  This allows the peak-finding logic to run in
    a psana2 environment while accessing psana1 (XTC1) experiments.

    Extra parameters:
        algorithm: Peak finding algorithm - "PyAlgos" (default), "Peakfinder8",
            or "Peakfinder8_v2".
        geometry_file: CrystFEL geometry file required for Peakfinder8/v2.
        eventfile: CSV file listing event indices to process, superseding
            n_events when provided.
    """

    algorithm: Literal["PyAlgos", "Peakfinder8", "Peakfinder8_v2"] = Field(
        "PyAlgos",
        description=(
            'Peak finding algorithm: "PyAlgos" (default), "Peakfinder8", '
            'or "Peakfinder8_v2".'
        ),
    )

    geometry_file: Optional[str] = Field(
        None,
        description=(
            "Path to a CrystFEL geometry file. Required when algorithm is "
            '"Peakfinder8" or "Peakfinder8_v2".'
        ),
    )

    eventfile: str = Field(
        "",
        description=(
            "CSV file with event indices to process. "
            "Supersedes n_events if provided."
        ),
    )

    n_senders: int = Field(
        0,
        description=(
            "Number of parallel psana1 sender processes. 0 (default) = auto-detect "
            "from XTC stream files: one sender per stream, each reading its own file "
            "with no I/O contention. Wall time scales as ~1/n_senders. "
            "Set > 0 to override with manual event-range splitting (requires "
            "n_events > 0 or eventfile)."
        ),
    )

    @validator("out_file", always=True)
    def validate_out_file(cls, out_file: str, values: Dict[str, Any]) -> str:
        from pathlib import Path

        if out_file == "":
            fname: Path = (
                Path(values["outdir"])
                / f"{values['lute_config'].experiment}_{values['lute_config'].run}_"
                f"{values['tag']}.list"
            )
            return str(fname)
        return out_file
