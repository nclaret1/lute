from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, List
import time
import numpy as np
import numpy.typing as npt

from .DrAlgo import LSDrAlgo, Factors, _check_2d


def soft_threshold(x: np.ndarray, t: float) -> np.ndarray:
    return np.sign(x) * np.maximum(np.abs(x) - t, 0.0)


def svt(M: np.ndarray, t: float) -> np.ndarray:
    U, s, Vt = np.linalg.svd(M, full_matrices=False)
    s_thr = np.maximum(s - t, 0.0)
    return (U * s_thr) @ Vt


def proj_fro_ball(X: np.ndarray, eps: float) -> np.ndarray:
    nrm = np.linalg.norm(X, ord="fro")
    if nrm <= eps or nrm < 1e-12:
        return X
    return (eps / nrm) * X


def grad_periodic(u: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    gx = np.roll(u, -1, axis=0) - u
    gy = np.roll(u, -1, axis=1) - u
    return gx, gy


def div_periodic(px: np.ndarray, py: np.ndarray) -> np.ndarray:
    dx = px - np.roll(px, 1, axis=0)
    dy = py - np.roll(py, 1, axis=1)
    return dx + dy


def laplacian_eigs_periodic(m: int, n: int) -> np.ndarray:
    k = np.arange(m)
    l = np.arange(n)
    wk = 2.0 * np.pi * k / m
    wl = 2.0 * np.pi * l / n
    eig_k = 2.0 - 2.0 * np.cos(wk)
    eig_l = 2.0 - 2.0 * np.cos(wl)
    return eig_k[:, None] + eig_l[None, :]


@dataclass
class ADMMHistory:
    r_norm: List[float]
    s_norm: List[float]
    r1: List[float]
    r2: List[float]
    r3: List[float]


def _stacked_primal_residuals_tv_only(M, L, S, R, Z, Wx, Wy):
    r1 = (M - L - S) - R
    r2 = L - Z
    gx, gy = grad_periodic(L)
    r3x = gx - Wx
    r3y = gy - Wy
    return r1, r2, r3x, r3y


def _primal_norm_tv_only(r1, r2, r3x, r3y) -> float:
    return float(
        np.sqrt(
            np.linalg.norm(r1, "fro") ** 2
            + np.linalg.norm(r2, "fro") ** 2
            + np.linalg.norm(r3x, "fro") ** 2
            + np.linalg.norm(r3y, "fro") ** 2
        )
    )


def _dual_residuals_tv_only(
    rho1,
    rho_z,
    rho_w,
    R,
    Z,
    Wx,
    Wy,
    R_prev,
    Z_prev,
    Wx_prev,
    Wy_prev,
) -> float:
    s2 = rho_z * (Z - Z_prev)
    dWx = Wx - Wx_prev
    dWy = Wy - Wy_prev
    s3 = rho_w * div_periodic(dWx, dWy)
    s1 = rho1 * (R - R_prev)
    return float(
        np.sqrt(
            np.linalg.norm(s1, "fro") ** 2
            + np.linalg.norm(s2, "fro") ** 2
            + np.linalg.norm(s3, "fro") ** 2
        )
    )


def admm_unmasked_tv_only(
    M: np.ndarray,
    lam: float,
    tau: float,
    eps: float,
    rho1: float = 1.0,
    rho_z: float = 1.0,
    rho_w: float = 1.0,
    max_iters: int = 500,
    abs_tol: float = 1e-2,
    rel_tol: float = 1e-1,
    verbose: bool = True,
):
    M = np.asarray(M, dtype=float)
    m, n = M.shape
    lap = laplacian_eigs_periodic(m, n)

    L = np.zeros_like(M)
    S = np.zeros_like(M)
    R = np.zeros_like(M)
    Z = np.zeros_like(M)
    Wx = np.zeros_like(M)
    Wy = np.zeros_like(M)

    U1 = np.zeros_like(M)
    Uz = np.zeros_like(M)
    Uwx = np.zeros_like(M)
    Uwy = np.zeros_like(M)

    denom_fft = (rho1 + rho_z) + rho_w * lap

    hist = ADMMHistory(r_norm=[], s_norm=[], r1=[], r2=[], r3=[])

    for k in range(max_iters):
        R_prev = R.copy()
        Z_prev = Z.copy()
        Wx_prev = Wx.copy()
        Wy_prev = Wy.copy()

        V = M - L - R + U1
        S = soft_threshold(V, lam / rho1)

        Q = (M - L - S) + U1
        R = proj_fro_ball(Q, eps)

        Z = svt(L + Uz, 1.0 / rho_z)

        gx, gy = grad_periodic(L)
        Wx = soft_threshold(gx + Uwx, tau / rho_w)
        Wy = soft_threshold(gy + Uwy, tau / rho_w)

        rhs = (
            rho1 * (M - S - R + U1)
            + rho_z * (Z - Uz)
            + rho_w * div_periodic(Wx - Uwx, Wy - Uwy)
        )
        L = np.real(np.fft.ifft2(np.fft.fft2(rhs) / denom_fft))

        U1 += (M - L - S) - R
        Uz += L - Z
        gx, gy = grad_periodic(L)
        Uwx += gx - Wx
        Uwy += gy - Wy

        r1, r2, r3x, r3y = _stacked_primal_residuals_tv_only(M, L, S, R, Z, Wx, Wy)
        r_norm = _primal_norm_tv_only(r1, r2, r3x, r3y)
        s_norm = _dual_residuals_tv_only(
            rho1, rho_z, rho_w, R, Z, Wx, Wy, R_prev, Z_prev, Wx_prev, Wy_prev
        )

        p = 4 * m * n
        eps_pri = np.sqrt(p) * abs_tol + rel_tol * max(
            np.linalg.norm(M, "fro"),
            np.linalg.norm(L + S + R, "fro"),
            np.linalg.norm(Z, "fro"),
            np.linalg.norm(gx, "fro") + np.linalg.norm(gy, "fro"),
            np.linalg.norm(Wx, "fro") + np.linalg.norm(Wy, "fro"),
        )
        eps_dual = np.sqrt(m * n) * abs_tol + rel_tol * max(
            np.linalg.norm(U1, "fro"),
            np.linalg.norm(Uz, "fro"),
            np.linalg.norm(Uwx, "fro") + np.linalg.norm(Uwy, "fro"),
        )

        hist.r_norm.append(float(r_norm))
        hist.s_norm.append(float(s_norm))
        hist.r1.append(float(np.linalg.norm(r1, "fro")))
        hist.r2.append(float(np.linalg.norm(r2, "fro")))
        hist.r3.append(
            float(
                np.sqrt(
                    np.linalg.norm(r3x, "fro") ** 2 + np.linalg.norm(r3y, "fro") ** 2
                )
            )
        )

        if verbose and (k % 10 == 0 or k == max_iters - 1):
            fit_abs = np.linalg.norm(M - L - S, "fro")
            fit = fit_abs / (np.linalg.norm(M, "fro") + 1e-12)
            print(
                f"iter {k:4d} | r {r_norm:.3e} (<= {eps_pri:.3e}) | "
                f"s {s_norm:.3e} (<= {eps_dual:.3e}) | fit {fit:.3e}"
            )

        if (r_norm <= eps_pri) and (s_norm <= eps_dual):
            break

    X = None
    return L, S, R, Z, (Wx, Wy), X, hist


class RPCATVADMM(LSDrAlgo):
    def __init__(
        self,
        n_components: Optional[int] = None,
        *,
        lam: float = 50.0,
        tau: float = 0.1,
        eps: Optional[float] = None,
        eps_factor: float = 1e-1,
        rho1: float = 1.0,
        rho_z: float = 1.0,
        rho_w: float = 1.0,
        max_iter: int = 500,
        abs_tol: float = 1e-2,
        rel_tol: float = 1e-1,
        center: bool = False,
        copy_: bool = True,
        verbose: bool = True,
        random_state: Optional[int] = None,
        tol: float = 1e-5,
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
        self.lam = float(lam)
        self.tau = float(tau)
        self.eps = None if eps is None else float(eps)
        self.eps_factor = float(eps_factor)
        self.rho1 = float(rho1)
        self.rho_z = float(rho_z)
        self.rho_w = float(rho_w)
        self.abs_tol = float(abs_tol)
        self.rel_tol = float(rel_tol)

        self.resid_: Optional[np.ndarray] = None
        self.Z_: Optional[np.ndarray] = None
        self.Wx_: Optional[np.ndarray] = None
        self.Wy_: Optional[np.ndarray] = None
        self.hist_: Optional[ADMMHistory] = None

    def _fit_core(self, Xc: npt.NDArray, **kwargs) -> None:
        _check_2d(Xc)

        eps = (
            float(self.eps)
            if self.eps is not None
            else float(self.eps_factor) * (np.linalg.norm(Xc, "fro") + 1e-12)
        )

        t0 = time.perf_counter()
        L, S, R, Z, (Wx, Wy), _, hist = admm_unmasked_tv_only(
            M=Xc,
            lam=self.lam,
            tau=self.tau,
            eps=eps,
            rho1=self.rho1,
            rho_z=self.rho_z,
            rho_w=self.rho_w,
            max_iters=self.max_iter,
            abs_tol=self.abs_tol,
            rel_tol=self.rel_tol,
            verbose=self.verbose,
        )
        t1 = time.perf_counter()

        self.low_rank_ = L
        self.sparse_ = S
        self.resid_ = R
        self.Z_ = Z
        self.Wx_, self.Wy_ = Wx, Wy
        self.hist_ = hist

        self.n_iter_ = len(hist.r_norm)
        self.errors_ = list(hist.r_norm)
        self.final_error_ = float(hist.r_norm[-1]) if hist.r_norm else None
        self.timers_ = [float(t1 - t0)]

        self.factors_ = Factors(L=L, S=S)

    def factors(self) -> Factors:
        if self.low_rank_ is None or self.sparse_ is None:
            raise RuntimeError
        return Factors(L=self.low_rank_, S=self.sparse_)
