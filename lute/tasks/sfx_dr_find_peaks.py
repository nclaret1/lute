"""
Classes for peak finding tasks in SFX.

Classes:
    DrFindPeaksPyAlgos: peak finding using psana's PyAlgos algorithm. Optional data
        compression and decompression with libpressio for data reduction tests.
"""

__all__ = ["DrFindPeaksPyAlgos"]
__author__ = "Noemie Claret"

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO, Tuple, Union, cast

import h5py  # type: ignore
import numpy
import numpy as np
import panel as pn  # type: ignore
from mpi4py.MPI import COMM_WORLD, SUM
from numpy.typing import NDArray
from psalgos.pypsalgos import PyAlgos  # type: ignore
from psana import Detector, EventId, MPIDataSource  # type: ignore
from PSCalib import GeometryAccess  # type: ignore

from lute.DrAlgo import import_dr_algo
from lute.execution.ipc import Message
from lute.io.models.sfx_dr_find_peaks import DrFindPeaksPyAlgosParameters
from lute.tasks.dataclasses import ElogSummaryPlots, TaskStatus
from lute.tasks.sfx_find_peaks import (
    CxiWriter,
    add_peaks_to_libpressio_configuration,
    generate_libpressio_configuration,
    write_master_file,
)
from lute.tasks.task import Task


# -----------------------------------------------------------------------------
# PSEUDO-SQRT MONKEY PATCH SUPPORT
# -----------------------------------------------------------------------------

def _stats_line(x: np.ndarray, *, name: str, max_adu: int) -> str:
    a = np.asarray(x, dtype=np.float32).ravel()
    n = a.size
    if n == 0:
        return f"[psqrt] {name}: empty"

    finite = np.isfinite(a)
    af = a[finite]
    if af.size == 0:
        return f"[psqrt] {name}: n={n} finite=0"

    p = np.percentile(af, [0.1, 1, 50, 99, 99.9]).astype(float)

    nneg = int(np.sum(af < 0))
    nzero = int(np.sum(af == 0))
    nmax = int(np.sum(af >= max_adu))

    return (
        f"[psqrt] {name}: n={n} finite={af.size} "
        f"min={float(af.min()):.3f} p0.1={p[0]:.3f} p1={p[1]:.3f} "
        f"p50={p[2]:.3f} p99={p[3]:.3f} p99.9={p[4]:.3f} "
        f"max={float(af.max()):.3f} mean={float(af.mean()):.3f} std={float(af.std()):.3f} "
        f"neg={nneg} zero={nzero} ge_max={nmax}"
    )




def psqrt_roundtrip_fracbits_gain_equalized(
    x: np.ndarray,
    *,
    frac_bits: float = 0.75,
    offset: float = 0.0,
    x_max: Optional[float] = None,
    min_bits_out: int = 1,
    max_bits_out: int = 16,
    peak_mode: str = "max",
    peak_percentile: float = 99.99,
) -> np.ndarray:
    if not (0.0 < frac_bits <= 1.0):
        raise ValueError("frac_bits must be in (0, 1].")
    if peak_mode not in ("max", "percentile"):
        raise ValueError("peak_mode must be 'max' or 'percentile'.")

    x0 = np.asarray(x, dtype=np.float32)
    finite0 = np.isfinite(x0)
    if not np.any(finite0):
        return np.full_like(x0, np.nan, dtype=np.float32)

    x0_pos = x0[finite0]
    x0_pos = x0_pos[x0_pos > 0.0]
    if x0_pos.size == 0:
        return (np.zeros_like(x0, dtype=np.float32) - np.float32(offset))

    if peak_mode == "max":
        x_peak_unshifted = float(np.max(x0_pos))
    else:
        x_peak_unshifted = float(np.percentile(x0_pos, peak_percentile))

    B_need = int(np.ceil(np.log2(x_peak_unshifted + 1.0)))
    B_out = int(np.ceil(frac_bits * B_need))
    B_out = max(min_bits_out, min(max_bits_out, B_out))
    y_max = (1 << B_out) - 1

    xf = x0 + np.float32(offset)
    finitef = np.isfinite(xf)

    if x_max is None:
        x_max_use = float(np.max(xf[finitef]))
    else:
        x_max_use = float(x_max)

    if x_max_use <= 0.0:
        return (np.zeros_like(x0, dtype=np.float32) - np.float32(offset))

    k = (float(y_max) * float(y_max)) / x_max_use

    xc = np.clip(xf, 0.0, x_max_use)
    code = np.rint(np.sqrt(k * xc))
    code = np.clip(code, 0.0, float(y_max))

    x_rec = (code * code) / k
    return (x_rec - np.float32(offset)).astype(np.float32)


