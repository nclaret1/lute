from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple, List, Dict, Sequence
import json
import struct
import zlib

import numpy as np
import numpy.typing as npt
import pywt

from .wavelet_bivariate_shrink import (
    WaveletBivariateShrink2D,
    WaveletStorageInfo,
)
from .DrAlgo import Factors, _fro_norm


def _quantize(arr: np.ndarray, step: float) -> np.ndarray:
    if step <= 0:
        raise ValueError("quantization step must be > 0")
    return np.rint(np.asarray(arr, dtype=np.float64) / float(step)).astype(np.int32)


def _dequantize(q: np.ndarray, step: float) -> np.ndarray:
    return np.asarray(q, dtype=np.float64) * float(step)


def _smallest_signed_int_dtype(max_abs: int) -> np.dtype:
    if max_abs <= np.iinfo(np.int8).max:
        return np.int8
    if max_abs <= np.iinfo(np.int16).max:
        return np.int16
    return np.int32


def _children_indices(
    r: int, c: int, child_shape: Tuple[int, int]
) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    r0 = 2 * r
    c0 = 2 * c
    for dr in (0, 1):
        rr = r0 + dr
        if rr >= child_shape[0]:
            continue
        for dc in (0, 1):
            cc = c0 + dc
            if cc >= child_shape[1]:
                continue
            out.append((rr, cc))
    return out


def _compute_subtree_zero_flags(bands: Sequence[np.ndarray]) -> List[np.ndarray]:
    """
    bands: coarse -> fine quantized detail bands for one orientation.
    flags[level][r,c] = True if coefficient is zero AND all descendants are zero.
    """
    L = len(bands)
    flags: List[np.ndarray] = [np.zeros_like(b, dtype=bool) for b in bands]
    if L == 0:
        return flags

    flags[-1] = bands[-1] == 0

    for lev in range(L - 2, -1, -1):
        curr = bands[lev]
        child_flags = flags[lev + 1]
        child_shape = bands[lev + 1].shape
        out = np.zeros_like(curr, dtype=bool)

        for r in range(curr.shape[0]):
            for c in range(curr.shape[1]):
                if curr[r, c] != 0:
                    out[r, c] = False
                    continue

                kids = _children_indices(r, c, child_shape)
                if not kids:
                    out[r, c] = True
                else:
                    out[r, c] = all(child_flags[rr, cc] for rr, cc in kids)

        flags[lev] = out

    return flags


def _encode_zerotree_bandstack(
    bands: Sequence[np.ndarray],
) -> Tuple[np.ndarray, np.ndarray, Dict[str, int]]:
    """
    EZW-like coding on one orientation stack (coarse -> fine).

    Symbols:
      0 = ZTR  : zero tree root
      1 = IZR  : isolated zero
      2 = POS  : positive nonzero
      3 = NEG  : negative nonzero

    Returns:
      symbols   : uint8 array
      magnitudes: positive ints for nonzero coefficients
      stats     : symbol counts
    """
    if len(bands) == 0:
        empty_u8 = np.zeros((0,), dtype=np.uint8)
        empty_i16 = np.zeros((0,), dtype=np.int16)
        return empty_u8, empty_i16, {"ZTR": 0, "IZR": 0, "POS": 0, "NEG": 0}

    subtree_zero = _compute_subtree_zero_flags(bands)
    symbols: List[int] = []
    mags: List[int] = []

    stats = {"ZTR": 0, "IZR": 0, "POS": 0, "NEG": 0}

    def visit(lev: int, r: int, c: int) -> None:
        v = int(bands[lev][r, c])

        if v == 0 and subtree_zero[lev][r, c]:
            symbols.append(0)
            stats["ZTR"] += 1
            return

        if v == 0:
            symbols.append(1)
            stats["IZR"] += 1
        elif v > 0:
            symbols.append(2)
            mags.append(v)
            stats["POS"] += 1
        else:
            symbols.append(3)
            mags.append(-v)
            stats["NEG"] += 1

        if lev + 1 >= len(bands):
            return

        child_shape = bands[lev + 1].shape
        for rr, cc in _children_indices(r, c, child_shape):
            visit(lev + 1, rr, cc)

    roots = bands[0]
    for r in range(roots.shape[0]):
        for c in range(roots.shape[1]):
            visit(0, r, c)

    max_abs = max(mags) if mags else 0
    mag_dtype = _smallest_signed_int_dtype(max_abs)

    return (
        np.asarray(symbols, dtype=np.uint8),
        np.asarray(mags, dtype=mag_dtype),
        stats,
    )


@dataclass
class WaveletCompressedInfo:
    payload: bytes
    payload_bytes: int
    header_bytes: int
    approx_bytes: int
    tree_symbol_bytes: int
    tree_magnitude_bytes: int
    q_approx_step: float
    q_detail_steps: List[float]


