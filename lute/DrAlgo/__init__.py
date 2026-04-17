from typing import Type
from lute.tasks.task import Task
from .DrAlgo import DrAlgo


class AlgNotFoundError(Exception):
    """Exception raised if an unrecognized Task is requested.

    The Task could be invalid (e.g. misspelled, nonexistent) or it may not have
    been registered with the `import_task` function below.
    """

    ...


def import_dr_algo(dr_method: str) -> Type[DrAlgo]:
    """Conditionally imports DrAlgo's to prevent environment conflicts.

    Args:
        dr_algo_name (str): The name of the DrAlgo to import.
    Returns:
        DrAlgoType (Type[DrAlgo]): The requested DrAlgo class.

    Raises:
        AlgNotFoundError: Raised if the requested DrAlgo is unrecognized.
            If the DrAlgo exists it may not have been registered.
    """

    if dr_method == "rpca_altproj":
        from .rpca_altproj import RPCAAltProj

        return RPCAAltProj

    if dr_method == "rsvd_density_power":
        from .rsvd_density_power import RSVDDensityPowerDR

        return RSVDDensityPowerDR

    if dr_method == "median_bg":
        from .median_bg import MedianBgAlgo

        return MedianBgAlgo

    if dr_method == "morph_open":
        from .morph_open import MorphOpenAlgo

        return MorphOpenAlgo

    if dr_method == "pysz_codec":
        from .pysz_codec import PyszCodecAlgo

        return PyszCodecAlgo

    if dr_method == "tv_rpca":
        from .tv_rpca import TVRPCAAltProj

        return TVRPCAAltProj

    if dr_method == "stable_pcp":
        from .stable_pcp import StablePCPAlgo

        return StablePCPAlgo

    if dr_method == "rpca_altproj_thresh":
        from .rpca_altproj_thresh import RPCAAltProjThresh

        return RPCAAltProjThresh

    if dr_method == "tv_reg_rpca":
        from .tv_reg_rpca import RPCATVADMM

        return RPCATVADMM

    if dr_method == "tv_reg_svd_rpca":
        from .tv_reg_svd_rpca import RPCATVSVDADMM

        return RPCATVSVDADMM

    if dr_method == "wavelet_bayes_shrink":
        from .wavelet_bayes_shrink import WaveletBayesShrink2D

        return WaveletBayesShrink2D

    if dr_method == "wavelet_bivariate_shrink":
        from .wavelet_bivariate_shrink import WaveletBivariateShrink2D

        return WaveletBivariateShrink2D

    if dr_method == "wavelet_hmt_shrink":
        from .wavelet_hmt_shrink import WaveletHMTShrink2D

        return WaveletHMTShrink2D

    if dr_method == "wavelet_simple_shrink":
        from .wavelet_simple_shrink import WaveletSimpleShrink2D

        return WaveletSimpleShrink2D

    if dr_method == "wavelet_sure_shrink":
        from .wavelet_sure_shrink import WaveletSureShrink2D

        return WaveletSureShrink2D

    if dr_method == "wavelet_bishrink_rpca":
        from .wavelet_bishrink_rpca import WaveletBishrinkThenRPCA

        return WaveletBishrinkThenRPCA
    if dr_method == "wavelet_bishrink_zerotree_compress":
        from .wavelet_bishrink_zerotree_compress import WaveletBivariateShrinkEZW2D

        return WaveletBivariateShrinkEZW2D

    if dr_method == "wavelet_spiht":
        from .wavelet_spiht import WaveletSPIHT2D

        return WaveletSPIHT2D

    if dr_method == "wavelet_dionisio":
        from .wavelet_dionisio import WaveletManualManip2D

        return WaveletManualManip2D
    if dr_method == "wavelet_quant_zerotree_compress":
        from .wavelet_quant_zerotree_compress import (
            WaveletBivariateShrinkEZW2D_noDenoise,
        )

        return WaveletBivariateShrinkEZW2D_noDenoise