def presqrt_k_for_nbits_out(max_adu: int, nbits_out: int) -> float:
    y_max = (1 << int(nbits_out)) - 1
    return (y_max * y_max) / float(max_adu)


def psqrt_roundtrip_adu(
    arrf_adu: np.ndarray,
    *,
    nbits_out: int = 9,
    k: Optional[float] = None,
    max_adu: int = (1 << 14) - 1,
    clip: bool = True,
) -> np.ndarray:
    kk = presqrt_k_for_nbits_out(max_adu, nbits_out) if k is None else float(k)
    x = np.asarray(arrf_adu, dtype=np.float32)
    if clip:
        x = np.clip(x, 0, max_adu)
    code = np.rint(np.sqrt(kk * x))
    x_rec = (code ** 2) / kk
    return x_rec


def patch_epix10ka_calib_nda_psqrt(
    *,
    nbits_out: int = 9,
    k: Optional[float] = None,
    max_adu: int = (1 << 14) - 1,
    DO_LOG: bool = True,
) -> None:
    """
    Monkey-patch Detector.UtilsEpix10ka.calib_epix10ka_nda to inject pseudo-sqrt
    on arrf (ADU) BEFORE gain-factor multiplication.
    """
    import logging
    from time import time

    import Detector.UtilsEpix10ka as ue  # type: ignore
    from Detector.GlobalUtils import info_ndarr  # type: ignore

    logger = logging.getLogger(__name__)

    def calib_epix10ka_nda_psqrt(arr, gfac, peds, mask, cmps, gmap, aone, DO_LOG=True):
        raw = arr
        gr0, gr1, gr2, gr3, gr4, gr5, gr6 = gmap

        factor = (
            np.select(
                gmap,
                (
                    gfac[0, :],
                    gfac[1, :],
                    gfac[2, :],
                    gfac[3, :],
                    gfac[4, :],
                    gfac[5, :],
                    gfac[6, :],
                ),
                default=1,
            )
            if gfac is not None
            else 1
        )

        pedest = (
            np.select(
                gmap,
                (
                    peds[0, :],
                    peds[1, :],
                    peds[2, :],
                    peds[3, :],
                    peds[4, :],
                    peds[5, :],
                    peds[6, :],
                ),
                default=0,
            )
            if peds is not None
            else 0
        )

        arrf = np.array(raw & ue.M14, dtype=np.float32) - pedest

        if DO_LOG:
            logger.info(_stats_line(arrf, name="BEFORE", max_adu=max_adu))

        arrf = psqrt_roundtrip_adu(
            arrf,
            nbits_out=nbits_out,
            k=k,
            max_adu=max_adu,
            clip=True,
        )

        if DO_LOG:
            logger.info(_stats_line(arrf, name="AFTER", max_adu=max_adu))

        mode, cormax = int(cmps[1]), cmps[2]
        npixmin = cmps[3] if len(cmps) > 3 else 10

        if mode > 0:
            t0_sec_cm = time()

            arr1 = aone
            grhm = np.select((gr0, gr1, gr3, gr4), (arr1, arr1, arr1, arr1), default=0)
            gmask = np.bitwise_and(grhm, mask) if mask is not None else grhm
            if gmask.ndim == 2:
                gmask.shape = (1, gmask.shape[-2], gmask.shape[-1])

            logger.debug(
                info_ndarr(gmask, "gmask")
                + "\n  per panel statistics of cm-corrected pixels: %s"
                % str(np.sum(gmask, axis=(1, 2), dtype=np.uint32) if gmask is not None else None)
            )

            hrows = 176
            for s in range(arrf.shape[0]):
                if mode & 4:
                    ue.common_mode_2d_hsplit_nbanks(
                        arrf[s, :hrows, :],
                        mask=gmask[s, :hrows, :],
                        nbanks=8,
                        cormax=cormax,
                        npix_min=npixmin,
                    )
                    ue.common_mode_2d_hsplit_nbanks(
                        arrf[s, hrows:, :],
                        mask=gmask[s, hrows:, :],
                        nbanks=8,
                        cormax=cormax,
                        npix_min=npixmin,
                    )

                if mode & 1:
                    ue.common_mode_rows_hsplit_nbanks(
                        arrf[s, :],
                        mask=gmask[s, :],
                        nbanks=8,
                        cormax=cormax,
                        npix_min=npixmin,
                    )

                if mode & 2:
                    ue.common_mode_cols(
                        arrf[s, :hrows, :],
                        mask=gmask[s, :hrows, :],
                        cormax=cormax,
                        npix_min=npixmin,
                    )
                    ue.common_mode_cols(
                        arrf[s, hrows:, :],
                        mask=gmask[s, hrows:, :],
                        cormax=cormax,
                        npix_min=npixmin,
                    )

            logger.debug(
                "TIME common-mode correction = %.6f sec for cmps=%s" % (time() - t0_sec_cm, str(cmps))
            )

        res = arrf * factor if mask is None else arrf * factor * mask
        return res

    ue.calib_epix10ka_nda = calib_epix10ka_nda_psqrt


