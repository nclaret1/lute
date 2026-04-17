from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple, List, Dict, Sequence
import zlib

import numpy as np
import numpy.typing as npt
import pywt

from .DrAlgo import XhatDrAlgo, Factors, _fro_norm


def _zlib_size_bytes(arr: np.ndarray, level: int = 6) -> int:
    b = np.ascontiguousarray(arr).tobytes()
    return len(zlib.compress(b, level))


def _hard_threshold(a: np.ndarray, t: float) -> np.ndarray:
    a = np.asarray(a)
    if t <= 0:
        return a
    return np.where(np.abs(a) < t, 0.0, a)


def _soft_threshold(a: np.ndarray, t: float) -> np.ndarray:
    a = np.asarray(a)
    if t <= 0:
        return a
    return np.sign(a) * np.maximum(np.abs(a) - t, 0.0)


def _mad_sigma(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=np.float64).ravel()
    if x.size == 0:
        return 0.0
    return float(np.median(np.abs(x - np.median(x))) / 0.6745)


@dataclass
class WaveletStorageInfo:
    coeff_arr: np.ndarray
    coeff_slices: Any
    coeff_shape: Tuple[int, int]
    wavelet: str
    level: int
    mode: str


class WaveletManualManip2D(XhatDrAlgo):

    def __init__(
        self,
        n_components: Optional[int] = None,
        *,
        wavelet: str = "haar",
        level: Optional[int] = 5,
        mode: str = "symmetric",
        keep_coeffs: bool = False,
        center: bool = False,
        copy_: bool = True,
        verbose: bool = True,
        random_state: Optional[int] = None,
        tol: float = 1e-5,
        max_iter: int = 1,
        zero_rules=[
            {"level": -1, "which": ("LL",)},
        ],
        threshold_rules: Optional[Sequence[Dict[str, Any]]] = None,
        apply_mask: bool = False,
        mask: Optional[npt.ArrayLike] = None,
        thresh_mult: float = 2.0,
        entropy_level: int = 9,
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
        self.wavelet = str(wavelet)
        self.level = level
        self.mode = str(mode)
        self.keep_coeffs = bool(keep_coeffs)

        self.zero_rules = list(zero_rules) if zero_rules is not None else []
        self.threshold_rules = list(threshold_rules) if threshold_rules is not None else []

        self.apply_mask = bool(apply_mask)
        self.mask = None if mask is None else np.asarray(mask)
        self.thresh_mult = float(thresh_mult)
        self.entropy_level = int(entropy_level)

        self.wavelet_storage_: Optional[WaveletStorageInfo] = None
        self.levels_: Optional[List[Dict[str, np.ndarray]]] = None
        print(self.level, self.zero_rules)

    def compute_metrics(self, max_autocorr_lag: int = 200) -> Dict[str, Any]:
        metrics = super().compute_metrics(max_autocorr_lag=max_autocorr_lag)
        storage = metrics.get("storage", {})
        if not isinstance(storage, dict):
            storage = {}

        extra = getattr(self, "_storage_metrics_", {})
        if isinstance(extra, dict) and extra:
            storage.update(extra)

            # prefer explicit compressed payload bytes if present
            x_bytes = float(storage.get("X_storage_bytes", 0.0))
            c_bytes = float(
                storage.get(
                    "compressed_payload_bytes",
                    storage.get("coeff_zlib_bytes", 0.0),
                )
            )
            if c_bytes > 0:
                storage["compression_ratio"] = x_bytes / c_bytes

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

    def _resolve_level(self, level: int, n_levels: int) -> int:
        lvl = int(level)
        if lvl < 0:
            lvl = n_levels + lvl
        if lvl < 0 or lvl >= n_levels:
            raise ValueError(f"Invalid level {level}; valid range is [0, {n_levels - 1}] or negative indexing.")
        return lvl

    def _validate_band_name(self, band: str) -> None:
        if band not in ("LL", "LH", "HL", "HH"):
            raise ValueError(f"Invalid band '{band}'. Use one of: 'LL', 'LH', 'HL', 'HH'.")

    def _decompose_levels(self, X: np.ndarray, n_levels: int) -> List[Dict[str, np.ndarray]]:
        current = X
        levels: List[Dict[str, np.ndarray]] = []
        for _ in range(n_levels):
            LL, (LH, HL, HH) = pywt.dwt2(current, wavelet=self.wavelet, mode=self.mode)
            levels.append({
                "LL": np.asarray(LL, dtype=np.float32),
                "LH": np.asarray(LH, dtype=np.float32),
                "HL": np.asarray(HL, dtype=np.float32),
                "HH": np.asarray(HH, dtype=np.float32),
            })
            current = LL
        return levels

    def _reconstruct_levels(self, levels: List[Dict[str, np.ndarray]], out_shape: Tuple[int, int]) -> np.ndarray:
        R = levels[-1]["LL"]
        for l in reversed(range(len(levels))):
            R = pywt.idwt2(
                (R, (levels[l]["LH"], levels[l]["HL"], levels[l]["HH"])),
                wavelet=self.wavelet,
                mode=self.mode,
            )
        R = np.asarray(R, dtype=np.float32)
        return R[: out_shape[0], : out_shape[1]]

    def _estimate_default_threshold(self, levels: List[Dict[str, np.ndarray]]) -> float:
        hh_finest = levels[0]["HH"]
        sigma = _mad_sigma(hh_finest)
        return float(self.thresh_mult) * sigma

    def _apply_zero_rules(self, levels: List[Dict[str, np.ndarray]]) -> None:
        n_levels = len(levels)
        for rule in self.zero_rules:
            lvl = self._resolve_level(rule["level"], n_levels)
            which = tuple(rule.get("which", ("LH", "HL", "HH")))
            for band in which:
                self._validate_band_name(band)
                levels[lvl][band] = np.zeros_like(levels[lvl][band])

    def _apply_threshold_rules(self, levels: List[Dict[str, np.ndarray]]) -> None:
        n_levels = len(levels)
        default_t = self._estimate_default_threshold(levels)

        for rule in self.threshold_rules:
            lvl = self._resolve_level(rule["level"], n_levels)
            which = tuple(rule.get("which", ("LH", "HL", "HH")))
            mode = str(rule.get("mode", "hard")).lower()
            threshold = float(rule.get("threshold", default_t))

            if mode == "hard":
                thr_fn = _hard_threshold
            elif mode == "soft":
                thr_fn = _soft_threshold
            else:
                raise ValueError("Threshold rule mode must be 'hard' or 'soft'.")

            for band in which:
                self._validate_band_name(band)
                levels[lvl][band] = np.asarray(
                    thr_fn(levels[lvl][band], threshold),
                    dtype=np.float32,
                )

    def _fit_core(self, Xc: npt.NDArray, **kwargs: Any) -> None:
        self._storage_metrics_ = {}

        X = np.asarray(Xc, dtype=np.float32, order="C")
        m, n = X.shape

        if self.level is None:
            wv = pywt.Wavelet(self.wavelet)
            max_lev_m = pywt.dwt_max_level(m, wv.dec_len)
            max_lev_n = pywt.dwt_max_level(n, wv.dec_len)
            n_levels = int(max(1, min(max_lev_m, max_lev_n)))
        else:
            n_levels = int(self.level)
            if n_levels < 1:
                raise ValueError("level must be >= 1")

        import time
        t0 = time.perf_counter()

        levels = self._decompose_levels(X, n_levels=n_levels)

        self._apply_zero_rules(levels)
        self._apply_threshold_rules(levels)

        Xhat = self._reconstruct_levels(levels, out_shape=(m, n))

        if self.apply_mask:
            if self.mask is None:
                raise ValueError("apply_mask=True but mask is None.")
            mask = np.asarray(self.mask, dtype=np.float32)
            if mask.shape != Xhat.shape:
                raise ValueError(f"mask shape {mask.shape} does not match image shape {Xhat.shape}.")
            Xhat = Xhat * mask

        resid = X - Xhat

        err = _fro_norm(resid) / (self._norm_X_ if self._norm_X_ else max(_fro_norm(X), 1e-12))
        self.errors_.append(float(err))
        self.final_error_ = float(err)
        self.n_iter_ = 1
        self.timers_.append(float(time.perf_counter() - t0))

        self.X_hat_ = Xhat
        self.Resid_ = resid
        self.levels_ = levels

        # [cA_n, (cH_n, cV_n, cD_n), ..., (cH_1, cV_1, cD_1)]
        coeffs_t = [levels[-1]["LL"]]
        for lvl in reversed(levels):
            coeffs_t.append((lvl["LH"], lvl["HL"], lvl["HH"]))

        coeff_arr, coeff_slices = pywt.coeffs_to_array(coeffs_t)
        coeff_arr = np.asarray(coeff_arr, dtype=np.float32, order="C")

        if self.keep_coeffs:
            self.wavelet_storage_ = WaveletStorageInfo(
                coeff_arr=coeff_arr,
                coeff_slices=coeff_slices,
                coeff_shape=(m, n),
                wavelet=self.wavelet,
                level=n_levels,
                mode=self.mode,
            )


        coeff_raw_bytes = int(coeff_arr.nbytes)
        coeff_zlib_bytes = int(_zlib_size_bytes(coeff_arr, level=self.entropy_level))
        coeff_zlib_ratio = float(coeff_raw_bytes / max(coeff_zlib_bytes, 1))

        raw_bytes = X.astype(np.float32, order="C").tobytes(order="C")
        lossless_blob = zlib.compress(raw_bytes, self.entropy_level)
        lossless_bytes = len(lossless_blob)

        x_bytes = len(raw_bytes)
        lossless_ratio = x_bytes / max(lossless_bytes, 1)

        wavelet_bytes = coeff_zlib_bytes
        wavelet_vs_lossless = lossless_bytes / max(wavelet_bytes, 1)

        num_pixels = int(m * n)
        bpp_wavelet = (wavelet_bytes * 8.0) / max(num_pixels, 1)
        bpp_lossless = (lossless_bytes * 8.0) / max(num_pixels, 1)

        coeff_nnz = int(np.count_nonzero(coeff_arr))
        coeff_total = int(coeff_arr.size)

        self._storage_metrics_ = {
            "coeff_raw_bytes": int(coeff_raw_bytes),
            "coeff_zlib_bytes": int(coeff_zlib_bytes),
            "coeff_zlib_ratio": float(coeff_zlib_ratio),

            "compressed_payload_bytes": int(coeff_zlib_bytes),

            "coeff_nnz": int(coeff_nnz),
            "coeff_total": int(coeff_total),
            "coeff_nnz_frac": float(coeff_nnz / max(coeff_total, 1)),

            "lossless_zlib_bytes": int(lossless_bytes),
            "lossless_zlib_ratio": float(lossless_ratio),
            "wavelet_vs_lossless_ratio": float(wavelet_vs_lossless),

            "bpp_wavelet": float(bpp_wavelet),
            "bpp_lossless_zlib": float(bpp_lossless),

            "manual_wavelet_levels": int(n_levels),
            "manual_wavelet_mode": str(self.mode),
            "manual_zero_rule_count": int(len(self.zero_rules)),
            "manual_threshold_rule_count": int(len(self.threshold_rules)),
        }

        if getattr(self, "verbose", False):
            ratio = x_bytes / max(wavelet_bytes, 1.0)
            print(f"[WaveletManualManip2D] levels={n_levels} wavelet={self.wavelet} mode={self.mode}")
            print(f"[WaveletManualManip2D] zero_rules={len(self.zero_rules)} threshold_rules={len(self.threshold_rules)}")
            print(f"[WaveletManualManip2D] coeff_zlib_bytes={coeff_zlib_bytes}")
            print(f"[WaveletManualManip2D] lossless_zlib_bytes={lossless_bytes}")
            print(f"[WaveletManualManip2D] wavelet_vs_lossless_ratio={wavelet_vs_lossless:.6g}x")
            print(f"[WaveletManualManip2D] bpp_wavelet={bpp_wavelet:.6g}")
            print(f"[WaveletManualManip2D] rel_fro_error={err:.6g}")