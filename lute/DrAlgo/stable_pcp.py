from __future__ import annotations

import time
from typing import Optional

import numpy as np
import numpy.typing as npt

from .DrAlgo import LSDrAlgo, _check_2d

try:
    from fbpca import pca as fbpca_pca
except ModuleNotFoundError:
    fbpca_pca = None


class StablePCPAlgo(LSDrAlgo):
    def __init__(
        self,
        n_components: Optional[int] = None,
        *,
        lamb: Optional[float] = None,
        mu0: Optional[float] = None,
        mu0_init: float = 1000.0,
        mu_fixed: bool = False,
        mu_min: Optional[float] = None,
        sigma: float = 1.0,
        eta: float = 0.9,
        max_rank: Optional[int] = None,
        use_fbpca: bool = False,
        fbpca_rank_ratio: float = 1,
        center: bool = False,
        copy_: bool = True,
        verbose: bool = False,
        tol: float = 1e-6,
        max_iter: int = 100,
        random_state: Optional[int] = None,
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

        self.lamb = lamb
        self.mu0 = mu0
        self.mu0_init = mu0_init
        self.mu_fixed = mu_fixed
        self.mu_min = mu_min
        self.sigma = sigma
        self.eta = eta
        self.max_rank = max_rank if max_rank is not None else n_components
        self.use_fbpca = use_fbpca
        self.fbpca_rank_ratio = fbpca_rank_ratio

        self.converged_: Optional[bool] = None
        self.rank_: Optional[int] = None

    @staticmethod
    def _soft_threshold(X: np.ndarray, tau: float) -> np.ndarray:
        return np.sign(X) * np.maximum(np.abs(X) - tau, 0.0)

    def _fit_core(self, Xc: npt.NDArray, **kwargs) -> None:
        Xc = _check_2d(Xc)
        m, n = Xc.shape

        #eff_max_rank = self.max_rank
        #if eff_max_rank is None:
            #eff_max_rank = self.n_components 
        #if eff_max_rank is None:
            #eff_max_rank = min(m, n)
        #self.max_rank = eff_max_rank
        #print("Effective max_rank:", eff_max_rank)
        self.max_rank = np.maximum(n, m)

        L0 = np.zeros((m, n))
        L1 = np.zeros((m, n))
        S0 = np.zeros((m, n))
        S1 = np.zeros((m, n))
        t0, t1 = 1.0, 1.0

        if self.mu_fixed:
            mu0 = np.sqrt(2.0 * max(m, n)) * self.sigma
            mu_min = mu0
        else:
            mu0 = self.mu0
            if mu0 is None:
                mu0 = min(
                    self.mu0_init * np.sqrt(2.0 * max(m, n)),
                    0.99 * float(np.linalg.norm(Xc, 2)),
                )
            mu_min = self.mu_min
            if mu_min is None:
                mu_min = np.sqrt(2.0 * max(m, n)) * self.sigma

        mu = float(mu0)

        lamb = self.lamb
        if lamb is None:
            lamb = 1.0 / np.sqrt(max(m, n))
        lamb = float(lamb)

        if self.use_fbpca and fbpca_pca is None:
            raise ModuleNotFoundError("fbpca is required when use_fbpca=True")

        errors = []
        timers = []

        last_Etot = np.inf
        rank = 0

        for _ in range(1, self.max_iter + 1):
            t_start = time.perf_counter()

            YL = L1 + (t0 - 1.0) / t1 * (L1 - L0)
            YS = S1 + (t0 - 1.0) / t1 * (S1 - S0)

            GL = YL - 0.5 * (YL + YS - Xc)

            if self.use_fbpca:
                k = self.max_rank
                if k is None:
                    k = max(1, int(min(GL.shape) * self.fbpca_rank_ratio))
                u, s, vh = fbpca_pca(GL, k, True, n_iter=5)
            else:
                u, s, vh = np.linalg.svd(GL, full_matrices=False)

            keep = s > (mu / 2.0)
            s_shrunk = s[keep] - (mu / 2.0)
            rank = int(s_shrunk.size)

            if self.max_rank is not None and rank > self.max_rank:
                print("max_rank: reducing rank from", rank, "to", self.max_rank)
                rank = int(self.max_rank)
                s_shrunk = s_shrunk[:rank]

            L0 = L1

            if rank == 0:
                L1 = np.zeros_like(L1)
                self.U_ = None
                self.V_ = None
                self.Sigma_ = None
            else:
                L1 = (u[:, :rank] * s_shrunk) @ vh[:rank, :]
                self.U_ = u[:, :rank].astype(np.float32, copy=False)
                self.V_ = vh[:rank, :].T.astype(np.float32, copy=False)
                self.Sigma_ = np.diag(s_shrunk.astype(np.float32, copy=False))

            GS = YS - 0.5 * (YL + YS - Xc)
            S0 = S1
            S1 = self._soft_threshold(GS, lamb * mu / 2.0)

            t0 = t1
            t1 = (1.0 + np.sqrt(4.0 * t1 * t1 + 1.0)) / 2.0

            if not self.mu_fixed:
                mu = max(self.eta * mu, float(mu_min))

            EA = 2.0 * (YL - L1) + (L1 + S1 - YL - YS)
            ES = 2.0 * (YS - S1) + (L1 + S1 - YL - YS)
            Etot = float(np.sqrt(np.linalg.norm(EA) ** 2 + np.linalg.norm(ES) ** 2))

            errors.append(Etot)
            timers.append(time.perf_counter() - t_start)

            last_Etot = Etot
            print("Last Etol", last_Etot)
            if Etot <= self.tol:
                break

        self.converged_ = bool(last_Etot <= self.tol)
        self.rank_ = rank

        self.low_rank_ = L1
        self.sparse_ = S1

        self.errors_ = errors
        self.timers_ = timers
        self.end_iter_ = len(errors)
        self.final_error_ = errors[-1] if errors else None
