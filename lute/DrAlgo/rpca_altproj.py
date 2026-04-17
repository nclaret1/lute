from __future__ import annotations
from typing import Optional, Tuple
import time, numpy as np, numpy.typing as npt
from numpy.linalg import norm
from scipy.sparse.linalg import svds
from scipy.linalg import svd, qr
from .DrAlgo import LSDrAlgo, Factors, _fro_norm, NotFittedError, _check_2d

def wthresh(A: np.ndarray, thresh: float) -> np.ndarray:
    out=A.copy(); out[np.abs(out)<thresh]=0; return out

#will need to implement if center
class RPCAAltProj(LSDrAlgo):
    def __init__(
            self, 
            n_components: Optional[int]=None, 
            *, 
            max_iter:int=1000,
            tol:float=1e-3, 
            beta:Optional[float]=None, 
            beta_init:Optional[float]=None,
            gamma:float=0.7, 
            mu:Tuple[float,float]=(5,5), 
            trim:bool=False,
            verbose:bool=False, 
            copy_:bool=True
            ):
        super().__init__(
            n_components=n_components,
            max_iter=max_iter,
            tol=tol, 
            verbose=False, 
            copy_=copy_
            )
        if n_components is not None and n_components <= 0:
            raise ValueError(
                f"Expected positive number of components, got {n_components} instead."
            )
        self.n_components = n_components
        self.max_iter = max_iter
        self.tol = tol
        self.beta = beta
        self.beta_init = beta_init
        self.gamma = gamma
        self.mu = mu
        self.trim = trim
        self.copy_ = copy_

    def _fit_core(self, X: npt.ArrayLike, y=None) -> "RPCAAltProj":
        self.__fit_core(np.asarray(X))
        return self

    def _initialisation(
        self, X: npt.ArrayLike
    ) -> Tuple[npt.NDArray, npt.NDArray, npt.NDArray, npt.NDArray, npt.NDArray]:
        X = np.asarray(X, dtype=float)

        n_samples, n_features = X.shape
        if self.beta is None:
            beta = 1 /  (2 * np.power(n_samples * n_features, 1 / 4))
        else:
            beta = self.beta
        if self.beta_init is None:
            beta_init = 4 * beta
        else:
            beta_init = self.beta_init
        if self.n_components is None:
            n_components = min(X.shape) - 1
        else:
            n_components = self.n_components

        zeta: float
        zeta = beta_init * svds(X, k=1, return_singular_vectors=False)[0]  # type: ignore
        S = wthresh(X, zeta)

        U: npt.NDArray
        Sigma: npt.NDArray
        V: npt.NDArray
        U, s, Vt = svds(X - S, n_components)  # type: ignore
        idx = np.argsort(s)[::-1] 
        s = s[idx]
        U = U[:, idx]
        Vt = Vt[idx, :]

        Sigma = np.diag(s)
        V = Vt.T
        # make Sigma a diag for consistency with matlab implementation
        L = U @ Sigma @ V.T
        zeta = beta * Sigma[0, 0]
        S = wthresh(X - L, zeta)

        self.beta_ = beta
        self.beta_init_ = beta_init
        self.n_samples_ = n_samples
        self.n_features_ = n_features
        self.n_components_ = n_components
        # transpose the V for consistency with matlab
        return L, S, U, Sigma, V

    def __fit_core(
        self, X: npt.NDArray
    ) -> Tuple[npt.NDArray, npt.NDArray, npt.NDArray, npt.NDArray, npt.NDArray]:
        
        _check_2d(X)

        errors = []
        timers = []
        norm_of_X: float
        
        #Noemie moving this after centering X
        #norm_of_X = norm(X, "fro")  # type: ignore
        #self.mean_ = np.mean(X, axis=0)
        #X = np.subtract(X, self.mean_)
        norm_of_X = norm(X, "fro")  # type: ignore

        init_start = time.perf_counter()
        L, S, U, Sigma, V = self._initialisation(X)
        #errors.append(self._compute_error(X, L, S, norm_of_X))
        init_timer = time.perf_counter() - init_start

        i = 1
        for i in range(1, self.max_iter + 1):
            iter_start_time = time.perf_counter()
            if self.trim:
                U, V = self._trim(
                    U,
                    Sigma[: self.n_components_, : self.n_components_],
                    V,
                    self.mu[0],
                    self.mu[-1],
                )
            # update L
            Z = X - S
            # These 2 QR can be computed in parallel
            Q1: npt.NDArray
            R1: npt.NDArray
            Q2: npt.NDArray
            R2: npt.NDArray
            Q1, R1 = qr(Z.T @ U - V @ ((Z @ V).T @ U), mode="economic")  # type: ignore
            Q2, R2 = qr(Z @ V - U @ (U.T @ Z @ V), mode="economic")  # type: ignore

            M = np.vstack(
                [np.hstack([U.T @ Z @ V, R1.T]), np.hstack([R2, np.zeros_like(R2)])]
            )
            
            U_of_M, Sigma, V_of_M = svd(M, full_matrices=False)
            V_of_M = V_of_M.T 
            Sigma = np.diag(Sigma)
            # These 2 matrices multiplications can be computed in parallel
            U = np.hstack([U, Q2]) @ U_of_M[:, : self.n_components_]
            V = np.hstack([V, Q1]) @ V_of_M[:, : self.n_components_]
            L = U @ Sigma[: self.n_components_, : self.n_components_ ] @ V.T

            # update S

            zeta = self.beta_ * (
            Sigma[self.n_components_, self.n_components_]
                + ((self.gamma**i) * Sigma[0, 0])
            )
            S = wthresh(X - L, zeta)


            error = self._compute_error(X, L, S, norm_of_X)
            errors.append(error)

            iter_time = time.perf_counter() - iter_start_time
            timers.append(iter_time)

            print(f"[{i}] Tolerance: {self.tol}\tCurrent error: {error}")
            if error < self.tol:
                #if self.verbose:
                print("Tolerance condition met.")
                break
        if error > self.tol:
            print("Tolerance condition not met.")

        self.L_ = L
        self.S_ = S
        self.U_ = U
        self.V_ = V
        self.Sigma_ = Sigma
        self.factors_ = Factors(
            {
                "L": L,
                "S": S,
            }
        )
        
        self.low_rank_ = L
        self.sparse_ = S

        # transpose V for consistency with sklearn's pca
        #self.components_ = V.T
        # flatten the Sigma for consistency with sklearn's pca
        #self.singular_values_ = np.diag(Sigma)[: self.n_components_]

        self.end_iter_ = i
        self.errors_ = errors
        self.final_error_ = errors[-1]
        timers[0] += init_timer
        self.timers_ = timers

        #print(f"Final error after {i} iterations: {errors[-1]}")
        return L, S, U, Sigma, V
    

    
    @staticmethod
    def __trim(X: npt.NDArray, mu_X: float) -> Tuple[npt.NDArray, npt.NDArray]:
        m, r = X.shape
        row_norm_square_X = np.sum(
            np.power(X, 2), axis=1
        )  # might need to set it to columns vector
        big_rows_X = row_norm_square_X > (mu_X * r / m)
        X[big_rows_X] = (
            X[big_rows_X]
            * ((mu_X * r / m) / np.sqrt(row_norm_square_X[big_rows_X]))[:, np.newaxis]
        )
        Q: npt.NDArray
        R: npt.NDArray
        Q, R = qr(X, mode="economic")  # type: ignore
        return Q, R

    def _trim(
        self, 
        U: npt.NDArray, 
        Sig: npt.NDArray, 
        V: npt.NDArray, 
        mu_V: float, 
        mu_U: float
    ) -> Tuple[npt.NDArray, npt.NDArray]:
        # these 2 qr can be computed in parallel
        Q1, R1 = self.__trim(U, mu_U)
        Q2, R2 = self.__trim(V, mu_V)
        U_tmp, _, V_tmp = svd(R1 @ Sig @ R2.T, full_matrices=False)
        return Q1 @ U_tmp, Q2 @ V_tmp.T

    @staticmethod
    def _compute_error(
        X: npt.NDArray, 
        L: npt.NDArray, 
        S: npt.NDArray, 
        norm_of_X: Optional[float]
    ) -> float:
        return norm(X - (L + S), "fro") / (
            norm(X, "fro") if norm_of_X is None else norm_of_X
        )  # type:ignore
    

