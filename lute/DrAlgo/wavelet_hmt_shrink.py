# wavelet_hmt_shrink.py
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


def _log_gauss0(x: np.ndarray, var: float) -> np.ndarray:
    """
    log N(x; 0, var) elementwise.
    """
    v = float(max(var, 1e-20))
    return -0.5 * (np.log(2.0 * np.pi * v) + (x * x) / v)


def _logsumexp2(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """
    log(exp(a)+exp(b)) stable, elementwise.
    """
    m = np.maximum(a, b)
    return m + np.log(np.exp(a - m) + np.exp(b - m))


def _softmax2(logp0: np.ndarray, logp1: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """
    Return probabilities (p0, p1) for 2-class log-probs.
    """
    m = np.maximum(logp0, logp1)
    e0 = np.exp(logp0 - m)
    e1 = np.exp(logp1 - m)
    z = e0 + e1
    return e0 / z, e1 / z

def _pad_to(a: np.ndarray, target_shape: Tuple[int, int], fill: float = 0.0) -> np.ndarray:
    """Pad array 'a' to target_shape with constant 'fill' (bottom/right padding only)."""
    h, w = a.shape
    th, tw = target_shape
    if h == th and w == tw:
        return a
    out = np.full((th, tw), fill, dtype=a.dtype)
    out[: min(h, th), : min(w, tw)] = a[: min(h, th), : min(w, tw)]
    return out


@dataclass
class WaveletStorageInfo:
    coeff_arr: np.ndarray
    coeff_slices: Any
    coeff_shape: Tuple[int, int]
    wavelet: str
    level: int
    mode: str


class WaveletHMTShrink2D(XhatDrAlgo):
    """
    Fast-ish Hidden Markov Tree (HMT)-style shrinkage for decimated 2D DWT.

    This implementation emphasizes:
      - Minimal assumptions: robust sigma estimate, only two "states" (small/large)
      - Interscale dependency (tree): state persistence via a fixed transition matrix A
      - Practicality: avoids heavy EM over transitions; updates only state variances

    Model (per orientation band independently, tied across levels):
      w | state k ~ N(0, sigma^2 + v_k)  with k in {0:small, 1:large}
      state transitions: P(child=k | parent=s) = A[s,k]  (fixed)
      root prior: pi (fixed or inferred from root posterior per iteration)

    Denoised coefficient uses posterior-weighted Wiener shrink:
      E[s|w,k] = (v_k / (v_k + sigma^2)) * w
      w_hat = sum_k P(k|data) * E[s|w,k]

    Parameters
    ----------
    wavelet : str
    level : Optional[int]
    mode : str
    sigma_from : str
        "HH1" or "all_finest"
    A : Optional[np.ndarray]
        2x2 transition matrix (rows=parent state, cols=child state).
        If None, uses a conservative default favoring persistence.
    pi : Optional[np.ndarray]
        length-2 prior for root state. If None, defaults to [0.9, 0.1].
    em_iters : int
        Number of variance-update iterations (typically 3-10).
    keep_coeffs : bool
    """

    def __init__(
        self,
        n_components: Optional[int] = None,
        *,
        wavelet: str = "db2",
        level: Optional[int] = None,
        mode: str = "periodization",
        sigma_from: str = "HH1",
        A: Optional[np.ndarray] = None,
        pi: Optional[np.ndarray] = None,
        em_iters: int = 6,
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
        self.sigma_from = sigma_from
        self.em_iters = int(em_iters)
        self.keep_coeffs = keep_coeffs

        if A is None:
            # persistence-heavy, but allows transitions to "large" at fine scales
            A = np.array([[0.95, 0.05],
                          [0.20, 0.80]], dtype=np.float64)
        A = np.asarray(A, dtype=np.float64)
        if A.shape != (2, 2):
            raise ValueError("A must be shape (2,2).")
        A = A / np.maximum(A.sum(axis=1, keepdims=True), 1e-20)
        self.A = A

        if pi is None:
            pi = np.array([0.90, 0.10], dtype=np.float64)
        pi = np.asarray(pi, dtype=np.float64).ravel()
        if pi.size != 2:
            raise ValueError("pi must be length 2.")
        pi = pi / np.maximum(pi.sum(), 1e-20)
        self.pi = pi

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
            f["coeff_shape"] = np.asarray(self.wavelet_storage_.coeff_shape, dtype=np.int64)
        return f

    def _upward_beta_quadtree(
        self,
        bands: List[np.ndarray],  # coarse -> fine
        var0: float,
        var1: float,
        A: np.ndarray,
        sigma2: float,
    ) -> List[Tuple[np.ndarray, np.ndarray]]:
        """
        Compute upward messages beta for each level in log-domain.
        Returns list beta_levels (coarse->fine), each as (beta0, beta1) arrays matching bands[level].

        Robust to odd-sized wavelet subbands by padding child messages to exactly 2x parent size.
        Missing children contribute a neutral factor (log=0).
        """
        L = len(bands)
        beta_levels: List[Tuple[np.ndarray, np.ndarray]] = [None] * L  # type: ignore

        logA00, logA01 = np.log(A[0, 0] + 1e-20), np.log(A[0, 1] + 1e-20)
        logA10, logA11 = np.log(A[1, 0] + 1e-20), np.log(A[1, 1] + 1e-20)

        for li in range(L - 1, -1, -1):
            w = bands[li]
            e0 = _log_gauss0(w, sigma2 + var0)
            e1 = _log_gauss0(w, sigma2 + var1)

            if li == L - 1:
                beta_levels[li] = (e0, e1)
                continue

            # Child messages at finer level
            c0, c1 = beta_levels[li + 1]

            ph, pw = w.shape
            target_child_shape = (2 * ph, 2 * pw)

            # Pad child to exact 2x parent to make all 2x2 slices align to (ph, pw)
            c0p = _pad_to(c0, target_child_shape, fill=0.0)
            c1p = _pad_to(c1, target_child_shape, fill=0.0)

            # 2x2 child blocks -> parent grid (each slice is (ph, pw))
            c00_0, c00_1 = c0p[0::2, 0::2], c1p[0::2, 0::2]
            c01_0, c01_1 = c0p[0::2, 1::2], c1p[0::2, 1::2]
            c10_0, c10_1 = c0p[1::2, 0::2], c1p[1::2, 0::2]
            c11_0, c11_1 = c0p[1::2, 1::2], c1p[1::2, 1::2]

            # Child->parent log-messages for parent state 0
            m00_s0 = _logsumexp2(logA00 + c00_0, logA01 + c00_1)
            m01_s0 = _logsumexp2(logA00 + c01_0, logA01 + c01_1)
            m10_s0 = _logsumexp2(logA00 + c10_0, logA01 + c10_1)
            m11_s0 = _logsumexp2(logA00 + c11_0, logA01 + c11_1)

            # For parent state 1
            m00_s1 = _logsumexp2(logA10 + c00_0, logA11 + c00_1)
            m01_s1 = _logsumexp2(logA10 + c01_0, logA11 + c01_1)
            m10_s1 = _logsumexp2(logA10 + c10_0, logA11 + c10_1)
            m11_s1 = _logsumexp2(logA10 + c11_0, logA11 + c11_1)

            beta0 = e0 + (m00_s0 + m01_s0 + m10_s0 + m11_s0)
            beta1 = e1 + (m00_s1 + m01_s1 + m10_s1 + m11_s1)

            beta_levels[li] = (beta0, beta1)

        return beta_levels

    def _posterior_from_beta(
        self, beta_levels: List[Tuple[np.ndarray, np.ndarray]], pi: np.ndarray, A: np.ndarray
    ) -> List[Tuple[np.ndarray, np.ndarray]]:
        """
        Approximate downward pass to compute node posteriors.
        Uses:
          root posterior from pi + beta
          child prior from parent's posterior and transition A
          posterior(child) ∝ prior(child) * exp(beta_child)
        Returns list gamma_levels (coarse->fine) where each is (g0, g1).
        """
        L = len(beta_levels)
        gamma_levels: List[Tuple[np.ndarray, np.ndarray]] = [None] * L  # type: ignore

        # root
        b0, b1 = beta_levels[0]
        logp0 = np.log(pi[0]) + b0
        logp1 = np.log(pi[1]) + b1
        g0, g1 = _softmax2(logp0, logp1)
        gamma_levels[0] = (g0, g1)

        for li in range(1, L):
            # prior for each child node from parent posterior (upsampled to child grid)
            p0, p1 = gamma_levels[li - 1]
            # child prior per parent location: prior_child = [p0,p1] @ A  (vector)
            # then expand to child grid (2x in each dim)
            prior0_parent = p0 * A[0, 0] + p1 * A[1, 0]
            prior1_parent = p0 * A[0, 1] + p1 * A[1, 1]

            prior0 = np.repeat(np.repeat(prior0_parent, 2, axis=0), 2, axis=1)
            prior1 = np.repeat(np.repeat(prior1_parent, 2, axis=0), 2, axis=1)

            b0, b1 = beta_levels[li]
            prior0 = prior0[: b0.shape[0], : b0.shape[1]]
            prior1 = prior1[: b1.shape[0], : b1.shape[1]]

            logp0 = np.log(np.maximum(prior0, 1e-20)) + b0
            logp1 = np.log(np.maximum(prior1, 1e-20)) + b1
            g0, g1 = _softmax2(logp0, logp1)
            gamma_levels[li] = (g0, g1)

        return gamma_levels

    def _hmt_shrink_band(
        self,
        bands: List[np.ndarray],  # coarse->fine for ONE orientation
        sigma: float,
        A: np.ndarray,
        pi: np.ndarray,
        em_iters: int,
        eps: float = 1e-12,
    ) -> List[np.ndarray]:
        """
        HMT shrink a single orientation band across levels.
        Returns shrunk bands (coarse->fine).
        """
        sigma2 = float(sigma * sigma)

        # Initialize state signal variances v0 (small), v1 (large)
        # v0 close to 0, v1 from robust var estimate
        allw = np.concatenate([b.ravel() for b in bands]) if bands else np.array([], dtype=np.float32)
        varw = float(np.var(allw)) if allw.size else 0.0
        v0 = 0.0
        v1 = max(varw - sigma2, 1e-6)

        for _ in range(max(em_iters, 1)):
            beta_levels = self._upward_beta_quadtree(bands, v0, v1, A, sigma2)
            gamma_levels = self._posterior_from_beta(beta_levels, pi, A)

            # Update pi from root posterior (mean over root grid)
            g0r, g1r = gamma_levels[0]
            pi0 = float(np.mean(g0r))
            pi1 = float(np.mean(g1r))
            s = max(pi0 + pi1, eps)
            pi = np.array([pi0 / s, pi1 / s], dtype=np.float64)

            # Update v1 (and optionally v0) using posteriors:
            # E[w^2 | state k] approx -> estimate signal variance v_k = E[w^2|k] - sigma^2
            num0 = 0.0
            den0 = 0.0
            num1 = 0.0
            den1 = 0.0
            for (w, (g0, g1)) in zip(bands, gamma_levels):
                w2 = w.astype(np.float64) ** 2
                num0 += float(np.sum(g0 * w2))
                den0 += float(np.sum(g0))
                num1 += float(np.sum(g1 * w2))
                den1 += float(np.sum(g1))

            if den0 > 0:
                v0_new = max(num0 / den0 - sigma2, 0.0)
            else:
                v0_new = v0
            if den1 > 0:
                v1_new = max(num1 / den1 - sigma2, 1e-6)
            else:
                v1_new = v1

            # keep "small" variance really small to encourage sparsity
            v0 = min(v0_new, 0.05 * v1_new)
            v1 = v1_new

        # Final posterior with updated variances
        beta_levels = self._upward_beta_quadtree(bands, v0, v1, A, sigma2)
        gamma_levels = self._posterior_from_beta(beta_levels, pi, A)

        # Posterior-weighted Wiener shrink per node
        out: List[np.ndarray] = []
        a0 = v0 / (v0 + sigma2 + eps)
        a1 = v1 / (v1 + sigma2 + eps)
        for w, (g0, g1) in zip(bands, gamma_levels):
            w_hat = (g0 * a0 + g1 * a1) * w
            out.append(w_hat.astype(w.dtype, copy=False))
        return out

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
        details = list(coeffs[1:])  # coarse->fine

        # sigma estimate (minimal assumption, robust)
        (cH1, cV1, cD1) = details[-1]
        if self.sigma_from == "HH1":
            sigma = _mad_sigma(cD1)
        elif self.sigma_from == "all_finest":
            sigma = _mad_sigma(np.concatenate([cH1.ravel(), cV1.ravel(), cD1.ravel()]))
        else:
            raise ValueError("sigma_from must be 'HH1' or 'all_finest'.")

        # Separate orientation bands across levels (coarse->fine)
        H_bands = [d[0] for d in details]
        V_bands = [d[1] for d in details]
        D_bands = [d[2] for d in details]

        # Apply HMT shrink per orientation (tied params across levels)
        H_shr = self._hmt_shrink_band(H_bands, sigma=sigma, A=self.A, pi=self.pi, em_iters=self.em_iters)
        V_shr = self._hmt_shrink_band(V_bands, sigma=sigma, A=self.A, pi=self.pi, em_iters=self.em_iters)
        D_shr = self._hmt_shrink_band(D_bands, sigma=sigma, A=self.A, pi=self.pi, em_iters=self.em_iters)

        new_details: List[Tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        nnz_before = 0
        nnz_after = 0
        for (cH, cV, cD), (h, v, d) in zip(details, zip(H_shr, V_shr, D_shr)):
            nnz_before += int(np.count_nonzero(cH) + np.count_nonzero(cV) + np.count_nonzero(cD))
            nnz_after += int(np.count_nonzero(h) + np.count_nonzero(v) + np.count_nonzero(d))
            new_details.append((h, v, d))

        coeffs_t = [cA] + new_details

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
            "hmt_em_iters": int(self.em_iters),
            "hmt_sigma_from": self.sigma_from,
            "hmt_A": np.asarray(self.A, dtype=np.float64),
            "hmt_pi": np.asarray(self.pi, dtype=np.float64),
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
            print(f"[WaveletHMTShrink2D] level={level} sigma≈{sigma:.4g} nnz kept ≈ {kept:.2f}%")
            print(f"[WaveletHMTShrink2D] rel_fro_error={err:.6g}")