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
    med = np.median(x)
    return float(np.median(np.abs(x - med)) / 0.6745)


def _expand_parent_to_child(
    parent: np.ndarray, child_shape: Tuple[int, int]
) -> np.ndarray:
    """
    Map parent coeffs (coarser scale) onto child grid by 2x nearest-neighbor expansion,
    then crop to child_shape. This implements parent-child mapping for decimated DWT.
    """
    p = np.asarray(parent)
    up = np.repeat(np.repeat(p, 2, axis=0), 2, axis=1)
    return up[: child_shape[0], : child_shape[1]]


# ---------- local statistics (no scipy) ----------


def _box_filter2d(x: np.ndarray, r: int) -> np.ndarray:
    """
    Fast 2D box filter via integral image. Window size = (2r+1)x(2r+1).
    Reflect padding to behave nicely at borders.
    """
    if r <= 0:
        return x.astype(np.float64, copy=False)

    x = np.asarray(x, dtype=np.float64)
    k = 2 * r + 1

    xp = np.pad(x, ((r, r), (r, r)), mode="reflect")
    # integral image with leading zero row/col
    ii = (
        np.pad(xp, ((1, 0), (1, 0)), mode="constant", constant_values=0.0)
        .cumsum(0)
        .cumsum(1)
    )

    # sum over kxk window using integral image
    s = ii[k:, k:] - ii[:-k, k:] - ii[k:, :-k] + ii[:-k, :-k]
    return s / float(k * k)


def _local_std(x: np.ndarray, r: int, eps: float = 1e-12) -> np.ndarray:
    """
    Local standard deviation in a (2r+1)x(2r+1) window.
    """
    x = np.asarray(x, dtype=np.float64)
    m1 = _box_filter2d(x, r)
    m2 = _box_filter2d(x * x, r)
    var = np.maximum(m2 - m1 * m1, 0.0)
    return np.sqrt(var + eps)


def _bishrink_classic(
    w: np.ndarray,
    p: np.ndarray,
    sigma_n: float,
    win_radius: int = 1,
    eps: float = 1e-12,
) -> np.ndarray:
    """
    Classic-ish bivariate shrinkage (Sendur & Selesnick “BiShrink” form):

        R = sqrt(w^2 + p^2)
        sigma_y = local std of w (or can use local std of R; classic variants differ)
        sigma = sqrt(max(sigma_y^2 - sigma_n^2, 0))
        lambda = sqrt(3) * sigma_n^2 / sigma
        w_hat = w * max(1 - lambda / R, 0)

    Notes:
    - Many descriptions estimate sigma_y locally per subband.
    - Using local std of w is common; using local std of R is also seen in some implementations.
    """
    w = np.asarray(w, dtype=np.float64)
    p = np.asarray(p, dtype=np.float64)

    if sigma_n <= 0:
        return w.astype(np.float64, copy=False)

    R = np.sqrt(w * w + p * p)  # magnitude
    # local observed std on child band
    sigma_y = _local_std(w, r=win_radius, eps=eps)
    # estimate underlying signal std
    sigma = np.sqrt(np.maximum(sigma_y * sigma_y - sigma_n * sigma_n, 0.0))

    lam = (np.sqrt(3.0) * (sigma_n * sigma_n)) / np.maximum(sigma, eps)
    scale = np.maximum(1.0 - lam / np.maximum(R, eps), 0.0)
    return w * scale


@dataclass
class WaveletStorageInfo:
    coeff_arr: np.ndarray
    coeff_slices: Any
    coeff_shape: Tuple[int, int]
    wavelet: str
    level: int
    mode: str


