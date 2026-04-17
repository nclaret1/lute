from __future__ import annotations

from typing import Optional, Tuple, Any, Dict

import numpy as np
import numpy.typing as npt

from .DrAlgo import LSDrAlgo, Factors, _check_2d, _fro_norm
from .wavelet_bivariate_shrink2 import WaveletBivariateShrink2D
from .rpca_altproj import RPCAAltProj


class WaveletBishrinkThenRPCA(LSDrAlgo):
    def __init__(
        self,
        n_components: Optional[int] = None,
        *,
        wavelet: str = "db2",
        wavelet_level: Optional[int] = None,
        wavelet_mode: str = "periodization",
        wavelet_win_radius: int = 1,
        rpca_max_iter: int = 1000,
        rpca_tol: float = 1e-3,
        rpca_beta: Optional[float] = None,
        rpca_beta_init: Optional[float] = None,
        rpca_gamma: float = 0.7,
        rpca_mu: Tuple[float, float] = (5.0, 5.0),
        rpca_trim: bool = False,
        center: bool = False,
        copy_: bool = True,
        verbose: bool = False,
        random_state: Optional[int] = None,
    ):
        super().__init__(
            n_components=n_components,
            center=center,
            copy_=copy_,
            verbose=verbose,
            random_state=random_state,
            tol=rpca_tol,
            max_iter=rpca_max_iter,
        )
        self.wavelet = wavelet
        self.wavelet_level = wavelet_level
        self.wavelet_mode = wavelet_mode
        self.wavelet_win_radius = int(wavelet_win_radius)

        self.rpca_max_iter = int(rpca_max_iter)
        self.rpca_tol = float(rpca_tol)
        self.rpca_beta = rpca_beta
        self.rpca_beta_init = rpca_beta_init
        self.rpca_gamma = float(rpca_gamma)
        self.rpca_mu = (float(rpca_mu[0]), float(rpca_mu[1]))
        self.rpca_trim = bool(rpca_trim)

        self.low_rank_: Optional[npt.NDArray] = None
        self.sparse_: Optional[npt.NDArray] = None
        self.denoised_: Optional[npt.NDArray] = None

        self._wavelet_metrics_: Dict[str, Any] = {}
        self._rpca_metrics_: Dict[str, Any] = {}

    def factors(self) -> Factors:
        if self.low_rank_ is None or self.sparse_ is None:
            raise Exception("Not fitted.")
        return Factors(L=self.low_rank_, S=self.sparse_)

    def _reconstruct(self, f: Factors) -> npt.NDArray:
        return f["L"] + f["S"]

    def compute_metrics(self, max_autocorr_lag: int = 200) -> Dict[str, Any]:
        metrics = super().compute_metrics(max_autocorr_lag=max_autocorr_lag)
        storage = metrics.get("storage", {})
        if not isinstance(storage, dict):
            storage = {}
        if self._wavelet_metrics_:
            metrics["wavelet"] = dict(self._wavelet_metrics_)
        if self._rpca_metrics_:
            metrics["rpca"] = dict(self._rpca_metrics_)
        metrics["storage"] = storage
        self.metrics_ = metrics
        return metrics

    def _fit_core(self, Xc: npt.NDArray, **kwargs: Any) -> None:
        Xc = _check_2d(Xc)
        Xc = np.asarray(Xc, dtype=np.float32, order="C")

        w = WaveletBivariateShrink2D(
            wavelet=self.wavelet,
            level=self.wavelet_level,
            mode=self.wavelet_mode,
            win_radius=self.wavelet_win_radius,
            keep_coeffs=False,
            center=False,
            copy_=False,
            verbose=False,
            random_state=self.random_state,
            tol=1e-5,
            max_iter=1,
        )
        w.fit(Xc)
        X_denoised = np.asarray(w.reconstruct(), dtype=np.float32, order="C")
        self.denoised_ = X_denoised

        self._wavelet_metrics_ = {}
        try:
            wm = w.compute_metrics()
            if isinstance(wm, dict):
                self._wavelet_metrics_ = wm
        except Exception:
            self._wavelet_metrics_ = {}

        rpca = RPCAAltProj(
            n_components=self.n_components,
            max_iter=self.rpca_max_iter,
            tol=self.rpca_tol,
            beta=self.rpca_beta,
            beta_init=self.rpca_beta_init,
            gamma=self.rpca_gamma,
            mu=self.rpca_mu,
            trim=self.rpca_trim,
            verbose=False,
            copy_=True,
        )
        rpca.fit(X_denoised)

        self.low_rank_ = np.asarray(rpca.low_rank_, dtype=np.float32, order="C")
        self.sparse_ = np.asarray(rpca.sparse_, dtype=np.float32, order="C")
        self.U_ = getattr(rpca, "U_", None)
        self.V_ = getattr(rpca, "V_", None)
        self.Sigma_ = getattr(rpca, "Sigma_", None)

        self.errors_ = list(getattr(rpca, "errors_", []))
        self.timers_ = list(getattr(rpca, "timers_", []))
        self.n_iter_ = getattr(rpca, "end_iter_", None)
        self.final_error_ = getattr(rpca, "final_error_", None)

        self._rpca_metrics_ = {}
        try:
            rm = rpca.compute_metrics()
            if isinstance(rm, dict):
                self._rpca_metrics_ = rm
        except Exception:
            self._rpca_metrics_ = {}

        resid = Xc - (self.low_rank_ + self.sparse_)
        err = _fro_norm(resid) / (self._norm_X_ if self._norm_X_ else max(_fro_norm(Xc), 1e-12))
        if self.final_error_ is None:
            self.final_error_ = float(err)
        if not self.errors_:
            self.errors_ = [float(err)]