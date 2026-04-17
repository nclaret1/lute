from __future__ import annotations

from typing import Optional, Tuple, Any

import numpy as np
import numpy.typing as npt

from .rpca_altproj import RPCAAltProj

try:
    from skimage.restoration import denoise_tv_chambolle
    _HAVE_SKIMAGE = True
except Exception:
    _HAVE_SKIMAGE = False


def tv_anisotropic_fast(
    Y: np.ndarray,
    weight: float,
    *,
    n_iter_max: int = 200,
) -> np.ndarray:
    if not _HAVE_SKIMAGE:
        raise RuntimeError("scikit-image not available")
    if Y.ndim != 2:
        raise ValueError(f"Expected 2D array, got shape={Y.shape}")

    return denoise_tv_chambolle(
        Y.astype(np.float32, copy=False),
        weight=weight,
        max_num_iter=n_iter_max,
        channel_axis=None,
    )


class TVRPCAAltProj(RPCAAltProj):
    def __init__(
        self,
        n_components: Optional[int] = None,
        *,
        tv_weight_factor: float = 0.04,
        tv_weight_min: float = 1e-6,
        tv_n_iter: int = 200,
        max_iter: int = 1000,
        tol: float = 1e-3,
        beta: Optional[float] = None,
        beta_init: Optional[float] = None,
        gamma: float = 0.7,
        mu: Tuple[float, float] = (5, 5),
        trim: bool = False,
        verbose: bool = False,
        copy_: bool = True,
    ):
        super().__init__(
            n_components=n_components,
            max_iter=max_iter,
            tol=tol,
            beta=beta,
            beta_init=beta_init,
            gamma=gamma,
            mu=mu,
            trim=trim,
            verbose=verbose,
            copy_=copy_,
        )

        self.tv_weight_factor = float(tv_weight_factor)
        self.tv_weight_min = float(tv_weight_min)
        self.tv_n_iter = int(tv_n_iter)

        self.frame_shape_ = None
        self.tv_weights_ = None

    def _fit_core(self, X: npt.ArrayLike, y=None, **kwargs: Any) -> "TVRPCAAltProj":
        X = np.asarray(X, dtype=float)

        if X.ndim != 2:
            raise ValueError(f"Expected single 2D panel (H,W), got shape={X.shape}")

        self.frame_shape_ = X.shape

        med = np.median(X)
        mad = np.median(np.abs(X - med)) + 1e-12
        sigma = 1.4826 * mad

        w = max(self.tv_weight_min, self.tv_weight_factor * sigma)
        X_tv = tv_anisotropic_fast(X, weight=w, n_iter_max=self.tv_n_iter)

        self.tv_weights_ = np.array([w], dtype=float)

        super()._fit_core(X_tv, y=y)


        return self
