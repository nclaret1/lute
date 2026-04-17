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
    return float(np.median(np.abs(x)) / 0.6745)


def _expand_parent_to_child(parent: np.ndarray, child_shape: Tuple[int, int]) -> np.ndarray:
    p = np.asarray(parent)
    up = np.repeat(np.repeat(p, 2, axis=0), 2, axis=1)
    return up[: child_shape[0], : child_shape[1]]


def _laplacian_sigma_from_observed(y: np.ndarray) -> float:
    y = np.asarray(y, dtype=np.float64)
    if y.size == 0:
        return 0.0
    return float(np.sqrt(2.0) * np.mean(np.abs(y)))


def _gaussian_sigma_from_observed(y: np.ndarray) -> float:
    y = np.asarray(y, dtype=np.float64)
    if y.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(y * y)))


def _estimate_model3_sigmas(
    child_band: np.ndarray,
    parent_band: np.ndarray,
    sigma_n: float,
    observed_sigma_method: str = "laplacian",
) -> Tuple[float, float, float, float]:
    if observed_sigma_method == "laplacian":
        sigma_y1 = _laplacian_sigma_from_observed(child_band)
        sigma_y2 = _laplacian_sigma_from_observed(parent_band)
    elif observed_sigma_method == "gaussian":
        sigma_y1 = _gaussian_sigma_from_observed(child_band)
        sigma_y2 = _gaussian_sigma_from_observed(parent_band)
    else:
        raise ValueError

    sigma1 = float(np.sqrt(max(sigma_y1 * sigma_y1 - sigma_n * sigma_n, 0.0)))
    sigma2 = float(np.sqrt(max(sigma_y2 * sigma_y2 - sigma_n * sigma_n, 0.0)))
    return sigma1, sigma2, sigma_y1, sigma_y2


def _bishrink_model3_successive_substitution(
    y1: np.ndarray,
    y2: np.ndarray,
    sigma_n: float,
    sigma1: float,
    sigma2: float,
    *,
    max_iter: int = 100,
    tol: float = 1e-6,
    eps: float = 1e-12,
    use_deadzone: bool = True,
    threshold_scale: float = 1.0,
) -> Tuple[np.ndarray, np.ndarray, Dict[str, float]]:
    y1 = np.asarray(y1, dtype=np.float64)
    y2 = np.asarray(y2, dtype=np.float64)

    out1 = np.zeros_like(y1, dtype=np.float64)
    out2 = np.zeros_like(y2, dtype=np.float64)

    if sigma_n <= 0:
        out1[...] = y1
        out2[...] = y2
        return out1, out2, {"n_iter": 0.0, "active_frac": 1.0}

    sigma1 = float(max(sigma1, eps))
    sigma2 = float(max(sigma2, eps))
    sq3_sig2 = float(threshold_scale) * np.sqrt(3.0) * (sigma_n * sigma_n)

    if use_deadzone:
        deadzone = np.sqrt((y1 * sigma1) ** 2 + (y2 * sigma2) ** 2) <= sq3_sig2
    else:
        deadzone = np.zeros_like(y1, dtype=bool)

    active = ~deadzone
    if not np.any(active):
        return out1, out2, {"n_iter": 0.0, "active_frac": 0.0}

    w1 = y1.copy()
    w2 = y2.copy()
    w1[deadzone] = 0.0
    w2[deadzone] = 0.0

    for it in range(1, int(max_iter) + 1):
        r = np.sqrt((w1[active] / sigma1) ** 2 + (w2[active] / sigma2) ** 2)
        r = np.maximum(r, eps)

        new1 = y1[active] / (1.0 + sq3_sig2 / (sigma1 * sigma1 * r))
        new2 = y2[active] / (1.0 + sq3_sig2 / (sigma2 * sigma2 * r))

        d1 = np.max(np.abs(new1 - w1[active])) if new1.size else 0.0
        d2 = np.max(np.abs(new2 - w2[active])) if new2.size else 0.0

        w1[active] = new1
        w2[active] = new2

        if max(d1, d2) <= tol:
            break

    out1[...] = w1
    out2[...] = w2
    return out1, out2, {
        "n_iter": float(it),
        "active_frac": float(np.count_nonzero(active) / active.size),
    }

