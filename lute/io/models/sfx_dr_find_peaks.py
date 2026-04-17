import os
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Union

from pydantic import BaseModel, Field, PositiveInt, validator, root_validator

from .base import ThirdPartyParameters, TaskParameters, TemplateConfig


class DrFindPeaksPyAlgosParameters(TaskParameters):
    """Parameters for crystallographic (Bragg) peak finding using PyAlgos.

    This peak finding Task optionally has the ability to compress/decompress
    data with SZ for the purpose of compression validation.
    """

    class Config(TaskParameters.Config):
        set_result: bool = True
        """Whether the Executor should mark a specified parameter as a result."""

    class SZCompressorParameters(BaseModel):
        compressor: Literal["qoz", "sz3"] = Field(
            "qoz", description='Compression algorithm ("qoz" or "sz3")'
        )
        abs_error: float = Field(10.0, description="Absolute error bound")
        bin_size: int = Field(2, description="Bin size")
        roi_window_size: int = Field(
            9,
            description="Default window size",
        )

    outdir: str = Field(
        description="Output directory for cxi files",
    )
    n_events: int = Field(
        0,
        description="Number of events to process (0 to process all events)",
    )
    det_name: str = Field(
        description="Psana name of the detector storing the image data",
    )
    event_receiver: Literal["evr0", "evr1"] = Field(
        description="Event Receiver to be used: evr0 or evr1",
    )
    tag: str = Field(
        "",
        description="Tag to add to the output file names",
    )
    pv_camera_length: Union[str, float] = Field(
        description="PV associated with camera length "
        "(if a number, camera length directly)",
    )
    event_logic: bool = Field(
        False,
        description="True if only events with a specific event code should be "
        "processed. False if the event code should be ignored",
    )
    event_code: int = Field(
        0,
        description="Required events code for events to be processed if event logic "
        "is True",
    )
    psana_mask: bool = Field(
        False,
        description="If True, apply mask from psana Detector object",
    )
    mask_file: Union[str, None] = Field(
        None,
        description="File with a custom mask to apply. If None, no custom mask is "
        "applied",
    )
    min_peaks: int = Field(10, description="Minimum number of peaks per image")
    max_peaks: int = Field(
        2048,
        description="Maximum number of peaks per image",
    )
    npix_min: int = Field(
        2,
        description="Minimum number of pixels per peak",
    )
    npix_max: int = Field(
        30,
        description="Maximum number of pixels per peak",
    )
    amax_thr: float = Field(
        40.0,
        description="Minimum intensity threshold for starting a peak",
    )
    atot_thr: float = Field(
        180.0,
        description="Minimum summed intensity threshold for pixel collection",
    )
    son_min: float = Field(
        10.0,
        description="Minimum signal-to-noise ratio to be considered a peak",
    )
    peak_rank: int = Field(
        3,
        description="Radius in which central peak pixel is a local maximum",
    )
    r0: float = Field(
        3.0,
        description="Radius of ring for background evaluation in pixels",
    )
    dr: float = Field(
        2.0,
        description="Width of ring for background evaluation in pixels",
    )
    nsigm: float = Field(
        10.0,
        description="Intensity threshold to include pixel in connected group",
    )
    compression: Optional[SZCompressorParameters] = Field(
        None,
        description="Options for the SZ Compression Algorithm",
    )
    out_file: str = Field(
        "",
        description="Path to output file.",
        flag_type="-",
        rename_param="o",
        is_result=True,
    )
    n_components: Optional[int] = Field(
        None,
        description="Target rank of background decomposition for L+S type dr algorithm.",
    )
    center: bool = Field(
        False,
        description="Center the data before decomposition",
    )
    copy_: bool = Field(
        True,
        description="Copy the data before decomposition",
    )
    verbose: bool = Field(
        True,
        description="Enable verbose output",
    )
    random_state: Optional[int] = Field(
        None,
        description="Random state for Dr algorithm reproducibility.",
    )
    tol: float = Field(
        1e-3,
        description="Tolerance for convergence of Dr Algorithm.",
    )
    max_iter: int = Field(
        1000,
        description="Maximum number of iterations for Dr algorithm.",
    )
    gamma: float = Field(
        0.8,
        description="Regularization parameter for Dr algorithm.",
    )

    size: int = Field(
        7,
        description="Size parameter:morh_open size of square, median filter size of kernel.",
    )

    dr_component: Literal["S", "L+S", "X_hat", None] = Field(
        None,
        description="For LSDrAlgo: 'S' or 'L+S'. None disables component selection.",
    )

    post_threshold_scale: float = Field(
        1e-2,
        description="Determine further quantization scale.",
    )

    dr_method: Optional[Literal["wavelet_quant_zerotree_compress", 
                                "wavelet_dionisio", "wavelet_spiht", 
                                "wavelet_bishrink_zerotree_compress",
                                "wavelet_bishrink_rpca","wavelet_sure_shrink", 
                                "wavelet_simple_shrink", "wavelet_hmt_shrink", 
                                "wavelet_bivariate_shrink", "wavelet_bayes_shrink", 
                                "stable_pcp", "tv_rpca", 
                                "tv_reg_rpca", "tv_reg_svd_rpca", 
                                "rpca_altproj", "rpca_altproj_thresh", 
                                "median_bg", "morph_open", "pysz_codec", 
                                "rsvd_density_power", "wavelet_mkt"]] = Field(
        None,
        description="For DrAlgo: reduction method to use. None disables DR.",
    )

    abs_error: float = Field(
        1e-3,
        description="Absolute error bound for SZ compression (if enabled).",
    )

    sz_algo: Literal["INTERP_LORENZO", "INTERP", "LORENZO_REG", "LOSSLESS"] = Field(
        "INTERP_LORENZO",
        description="SZ compression algorithm to use (if enabled).",
    )


    @validator("out_file", always=True)
    def validate_out_file(cls, out_file: str, values: Dict[str, Any]) -> str:
        if out_file == "":
            fname: Path = (
                Path(values["outdir"])
                / f"{values['lute_config'].experiment}_{values['lute_config'].run}_"
                f"{values['tag']}.list"
            )
            return str(fname)
        return out_file

