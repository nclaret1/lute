from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple, List, Dict

import numpy as np
import numpy.typing as npt
import pywt
import zlib

from .DrAlgo import XhatDrAlgo, Factors, _fro_norm

def _zlib_size_bytes(arr: np.ndarray, level: int = 6) -> int:
    b = np.ascontiguousarray(arr).tobytes()
    return len(zlib.compress(b, level))

def _mad_sigma(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=np.float64).ravel()
    if x.size == 0:
        return 0.0
    return float(np.median(np.abs(x - np.median(x))) / 0.6745)


def _soft_threshold(a: np.ndarray, t: float) -> np.ndarray:
    a = np.asarray(a)
    if t <= 0:
        return a
    return np.sign(a) * np.maximum(np.abs(a) - t, 0.0)


def _bayes_shrink_threshold(detail: np.ndarray, sigma: float, eps: float = 1e-12) -> float:
    if sigma <= 0:
        return 0.0
    v = float(np.var(detail))
    sx2 = max(v - sigma * sigma, 0.0)
    if sx2 <= 0:
        return float(np.max(np.abs(detail))) if detail.size else 0.0
    sx = np.sqrt(sx2)
    return float((sigma * sigma) / (sx + eps))


@dataclass
class WaveletStorageInfo:
    coeff_arr: np.ndarray
    coeff_slices: Any
    coeff_shape: Tuple[int, int]
    wavelet: str
    level: int
    mode: str