class WaveletBivariateShrinkEZW2D_noDenoise(WaveletBivariateShrink2D):

    def __init__(
        self,
        n_components: Optional[int] = None,
        *,
        wavelet: str = "db2",
        level: Optional[int] = None,
        mode: str = "symmetric",
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
        q_approx_step: float = 1.0,
        q_detail_step: float = 2.0,
        q_detail_growth: float = 1.35,
        entropy_level: int = 9,
        keep_payload: bool = False,
    ):
        super().__init__(
            n_components=n_components,
            wavelet=wavelet,
            level=level,
            mode=mode,
            keep_coeffs=keep_coeffs,
            observed_sigma_method=observed_sigma_method,
            ss_max_iter=ss_max_iter,
            ss_tol=ss_tol,
            use_deadzone=use_deadzone,
            center=center,
            copy_=copy_,
            verbose=verbose,
            random_state=random_state,
            tol=tol,
            max_iter=max_iter,
            threshold_scale=threshold_scale,
            post_threshold_scale=post_threshold_scale,
            coarsest_mode=coarsest_mode,
        )
        self.q_approx_step = float(q_approx_step)
        self.q_detail_step = float(q_detail_step)
        self.q_detail_growth = float(q_detail_growth)
        self.entropy_level = int(entropy_level)
        self.keep_payload = bool(keep_payload)

        self.compressed_info_: Optional[WaveletCompressedInfo] = None
        self.quantized_wavelet_storage_: Optional[WaveletStorageInfo] = None

    def factors(self) -> Factors:
        if self.X_hat_ is None or self.Resid_ is None:
            raise Exception("Not fitted.")
        f = Factors(X_hat=self.X_hat_, Resid=self.Resid_)

        if self.keep_coeffs and self.quantized_wavelet_storage_ is not None:
            f["qcoeff_arr"] = self.quantized_wavelet_storage_.coeff_arr
            f["qcoeff_shape"] = np.asarray(
                self.quantized_wavelet_storage_.coeff_shape, dtype=np.int64
            )

        if self.keep_payload and self.compressed_info_ is not None:
            f["compressed_payload_bytes"] = np.asarray(
                [self.compressed_info_.payload_bytes], dtype=np.int64
            )
        return f

    def compute_metrics(self, max_autocorr_lag: int = 200) -> Dict[str, Any]:
        metrics = super().compute_metrics(max_autocorr_lag=max_autocorr_lag)
        storage = metrics.get("storage", {})
        if not isinstance(storage, dict):
            storage = {}

        extra = getattr(self, "_compression_metrics_", {})
        if isinstance(extra, dict) and extra:
            storage.update(extra)
            x_bytes = float(storage.get("X_storage_bytes", 0.0))
            c_bytes = float(storage.get("compressed_payload_bytes", 0.0))
            if c_bytes > 0:
                storage["compression_ratio"] = x_bytes / c_bytes

        ezw = getattr(self, "_ezw_metrics_", {})
        if isinstance(ezw, dict) and ezw:
            metrics["ezw"] = ezw

        metrics["storage"] = storage
        self.metrics_ = metrics
        return metrics

    def _serialize_payload(
        self,
        qA: np.ndarray,
        qdetails: List[Tuple[np.ndarray, np.ndarray, np.ndarray]],
        *,
        original_shape: Tuple[int, int],
    ) -> WaveletCompressedInfo:
        max_abs_ca = int(np.max(np.abs(qA))) if qA.size else 0
        ca_dtype = _smallest_signed_int_dtype(max_abs_ca)
        qA_cast = np.asarray(qA, dtype=ca_dtype, order="C")
        approx_bytes_raw = qA_cast.tobytes(order="C")
        approx_bytes = zlib.compress(approx_bytes_raw, self.entropy_level)

        orient_names = ("H", "V", "D")
        tree_symbol_bytes_total = 0
        tree_mag_bytes_total = 0
        symbol_stats: Dict[str, Dict[str, int]] = {}
        detail_shapes: Dict[str, List[Tuple[int, int]]] = {}
        chunks: List[bytes] = []

        def pack_chunk(name: str, payload: bytes) -> bytes:
            name_b = name.encode("ascii")
            return (
                struct.pack("<I", len(name_b))
                + name_b
                + struct.pack("<Q", len(payload))
                + payload
            )

        for orient_idx, orient_name in enumerate(orient_names):
            bands = [
                np.asarray(qdetails[lev][orient_idx], dtype=np.int32, order="C")
                for lev in range(len(qdetails))
            ]
            detail_shapes[orient_name] = [tuple(map(int, b.shape)) for b in bands]

            symbols, mags, stats = _encode_zerotree_bandstack(bands)
            symbol_stats[orient_name] = stats

            sym_blob = zlib.compress(symbols.tobytes(order="C"), self.entropy_level)
            mag_blob = zlib.compress(
                np.asarray(mags, order="C").tobytes(order="C"), self.entropy_level
            )

            tree_symbol_bytes_total += len(sym_blob)
            tree_mag_bytes_total += len(mag_blob)

            chunks.append(pack_chunk(f"{orient_name}_sym", sym_blob))
            chunks.append(pack_chunk(f"{orient_name}_mag", mag_blob))

        q_steps = [
            float(self.q_detail_step * (self.q_detail_growth**lev))
            for lev in range(len(qdetails))
        ]

        header = {
            "kind": "wavelet_ezw_v1",
            "shape": [int(original_shape[0]), int(original_shape[1])],
            "wavelet": self.wavelet,
            "mode": self.mode,
            "level": int(len(qdetails)),
            "q_approx_step": float(self.q_approx_step),
            "q_detail_steps": q_steps,
            "ca_shape": [int(qA.shape[0]), int(qA.shape[1])],
            "ca_dtype": np.dtype(ca_dtype).name,
            "detail_shapes": detail_shapes,
            "symbol_stats": symbol_stats,
        }
        header_blob = zlib.compress(
            json.dumps(header, separators=(",", ":")).encode("utf-8"),
            self.entropy_level,
        )

        payload_parts = [
            pack_chunk("header", header_blob),
            pack_chunk("cA", approx_bytes),
        ] + chunks

        payload = b"".join(payload_parts)

        self._ezw_metrics_ = {
            "symbol_stats": symbol_stats,
            "q_approx_step": float(self.q_approx_step),
            "q_detail_steps": q_steps,
        }

        return WaveletCompressedInfo(
            payload=payload,
            payload_bytes=len(payload),
            header_bytes=len(header_blob),
            approx_bytes=len(approx_bytes),
            tree_symbol_bytes=tree_symbol_bytes_total,
            tree_magnitude_bytes=tree_mag_bytes_total,
            q_approx_step=float(self.q_approx_step),
            q_detail_steps=q_steps,
        )

    def _fit_core(self, Xc: npt.NDArray, **kwargs: Any) -> None:
        self._storage_metrics_ = {}
        self._model3_metrics_ = {}
        self._compression_metrics_ = {}
        self._ezw_metrics_ = {}

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
        details = list(coeffs[1:])  # coarse -> fine

        # 1) No shrinkage, keep detail coeffs as-is
        passthrough_details: List[Tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        nnz_before = 0
        nnz_after = 0

        for cH, cV, cD in details:
            nnz_before += int(
                np.count_nonzero(cH) + np.count_nonzero(cV) + np.count_nonzero(cD)
            )

            cH_t = np.asarray(cH, dtype=np.float32)
            cV_t = np.asarray(cV, dtype=np.float32)
            cD_t = np.asarray(cD, dtype=np.float32)

            nnz_after += int(
                np.count_nonzero(cH_t) + np.count_nonzero(cV_t) + np.count_nonzero(cD_t)
            )
            passthrough_details.append((cH_t, cV_t, cD_t))

        # 2) Quantization
        qA = _quantize(np.asarray(cA, dtype=np.float32), self.q_approx_step)

        qdetails: List[Tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        detail_steps: List[float] = []
        for lev, (cH_t, cV_t, cD_t) in enumerate(passthrough_details):
            step = float(self.q_detail_step * (self.q_detail_growth**lev))
            detail_steps.append(step)
            qH = _quantize(cH_t, step)
            qV = _quantize(cV_t, step)
            qD = _quantize(cD_t, step)
            qdetails.append((qH, qV, qD))

        # 3) Tree significance coding + 4) entropy coding
        compressed_info = self._serialize_payload(qA, qdetails, original_shape=(m, n))
        self.compressed_info_ = compressed_info

        # 5) Reconstruct from quantized coeffs
        cA_rec = _dequantize(qA, self.q_approx_step).astype(np.float32)
        details_rec: List[Tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        for lev, (qH, qV, qD) in enumerate(qdetails):
            step = detail_steps[lev]
            details_rec.append(
                (
                    _dequantize(qH, step).astype(np.float32),
                    _dequantize(qV, step).astype(np.float32),
                    _dequantize(qD, step).astype(np.float32),
                )
            )

        coeffs_q = [cA_rec] + details_rec

        Xhat = pywt.waverec2(coeffs_q, wavelet=self.wavelet, mode=self.mode)
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

        coeff_arr_q, coeff_slices_q = pywt.coeffs_to_array(coeffs_q)
        coeff_arr_q = np.asarray(coeff_arr_q, dtype=np.float32, order="C")

        if self.keep_coeffs:
            self.quantized_wavelet_storage_ = WaveletStorageInfo(
                coeff_arr=coeff_arr_q,
                coeff_slices=coeff_slices_q,
                coeff_shape=(m, n),
                wavelet=self.wavelet,
                level=level,
                mode=self.mode,
            )

        # Lossless baseline compression (zlib on raw fp32)
        raw_bytes = X.astype(np.float32, order="C").tobytes(order="C")
        lossless_blob = zlib.compress(raw_bytes, self.entropy_level)
        lossless_bytes = len(lossless_blob)

        x_bytes = len(raw_bytes)
        lossless_ratio = x_bytes / max(lossless_bytes, 1)

        wavelet_bytes = compressed_info.payload_bytes
        wavelet_vs_lossless = lossless_bytes / max(wavelet_bytes, 1)

        coeff_nnz = int(np.count_nonzero(coeff_arr_q))
        coeff_total = int(coeff_arr_q.size)

        num_pixels = int(m * n)
        bpp_wavelet = (wavelet_bytes * 8.0) / max(num_pixels, 1)
        bpp_lossless = (lossless_bytes * 8.0) / max(num_pixels, 1)

        self._storage_metrics_ = {
            "coeff_nnz": coeff_nnz,
            "coeff_total": coeff_total,
            "coeff_nnz_frac": float(coeff_nnz / max(coeff_total, 1)),
            "model3_sigma_n_from": "disabled",
            "model3_observed_sigma_method": "disabled",
            "model3_wavelet_level": int(level),
            "model3_mode": str(self.mode),
            "model3_threshold_scale": 0.0,
        }

        self._model3_metrics_ = {
            "sigma_n": np.nan,
            "observed_sigma_method": "disabled",
            "successive_substitution_max_iter": 0,
            "successive_substitution_tol": 0.0,
            "use_deadzone": 0,
            "threshold_scale": 0.0,
            "post_threshold_scale": 0.0,
        }

        self._compression_metrics_ = {
            "compressed_payload_bytes": int(compressed_info.payload_bytes),
            "compressed_header_bytes": int(compressed_info.header_bytes),
            "compressed_approx_bytes": int(compressed_info.approx_bytes),
            "compressed_tree_symbol_bytes": int(compressed_info.tree_symbol_bytes),
            "compressed_tree_magnitude_bytes": int(
                compressed_info.tree_magnitude_bytes
            ),
            "q_approx_step": float(self.q_approx_step),
            "q_detail_step_base": float(self.q_detail_step),
            "q_detail_growth": float(self.q_detail_growth),
            "lossless_zlib_bytes": int(lossless_bytes),
            "lossless_zlib_ratio": float(lossless_ratio),
            "wavelet_vs_lossless_ratio": float(wavelet_vs_lossless),
            "bpp_wavelet": float(bpp_wavelet),
            "bpp_lossless_zlib": float(bpp_lossless),
        }

        if getattr(self, "verbose", False):
            x_bytes = float(getattr(self, "_X_storage_ref_", X).nbytes)
            c_bytes = float(compressed_info.payload_bytes)
            ratio = x_bytes / max(c_bytes, 1.0)
            kept = (nnz_after / max(nnz_before, 1)) * 100.0

            print(f"[WaveletBivariateShrinkEZW2D] level={level}")
            print("[WaveletBivariateShrinkEZW2D] bivariate shrinkage=DISABLED")
            print("[WaveletBivariateShrinkEZW2D] post-thresholding=DISABLED")
            print(
                f"[WaveletBivariateShrinkEZW2D] q_approx_step={self.q_approx_step:.6g}"
            )
            print(
                f"[WaveletBivariateShrinkEZW2D] q_detail_step={self.q_detail_step:.6g}"
            )
            print(
                f"[WaveletBivariateShrinkEZW2D] coeff nnz kept before/after passthrough={kept:.2f}%"
            )
            print(
                f"[WaveletBivariateShrinkEZW2D] compressed_payload_bytes={int(c_bytes)}"
            )
            print(f"[WaveletBivariateShrinkEZW2D] compression_ratio={ratio:.6g}x")
            print(f"[WaveletBivariateShrinkEZW2D] rel_fro_error={err:.6g}")
            print(f"[WaveletBivariateShrinkEZW2D] lossless_zlib_bytes={lossless_bytes}")
            print(
                f"[WaveletBivariateShrinkEZW2D] wavelet_vs_lossless_ratio={wavelet_vs_lossless:.4f}x"
            )
            print(f"[WaveletBivariateShrinkEZW2D] bpp_wavelet={bpp_wavelet:.4f}")
