# lute/DrAlgo/pysz_codec.py
from __future__ import annotations
from typing import Dict, Any, Optional, Literal
import numpy as np
import numpy.typing as npt
from pysz import sz


from .DrAlgo import XhatDrAlgo, Factors, NotFittedError, _check_2d

AlgoName = Literal["INTERP_LORENZO", "INTERP", "LORENZO_REG", "LOSSLESS"]


class PySZCodecAlgo(XhatDrAlgo):
    def __init__(
        self,
        *,
        abs_error: float = 1e-3,
        sz_algo: AlgoName = "INTERP_LORENZO",
        center: bool = True,
        copy_: bool = True,
        verbose: bool = True,
        random_state: Optional[int] = None,
        tol: float = 1e-5,
        max_iter: int = 1,
    ):
        super().__init__(
            n_components=None,
            center=center,
            copy_=copy_,
            verbose=verbose,
            random_state=random_state,
            tol=tol,
            max_iter=max_iter,
        )
        self.abs_error = float(abs_error)
        self.sz_algo: AlgoName = sz_algo
        self.X_hat_: Optional[npt.NDArray] = None
        self.Resid_: Optional[npt.NDArray] = None
        self.compressed_bytes_: Optional[int] = None
        self.compression_ratio_: Optional[float] = None
        self.max_abs_err_: Optional[float] = None

    def _fit_core(self, Xc: npt.NDArray, **_: Any) -> None:

        Xc = _check_2d(Xc)
        m, n = Xc.shape
        dtype = Xc.dtype
        if dtype not in (np.float32, np.float64):
            Xc = Xc.astype(np.float32, copy=False)
            dtype = Xc.dtype
        cfg = sz.pyConfig((m, n))
        cfg.errorBoundMode = sz.pyConfig.EB.ABS
        cfg.absErrorBound = float(self.abs_error)
        algo_enum = _algo_string_to_enum(self.sz_algo)
        cfg.cmprAlgo = algo_enum
        cbuf = sz.compress(Xc, cfg)
        Xhat = sz.decompress(cbuf, cfg).astype(dtype, copy=False)
        self.Resid_ = Xhat - Xc

        orig_bytes = Xc.size * Xc.itemsize
        comp_bytes = int(len(cbuf)) if hasattr(cbuf, "__len__") else int(cbuf.nbytes)
        self.X_hat_ = Xhat
        self._Xc_ = Xc
        self.compressed_bytes_ = comp_bytes
        self.compression_ratio_ = (
            (orig_bytes / comp_bytes) if comp_bytes > 0 else np.inf
        )

        self.max_abs_err_ = float(np.max(np.abs(self.Resid_))) if Xc.size else 0.0
        denom = float(np.linalg.norm(Xc, ord="fro")) or 1.0

        self.errors_ = [float(np.linalg.norm(self.Resid_, ord="fro") / denom)]
        self.final_error_ = self.errors_[-1]
        self.timers_ = []
        self.factors_ = Factors(Xhat=self.X_hat_, Resid=self.Resid_)

    def factors(self) -> Factors:
        if self.X_hat_ is None:
            raise NotFittedError
        return self.factors_

    def _reconstruct(self, f: Factors) -> npt.NDArray:
        return f["Xhat"]

    def get_params(self, deep: bool = True) -> Dict[str, Any]:
        p = super().get_params(deep)
        p.update({"abs_error": self.abs_error, "sz_algo": self.sz_algo})
        return p

    def set_params(self, **params: Any) -> "PySZCodecAlgo":
        if "abs_error" in params:
            self.abs_error = float(params.pop("abs_error"))
        if "sz_algo" in params:
            val = str(params.pop("sz_algo"))
            _ = _algo_string_to_enum(val)
            self.sz_algo = val
        return super().set_params(**params)


def _algo_string_to_enum(name: str):
    name = name.upper()
    try:
        return getattr(sz.pyConfig.ALGO, name)
    except AttributeError:
        valid = [a for a in dir(sz.pyConfig.ALGO) if not a.startswith("_")]
        raise ValueError(f"Unknown SZ algo '{name}'. Valid: {valid}")
