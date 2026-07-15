"""ZMQ-streaming DR peak-finding task.

Spawns sfx_dr_zmq_push.py in a psana1 subprocess (ZMQ PUSH), receives
calibrated detector arrays in the current psana2 process (ZMQ PULL), and runs
DR + peak-finding (PyAlgos or Peakfinder8) — including event-code filtering,
MetricsWriter, CxiWriter and the master-file summary.

No intermediate XTC2 file is written.

Classes:
    SfxDrFindPeaksZmq
"""

__all__ = ["SfxDrFindPeaksZmq"]
__author__ = "Noemie Claret"

import logging
import os
import pickle
import subprocess
import time
import zlib
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union, cast

import h5py
import numpy as np
import numpy as numpy
from numpy.typing import NDArray

from psana.peakFinder.pypsalg import peaks_adaptive  # type: ignore

import zmq

from lute.DrAlgo import import_dr_algo
from lute.io.models.sfx_dr_find_peaks_zmq import SfxDrFindPeaksZmqParameters
from lute.tasks.dataclasses import TaskStatus
from lute.tasks.sfx_zmq_cxi_utils import (
    CxiWriter,
    MetricsWriter,
    Peakfinder8PeakList,
    Peakfinder8_v2PeakList,
    add_peaks_to_libpressio_configuration,
    generate_libpressio_configuration,
    psqrt_roundtrip_fracbits_gain_equalized,
    write_master_file,
)
from lute.tasks.task import Task

logger: logging.Logger = logging.getLogger(__name__)


