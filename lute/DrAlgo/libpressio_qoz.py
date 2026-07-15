from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import numpy.typing as npt

from .DrAlgo import XhatDrAlgo, Factors, NotFittedError, _check_2d


class LibpressioQoZAlgo(XhatDrAlgo):
    """QoZ lossy compression as a DR algorithm via libpressio/roibin."""

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
        self.max_abs_err_: Optional[float] = None

    def _fit_core(self, Xc: npt.NDArray, **_: Any) -> None:
        Xc = _check_2d(Xc)
        m, n = Xc.shape

        from libpressio import PressioCompressor  # type: ignore
        from lute.tasks.sfx_zmq_cxi_utils import generate_libpressio_configuration

        mask = np.ones((m, n), dtype=np.uint8)
        lp_config = generate_libpressio_configuration(
            compressor="qoz",
            roi_window_size=self.roi_window_size,
            bin_size=self.bin_size,
            abs_error=self.abs_error,
            libpressio_mask=mask,
        )

        compressor = PressioCompressor.from_config(lp_config)
        data_4d = Xc.astype(np.float32).reshape(1, 1, m, n)
        compressed = compressor.encode(data_4d)
        decompressed = np.zeros_like(data_4d)
        compressor.decode(compressed, decompressed)
        X_hat = decompressed.reshape(m, n).astype(Xc.dtype)

        orig_bytes = int(Xc.size * Xc.itemsize)
        comp_bytes = len(compressed) if hasattr(compressed, "__len__") else int(compressed.nbytes)

        self.X_hat_ = X_hat
        self.Resid_ = X_hat - Xc
        self.compression_ratio_ = orig_bytes / comp_bytes if comp_bytes > 0 else np.inf
        self.compressed_bytes_ = comp_bytes
        self.max_abs_err_ = float(np.max(np.abs(self.Resid_)))

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
        metrics["storage"]["original_bytes"] = (
            int(self.X_hat_.size * self.X_hat_.itemsize) if self.X_hat_ is not None else None
        )
        metrics["quality"]["max_abs_err"] = self.max_abs_err_
        metrics["quality"]["abs_error_bound"] = self.abs_error
        return metrics

    def get_params(self, deep: bool = True) -> Dict[str, Any]:
        p = super().get_params(deep)
        p.update({
            "abs_error": self.abs_error,
            "bin_size": self.bin_size,
            "roi_window_size": self.roi_window_size,
        })
        return p

    def set_params(self, **params: Any) -> "LibpressioQoZAlgo":
        for k in ("abs_error", "bin_size", "roi_window_size"):
            if k in params:
                setattr(self, k, type(getattr(self, k))(params.pop(k)))
        return super().set_params(**params)