class MetricsWriter:
    def __init__(
        self,
        outdir: Union[str, Path],
        rank: int,
        exp: str,
        run: int,
        n_events: int,
        tag: str = "",
        save_debug_panels: bool = True,
        n_debug_events: int = 100,
        panels_per_event: int = 1,
        debug_dtype=np.float32,
        debug_compression: str = "lzf",
        debug_gzip_level: int = 4,
        debug_seed: int = 12345,
    ):
        self.outdir = Path(outdir)
        self.rank = int(rank)
        self.exp = exp
        self.run = int(run)
        self.n_events = int(n_events)
        self.tag = tag
        self.save_debug_panels = bool(save_debug_panels)
        self.n_debug_events = int(n_debug_events)
        self.panels_per_event = int(panels_per_event)
        self.debug_dtype = debug_dtype
        self.debug_compression = debug_compression
        self.debug_gzip_level = int(debug_gzip_level)

        self._rng = np.random.default_rng(int(debug_seed) + self.rank)
        self._seen = 0
        self._reservoir: list[dict[str, Any]] = []

        self.outdir.mkdir(parents=True, exist_ok=True)
        self.filename = self.outdir / f"r{self.run:04d}_metrics_rank{self.rank:03d}{self.tag}.h5"
        self.file: Optional[h5py.File] = None
        self.event_count = 0

    def open_file(self):
        if self.file is None or not self.file.id.valid:
            self.file = h5py.File(self.filename, "w")

    def write_event_metrics(
        self,
        event_id: int,
        metrics: Dict[str, Any],
        timestamp: Optional[float] = None,
    ) -> None:
        self.open_file()
        assert self.file is not None
        eg = self.file.create_group(f"event_{int(event_id)}")
        if timestamp is not None:
            eg.attrs["timestamp"] = float(timestamp)
        for category, category_metrics in metrics.items():
            cg = eg.create_group(str(category))
            self._write_metrics_recursive(cg, category_metrics)
        self.event_count += 1
        self.file.flush()

    def _write_metrics_recursive(self, group: h5py.Group, metrics: Dict[str, Any]) -> None:
        for key, value in metrics.items():
            k = str(key)
            if isinstance(value, dict):
                sg = group.create_group(k)
                self._write_metrics_recursive(sg, value)
            elif isinstance(value, (list, np.ndarray)):
                group.create_dataset(k, data=np.array(value))
            else:
                group.attrs[k] = value

    def maybe_write_debug_panels(
        self,
        *,
        event_id: int,
        timestamp: float,
        original_img: np.ndarray,
        reconstructed_img: np.ndarray,
    ) -> None:
        if (not self.save_debug_panels) or (self.n_debug_events <= 0) or (self.panels_per_event <= 0):
            return

        P = int(original_img.shape[0])
        if P <= 0:
            return
        k = min(self.panels_per_event, P)
        panel_ids = self._rng.choice(P, size=k, replace=False).astype(np.int32)

        payload = {
            "event_id": int(event_id),
            "timestamp": float(timestamp),
            "panel_ids": panel_ids,
            "original": [np.asarray(original_img[int(p)], dtype=self.debug_dtype, order="C") for p in panel_ids],
            "reconstructed": [np.asarray(reconstructed_img[int(p)], dtype=self.debug_dtype, order="C") for p in panel_ids],
        }

        self._seen += 1
        if len(self._reservoir) < self.n_debug_events:
            self._reservoir.append(payload)
            return

        j = int(self._rng.integers(0, self._seen))
        if j < self.n_debug_events:
            self._reservoir[j] = payload

    def _write_small_dataset(self, group: h5py.Group, name: str, arr2d: np.ndarray) -> None:
        if name in group:
            del group[name]
        a = np.asarray(arr2d, dtype=self.debug_dtype, order="C")

        kwargs: dict[str, Any] = {}
        if self.debug_compression == "gzip":
            kwargs["compression"] = "gzip"
            kwargs["compression_opts"] = self.debug_gzip_level
        elif self.debug_compression == "lzf":
            kwargs["compression"] = "lzf"

        kwargs["chunks"] = (min(256, a.shape[0]), min(256, a.shape[1]))
        group.create_dataset(name, data=a, **kwargs)

    def _flush_debug_panels(self) -> None:
        if (not self.save_debug_panels) or (len(self._reservoir) == 0):
            return
        self.open_file()
        assert self.file is not None
        dbg_root = self.file.require_group("debug_panels")

        for item in self._reservoir:
            eid = int(item["event_id"])
            eg = dbg_root.require_group(f"event_{eid}")
            eg.attrs["timestamp"] = float(item["timestamp"])
            panel_ids = np.asarray(item["panel_ids"], dtype=np.int32)
            eg.attrs["panel_ids"] = panel_ids

            orig_list = item["original"]
            rec_list = item["reconstructed"]
            for idx, p in enumerate(panel_ids.tolist()):
                pg = eg.require_group(f"panel_{int(p):03d}")
                pg.attrs["panel_index"] = int(p)
                self._write_small_dataset(pg, "original", orig_list[idx])
                self._write_small_dataset(pg, "reconstructed", rec_list[idx])

        self.file.flush()

    def write_summary(self, summary_metrics: Dict[str, Any]) -> None:
        self.open_file()
        assert self.file is not None
        sg = self.file.create_group("summary")
        self._write_metrics_recursive(sg, summary_metrics)
        self.file.flush()

    def close(self) -> None:
        if self.file is not None and self.file.id.valid:
            self._flush_debug_panels()
            self.file.close()
            self.file = None

    def __enter__(self):
        self.open_file()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()




