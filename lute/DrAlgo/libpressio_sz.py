from __future__ import annotations

import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Dict, Optional, Tuple

import numpy as np
import numpy.typing as npt

from .DrAlgo import XhatDrAlgo, Factors, NotFittedError, _check_2d

# Spawned worker process: starts a fresh Python interpreter with no mpi4py loaded,
# so libpressio's MPI (ps-4.6.1 OpenMPI) cannot conflict with the task's mpi4py
# (ps_20241122 OpenMPI, incompatible ABI — sizeof(MPI_Op) 40 vs 56).
_pool: Optional[ProcessPoolExecutor] = None


def _get_pool() -> ProcessPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ProcessPoolExecutor(
            max_workers=1, mp_context=multiprocessing.get_context("spawn")
        )
    return _pool


def _pressio_worker(
    data_bytes: bytes,
    shape: Tuple[int, int],
    dtype_str: str,
    abs_error: float,
    bin_size: int,
    roi_window_size: int,
) -> Dict[str, Any]:
    import sys
    import numpy as np

    # _pressio.so was compiled against ps-4.6.1's mpi4py (OpenMPI 4.x). The task
    # environment activates ps_20241122's mpi4py (OpenMPI 5.x), which has an
    # incompatible ABI. Prepend ps-4.6.1's site-packages so the compatible mpi4py
    # is imported first and satisfies _pressio.so's __pyx_capi__ lookup.
    _PS461 = "/sdf/group/lcls/ds/ana/sw/conda2/inst/envs/ps-4.6.1/lib/python3.9/site-packages"
    if _PS461 not in sys.path:
        sys.path.insert(0, _PS461)
    import mpi4py

    mpi4py.rc.initialize = (
        False  # spawned subprocess is not an MPI process; skip MPI_Init
    )
    import mpi4py.MPI  # noqa: F401 — provides __pyx_capi__ for _pressio.so type registration

    from libpressio import PressioCompressor  # type: ignore

    dtype = np.dtype(dtype_str)
    Xc = np.frombuffer(data_bytes, dtype=dtype).reshape(shape)
    m, n = shape

    # Use SZ3 directly — roibin with centers=None (no peaks) outputs all zeros.
    lp_config = {
        "compressor_id": "pressio",
        "early_config": {"pressio": {"pressio:compressor": "sz3"}},
        "compressor_config": {"pressio": {"pressio:abs": abs_error}},
        "name": "pressio",
    }
    compressor = PressioCompressor.from_config(lp_config)
    data_2d = Xc.astype(np.float32)
    compressed = compressor.encode(data_2d)
    decompressed = compressor.decode(compressed, np.zeros_like(data_2d))
    X_hat = decompressed.astype(dtype)

    import zlib

    comp_bytes = (
        len(compressed) if hasattr(compressed, "__len__") else int(compressed.nbytes)
    )
    lossless_bytes = len(zlib.compress(Xc.astype(np.float32, order="C").tobytes(), 6))
    return {
        "X_hat_bytes": X_hat.tobytes(),
        "comp_bytes": comp_bytes,
        "orig_bytes": int(Xc.size * Xc.itemsize),
        "lossless_bytes": lossless_bytes,
        "max_abs_err": float(
            np.max(np.abs(X_hat.astype(np.float64) - Xc.astype(np.float64)))
        ),
    }


class LibpressioSZ3Algo(XhatDrAlgo):
    """SZ3 lossy compression as a DR algorithm via libpressio/roibin."""

    def __init__(
        self,
        *,
        abs_error: float = 10.0,
        bin_size: int = 2,
        roi_window_size: int = 9,
        center: bool = False,
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
        self.bin_size = int(bin_size)
        self.roi_window_size = int(roi_window_size)
        self.X_hat_: Optional[npt.NDArray] = None
        self.Resid_: Optional[npt.NDArray] = None
        self.compression_ratio_: Optional[float] = None
        self.compressed_bytes_: Optional[int] = None
        self.lossless_bytes_: Optional[int] = None
        self.sz3_vs_lossless_: Optional[float] = None
        self.max_abs_err_: Optional[float] = None

    def _fit_core(self, Xc: npt.NDArray, **_: Any) -> None:
        Xc = _check_2d(Xc)
        m, n = Xc.shape

        result = (
            _get_pool()
            .submit(
                _pressio_worker,
                Xc.tobytes(),
                (m, n),
                Xc.dtype.str,
                self.abs_error,
                self.bin_size,
                self.roi_window_size,
            )
            .result()
        )

        X_hat = (
            np.frombuffer(result["X_hat_bytes"], dtype=Xc.dtype).reshape(m, n).copy()
        )
        orig_bytes = result["orig_bytes"]
        comp_bytes = result["comp_bytes"]

        lossless_bytes = result["lossless_bytes"]
        self.X_hat_ = X_hat
        self.Resid_ = X_hat - Xc
        self.compression_ratio_ = orig_bytes / comp_bytes if comp_bytes > 0 else np.inf
        self.compressed_bytes_ = comp_bytes
        self.lossless_bytes_ = lossless_bytes
        self.sz3_vs_lossless_ = (
            lossless_bytes / comp_bytes if comp_bytes > 0 else np.inf
        )
        self.max_abs_err_ = result["max_abs_err"]

        denom = float(np.linalg.norm(Xc, ord="fro")) or 1.0
        self.errors_ = [float(np.linalg.norm(self.Resid_, ord="fro") / denom)]
        self.final_error_ = self.errors_[-1]
        self.timers_: list = []
        self.factors_ = Factors(X_hat=self.X_hat_, Resid=self.Resid_)

    def factors(self) -> Factors:
        if self.X_hat_ is None:
            raise NotFittedError
        return self.factors_

    def _reconstruct(self, f: Factors) -> npt.NDArray:
        return f["X_hat"]

    def compute_metrics(self, max_autocorr_lag: int = 200) -> Dict[str, Any]:
        metrics = super().compute_metrics(max_autocorr_lag)
        metrics["storage"]["compression_ratio"] = self.compression_ratio_
        metrics["storage"]["compressed_bytes"] = self.compressed_bytes_
        metrics["storage"]["lossless_bytes"] = self.lossless_bytes_
        metrics["storage"]["sz3_vs_lossless"] = self.sz3_vs_lossless_
        metrics["storage"]["original_bytes"] = (
            int(self.X_hat_.size * self.X_hat_.itemsize)
            if self.X_hat_ is not None
            else None
        )
        metrics["quality"]["max_abs_err"] = self.max_abs_err_
        metrics["quality"]["abs_error_bound"] = self.abs_error
        return metrics

    def get_params(self, deep: bool = True) -> Dict[str, Any]:
        p = super().get_params(deep)
        p.update(
            {
                "abs_error": self.abs_error,
                "bin_size": self.bin_size,
                "roi_window_size": self.roi_window_size,
            }
        )
        return p

    def set_params(self, **params: Any) -> "LibpressioSZ3Algo":
        for k in ("abs_error", "bin_size", "roi_window_size"):
            if k in params:
                setattr(self, k, type(getattr(self, k))(params.pop(k)))
        return super().set_params(**params)
