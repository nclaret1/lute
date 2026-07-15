"""Parameter model for local-file SFX peak finding.

Classes:
    FindPeaksSFXLocalParameters: parameters for FindPeaksSFXLocal, which runs
        Peakfinder8 on simulation HDF5 files instead of a psana data source.
"""

from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import Field, validator

from lute.io.models.base import TaskParameters


class FindPeaksSFXLocalParameters(TaskParameters):
    """Parameters for Peakfinder8 peak finding on local simulation HDF5 files.

    Images are read directly from HDF5 files (dataset ``panel_0``). Detector
    geometry is extracted from the ``dxtbx_detector_json`` attribute embedded
    in each file, so no CrystFEL geometry file or psana data source is needed.
    """

    class Config(TaskParameters.Config):
        set_result: bool = True

    # --- Input ---
    input_dir: str = Field(
        description="Directory containing per-frame HDF5 files.",
    )
    file_pattern: str = Field(
        "frame_*.h5",
        description="Glob pattern to select H5 files inside input_dir.",
    )

    # --- Output naming (replace exp/run from lute_config for CXI filenames) ---
    exp_label: str = Field(
        "sim",
        description="Experiment label used in CXI output filenames.",
    )
    run_label: int = Field(
        0,
        description="Run number used in CXI output filenames.",
    )
    outdir: str = Field(
        description="Output directory for CXI files.",
    )
    out_file: str = Field(
        "",
        description="Path to file that will contain the master CXI path.",
        is_result=True,
    )
    tag: str = Field(
        "",
        description="Tag appended to output filenames.",
    )

    # --- Event selection ---
    n_events: int = Field(
        0,
        description="Maximum number of frames to process (0 = all).",
    )

    # --- Peak finding ---
    min_peaks: int = Field(2, description="Minimum number of peaks per image.")
    max_peaks: int = Field(2048, description="Maximum number of peaks per image.")
    npix_min: int = Field(2, description="Minimum number of pixels per peak.")
    npix_max: int = Field(30, description="Maximum number of pixels per peak.")
    amax_thr: float = Field(
        80.0,
        description="Minimum ADC threshold for starting a peak.",
    )
    son_min: float = Field(
        7.0,
        description="Minimum signal-to-noise ratio to be considered a peak.",
    )
    r0: float = Field(
        3.0,
        description="Radius of the local background ring in pixels.",
    )

    # --- Optional mask ---
    mask_file: Optional[str] = Field(
        None,
        description=(
            "Path to an HDF5 mask file (dataset entry_1/data_1/mask). "
            "If None, no custom mask is applied."
        ),
    )

    # --- Diagnostics ---
    make_powder_plots: bool = Field(
        True,
        description="Whether to generate powder (hit/miss projection) plots.",
    )

    @validator("out_file", always=True)
    def validate_out_file(cls, out_file: str, values: Dict[str, Any]) -> str:
        if out_file == "":
            tag: str = values.get("tag", "")
            if tag and tag[0] != "_":
                tag = "_" + tag
            fname: Path = (
                Path(values["outdir"])
                / f"{values['exp_label']}_r{values['run_label']:04d}{tag}.list"
            )
            return str(fname)
        return out_file