# -----------------------------------------------------------------------------
# TASK
# -----------------------------------------------------------------------------

class DrFindPeaksPyAlgos(Task):
    """
    Task that compresses images, benchmarks speed/bandwidth and performs peak finding
    using the PyAlgos peak finding algorithms and writes the peak information to CXI files.
    """

    def __init__(self, *, params: DrFindPeaksPyAlgosParameters, use_mpi: bool = True) -> None:
        super().__init__(params=params, use_mpi=use_mpi)

    def _run(self) -> None:
        APPLY_PSQRT = False

        PSQRT_NBITS_OUT = 11
        PSQRT_MAX_ADU = (1 << 14) - 1
        PSQRT_K = None
        DO_LOG = True
        APPLY_PSQRT_KEV = False
        FRAC_BITS = 0.75

        self._task_parameters = cast(DrFindPeaksPyAlgosParameters, self._task_parameters)
        ENABLE_ELOG: bool = False  # os.getenv("LUTE_ENABLE_ELOG", "0") == "1"

        ds: Any = MPIDataSource(
            f"exp={self._task_parameters.lute_config.experiment}:"
            f"run={self._task_parameters.lute_config.run}:smd"
        )
        if self._task_parameters.n_events != 0:
            ds.break_after(self._task_parameters.n_events)

        det: Any = Detector(self._task_parameters.det_name)
        det.do_reshape_2d_to_3d(flag=True)

        if APPLY_PSQRT:
            patch_epix10ka_calib_nda_psqrt(
                nbits_out=PSQRT_NBITS_OUT,
                k=PSQRT_K,
                max_adu=PSQRT_MAX_ADU,
                DO_LOG=DO_LOG,
            )
            print("[psqrt] calib_epix10ka_nda patched:", True)
        

        evr: Any = Detector(self._task_parameters.event_receiver)

        i_x: Any = det.indexes_x(self._task_parameters.lute_config.run).astype(numpy.int64)
        i_y: Any = det.indexes_y(self._task_parameters.lute_config.run).astype(numpy.int64)
        ipx, ipy = det.point_indexes(self._task_parameters.lute_config.run, pxy_um=(0, 0))

        alg: Any = None
        num_hits: int = 0
        num_events: int = 0
        num_empty_images: int = 0

        tag: str = self._task_parameters.tag
        if (tag != "") and (tag[0] != "_"):
            tag = "_" + tag

        dr_algo: Any = None
        if self._task_parameters.dr_method:
            AlgoClass = import_dr_algo(self._task_parameters.dr_method)
            dr_algo = AlgoClass()
            dr_algo.set_params(
                n_components=self._task_parameters.n_components,
                tol=self._task_parameters.tol,
                gamma=self._task_parameters.gamma,
                max_iter=self._task_parameters.max_iter,
                size=self._task_parameters.size,
                dr_component=self._task_parameters.dr_component,
            )

        metrics_writer = MetricsWriter(
            outdir=self._task_parameters.outdir,
            rank=ds.rank,
            exp=self._task_parameters.lute_config.experiment,
            run=int(self._task_parameters.lute_config.run),
            n_events=self._task_parameters.n_events,
            tag=tag,
            save_debug_panels=(ds.rank == 0),
            n_debug_events=50,
            panels_per_event=2,
        )

        event_number = 0
        for evt in ds.events():
            evt_id: Any = evt.get(EventId)
            timestamp_seconds: int = evt_id.time()[0]
            timestamp_nanoseconds: int = evt_id.time()[1]
            timestamp_fiducials: int = evt_id.fiducials()
            event_codes: Any = evr.eventCodes(evt)

            if isinstance(self._task_parameters.pv_camera_length, float):
                clen: float = self._task_parameters.pv_camera_length
            else:
                clen = ds.env().epicsStore().value(self._task_parameters.pv_camera_length)

            # Event logic (skip non-matching codes)
            if self._task_parameters.event_logic:
                if self._task_parameters.event_code not in event_codes:
                    continue

            img: Any = det.calib(evt)
            if img is None:
                num_empty_images += 1
                continue
            original_img = np.array(img, copy=True)

            if APPLY_PSQRT_KEV:
                min_val = np.nanmin(img)
                offset = -min_val if min_val < 0 else 0.0

                img_psqrt = psqrt_roundtrip_fracbits_gain_equalized(
                    img,
                    frac_bits=FRAC_BITS,
                    offset=offset
                )

            # Optional DR stage (per-panel)
            if self._task_parameters.dr_method:
                if event_number == 0:
                    reconstructed_img: Any = numpy.zeros(img.shape)
                    assert img.ndim == 3
                    P, H, W = img.shape

                    r_frac = 0.2
                    desired = int(max(1, round(r_frac * min(H, W))))
                    k = min(desired, min(H, W) - 1)
                    self._task_parameters.n_components = k
                    dr_algo.set_params(n_components=self._task_parameters.n_components)

                evt_tag = f"event{timestamp_seconds}_{timestamp_nanoseconds}_{timestamp_fiducials}"
                event_number += 1

                P = img.shape[0]
                reconstructed_img = numpy.zeros_like(img)

                for p in range(P):
                    panel = img[p, :, :]
                    try:
                        dr_algo.fit(panel)
                        metrics = dr_algo.compute_metrics()
                        metrics_writer.write_event_metrics(
                            event_id=event_number * P + p,
                            metrics=metrics,
                            timestamp=timestamp_seconds + timestamp_nanoseconds * 1e-9
                        )

                        if self._task_parameters.dr_component == "S":
                            reconstructed_img[p] = dr_algo.sparse_
                        elif self._task_parameters.dr_component in ("L+S", "X_hat"):
                            reconstructed_img[p] = dr_algo.reconstruct()
                        
                        else:
                            reconstructed_img[p] = panel
                    except Exception as e:
                        print(f"[Frame {p}] Error during fit: {e}")
                        reconstructed_img[p] = panel
                        save_dir = "/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/analysis/DrComp/failed_frames"
                        os.makedirs(save_dir, exist_ok=True)
                        bad_img_path = os.path.join(save_dir, f"failed_frame_{p}.npy")
                        np.save(bad_img_path, panel)
                        print(f"Saved failed frame to {bad_img_path}")
                d = reconstructed_img - original_img
                print("max_abs_diff", float(np.nanmax(np.abs(d))), "nnz", int(np.sum(d != 0)))
                metrics_writer.maybe_write_debug_panels(
                    event_id=event_number, 
                    timestamp=timestamp_seconds + timestamp_nanoseconds * 1e-9,
                    original_img=original_img,
                    reconstructed_img=reconstructed_img,
                )
                img = reconstructed_img

            
            
            # Initialize peak finder + writers once we have an image
            if alg is None:
                det_shape: Tuple[int, ...] = img.shape
                if len(det_shape) == 3:
                    det_shape = (det_shape[0] * det_shape[1], det_shape[2])
                else:
                    det_shape = img.shape

                mask: NDArray[numpy.uint16] = numpy.ones(det_shape).astype(numpy.uint16)

                if self._task_parameters.psana_mask:
                    mask = det.mask(
                        int(self._task_parameters.lute_config.run),
                        calib=False,
                        status=True,
                        edges=False,
                        centra=False,
                        unbond=False,
                        unbondnbrs=False,
                    ).astype(numpy.uint16)

                if self._task_parameters.mask_file is not None:
                    with h5py.File(self._task_parameters.mask_file, "r") as hdffh:
                        loaded_mask: NDArray[numpy.int64] = hdffh["entry_1/data_1/mask"][:]
                        mask *= loaded_mask.astype(numpy.uint16)

                file_writer: CxiWriter = CxiWriter(
                    outdir=self._task_parameters.outdir,
                    rank=ds.rank,
                    exp=self._task_parameters.lute_config.experiment,
                    run=int(self._task_parameters.lute_config.run),
                    n_events=self._task_parameters.n_events,
                    det_shape=det_shape,
                    raw_det_shape=img.shape,
                    i_x=i_x,
                    i_y=i_y,
                    ipx=ipx,
                    ipy=ipy,
                    min_peaks=self._task_parameters.min_peaks,
                    max_peaks=self._task_parameters.max_peaks,
                    tag=tag,
                )

                alg = PyAlgos(mask=mask, pbits=0)
                alg.set_peak_selection_pars(
                    npix_min=self._task_parameters.npix_min,
                    npix_max=self._task_parameters.npix_max,
                    amax_thr=self._task_parameters.amax_thr,
                    atot_thr=self._task_parameters.atot_thr,
                    son_min=self._task_parameters.son_min,
                )

                libpressio_config = None
                if self._task_parameters.compression is not None:
                    libpressio_config = generate_libpressio_configuration(
                        compressor=self._task_parameters.compression.compressor,
                        roi_window_size=self._task_parameters.compression.roi_window_size,
                        bin_size=self._task_parameters.compression.bin_size,
                        abs_error=self._task_parameters.compression.abs_error,
                        libpressio_mask=mask,
                    )

                powder_hits: NDArray[numpy.float64] = numpy.zeros(det_shape)
                powder_misses: NDArray[numpy.float64] = numpy.zeros(det_shape)

            # Find peaks
            peaks: Any = alg.peak_finder_v3r3(
                img,
                rank=self._task_parameters.peak_rank,
                r0=self._task_parameters.r0,
                dr=self._task_parameters.dr,
                nsigm=self._task_parameters.nsigm,
            )

            num_events += 1

            if (peaks.shape[0] >= self._task_parameters.min_peaks) and (
                peaks.shape[0] <= self._task_parameters.max_peaks
            ):
                if self._task_parameters.compression is not None and libpressio_config is not None:
                    from libpressio import PressioCompressor  # type: ignore

                    libpressio_config_with_peaks = add_peaks_to_libpressio_configuration(
                        libpressio_config, peaks
                    )
                    compressor = PressioCompressor.from_config(libpressio_config_with_peaks)
                    compressed_img = compressor.encode(img)
                    decompressed_img = numpy.zeros_like(img)
                    _ = compressor.decode(compressed_img, decompressed_img)
                    img = decompressed_img

                # Photon energy
                try:
                    photon_energy = Detector("EBeam").get(evt).ebeamPhotonEnergy()
                    if numpy.isinf(photon_energy):
                        raise ValueError
                except (AttributeError, ValueError):
                    photon_energy = (
                        1.23984197386209e-06
                        / ds.env().epicsStore().value("SIOC:SYS0:ML00:AO192")
                    ) * 1e9

                file_writer.write_event(
                    img=img,
                    peaks=peaks,
                    timestamp_seconds=timestamp_seconds,
                    timestamp_nanoseconds=timestamp_nanoseconds,
                    timestamp_fiducials=timestamp_fiducials,
                    photon_energy=photon_energy,
                    clen=clen,
                )
                num_hits += 1

            # update powders
            if peaks.shape[0] >= self._task_parameters.min_peaks:
                powder_hits = numpy.maximum(powder_hits, img.reshape(-1, img.shape[-1]))
            else:
                powder_misses = numpy.maximum(powder_misses, img.reshape(-1, img.shape[-1]))


        summary_metrics = {
                "total_events": num_events,
                "total_hits": num_hits,
                "hit_rate": num_hits / num_events if num_events > 0 else 0,
                "empty_images": num_empty_images,
            }
        metrics_writer.write_summary(summary_metrics)
        metrics_writer.close()
        
        if num_empty_images != 0 and ENABLE_ELOG:
            msg: Message = Message(
                contents=f"Rank {ds.rank} encountered {num_empty_images} empty images."
            )
            self._report_to_executor(msg)

        file_writer.write_non_event_data(
            powder_hits=powder_hits,
            powder_misses=powder_misses,
            mask=mask,
        )

        file_writer.optimize_and_close_file(num_hits=num_hits, max_peaks=self._task_parameters.max_peaks)

        COMM_WORLD.Barrier()

        num_hits_per_rank: List[int] = cast(List[int], COMM_WORLD.gather(num_hits, root=0))
        num_hits_total: int = cast(int, COMM_WORLD.reduce(num_hits, SUM))
        num_events_total: int = cast(int, COMM_WORLD.reduce(num_events, SUM))

        if ds.rank == 0:
            master_fname: Path = write_master_file(
                mpi_size=ds.size,
                outdir=self._task_parameters.outdir,
                exp=self._task_parameters.lute_config.experiment,
                run=int(self._task_parameters.lute_config.run),
                tag=tag,
                n_hits_per_rank=num_hits_per_rank,
                n_hits_total=num_hits_total,
            )

            with open(Path(self._task_parameters.outdir) / f"peakfinding{tag}.summary", "w") as f:
                print(f"Number of events processed: {num_events_total}", file=f)
                print(f"Number of hits found: {num_hits_total}", file=f)
                print(f"Fractional hit rate: {(num_hits_total/num_events_total):.2f}", file=f)
                print(f"No. hits per rank: {num_hits_per_rank}", file=f)

            with h5py.File(master_fname, "r") as f:
                final_powder_hits: NDArray[numpy.float64] = f["entry_1/data_1/powderHits"][:]
                final_powder_misses: NDArray[numpy.float64] = f["entry_1/data_1/powderMisses"][:]

            if ENABLE_ELOG:
                powder_plots: pn.Tabs = self._create_powder_plots(det, final_powder_hits, final_powder_misses)
                text_summary: Dict[str, str] = {
                    "Number of events processed": str(num_events_total),
                    "Number of hits found": str(num_hits_total),
                    "Fractional hit rate": f"{num_hits_total/num_events_total:.2f}",
                }
                self._result.summary = (
                    text_summary,
                    ElogSummaryPlots(f"r{self._task_parameters.lute_config.run}/powders", powder_plots),
                )

            with open(Path(self._task_parameters.out_file), "w") as f:
                print(f"{master_fname}", file=f)

    def _post_run(self) -> None:
        super()._post_run()
        self._result.task_status = TaskStatus.COMPLETED

    def _assemble_image(self, det: Detector, img: NDArray[numpy.float64]) -> NDArray[numpy.float64]:
        geom: GeometryAccess = det.geometry(self._task_parameters.lute_config.run)
        tmp: Tuple[NDArray[numpy.uint64], ...] = geom.get_pixel_coord_indexes()
        pixel_map: NDArray[numpy.uint64] = numpy.zeros(tmp[0].shape[1:] + (2,), dtype=numpy.uint64)
        pixel_map[..., 0] = tmp[0][0]
        pixel_map[..., 1] = tmp[1][0]
        unflattened_img: NDArray[numpy.float64] = img.reshape(pixel_map.shape[:-1])
        idx_max_y: int = int(numpy.max(pixel_map[..., 0]) + 1)
        idx_max_x: int = int(numpy.max(pixel_map[..., 1]) + 1)
        assembled_img: NDArray[numpy.float64] = numpy.zeros((idx_max_y, idx_max_x))
        assembled_img[pixel_map[..., 0], pixel_map[..., 1]] = unflattened_img
        return assembled_img

    def _create_powder_plots(
        self,
        det: Detector,
        powder_hits: NDArray[numpy.float64],
        powder_misses: NDArray[numpy.float64],
    ) -> Any:
        self._task_parameters = cast(DrFindPeaksPyAlgosParameters, self._task_parameters)

        from mpi4py import MPI
        if MPI.COMM_WORLD.Get_rank() != 0:
            return None

        import holoviews as hv  # type: ignore
        import panel as pn      # type: ignore
        hv.extension("bokeh")
        pn.extension()

        assembled_powder_hits: NDArray[numpy.float64] = self._assemble_image(det, powder_hits)
        assembled_powder_misses: NDArray[numpy.float64] = self._assemble_image(det, powder_misses)

        grid_hits: pn.GridSpec = pn.GridSpec(
            sizing_mode="stretch_both",
            max_width=700,
            name=f"{self._task_parameters.det_name} - Hits",
        )
        dim: hv.Dimension = hv.Dimension(
            ("image", "Hits"),
            range=(numpy.nanpercentile(assembled_powder_hits, 1), numpy.nanpercentile(assembled_powder_hits, 99)),
        )
        grid_hits[0, 0] = pn.Row(
            hv.Image(assembled_powder_hits, vdims=[dim], name=dim.label).options(colorbar=True, cmap="rainbow")
        )

        grid_misses: pn.GridSpec = pn.GridSpec(
            sizing_mode="stretch_both",
            max_width=700,
            name=f"{self._task_parameters.det_name} - Misses",
        )
        dim = hv.Dimension(
            ("image", "Misses"),
            range=(numpy.nanpercentile(assembled_powder_misses, 1), numpy.nanpercentile(assembled_powder_misses, 99)),
        )
        grid_misses[0, 0] = pn.Row(
            hv.Image(assembled_powder_misses, vdims=[dim], name=dim.label).options(colorbar=True, cmap="rainbow")
        )

        tabs: pn.Tabs = pn.Tabs(grid_hits)
        tabs.append(grid_misses)
        return tabs