class WaveletBayesShrink2D(XhatDrAlgo):
    def __init__(
        self,
        n_components: Optional[int] = None,
        *,
        wavelet: str = "haar",
        level: Optional[int] = None,
        mode: str = "periodization",
        estimate_sigma_from: str = "HH1",
        keep_coeffs: bool = False,
        center: bool = False,
        copy_: bool = True,
        verbose: bool = False,
        random_state: Optional[int] = None,
        tol: float = 1e-5,
        max_iter: int = 1,
        thresh_mult: float = 2.0,
    ):
        super().__init__(
            n_components=n_components,
            center=center,
            copy_=copy_,
            verbose=verbose,
            random_state=random_state,
            tol=tol,
            max_iter=max_iter,
        )
        self.wavelet = wavelet
        self.level = level
        self.mode = mode
        self.estimate_sigma_from = estimate_sigma_from
        self.keep_coeffs = keep_coeffs
        self.thresh_mult = float(thresh_mult)
        self.wavelet_storage_: Optional[WaveletStorageInfo] = None


    def compute_metrics(self, max_autocorr_lag: int = 200)-> Dict[str, Any]:

        metrics = super().compute_metrics(max_autocorr_lag=max_autocorr_lag)
        storage = metrics.get("storage", {})
        if not isinstance(storage, dict):
            storage = {}

        extra = getattr(self, "_storage_metrics_", {})
        if isinstance(extra, dict) and extra:
            storage.update(extra)

            if "coeff_zlib_bytes" in extra:
                x_bytes = float(storage.get("X_storage_bytes", 0.0))
                c_bytes = float(extra["coeff_zlib_bytes"])
                storage["compression_ratio"] = x_bytes / max(c_bytes, 1.0)

        metrics["storage"] = storage
        self.metrics_ = metrics
        return metrics


    def factors(self) -> Factors:
        if self.X_hat_ is None or self.Resid_ is None:
            raise Exception("Not fitted.")
        f = Factors(X_hat=self.X_hat_, Resid=self.Resid_)
        if self.keep_coeffs and self.wavelet_storage_ is not None:
            f["coeff_arr"] = self.wavelet_storage_.coeff_arr
            f["coeff_shape"] = np.asarray(self.wavelet_storage_.coeff_shape, dtype=np.int64)
        return f

    def _fit_core(self, Xc: npt.NDArray, **kwargs: Any) -> None:
        self._storage_metrics_ = {}
        X = np.asarray(Xc, dtype=np.float32, order="C")
        m, n = X.shape

        if self.level is None:
            w = pywt.Wavelet(self.wavelet)
            max_lev_m = pywt.dwt_max_level(m, w.dec_len)
            max_lev_n = pywt.dwt_max_level(n, w.dec_len)
            level = int(max(1, min(max_lev_m, max_lev_n)))
        else:
            level = int(self.level)

        import time
        t0 = time.perf_counter()

        coeffs = pywt.wavedec2(X, wavelet=self.wavelet, level=level, mode=self.mode)
        cA = coeffs[0]
        details = list(coeffs[1:])

        (cH1, cV1, cD1) = details[-1]
        if self.estimate_sigma_from == "HH1":
            sigma = _mad_sigma(cD1)
        elif self.estimate_sigma_from == "all_finest":
            sigma = _mad_sigma(np.concatenate([cH1.ravel(), cV1.ravel(), cD1.ravel()]))
        else:
            raise ValueError

        new_details: List[Tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        nnz_before = 0
        nnz_after = 0

        for (cH, cV, cD) in details:
            nnz_before += int(np.count_nonzero(cH) + np.count_nonzero(cV) + np.count_nonzero(cD))

            tH = self.thresh_mult * _bayes_shrink_threshold(cH, sigma)
            tV = self.thresh_mult * _bayes_shrink_threshold(cV, sigma)
            tD = self.thresh_mult * _bayes_shrink_threshold(cD, sigma)

            cH_t = _soft_threshold(cH, tH)
            cV_t = _soft_threshold(cV, tV)
            cD_t = _soft_threshold(cD, tD)

            nnz_after += int(np.count_nonzero(cH_t) + np.count_nonzero(cV_t) + np.count_nonzero(cD_t))
            new_details.append((cH_t, cV_t, cD_t))

        coeffs_t = [cA] + new_details

        Xhat = pywt.waverec2(coeffs_t, wavelet=self.wavelet, mode=self.mode)
        Xhat = np.asarray(Xhat, dtype=np.float32)
        Xhat = Xhat[:m, :n]

        resid = X - Xhat
        print("resid max abs", float(np.max(np.abs(resid))))

        err = _fro_norm(resid) / (self._norm_X_ if self._norm_X_ else max(_fro_norm(X), 1e-12))
        self.errors_.append(float(err))
        self.final_error_ = float(err)
        self.n_iter_ = 1
        self.timers_.append(float(time.perf_counter() - t0))

        self.X_hat_ = Xhat
        self.Resid_ = resid

        coeff_arr, coeff_slices = pywt.coeffs_to_array(coeffs_t)
        coeff_arr = np.asarray(coeff_arr, dtype=np.float32, order="C")

        raw_bytes = int(coeff_arr.nbytes)
        zbytes = int(_zlib_size_bytes(coeff_arr, level=6))
        ratio = float(raw_bytes / max(zbytes, 1))

        self._storage_metrics_ = {
            "coeff_raw_bytes": raw_bytes,
            "coeff_zlib_bytes": zbytes,
            "coeff_zlib_ratio": ratio,
            "coeff_nnz": int(np.count_nonzero(coeff_arr)),
            "coeff_total": int(coeff_arr.size),
            "coeff_nnz_frac": float(np.count_nonzero(coeff_arr) / max(coeff_arr.size, 1)),
        }

        if self.keep_coeffs:
            self.wavelet_storage_ = WaveletStorageInfo(
                    coeff_arr=coeff_arr,
                    coeff_slices=coeff_slices,
                    coeff_shape=(m, n),
                    wavelet=self.wavelet,
                    level=level,
                    mode=self.mode,
                )

        if getattr(self, "verbose", False):
            kept = (nnz_after / max(nnz_before, 1)) * 100.0
            print(f"[WaveletBayesShrink2D] level={level} sigma≈{sigma:.4g} detail nnz kept ≈ {kept:.2f}%")
            print(f"[WaveletBayesShrink2D] rel_fro_error={err:.6g}")