from __future__ import annotations
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, List

import numpy as np
import numpy.typing as npt
from scipy.linalg import pinv, norm
from typing import Tuple
from scipy import sparse

__all__ = ["DrAlgo"]


class NotFittedError(Exception):
    pass


def _fro_norm(X: npt.NDArray) -> float:
    return float(np.linalg.norm(X, ord="fro"))


def _check_2d(X: npt.ArrayLike) -> npt.NDArray:
    X = np.asarray(X)
    if X.ndim != 2:
        raise ValueError(f"Expected 2D array, got {X.ndim}D.")
    return X


class Factors(dict):
    pass


class DrAlgo(ABC):
    def __init__(
        self,
        n_components: Optional[int] = None,
        *,
        center: bool = False,
        copy_: bool = True,
        verbose: bool = True,
        random_state: Optional[int] = None,
        tol: float = 1e-5,
        max_iter: int = 1000,
    ):
        self.n_components = n_components
        self.center = center
        self.copy = copy_
        self.verbose = verbose
        self.random_state = random_state
        self.tol = tol
        self.max_iter = max_iter
        self.mean_: Optional[npt.NDArray] = None
        self.errors_: List[float] = []
        self.timers_: List[float] = []
        self.n_iter_: Optional[int] = None
        self.final_error_: Optional[float] = None
        self._norm_X_: Optional[float] = None
        self._Xc_: Optional[npt.NDArray] = None
        self._X_orig_: Optional[np.ndarray] = None

    # can actually use "DrAlgo" for forward reference
    def fit(self, X: npt.ArrayLike, y: Any = None, **kwargs: Any) -> "DrAlgo":
        storage_ref = kwargs.pop("storage_ref", None)

        X = _check_2d(X)
        self.factors_ = {}
        self.errors_ = []
        self.final_error_ = None
        self.timers_ = []
        self.end_iter_ = None

        if self.copy:
            X = X.copy()
        if self.random_state is not None:
            np.random.seed(int(self.random_state))
        if self.center:
            self.mean_ = np.mean(X, axis=0, keepdims=True)
            Xc = X - self.mean_
        else:
            self.mean_ = np.zeros((1, X.shape[1]), dtype=X.dtype)
            Xc = X

        self._X_orig_ = X.copy()
        self._Xc_ = Xc
        self._norm_X_ = _fro_norm(Xc)

        if storage_ref is None:
            self._X_storage_ref_ = self._X_orig_
        else:
            self._X_storage_ref_ = _check_2d(storage_ref)

        self._fit_core(Xc, **kwargs)
        self._check_fitted_shapes(Xc)
        self.factors_ = self.factors()
        return self

    @abstractmethod
    def _fit_core(self, Xc: npt.NDArray, **kwargs: Any) -> None:
        # should set self.factors, self.errors, e=self.timers
        raise NotImplementedError

    def reconstruct(self) -> npt.NDArray:
        self._ensure_fitted()
        f = self.factors()
        return self._reconstruct(f)

    @abstractmethod
    def _reconstruct(self, f) -> npt.NDArray:
        # if {"L", "S"} <= f.keys():
        # return f["L"] + f["S"]
        # raise RuntimeError
        raise NotImplementedError

    @abstractmethod
    def factors(self) -> Factors:
        raise NotImplementedError

    def _ensure_fitted(self) -> None:
        try:
            f = self.factors()
        except Exception as e:
            raise NotFittedError from e
        if not isinstance(f, dict) or not f:
            raise NotFittedError

    def _check_fitted_shapes(self, Xc: npt.NDArray) -> None:
        f = self.factors()
        m, n = Xc.shape
        if "L" in f and f["L"].shape != (m, n):
            raise RuntimeError
        if "S" in f and f["S"].shape != (m, n):
            raise RuntimeError
        if {"C", "U", "R"} <= f.keys():
            rC = f["C"].shape[1]
            rR = f["R"].shape[0]
            if f["C"].shape[0] != m or f["R"].shape[1] != n or f["U"].shape != (rC, rR):
                raise RuntimeError
        if "X_hat" in f and f["X_hat"].shape != (m, n):
            raise RuntimeError

    def inverse_transform(self, X_centered_like: npt.ArrayLike) -> npt.NDArray:
        self._ensure_fitted()
        Xc = _check_2d(X_centered_like)
        mean = self.mean_ if self.mean_ is not None else 0.0
        return Xc + mean

    def residual(self) -> npt.NDArray:
        self._ensure_fitted()
        if self._X_orig_ is None:
            return np.zeros_like(self.reconstruct())
        return self._X_orig_ - self.reconstruct()

    # For scikit-learn compatibility
    def get_params(self, deep: bool = True) -> Dict[str, Any]:
        return {
            "n_components": getattr(self, "n_components", None),
            "center": getattr(self, "center", None),
            "copy_": getattr(self, "copy", None),
            "verbose": getattr(self, "verbose", None),
            "random_state": getattr(self, "random_state", None),
            "tol": getattr(self, "tol", None),
            "max_iter": getattr(self, "max_iter", None),
            "gamma": getattr(self, "gamma", None),
            "beta": getattr(self, "beta", None),
            "size": getattr(self, "size", None),
            "dr_components": getattr(self, "dr_components", None),
            "dr_method": getattr(self, "dr_method", None),
            "abs_error": getattr(self, "abs_error", None),
            "sz_algo": getattr(self, "sz_algo", None),
            "post_threshold_scale": getattr(self, "post_threshold_scale", None),
        }

    def set_params(self, **params: Any) -> "DrAlgo":
        for k, v in params.items():
            if not hasattr(self, k):
                if getattr(self, "verbose", True):
                    print(f"[DrAlgo] Ignoring unknown parameter: {k}={v!r}")
                continue
            setattr(self, k, v)
        return self

    def _psnr(self, X: np.ndarray, Xhat: np.ndarray) -> float:
        mse = np.mean((X - Xhat) ** 2)
        if mse == 0:
            return float("inf")
        data_range = X.max() - X.min()
        if data_range == 0:
            data_range = np.max(np.abs(X)) or 1.0
        return float(20 * np.log10(data_range) - 10 * np.log10(mse))

    def _ssim_global(self, X: np.ndarray, Xhat: np.ndarray) -> float:
        L = X.max() - X.min()
        L = L if L > 0 else 1.0

        C1 = (0.01 * L) ** 2
        C2 = (0.03 * L) ** 2

        mu_x, mu_y = X.mean(), Xhat.mean()
        var_x, var_y = X.var(), Xhat.var()
        cov = np.mean((X - mu_x) * (Xhat - mu_y))

        num = (2 * mu_x * mu_y + C1) * (2 * cov + C2)
        den = (mu_x**2 + mu_y**2 + C1) * (var_x + var_y + C2)
        return float(num / den)

    def _autocorr(self, x: np.ndarray, max_lag: int = 200) -> np.ndarray:
        x = x.ravel().astype(float, copy=False)
        x -= x.mean()
        n = len(x)
        fft = np.fft.fft(x, n=2 * n)
        ac = np.fft.ifft(fft * np.conj(fft)).real[:n]
        return (ac / ac[0])[: max_lag + 1]

    def compute_metrics(self, max_autocorr_lag: int = 200) -> Dict[str, Any]:
        self._ensure_fitted()
        if self._Xc_ is None:
            raise RuntimeError("Original centered data not stored.")

        X = self._X_orig_
        Xhat = self.reconstruct()
        resid = self.residual()
        f = self.factors()

        X_for_storage = getattr(self, "_X_storage_ref_", None)
        if X_for_storage is None:
            X_for_storage = self._X_orig_
        storage = {"X_storage_bytes": X_for_storage.nbytes}
        U = getattr(self, "U_", None)
        V = getattr(self, "V_", None)
        Sigma = getattr(self, "Sigma_", None)

        if "L" in f:
            if U is not None and V is not None and Sigma is not None:
                s = np.diag(Sigma)
                r = int(min(U.shape[1], V.shape[1], s.shape[0]))

                U_r = np.asarray(U[:, :r], dtype=np.float32, order="C")
                V_r = np.asarray(V[:, :r], dtype=np.float32, order="C")
                s_r = np.asarray(s[:r], dtype=np.float32, order="C")
            else:
                L = f["L"]
                U_svd, s_svd, Vt_svd = np.linalg.svd(L, full_matrices=False)
                r = int(s_svd.shape[0])

                U_r = np.asarray(U_svd[:, :r], dtype=np.float32, order="C")
                V_r = np.asarray(Vt_svd[:r, :].T, dtype=np.float32, order="C")
                s_r = np.asarray(s_svd[:r], dtype=np.float32, order="C")

            storage["U_storage_bytes"] = int(U_r.nbytes)
            storage["V_storage_bytes"] = int(V_r.nbytes)
            storage["Sigma_storage_bytes"] = int(s_r.nbytes)

            L_bytes = (
                storage["U_storage_bytes"]
                + storage["V_storage_bytes"]
                + storage["Sigma_storage_bytes"]
            )
            storage["L_storage_bytes"] = L_bytes

        if "S" in f:
            S_csr = sparse.csr_matrix(f["S"])
            storage["S_storage_bytes"] = (
                S_csr.data.nbytes + S_csr.indices.nbytes + S_csr.indptr.nbytes
            )

            denom = storage.get("L_storage_bytes", 0) + storage["S_storage_bytes"]
            storage["compression_ratio"] = (
                storage["X_storage_bytes"] / denom if denom else float("inf")
            )

        quality = {
            "PSNR": self._psnr(X, Xhat),
            "SSIM": self._ssim_global(X, Xhat),
            "residual_autocorr": self._autocorr(resid, max_autocorr_lag),
        }

        abs_err = {
            "fro": float(norm(resid, "fro")),
            "2": float(norm(resid, 2)),
            "inf": float(norm(resid, np.inf)),
        }

        rel_err = {
            "fro": abs_err["fro"] / float(norm(X, "fro")),
            "2": abs_err["2"] / float(norm(X, 2)),
            "inf": abs_err["inf"] / float(norm(X, np.inf)),
        }

        metrics = {
            "storage": storage,
            "quality": quality,
            "final_error": {
                "absolute": abs_err,
                "relative": rel_err,
            },
            "history": {
                "errors": np.asarray(self.errors_, dtype=float),
                "timers": np.asarray(self.timers_, dtype=float),
            },
        }

        self.metrics_ = metrics
        # print("L rank:", U_r.shape[1] if "L" in f else "N/A")
        # print("S sparsity: {:.4f} %".format(
        # 100 * np.count_nonzero(f["S"]) / f["S"].size if "S" in f else 0.0
        # ))
        # print("S_csr.data.nbytes:", storage.get("S_storage_bytes", 0), "inferred sparsity: {:.4f} %".format(
        # 100 * (f["S"].size - (storage.get("S_storage_bytes", 0) / (f["S"].dtype.itemsize * 2))) / f["S"].size if "S" in f else 0.0
        # ))
        return metrics


