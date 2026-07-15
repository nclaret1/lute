"""Parameter model for the streaming XTC1 → peak finding task.

Classes:
    StreamFindPeaksPyAlgosParameters: parameters for StreamFindPeaksPyAlgos.
"""

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, Field, validator

from lute.io.models.base import TaskParameters
from lute.io.models.xtc import ConversionSpecification


class StreamFindPeaksPyAlgosParameters(TaskParameters):
    """Parameters for streaming XTC1-to-peak-finding without an intermediate XTC2 file.

    xtc_push.py is spawned in a psana1 subprocess and streams calibrated
    detector arrays over ZMQ. The peak finder runs in the current process
    (psana2 env), enabling SZ/QoZ compression via libpressio.
    """

    class Config(TaskParameters.Config):
        set_result: bool = True

    class SZCompressorParameters(BaseModel):
        compressor: Literal["qoz", "sz3"] = Field(
            "qoz", description='Compression algorithm ("qoz" or "sz3")'
        )
        abs_error: float = Field(10.0, description="Absolute error bound")
        bin_size: int = Field(2, description="Bin size")
        roi_window_size: int = Field(9, description="Default ROI window size")

    # --- ZMQ / data source ---
    xtc1_access_pattern: Dict[str, List[ConversionSpecification]] = Field(
        description=(
            "How to access detector data in XTC1. Keys become detector names "
            "in the ZMQ stream (same format as ConvertXtc1to2)."
        )
    )
    nevents: Optional[int] = Field(
        None,
        description="Number of events to process. None processes all events.",
    )
    eventfile: str = Field(
        "",
        description="CSV file with event numbers. Supersedes nevents if provided.",
    )
    use_zmq_mask: bool = Field(
        True,
        description="Apply the pixel-status mask received in the ZMQ calib constants.",
    )

    # --- Detector / geometry ---
    det_name: str = Field(
        description="Key name of the detector in the ZMQ stream (top-level key in xtc1_access_pattern).",
    )
    camera_length_mm: Union[str, float] = Field(
        description="Detector distance in mm, or an epics PV name to read it from.",
    )
    photon_energy_eV: float = Field(
        0.0,
        description="Photon energy in eV written to the CXI file. 0 = unknown.",
    )

    # --- Output ---
    outdir: str = Field(description="Output directory for CXI files.")
    tag: str = Field("", description="Tag appended to output file names.")
    out_file: str = Field(
        "",
        description="Path to output list file.",
        flag_type="-",
        rename_param="o",
        is_result=True,
    )

    # --- Peak finding ---
    min_peaks: int = Field(10, description="Minimum number of peaks per image.")
    max_peaks: int = Field(2048, description="Maximum number of peaks per image.")
    npix_min: int = Field(2, description="Minimum pixels per peak.")
    npix_max: int = Field(30, description="Maximum pixels per peak.")
    amax_thr: float = Field(40.0, description="Minimum peak-pixel intensity.")
    atot_thr: float = Field(180.0, description="Minimum summed peak intensity.")
    son_min: float = Field(10.0, description="Minimum signal-to-noise ratio.")
    peak_rank: int = Field(3, description="Local-maximum search radius.")
    r0: float = Field(3.0, description="Background ring inner radius (pixels).")
    dr: float = Field(2.0, description="Background ring width (pixels).")
    nsigm: float = Field(10.0, description="SNR threshold for connected groups.")
    mask_file: Union[str, None] = Field(
        None,
        description="Optional HDF5 mask file (entry_1/data_1/mask). Applied on "
        "top of the ZMQ calibration mask.",
    )

    # --- Compression (libpressio SZ/QoZ) ---
    compression: Optional[SZCompressorParameters] = Field(
        None,
        description="SZ/QoZ compression applied per hit before CXI writing.",
    )

    # --- DR (dimensionality-reduction) algo ---
    dr_method: Optional[
        Literal[
            "wavelet_quant_zerotree_compress",
            "wavelet_dionisio",
            "wavelet_spiht",
            "wavelet_bishrink_zerotree_compress",
            "wavelet_bishrink_rpca",
            "wavelet_sure_shrink",
            "wavelet_simple_shrink",
            "wavelet_hmt_shrink",
            "wavelet_bivariate_shrink",
            "wavelet_bayes_shrink",
            "stable_pcp",
            "tv_rpca",
            "tv_reg_rpca",
            "tv_reg_svd_rpca",
            "rpca_altproj",
            "rpca_altproj_thresh",
            "median_bg",
            "morph_open",
            "pysz_codec",
            "rsvd_density_power",
            "wavelet_mkt",
            "libpressio_sz3",
            "libpressio_qoz",
        ]
    ] = Field(None, description="DR reduction method. None disables DR.")
    n_components: Optional[int] = Field(None, description="Target rank for L+S DR.")
    tol: float = Field(1e-3, description="Convergence tolerance for DR.")
    max_iter: int = Field(1000, description="Maximum DR iterations.")
    gamma: float = Field(0.8, description="DR regularisation parameter.")
    size: int = Field(7, description="Morphological / median filter size.")
    dr_component: Literal["S", "L+S", "X_hat", None] = Field(
        None, description="DR output component. None disables component selection."
    )
    center: bool = Field(False, description="Centre data before DR.")
    copy_: bool = Field(True, description="Copy data before DR.")
    verbose: bool = Field(True, description="Verbose DR output.")
    random_state: Optional[int] = Field(None, description="DR random seed.")
    abs_error: float = Field(1e-3, description="Absolute error for SZ (if enabled).")
    sz_algo: Literal["INTERP_LORENZO", "INTERP", "LORENZO_REG", "LOSSLESS"] = Field(
        "INTERP_LORENZO", description="SZ algorithm variant."
    )
    post_threshold_scale: float = Field(1e-2, description="Post-quantisation scale.")

    @validator("out_file", always=True)
    def validate_out_file(cls, out_file: str, values: Dict[str, Any]) -> str:
        if out_file == "":
            fname: Path = (
                Path(values["outdir"])
                / f"{values['lute_config'].experiment}_{values['lute_config'].run}_"
                f"{values.get('tag', '')}.list"
            )
            return str(fname)
        return out_file