class WaveletBivariateShrink2D(XhatDrAlgo):
    """
    Decimated DWT + parent-child (interscale) bivariate shrinkage (classic BiShrink-style).

    Key points to match classic form:
    - sigma_n estimated from finest diagonal band (HH1) via MAD
    - local variance estimate per subband for sigma (windowed)
    - shrink rule: w_hat = w * max(1 - lambda/R, 0), R = sqrt(w^2 + p^2)
    - lambda = sqrt(3) * sigma_n^2 / sigma

    Parameters
    ----------
    wavelet : str
    level : Optional[int]
    mode : str
    win_radius : int
        Window radius r for local stats (r=1 => 3x3).
    keep_coeffs : bool
    """

    def __init__(
        self,
        n_components: Optional[int] = None,
        *,
        wavelet: str = "db2",
        level: Optional[int] = None,
        mode: str = "periodization",
        win_radius: int = 1,
        keep_coeffs: bool = False,
        center: bool = False,
        copy_: bool = True,
        verbose: bool = False,
        random_state: Optional[int] = None,
        tol: float = 1e-5,
        max_iter: int = 1,
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
        self.win_radius = int(win_radius)
        self.keep_coeffs = keep_coeffs
        self.wavelet_storage_: Optional[WaveletStorageInfo] = None

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

        metrics["storage"] = storage
        self.metrics_ = metrics
        return metrics

    def factors(self) -> Factors:
        if self.X_hat_ is None or self.Resid_ is None:
            raise Exception("Not fitted.")
        f = Factors(X_hat=self.X_hat_, Resid=self.Resid_)
        if self.keep_coeffs and self.wavelet_storage_ is not None:
            f["coeff_arr"] = self.wavelet_storage_.coeff_arr
            f["coeff_shape"] = np.asarray(
                self.wavelet_storage_.coeff_shape, dtype=np.int64
            )
        return f

    def _fit_core(self, Xc: npt.NDArray, **kwargs: Any) -> None:
        print("wrong fit core")
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
        details = list(
            coeffs[1:]
        )  # coarse->fine: [(cH_L,cV_L,cD_L), ..., (cH_1,cV_1,cD_1)]

        # ---- classic: sigma_n from finest diagonal HH1 (cD_1) via MAD
        cH1, cV1, cD1 = details[-1]
        sigma_n = float(_mad_sigma(cD1))

        new_details: List[Tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        nnz_before = 0
        nnz_after = 0

        for i, (cH, cV, cD) in enumerate(details):
            nnz_before += int(
                np.count_nonzero(cH) + np.count_nonzero(cV) + np.count_nonzero(cD)
            )

            if i == 0:
                pH = np.zeros_like(cH)
                pV = np.zeros_like(cV)
                pD = np.zeros_like(cD)
            else:
                pH0, pV0, pD0 = details[i - 1]
                pH = _expand_parent_to_child(pH0, cH.shape)
                pV = _expand_parent_to_child(pV0, cV.shape)
                pD = _expand_parent_to_child(pD0, cD.shape)

            # classic BiShrink-style per orientation
            cH_t = _bishrink_classic(
                cH, pH, sigma_n=sigma_n, win_radius=self.win_radius
            ).astype(np.float64)
            cV_t = _bishrink_classic(
                cV, pV, sigma_n=sigma_n, win_radius=self.win_radius
            ).astype(np.float64)
            cD_t = _bishrink_classic(
                cD, pD, sigma_n=sigma_n, win_radius=self.win_radius
            ).astype(np.float64)

            nnz_after += int(
                np.count_nonzero(cH_t) + np.count_nonzero(cV_t) + np.count_nonzero(cD_t)
            )
            new_details.append(
                (
                    cH_t.astype(np.float32),
                    cV_t.astype(np.float32),
                    cD_t.astype(np.float32),
                )
            )

        coeffs_t = [cA.astype(np.float32)] + new_details

        Xhat = pywt.waverec2(coeffs_t, wavelet=self.wavelet, mode=self.mode)
        Xhat = np.asarray(Xhat, dtype=np.float32)[:m, :n]
        resid = X - Xhat

        err = _fro_norm(resid) / (
            self._norm_X_ if self._norm_X_ else max(_fro_norm(X), 1e-12)
        )
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
            "coeff_nnz_frac": float(
                np.count_nonzero(coeff_arr) / max(coeff_arr.size, 1)
            ),
            "bishrink_sigma_n_from": "HH1_MAD",
            "bishrink_win_radius": int(self.win_radius),
            "bishrink_wavelet_level": int(level),
            "bishrink_mode": str(self.mode),
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
            print(f"[WaveletBivariateShrink2D] level={level} nnz kept ≈ {kept:.2f}%")
            print(f"[WaveletBivariateShrink2D] sigma_n(HH1 MAD)={sigma_n:.6g}")
            print(f"[WaveletBivariateShrink2D] rel_fro_error={err:.6g}")