def _univariate_gaussian_shrink(
    y: np.ndarray,
    sigma_n: float,
    *,
    observed_sigma_method: str = "gaussian",
    eps: float = 1e-12,
) -> tuple[np.ndarray, dict[str, float]]:
    y = np.asarray(y, dtype=np.float64)

    if y.size == 0:
        return y.copy(), {"sigma": 0.0, "gain": 1.0}

    if sigma_n <= 0:
        return y.copy(), {"sigma": float(np.sqrt(np.mean(y * y))), "gain": 1.0}

    if observed_sigma_method == "gaussian":
        sigma_y = float(np.sqrt(np.mean(y * y)))
    elif observed_sigma_method == "laplacian":
        sigma_y = float(np.sqrt(2.0) * np.mean(np.abs(y)))
    else:
        raise ValueError("observed_sigma_method must be 'gaussian' or 'laplacian'")

    sigma2 = max(sigma_y * sigma_y - sigma_n * sigma_n, 0.0)

    gain = sigma2 / max(sigma2 + sigma_n * sigma_n, eps)

    return gain * y, {"sigma": float(np.sqrt(sigma2)), "gain": float(gain)}

@dataclass
class WaveletStorageInfo:
    coeff_arr: np.ndarray
    coeff_slices: Any
    coeff_shape: Tuple[int, int]
    wavelet: str
    level: int
    mode: str