class LSDrAlgo(DrAlgo):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.low_rank_: Optional[npt.NDArray] = None
        self.sparse_: Optional[npt.NDArray] = None

    def factors(self) -> Factors:
        if self.low_rank_ is None or self.sparse_ is None:
            raise NotFittedError
        return Factors(L=self.low_rank_, S=self.sparse_)

    def _reconstruct(self, f: Factors) -> npt.NDArray:
        return f["L"] + f["S"]


class IrcurAlgo(DrAlgo):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.C_: Optional[npt.NDArray] = None
        self.U_: Optional[npt.NDArray] = None
        self.R_: Optional[npt.NDArray] = None

    def factors(self) -> Factors:
        if self.C_ is None or self.U_ is None or self.R_ is None:
            raise NotFittedError
        return Factors(C=self.C_, U=self.U_, R=self.R_)

    def _reconstruct(self, f: Factors) -> npt.NDArray:
        u_pinv = pinv(f["U"])
        return f["C"] @ u_pinv @ f["R"]


class XhatDrAlgo(DrAlgo):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.X_hat_: Optional[npt.NDArray] = None
        self.Resid_: Optional[npt.NDArray] = None

    def factors(self) -> Factors:
        if self.X_hat_ is None or self.Resid_ is None:
            raise NotFittedError
        return Factors(X_hat=self.X_hat_, Resid=self.Resid_)

    def _reconstruct(self, f: Factors) -> npt.NDArray:
        return f["X_hat"]
