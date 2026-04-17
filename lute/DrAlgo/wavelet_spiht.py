import json
import struct
import zlib
from typing import Any, Optional

import numpy as np
from sklearn.exceptions import NotFittedError

from .DrAlgo import XhatDrAlgo, Factors, _check_2d

from spiht import encode_image, decode_image, SpihtSettings


class WaveletSPIHTAlgo(XhatDrAlgo):
    """
    2D single-channel image compressor using the spiht package public API.

    This version:
    - avoids the circular import issue
    - uses encode_image/decode_image directly
    - treats the input X as a 1-channel image of shape (1, H, W)
    """

    def __init__(
        self,
        wavelet: str = "bior2.2",
        level: Optional[int] = None,
        quantization_scale: float = 50.0,
        mode: str = "reflect",
        max_bits: Optional[int] = None,
        entropy_level: int = 9,
        keep_payload: bool = False,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.wavelet = wavelet
        self.level = level
        self.quantization_scale = quantization_scale
        self.mode = mode
        self.max_bits = max_bits
        self.entropy_level = entropy_level
        self.keep_payload = keep_payload

        self.compressed_info_: Optional[dict] = None
        self.encoding_result_: Optional[Any] = None
        self.spiht_settings_: Optional[SpihtSettings] = None

    def _fit_core(self, Xc: np.ndarray, **kwargs: Any) -> None:
        X = _check_2d(Xc).astype(np.float32, copy=False)
        m, n = X.shape

        # spiht.encode_image expects (C, H, W)
        image = X[None, :, :]

        spiht_settings = SpihtSettings(
            wavelet=self.wavelet,
            quantization_scale=self.quantization_scale,
            mode=self.mode,
            color_model=None,
            per_channel_quant_scales=None,
        )

        enc = encode_image(
            image=image,
            spiht_settings=spiht_settings,
            level=self.level,
            max_bits=self.max_bits,
        )

        rec = decode_image(
            encoding_result=enc,
            spiht_settings=spiht_settings,
            return_metadata=False,
        )

        # decode_image returns (C, H, W)
        X_hat = np.asarray(rec[0, :m, :n], dtype=np.float32)

        self.X_hat_ = X_hat
        self.Resid_ = X - X_hat
        self.encoding_result_ = enc
        self.spiht_settings_ = spiht_settings

        if self.keep_payload:
            header = {
                "kind": "wavelet_spiht_v2",
                "shape": [m, n],
                "wavelet": self.wavelet,
                "level": self.level,
                "quantization_scale": self.quantization_scale,
                "mode": self.mode,
                "max_bits": self.max_bits,
                "encoding_result_meta": enc.to_dict(),
            }

            header_blob = zlib.compress(
                json.dumps(header).encode("utf-8"),
                self.entropy_level,
            )
            bitstream = enc.encoded_bytes

            payload = (
                struct.pack("<I", len(header_blob))
                + header_blob
                + struct.pack("<Q", len(bitstream))
                + bitstream
            )

            self.compressed_info_ = {
                "payload": payload,
                "payload_bytes": len(payload),
                "header_bytes": len(header_blob),
                "coeff_bytes": len(bitstream),
            }

    def factors(self) -> Factors:
        if self.X_hat_ is None or self.Resid_ is None:
            raise NotFittedError("WaveletSPIHTAlgo is not fitted yet.")
        return Factors(X_hat=self.X_hat_, Resid=self.Resid_)