class SfxDrFindPeaksZmq(Task):
    """Peak finder that streams calibrated XTC1 data from a psana1 subprocess via ZMQ.

    sfx_dr_zmq_push.py is spawned in a psana1 (conda1) environment.  The
    current process (psana2 env) connects to its ZMQ PUSH socket and receives:
      - an init message  (pixel index maps, optional psana mask)
      - per-event messages (calibrated image, timestamp, event codes,
                            photon energy, camera length)
      - an end message

    Peak finding is performed with either PyAlgos (default) or Peakfinder8 /
    Peakfinder8_v2, selected via the ``algorithm`` parameter.  Peakfinder8
    variants require a CrystFEL geometry file (``geometry_file``) for radius-
    map computation.
    """

    def __init__(self, *, params: SfxDrFindPeaksZmqParameters, row_ids=None) -> None:
        super().__init__(params=params, use_mpi=True, row_ids=row_ids)
        self._algo: Literal["PyAlgos", "Peakfinder8", "Peakfinder8_v2"] = (
            params.algorithm
        )

    # ------------------------------------------------------------------
    # Geometry helpers (used by Peakfinder8)
    # ------------------------------------------------------------------

    def get_radius_map(self) -> NDArray[np.float64]:
        from lute.tasks.util.geometry import (
            CrystfelDetectorGeometry,
            PixelMaps,
            crystfel_to_pixel_map,
            parse_crystfel_geometry_file,
        )

        par: SfxDrFindPeaksZmqParameters = cast(
            SfxDrFindPeaksZmqParameters, self._task_parameters
        )
        if par.geometry_file is None:
            raise RuntimeError(
                "geometry_file is required for Peakfinder8 / Peakfinder8_v2."
            )
        geom_desc: CrystfelDetectorGeometry = parse_crystfel_geometry_file(
            file_path=par.geometry_file
        )
        pixel_maps: PixelMaps = crystfel_to_pixel_map(geometry_desc=geom_desc)
        return pixel_maps["radius_map"]

    def _compute_num_radial_bins(
        self,
        indices: Tuple[slice, ...],
        radius_map: NDArray[np.float64],
    ) -> int:
        return int(np.ceil(radius_map[indices].max()) + 1)

    def compute_radial_statistics(
        self,
        radius_map: NDArray[np.float64],
        shape: Tuple[int, ...],
        num_bins: int = 100,
    ) -> Dict[str, Any]:
        import random

        radius_map_int: NDArray[np.int64] = np.rint(radius_map).astype(int).ravel()
        peak_index: List[int] = []
        radius: List[int] = []
        for idx in np.split(
            np.argsort(radius_map_int, kind="mergesort"),
            np.cumsum(np.bincount(radius_map_int)[:-1]),
        ):
            if len(idx) < num_bins:
                peak_index.extend(idx)
                radius.extend(radius_map_int[(idx,)])
            else:
                idx_sample: List[int] = random.sample(list(idx), num_bins)
                peak_index.extend(idx_sample)
                radius.extend(radius_map_int[(idx_sample,)])

        rstats_pixel_index: NDArray[np.int32] = np.array(peak_index, dtype=np.int32)
        rstats_radius: NDArray[np.int32] = np.array(radius, dtype=np.int32)
        return {
            "rstats_pixel_index": rstats_pixel_index,
            "rstats_radius": rstats_radius,
            "rstats_num_pix": rstats_pixel_index.size,
            "radial_map": radius_map.reshape(shape),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _start_proc(self, cmd: str, name: str) -> subprocess.Popen:
        proc: subprocess.Popen = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, text=True
        )
        logger.debug(f"{name} started (PID {proc.pid})")
        logger.debug(f"Command: {cmd}")
        return proc

    @staticmethod
    def _is_running(proc: subprocess.Popen) -> bool:
        return proc.poll() is None

    @staticmethod
    def _drain(proc: subprocess.Popen) -> None:
        if proc.stdout:
            for line in proc.stdout:
                print(line, end="", flush=True)
        if proc.stderr:
            for line in proc.stderr:
                print(line, end="", flush=True)

    # ------------------------------------------------------------------
    # Main run method
    # ------------------------------------------------------------------

    def _run(self) -> None:
        APPLY_PSQRT_KEV: bool = False
        FRAC_BITS: float = 0.75

        # ── 0. MPI setup ──────────────────────────────────────────────────────
        try:
            from mpi4py import MPI  # type: ignore

            comm = MPI.COMM_WORLD
            rank: int = comm.Get_rank()
            mpi_size: int = comm.Get_size()
        except ImportError:
            comm = None
            rank = 0
            mpi_size = 1

        par: SfxDrFindPeaksZmqParameters = cast(
            SfxDrFindPeaksZmqParameters, self._task_parameters
        )
        exp: str = par.lute_config.experiment
        run: int = int(par.lute_config.run)

        # ── 1. Rank 0: reserve ports and launch sender(s) ────────────────────
        lute_path: str = os.getenv("LUTE_PATH", "")
        assert lute_path, "LUTE_PATH environment variable must be set"

        # Auto-detect n_senders from XTC stream files when par.n_senders == 0.
        # Each rank does this independently (fast glob; no broadcast needed).
        _use_stream_splitting: bool = par.n_senders == 0
        if _use_stream_splitting:
            import glob as _glob
            hutch: str = exp[:3].lower()
            xtc_dir: str = f"/sdf/data/lcls/ds/{hutch}/{exp}/xtc"
            _stream_files: List[str] = sorted(
                _glob.glob(f"{xtc_dir}/{exp}-r{run:04d}-s*-c00.xtc")
            )
            n_senders: int = len(_stream_files)
            if n_senders == 0:
                logger.warning(
                    f"No XTC stream files found under {xtc_dir} for run {run}; "
                    "falling back to n_senders=1 (all streams)."
                )
                n_senders = 1
                _use_stream_splitting = False
            else:
                logger.info(
                    f"Auto-detected {n_senders} XTC stream(s) for {exp} r{run:04d}; "
                    "launching one sender per stream."
                )
        else:
            n_senders: int = par.n_senders
        ports: List[int] = []
        rank0_host: str = ""
        push_procs: List[subprocess.Popen] = []
        push_logs: List[Any] = []

        if rank == 0:
            import csv as _csv
            import socket as _socket

            rank0_host = _socket.gethostname()
            Path(par.outdir).mkdir(parents=True, exist_ok=True)

            # Reserve n_senders random ports
            ctx_tmp: zmq.Context = zmq.Context()
            for _ in range(n_senders):
                sock_tmp: zmq.Socket = ctx_tmp.socket(zmq.PULL)
                ports.append(sock_tmp.bind_to_random_port("tcp://*"))
                sock_tmp.close()
            ctx_tmp.term()

            pv_arg: str = (
                str(par.pv_camera_length) if par.pv_camera_length is not None else ""
            )

            # Exact worker count per sender: rank r belongs to group r % n_senders
            _base: int = mpi_size // n_senders
            _rem: int = mpi_size % n_senders
            workers_per_sender_list: List[int] = [
                _base + (1 if s < _rem else 0) for s in range(n_senders)
            ]

            # Build per-sender CLI args for data selection
            sender_event_args: List[str] = []
            if _use_stream_splitting:
                # Each sender reads one XTC stream file; no event-range splitting needed.
                for s in range(n_senders):
                    sender_event_args.append(f"--stream {s} ")
            elif par.eventfile:
                all_indices: List[int] = []
                with open(par.eventfile, newline="") as _f:
                    for _row in _csv.reader(_f):
                        all_indices += list(map(int, _row))
                chunk: int = (len(all_indices) + n_senders - 1) // n_senders
                for s in range(n_senders):
                    sl = all_indices[s * chunk : (s + 1) * chunk]
                    tmp_csv: Path = Path(par.outdir) / f"_sender_{s:02d}_events.csv"
                    with open(tmp_csv, "w") as _f:
                        _f.write(",".join(map(str, sl)))
                    sender_event_args.append(f"-f {tmp_csv} ")
            elif n_senders > 1:
                if par.n_events <= 0:
                    raise RuntimeError(
                        "Set n_events > 0 in the YAML when using n_senders > 1 and no eventfile."
                    )
                chunk = (par.n_events + n_senders - 1) // n_senders
                for s in range(n_senders):
                    start_ev: int = s * chunk
                    cnt: int = min(chunk, par.n_events - start_ev)
                    sender_event_args.append(f"--start-event {start_ev} -n {cnt} ")
            else:
                # n_senders == 1, no eventfile, no stream splitting
                if par.n_events:
                    sender_event_args = [f"-n {par.n_events} "]
                else:
                    sender_event_args = [""]

            for s in range(n_senders):
                push_cmd: str = (
                    "unset PYTHONPATH && "
                    "source /sdf/group/lcls/ds/ana/sw/conda1/manage/bin/psconda.sh && "
                    f"python3 {lute_path}/lute/tasks/util/sfx_dr_zmq_push.py "
                    f"-e {exp} -r {run} -p {ports[s]} "
                    f"-d {par.det_name} "
                    f"--event-receiver {par.event_receiver} "
                    f"--n-workers {workers_per_sender_list[s]} "
                )
                if pv_arg:
                    push_cmd += f"--pv-camera-length {pv_arg} "
                if par.psana_mask:
                    push_cmd += "--psana-mask "
                push_cmd += sender_event_args[s]

                push_log_suffix = f"_s{s:02d}" if n_senders > 1 else ""
                push_log_path: Path = (
                    Path(par.outdir) / f"sfx_dr_zmq_push_r{run:04d}{push_log_suffix}.log"
                )
                push_log = open(push_log_path, "w")
                push_logs.append(push_log)
                push_proc: subprocess.Popen = subprocess.Popen(
                    push_cmd, stdout=push_log, stderr=push_log, shell=True
                )
                push_procs.append(push_proc)
                logger.info(
                    f"Launched sender {s}/{n_senders} (PID {push_proc.pid}), "
                    f"port {ports[s]}, log: {push_log_path}"
                )

            time.sleep(2)

        # ── 2. Broadcast (hostname, ports) to all ranks, then connect ─────────
        if comm is not None:
            rank0_host, ports = comm.bcast((rank0_host, ports), root=0)

        my_port: int = ports[rank % n_senders]
        context: zmq.Context = zmq.Context()
        zmq_socket: zmq.Socket = context.socket(zmq.PULL)
        zmq_socket.setsockopt(zmq.RCVTIMEO, 300_000)  # 5-min timeout per receive
        zmq_socket.connect(f"tcp://{rank0_host}:{my_port}")

        def recv() -> Any:
            return pickle.loads(zlib.decompress(zmq_socket.recv()))

        # ── 4. Receive init message (pixel maps + optional mask) ──────────────
        try:
            init: Dict[str, Any] = recv()
        except zmq.Again:
            logger.error("Timed out waiting for init message from sfx_dr_zmq_push.py")
            self._result.task_status = TaskStatus.FAILED
            if rank == 0:
                for _p, _l in zip(push_procs, push_logs):
                    _p.kill()
                    _l.close()
            return

        if "init" not in init:
            logger.error(f"Expected init message, got keys: {list(init.keys())}")
            self._result.task_status = TaskStatus.FAILED
            if rank == 0:
                for _p, _l in zip(push_procs, push_logs):
                    _p.kill()
                    _l.close()
            return

        i_x: NDArray[np.int64] = init["i_x"]
        i_y: NDArray[np.int64] = init["i_y"]
        ipx: int = int(init["ipx"])
        ipy: int = int(init["ipy"])
        zmq_psana_mask: Optional[NDArray[np.uint16]] = init.get("psana_mask")

        # ── 5. DR algo setup (identical to DrFindPeaksPyAlgos) ───────────────
        tag: str = par.tag
        if tag and not tag.startswith("_"):
            tag = "_" + tag

        dr_algo: Any = None
        if par.dr_method:
            AlgoClass = import_dr_algo(par.dr_method)
            dr_algo = AlgoClass()
            dr_algo.set_params(
                n_components=par.n_components,
                tol=par.tol,
                gamma=par.gamma,
                max_iter=par.max_iter,
                size=par.size,
                dr_component=par.dr_component,
            )
            if par.compression is not None:
                dr_algo.set_params(
                    abs_error=par.compression.abs_error,
                    bin_size=par.compression.bin_size,
                    roi_window_size=par.compression.roi_window_size,
                )

        metrics_writer: MetricsWriter = MetricsWriter(
            outdir=par.outdir,
            rank=rank,
            exp=exp,
            run=run,
            n_events=par.n_events,
            tag=tag,
            save_debug_panels=True,
            n_debug_events=50,
            panels_per_event=2,
        )

        # ── 6. Event loop ─────────────────────────────────────────────────────
        alg: Any = None
        base_peakfinder_options: Dict[str, Any] = {}
        file_writer: Optional[CxiWriter] = None
        mask: Optional[NDArray[np.uint16]] = None
        powder_hits: Optional[NDArray[np.float64]] = None
        powder_misses: Optional[NDArray[np.float64]] = None
        libpressio_config: Any = None

        num_hits: int = 0
        num_events: int = 0
        num_empty_images: int = 0
        event_number: int = 0

        while True:
            try:
                obj: Dict[str, Any] = recv()
            except zmq.Again:
                logger.error("ZMQ receive timed out during event loop")
                if rank == 0 and push_procs and not self._is_running(push_procs[rank % n_senders]):
                    logger.error("Sender subprocess has exited; stopping.")
                break

            if "end" in obj:
                logger.info("Received end-of-stream signal")
                break

            if "empty" in obj:
                num_empty_images += 1
                continue

            img: NDArray[np.float32] = np.array(obj["img"], dtype=np.float32)
            timestamp_seconds: int = int(obj["timestamp_seconds"])
            timestamp_nanoseconds: int = int(obj["timestamp_nanoseconds"])
            timestamp_fiducials: int = int(obj["timestamp_fiducials"])
            event_codes: List[int] = obj["event_codes"]
            photon_energy: float = float(obj["photon_energy"])
            clen: float = float(obj["clen"])

            # Event-code filter (same logic as DrFindPeaksPyAlgos)
            if par.event_logic:
                if par.event_code not in event_codes:
                    continue

            original_img = np.array(img, copy=True)

            if APPLY_PSQRT_KEV:
                min_val = np.nanmin(img)
                offset = -min_val if min_val < 0 else 0.0
                img = psqrt_roundtrip_fracbits_gain_equalized(
                    img, frac_bits=FRAC_BITS, offset=offset
                )

            # ── Optional DR stage (per panel) ─────────────────────────────────
            if dr_algo is not None:
                if event_number == 0 and img.ndim == 3:
                    P, H, W = img.shape
                    k = min(int(max(1, round(0.2 * min(H, W)))), min(H, W) - 1)
                    par.n_components = k
                    dr_algo.set_params(n_components=k)

                P = img.shape[0]
                reconstructed_img: NDArray = numpy.zeros_like(img)
                for p in range(P):
                    panel = img[p, :, :]
                    try:
                        dr_algo.fit(panel)
                        metrics = dr_algo.compute_metrics()
                        metrics_writer.write_event_metrics(
                            event_id=event_number * P + p,
                            metrics=metrics,
                            timestamp=timestamp_seconds + timestamp_nanoseconds * 1e-9,
                        )
                        if par.dr_component == "S":
                            reconstructed_img[p] = dr_algo.sparse_
                        elif par.dr_component in ("L+S", "X_hat"):
                            reconstructed_img[p] = dr_algo.reconstruct()
                        else:
                            reconstructed_img[p] = panel
                    except Exception as exc:
                        logger.error(f"DR failed on panel {p}: {exc}")
                        reconstructed_img[p] = panel

                d = reconstructed_img - original_img
                print(
                    "max_abs_diff",
                    float(np.nanmax(np.abs(d))),
                    "nnz",
                    int(np.sum(d != 0)),
                    flush=True,
                )
                metrics_writer.maybe_write_debug_panels(
                    event_id=event_number,
                    timestamp=timestamp_seconds + timestamp_nanoseconds * 1e-9,
                    original_img=original_img,
                    reconstructed_img=reconstructed_img,
                )
                img = reconstructed_img

            event_number += 1

            # ── Lazy initialisation of alg + file_writer on first image ───────
            if alg is None:
                shape: Tuple[int, ...] = img.shape
                det_shape: Tuple[int, ...] = shape
                if self._algo == "Peakfinder8_v2":
                    det_shape = shape
                elif img.ndim == 3:
                    det_shape = (shape[0] * shape[1], shape[2])

                mask = numpy.ones(det_shape, dtype=numpy.uint16)

                if zmq_psana_mask is not None:
                    mask *= zmq_psana_mask.reshape(det_shape).astype(numpy.uint16)

                if par.mask_file is not None:
                    with h5py.File(par.mask_file, "r") as hdffh:
                        loaded_mask: NDArray[numpy.int64] = hdffh[
                            "entry_1/data_1/mask"
                        ][:]
                        mask *= loaded_mask.astype(numpy.uint16)

                if "Peakfinder8" in self._algo:
                    from lute.tasks.algorithms import _peakfinders_ext  # type: ignore

                    radius_map: NDArray[np.float64] = self.get_radius_map()

                    adc_thresh = par.amax_thr
                    hitfinder_min_snr = par.son_min
                    hitfinder_min_pix_count = par.npix_min
                    hitfinder_max_pix_count = par.npix_max
                    local_bg_radius = par.r0

                    if self._algo == "Peakfinder8":
                        alg = _peakfinders_ext.peakfinder_8

                        num_radial_bins: int = self._compute_num_radial_bins(
                            indices=tuple(slice(None, d) for d in radius_map.shape),
                            radius_map=radius_map,
                        )
                        radial_stats: Dict[str, Any] = self.compute_radial_statistics(
                            radius_map=radius_map,
                            shape=det_shape,
                            num_bins=num_radial_bins,
                        )

                        if img.ndim == 3:
                            asic_nx: int = shape[2]
                            asic_ny: int = shape[1]
                            nasics_x: int = 1
                            nasics_y: int = shape[0]
                        else:
                            asic_nx = shape[1]
                            asic_ny = shape[0]
                            nasics_x = 1
                            nasics_y = 1

                        img_slab: NDArray[np.float32] = (
                            img.reshape(shape[0] * shape[1], shape[2])
                            if img.ndim == 3
                            else img
                        )
                        base_peakfinder_options = {
                            "max_num_peaks": par.max_peaks,
                            "data": img_slab,
                            "mask": mask.astype(np.int8),
                            "pix_r": radial_stats["radial_map"],
                            "rstats_num_pix": radial_stats["rstats_num_pix"],
                            "rstats_pidx": radial_stats["rstats_pixel_index"],
                            "rstats_radius": radial_stats["rstats_radius"],
                            "fast": 1,
                            "asic_nx": asic_nx,
                            "asic_ny": asic_ny,
                            "nasics_x": nasics_x,
                            "nasics_y": nasics_y,
                            "adc_thresh": adc_thresh,
                            "hitfinder_min_snr": hitfinder_min_snr,
                            "hitfinder_min_pix_count": hitfinder_min_pix_count,
                            "hitfinder_max_pix_count": hitfinder_max_pix_count,
                            "hitfinder_local_bg_radius": int(local_bg_radius),
                        }
                    else:  # Peakfinder8_v2
                        alg = _peakfinders_ext.peakfinder_8_v2
                        base_peakfinder_options = {
                            "max_num_peaks": par.max_peaks,
                            "data": img,
                            "mask": mask.astype(np.int8),
                            "pix_r": radius_map,
                            "adc_thresh": adc_thresh,
                            "hitfinder_min_snr": hitfinder_min_snr,
                            "hitfinder_min_pix_count": hitfinder_min_pix_count,
                            "hitfinder_max_pix_count": hitfinder_max_pix_count,
                            "hitfinder_local_bg_radius": int(local_bg_radius),
                        }
                else:
                    alg = True  # PyAlgos: params passed directly to peaks_adaptive

                file_writer = CxiWriter(
                    outdir=par.outdir,
                    rank=rank,
                    exp=exp,
                    run=run,
                    n_events=par.n_events,
                    det_shape=det_shape,
                    raw_det_shape=shape,
                    i_x=i_x,
                    i_y=i_y,
                    ipx=ipx,
                    ipy=ipy,
                    min_peaks=par.min_peaks,
                    max_peaks=par.max_peaks,
                    tag=tag,
                    algo=self._algo,
                )

                # Only build a post-peak libpressio config when compression is
                # requested AND the DR algo isn't already a libpressio variant
                # (to avoid compressing twice).
                _dr_is_libpressio = par.dr_method in ("libpressio_sz3", "libpressio_qoz")
                if par.compression is not None and not _dr_is_libpressio:
                    libpressio_config = generate_libpressio_configuration(
                        compressor=par.compression.compressor,
                        roi_window_size=par.compression.roi_window_size,
                        bin_size=par.compression.bin_size,
                        abs_error=par.compression.abs_error,
                        libpressio_mask=mask,
                    )

                powder_hits = numpy.zeros(det_shape, dtype=numpy.float64)
                powder_misses = numpy.zeros(det_shape, dtype=numpy.float64)

            # ── Peak finding ──────────────────────────────────────────────────
            peaks: Any
            num_peaks: int

            if self._algo == "Peakfinder8":
                img_slab = (
                    img.reshape(img.shape[0] * img.shape[1], img.shape[2])
                    if img.ndim == 3
                    else img
                )
                base_peakfinder_options["data"] = img_slab
                raw_pf8 = alg(**base_peakfinder_options)
                peaks = Peakfinder8PeakList(
                    num_peaks=len(raw_pf8[0]),
                    fs=raw_pf8[0],
                    ss=raw_pf8[1],
                    intensity=raw_pf8[2],
                    num_pixels=raw_pf8[4],
                    max_pixel_intensity=raw_pf8[5],
                    snr=raw_pf8[6],
                )
                num_peaks = peaks["num_peaks"]
            elif self._algo == "Peakfinder8_v2":
                base_peakfinder_options["data"] = img
                raw_pf8_v2 = alg(**base_peakfinder_options)
                peaks = Peakfinder8_v2PeakList(
                    num_peaks=len(raw_pf8_v2[0]),
                    fs=raw_pf8_v2[0],
                    ss=raw_pf8_v2[1],
                    intensity=raw_pf8_v2[2],
                    num_pixels=raw_pf8_v2[4],
                    max_pixel_intensity=raw_pf8_v2[5],
                    snr=raw_pf8_v2[6],
                    panel_number=raw_pf8_v2[8],
                )
                num_peaks = peaks["num_peaks"]
            else:  # PyAlgos
                _mask_nd = mask.reshape(img.shape) if img.ndim == 3 else mask
                _peak_list = peaks_adaptive(
                    img,
                    _mask_nd,
                    rank=par.peak_rank,
                    r0=par.r0,
                    dr=par.dr,
                    nsigm=par.nsigm,
                    npix_min=par.npix_min,
                    npix_max=par.npix_max,
                    amax_thr=par.amax_thr,
                    atot_thr=par.atot_thr,
                    son_min=par.son_min,
                )
                peaks = (
                    numpy.array(
                        [p.parameters() for p in _peak_list], dtype=numpy.float64
                    )
                    if _peak_list
                    else numpy.empty((0, 17), dtype=numpy.float64)
                )
                num_peaks = peaks.shape[0]

            num_events += 1

            if par.min_peaks <= num_peaks <= par.max_peaks:
                if par.compression is not None and libpressio_config is not None:
                    from libpressio import PressioCompressor  # type: ignore

                    cfg_with_peaks = add_peaks_to_libpressio_configuration(
                        libpressio_config, peaks, algo=self._algo
                    )
                    compressor = PressioCompressor.from_config(cfg_with_peaks)
                    compressed = compressor.encode(img)
                    img = compressor.decode(compressed, numpy.zeros_like(img))

                file_writer.write_event(
                    img=img,
                    peaks=peaks,
                    timestamp_seconds=timestamp_seconds,
                    timestamp_nanoseconds=timestamp_nanoseconds,
                    timestamp_fiducials=timestamp_fiducials,
                    photon_energy=photon_energy,
                    clen=clen,
                    algo=self._algo,
                )
                num_hits += 1

            if num_peaks >= par.min_peaks:
                powder_hits = numpy.maximum(
                    powder_hits, img.reshape(-1, img.shape[-1])
                )
            else:
                powder_misses = numpy.maximum(
                    powder_misses, img.reshape(-1, img.shape[-1])
                )

        # ── 7. Teardown ───────────────────────────────────────────────────────
        zmq_socket.close()
        context.term()

        if rank == 0:
            for _proc, _log in zip(push_procs, push_logs):
                _proc.wait(timeout=60)
                _log.close()

        metrics_writer.write_summary(
            {
                "total_events": num_events,
                "total_hits": num_hits,
                "hit_rate": num_hits / num_events if num_events > 0 else 0.0,
                "empty_images": num_empty_images,
            }
        )
        metrics_writer.close()

        if file_writer is None or alg is None or powder_hits is None:
            logger.warning(f"Rank {rank}: no events processed — no CXI output written.")
        else:
            file_writer.write_non_event_data(
                powder_hits=powder_hits,
                powder_misses=powder_misses,
                mask=mask,
            )
            file_writer.optimize_and_close_file(
                num_hits=num_hits, max_peaks=par.max_peaks, algo=self._algo
            )

        # ── 8. Rank 0 gathers results and writes master file ──────────────────
        if comm is not None:
            all_hits: List[int] = comm.gather(num_hits, root=0)
            all_events: List[int] = comm.gather(num_events, root=0)
        else:
            all_hits = [num_hits]
            all_events = [num_events]

        if rank == 0:
            total_hits: int = sum(all_hits)
            total_events: int = sum(all_events)

            master_fname: Path = write_master_file(
                mpi_size=mpi_size,
                outdir=par.outdir,
                exp=exp,
                run=run,
                tag=tag,
                n_hits_per_rank=all_hits,
                n_hits_total=total_hits,
            )

            summary_path: Path = Path(par.outdir) / f"peakfinding{tag}.summary"
            with open(summary_path, "w") as fh:
                print(f"Number of events processed: {total_events}", file=fh)
                print(f"Number of hits found: {total_hits}", file=fh)
                if total_events:
                    print(f"Fractional hit rate: {total_hits / total_events:.4f}", file=fh)

            with open(Path(par.out_file), "w") as fh:
                print(str(master_fname), file=fh)

            logger.info(
                f"Done — {total_hits}/{total_events} hits across {mpi_size} rank(s). Master CXI: {master_fname}"
            )

    def _post_run(self) -> None:
        super()._post_run()
        self._result.task_status = TaskStatus.COMPLETED
