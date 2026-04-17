# lute/DrAlgo/median_bg.py
from __future__ import annotations
from typing import Optional
import numpy as np, numpy.typing as npt
from scipy.ndimage import median_filter
from .DrAlgo import LSDrAlgo, Factors, _check_2d

class MedianBgAlgo(LSDrAlgo):
    """
    L = median_filter(X, size=footprint)  (background)
    S = X - L                              (peaks / high-frequency)
    """
    def __init__(self, *, size: int = 7, center: bool = True, copy_: bool = True,
                 verbose: bool = True):
        super().__init__(n_components=None, center=center, copy_=copy_, verbose=verbose)
        if size < 1 or size % 2 == 0:
            raise ValueError("`size` should be a positive odd integer.")
        self.size = int(size)

    def _fit_core(self, Xc: npt.NDArray, **_: dict) -> None:
        Xc = _check_2d(Xc)
        L = median_filter(Xc, size=(self.size, self.size)).astype(Xc.dtype)
        S = Xc - L
        self.low_rank_  = L
        self.sparse_    = S
        self.factors_   = Factors(L=L, S=S)
        self.errors_    = [0.0]
        self.final_error_ = 0.0
        self.timers_    = []

    def get_params(self, deep: bool = True):
        p = super().get_params(deep)
        p.update({"size": self.size})
        return p

    def set_params(self, **params):
        if "size" in params:
            size = int(params["size"])
            if size < 1 or size % 2 == 0:
                raise ValueError("`size` should be a positive odd integer.")
            self.size = size
        return super().set_params(**{k:v for k,v in params.items() if k!="size"})
