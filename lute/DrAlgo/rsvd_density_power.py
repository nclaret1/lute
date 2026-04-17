from __future__ import annotations
from typing import Optional, Any
import time

import numpy as np
import numpy.typing as npt
from scipy.linalg import norm


from .DrAlgo import Factors, NotFittedError
from .DrAlgo import LSDrAlgo, _check_2d
from decompy.matrix_factorization import RobustSVDDensityPowerDivergence

__all__ = ["RSVDDensityPowerDR"]


def _l1_median_weizsfeld(
    X: npt.NDArray,
    tol: float = 1e-6,
    max_iter: int = 500,
    eps: float = 1e-12,
) -> npt.NDArray:
    X = np.asarray(X, dtype=float)
    n, p = X.shape
    mu = np.median(X, axis=0)
    for _ in range(max_iter):
        diff = X - mu
        dist = np.linalg.norm(diff, axis=1)
        zero_mask = dist < eps
        if np.any(zero_mask):
            return X[zero_mask][0].copy()
        w = 1.0 / np.maximum(dist, eps)
        w_sum = np.sum(w)
        mu_new = (w[:, None] * X).sum(axis=0) / w_sum
        if np.linalg.norm(mu_new - mu) < tol:
            return mu_new
        mu = mu_new
    return mu


class RSVDDensityPowerDR(LSDrAlgo):
    def __init__(
        self,
        n_components: int = None,
        *,
        alpha: float = 0.5,
        residual_split_k: float = 2.0,
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
        self.alpha = alpha
        self.residual_split_k = residual_split_k
        self.U_ = None
        self.V_ = None
        self.Sigma_ = None
        self.noise_ = None
        self.mu_ = None

    def _fit_core(self, Xc: npt.NDArray, **kwargs: Any) -> None:
        X = _check_2d(Xc)
        self._Xc_ = X
        m, n = X.shape
        min_dim = min(m, n)

        if self.n_components is None:
            raise ValueError("n_components must be specified.")

        rank_eff = int(self.n_components)
        if rank_eff <= 0:
            raise ValueError(f"Invalid n_components={self.n_components}")
        rank_eff = max(1, min(rank_eff, min_dim))

        mu = _l1_median_weizsfeld(X, tol=self.tol)
        Y = X - mu

        model = RobustSVDDensityPowerDivergence(alpha=self.alpha, method="v1")

        t0 = time.perf_counter()
        res = model.decompose(Y, rank=rank_eff)

        U, V = res.singular_vectors(type="both")
        S_mat = res.singular_values()
        S_mat = np.asarray(S_mat)

        if S_mat.ndim == 1:
            Sigma = np.diag(S_mat)
            svals = S_mat
        else:
            Sigma = S_mat
            svals = np.diag(S_mat)

        L_centered = U @ Sigma @ V.T
        L = L_centered
        S = X - mu - L
        N = np.zeros_like(X)

        self.low_rank_ = L
        self.sparse_ = S
        self.noise_ = N
        self.mu_ = mu

        self.U_ = U
        self.V_ = V
        self.Sigma_ = Sigma

        self.n_components_ = rank_eff
        self.components_ = V.T
        self.singular_values_ = svals

        fro_X = norm(X, "fro")
        X_hat = mu + L + S
        abs_err = norm(X - X_hat, "fro")
        rel_err = abs_err / (fro_X + 1e-12)

        self._norm_X_ = fro_X
        self.final_error_ = float(rel_err)
        self.errors_ = [float(rel_err)]

        dt = time.perf_counter() - t0
        self.timers_ = [dt]
        self.end_iter_ = 1

        if self.verbose:
            nnz = int(np.count_nonzero(S))
            density = nnz / S.size if S.size else 0.0
            print("==== RSVDDensityPowerDR (rPCAdpd) Summary ====")
            print(f"shape(X)                 : {X.shape}")
            print(f"target rank              : {rank_eff}")
            print(f"alpha                    : {self.alpha}")
            print(f"||mu||_2                 : {np.linalg.norm(mu):.4e}")
            print(f"Frobenius ||X||          : {fro_X:.4e}")
            print(f"abs_error ||X-(μ+L+S)||  : {abs_err:.4e}")
            print(f"rel_error                : {rel_err:.4e}")
            print(f"S nnz                    : {nnz}")
            print(f"S density                : {density:.4e}")
            print(f"time (s)                 : {dt:.4f}")


def factors(self) -> Factors:
    if self.low_rank_ is None or self.sparse_ is None:
        raise NotFittedError
    return Factors(L=self.low_rank_, S=self.sparse_)


def _reconstruct(self, f: Factors) -> npt.NDArray:
    return f["L"] + f["S"]