class WaveletBivariateShrink2D(XhatDrAlgo):
    def __init__(
        self,
        n_components: Optional[int] = None,
        *,
        wavelet: str = "db2",
        level: Optional[int] = None,
        mode: str = "periodization",
        keep_coeffs: bool = False,
        observed_sigma_method: str = "laplacian",
        ss_max_iter: int = 100,
        ss_tol: float = 1e-6,
        use_deadzone: bool = True,
        center: bool = False,
        copy_: bool = True,
        verbose: bool = True,
        random_state: Optional[int] = None,
        tol: float = 1e-5,
        max_iter: int = 1,
        threshold_scale: float = 1.0,
        post_threshold_scale: float = 1e-2,
        coarsest_mode: str = "gaussian_univariate",
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
        self.keep_coeffs = keep_coeffs
        self.observed_sigma_method = str(observed_sigma_method).lower()
        self.ss_max_iter = int(ss_max_iter)
        self.ss_tol = float(ss_tol)
        self.use_deadzone = bool(use_deadzone)
        self.wavelet_storage_: Optional[WaveletStorageInfo] = None
        self.threshold_scale = float(threshold_scale)
        self.post_threshold_scale = float(post_threshold_scale)
        self.coarsest_mode = str(coarsest_mode)

    def compute_metrics(self, max_autocorr_lag: int = 200) -> Dict[str, Any]:
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

        model3 = getattr(self, "_model3_metrics_", {})
        if isinstance(model3, dict) and model3:
            metrics["model3"] = model3

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
        print("ENTERED WaveletBivariateShrink2D._fit_core", flush=True)
        self._storage_metrics_ = {}
        self._model3_metrics_ = {}

        X = np.asarray(Xc, dtype=np.float32, order="C")
        m, n = X.shape

        if self.level is None:
            wv = pywt.Wavelet(self.wavelet)
            max_lev_m = pywt.dwt_max_level(m, wv.dec_len)
            max_lev_n = pywt.dwt_max_level(n, wv.dec_len)
            level = int(max(1, min(max_lev_m, max_lev_n)))
        else:
            level = int(self.level)

        import time
        t0 = time.perf_counter()

        coeffs = pywt.wavedec2(X, wavelet=self.wavelet, level=level, mode=self.mode)
        cA = coeffs[0]
        details = list(coeffs[1:])

        (_, _, cD1) = details[-1]
        sigma_n = float(_mad_sigma(cD1))

        new_details: List[Tuple[np.ndarray, np.ndarray, np.ndarray]] = []

        nnz_before = 0
        nnz_after = 0

        for i, (cH, cV, cD) in enumerate(details):
            nnz_before += int(np.count_nonzero(cH) + np.count_nonzero(cV) + np.count_nonzero(cD))

            if i == 0:
                cH_t, infoH = _univariate_gaussian_shrink(
                    cH,
                    sigma_n=sigma_n,
                    observed_sigma_method="gaussian",
                )
                cV_t, infoV = _univariate_gaussian_shrink(
                    cV,
                    sigma_n=sigma_n,
                    observed_sigma_method="gaussian",
                )
                cD_t, infoD = _univariate_gaussian_shrink(
                    cD,
                    sigma_n=sigma_n,
                    observed_sigma_method="gaussian",
                )
            else:
                (pH0, pV0, pD0) = details[i - 1]

                pH = _expand_parent_to_child(pH0, cH.shape)
                pV = _expand_parent_to_child(pV0, cV.shape)
                pD = _expand_parent_to_child(pD0, cD.shape)

                s1_H, s2_H, _, _ = _estimate_model3_sigmas(
                    cH, pH0, sigma_n=sigma_n, observed_sigma_method=self.observed_sigma_method
                )
                s1_V, s2_V, _, _ = _estimate_model3_sigmas(
                    cV, pV0, sigma_n=sigma_n, observed_sigma_method=self.observed_sigma_method
                )
                s1_D, s2_D, _, _ = _estimate_model3_sigmas(
                    cD, pD0, sigma_n=sigma_n, observed_sigma_method=self.observed_sigma_method
                )

                cH_t, _, _ = _bishrink_model3_successive_substitution(
                    cH, pH, sigma_n=sigma_n, sigma1=s1_H, sigma2=s2_H,
                    max_iter=self.ss_max_iter, tol=self.ss_tol,
                    use_deadzone=self.use_deadzone, threshold_scale=self.threshold_scale,
                )
                cV_t, _, _ = _bishrink_model3_successive_substitution(
                    cV, pV, sigma_n=sigma_n, sigma1=s1_V, sigma2=s2_V,
                    max_iter=self.ss_max_iter, tol=self.ss_tol,
                    use_deadzone=self.use_deadzone, threshold_scale=self.threshold_scale,
                )
                cD_t, _, _ = _bishrink_model3_successive_substitution(
                    cD, pD, sigma_n=sigma_n, sigma1=s1_D, sigma2=s2_D,
                    max_iter=self.ss_max_iter, tol=self.ss_tol,
                    use_deadzone=self.use_deadzone, threshold_scale=self.threshold_scale,
                )

            cH_t = np.asarray(cH_t, dtype=np.float32)
            cV_t = np.asarray(cV_t, dtype=np.float32)
            cD_t = np.asarray(cD_t, dtype=np.float32)

            tau = float(self.post_threshold_scale) * float(sigma_n)
            if tau > 0.0:
                cH_t[np.abs(cH_t) < tau] = 0.0
                cV_t[np.abs(cV_t) < tau] = 0.0
                cD_t[np.abs(cD_t) < tau] = 0.0

            nnz_after += int(np.count_nonzero(cH_t) + np.count_nonzero(cV_t) + np.count_nonzero(cD_t))
            new_details.append((cH_t, cV_t, cD_t))

        coeffs_t = [np.asarray(cA, dtype=np.float32)] + new_details

        Xhat = pywt.waverec2(coeffs_t, wavelet=self.wavelet, mode=self.mode)
        Xhat = np.asarray(Xhat, dtype=np.float32)[:m, :n]
        resid = X - Xhat

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
            "model3_sigma_n_from": "HH1_MAD",
            "model3_observed_sigma_method": self.observed_sigma_method,
            "model3_wavelet_level": int(level),
            "model3_mode": str(self.mode),
            "model3_threshold_scale": float(self.threshold_scale),
        }

        self._model3_metrics_ = {
            "sigma_n": float(sigma_n),
            "observed_sigma_method": str(self.observed_sigma_method),
            "successive_substitution_max_iter": int(self.ss_max_iter),
            "successive_substitution_tol": float(self.ss_tol),
            "use_deadzone": int(self.use_deadzone),
            "threshold_scale": float(self.threshold_scale),
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
            print(f"[WaveletBivariateShrink2D] level={level}")
            print(f"[WaveletBivariateShrink2D] sigma_n={sigma_n:.6g}")
            print(f"[WaveletBivariateShrink2D] threshold_scale={self.threshold_scale:.6g}")
            print(f"[WaveletBivariateShrink2D] coeff nnz kept={kept:.2f}%")
            print(f"[WaveletBivariateShrink2D] rel_fro_error={err:.6g}")