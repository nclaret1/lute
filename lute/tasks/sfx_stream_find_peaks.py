"""Streaming XTC1 → peak-finding task.

Spawns xtc_push.py in a psana1 subprocess (ZMQ PUSH), receives calibrated
detector arrays in the current psana2 process (ZMQ PULL), runs PyAlgos peak
finding, and writes CXI output.  No intermediate XTC2 file is written.

Classes:
    StreamFindPeaksPyAlgos: combines the XTC1 ZMQ sender with inline
        PyAlgos peak finding, mirroring DrFindPeaksPyAlgos.
"""

__all__ = ["StreamFindPeaksPyAlgos"]
__author__ = "Noemie Claret"

import json
import logging
import os
import pickle
import subprocess
import time
import zlib
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union, cast

import h5py
import numpy as np
import numpy.typing as npt
import zmq
from psana.peakFinder.pypsalg import peaks_adaptive  # type: ignore

from lute.DrAlgo import import_dr_algo
from lute.io.models.sfx_stream_find_peaks import StreamFindPeaksPyAlgosParameters
from lute.tasks.dataclasses import TaskStatus
from lute.tasks.sfx_dr_find_peaks import (
    MetricsWriter,
    psqrt_roundtrip_adu,
    psqrt_roundtrip_fracbits_gain_equalized,
)
from lute.tasks.sfx_find_peaks import (
    CxiWriter,
    add_peaks_to_libpressio_configuration,
    generate_libpressio_configuration,
    write_master_file,
)
from lute.tasks.task import Task

logger: logging.Logger = logging.getLogger(__name__)


def _decode_lcls_timestamp(raw_ts: int) -> Tuple[int, int]:
    """Decode a psana1 EventTime 64-bit integer into (seconds, nanoseconds).

    LCLS-I format: upper 32 bits = Unix seconds, lower 32 bits = nanoseconds
    within that second.
    """
    return int(raw_ts >> 32), int(raw_ts & 0xFFFFFFFF)


class StreamFindPeaksPyAlgos(Task):
    """Peak finder that streams calibrated data from XTC1 via ZMQ.

    xtc_push.py is spawned as a psana1 subprocess.  The current process
    (psana2 env) connects to its ZMQ PUSH socket, receives calibrated arrays,
    optionally applies a DR algorithm, runs PyAlgos, and writes CXI output.
    Geometry and pixel-status mask are taken from the calib constants sent at
    stream start — no psana1 Detector calls are made in the current process.
    """

    def __init__(self, *, params: StreamFindPeaksPyAlgosParameters) -> None:
        super().__init__(params=params)

    def _run(self) -> None:
        par: StreamFindPeaksPyAlgosParameters = cast(
            StreamFindPeaksPyAlgosParameters, self._task_parameters
        )
        exp: str = par.lute_config.experiment
        run: Union[int, str] = par.lute_config.run

        # ── 1. Reserve a free TCP port ──────────────────────────────────────
        ctx_tmp: zmq.Context = zmq.Context()
        sock_tmp: zmq.Socket = ctx_tmp.socket(zmq.PULL)
        port: int = sock_tmp.bind_to_random_port("tcp://*")
        sock_tmp.close()
        ctx_tmp.term()

        # ── 2. Build and launch xtc_push.py in psana1 env ───────────────────
        data_spec: Dict[str, Any] = {
            detname: [spec.dict() for spec in specs]
            for detname, specs in par.xtc1_access_pattern.items()
        }
        lute_path: str = os.getenv("LUTE_PATH", "")
        assert lute_path, "LUTE_PATH environment variable must be set"

        push_cmd: str = (
            "source /sdf/group/lcls/ds/ana/sw/conda1/manage/bin/psconda.sh && "
            f"python3 {lute_path}/lute/tasks/util/xtc_push.py "
            f"-a '{json.dumps(data_spec)}' -e {exp} -p {port} -r {run}"
        )
        if par.eventfile:
            push_cmd += f" -f {par.eventfile}"
        elif par.nevents is not None:
            push_cmd += f" -n {par.nevents}"

        Path(par.outdir).mkdir(parents=True, exist_ok=True)
        push_log_path: Path = Path(par.outdir) / f"xtc_push_r{run}.log"
        push_log = open(push_log_path, "w")
        push_proc: subprocess.Popen = subprocess.Popen(
            push_cmd, stdout=push_log, stderr=push_log, shell=True
        )
        logger.info(f"Launched xtc_push.py (PID {push_proc.pid}), log: {push_log_path}")
        time.sleep(2)

        # ── 3. Connect PULL socket ───────────────────────────────────────────
        context: zmq.Context = zmq.Context()
        zmq_socket: zmq.Socket = context.socket(zmq.PULL)
        zmq_socket.setsockopt(zmq.RCVTIMEO, 300_000)  # 5-min receive timeout
        zmq_socket.connect(f"tcp://127.0.0.1:{port}")

        def recv() -> Any:
            return pickle.loads(zlib.decompress(zmq_socket.recv()))

        # ── 4. Receive DATA_TYPE_INFO ────────────────────────────────────────
        try:
            obj = recv()
        except zmq.Again:
            logger.error("Timed out waiting for DATA_TYPE_INFO from xtc_push.py")
            self._result.task_status = TaskStatus.FAILED
            push_proc.kill()
            push_log.close()
            return

        if "DATA_TYPE_INFO" not in obj:
            logger.error(f"Expected DATA_TYPE_INFO, got: {list(obj.keys())}")
            self._result.task_status = TaskStatus.FAILED
            push_proc.kill()
            push_log.close()
            return

        # ── 5. Receive start message (calib constants + geometry) ────────────
        obj = recv()
        if "start" not in obj:
            logger.error(f"Expected start message, got: {list(obj.keys())}")
            self._result.task_status = TaskStatus.FAILED
            push_proc.kill()
            push_log.close()
            return

        i_x: Optional[npt.NDArray[np.int64]] = None
        i_y: Optional[npt.NDArray[np.int64]] = None
        ipx: int = 0
        ipy: int = 0
        zmq_mask: Optional[npt.NDArray[np.uint16]] = None

        if "calib_const" in obj:
            for detname, consts in obj["calib_const"].items():
                if detname == "timestamp":
                    continue
                pim = consts.get("pixel_index_map")
                if pim is not None:
                    # pixel_index_map: (n_panels, h, w, 2), [...,0]=x, [...,1]=y
                    i_x = pim[..., 0].astype(np.int64)
                    i_y = pim[..., 1].astype(np.int64)
                    ipx = int(np.max(i_x) // 2)
                    ipy = int(np.max(i_y) // 2)
                raw_mask = consts.get("mask")
                if raw_mask is not None and par.use_zmq_mask:
                    zmq_mask = raw_mask.astype(np.uint16)
                break  # single detector

        # ── 6. Setup DR algo ─────────────────────────────────────────────────
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

        tag: str = par.tag
        if tag and not tag.startswith("_"):
            tag = "_" + tag

        # ── 7. Event loop ────────────────────────────────────────────────────
        alg: Optional[bool] = None  # lazy-init done flag
        file_writer: Optional[CxiWriter] = None
        mask: Optional[npt.NDArray[np.uint16]] = None
        powder_hits: Optional[npt.NDArray[np.float64]] = None
        powder_misses: Optional[npt.NDArray[np.float64]] = None
        libpressio_config: Any = None
        num_hits: int = 0
        num_events: int = 0
        num_empty: int = 0
        event_number: int = 0

        metrics_writer: MetricsWriter = MetricsWriter(
            outdir=par.outdir,
            rank=0,
            exp=exp,
            run=int(run),
            n_events=par.nevents or 0,
            tag=tag,
            save_debug_panels=True,
            n_debug_events=50,
            panels_per_event=2,
        )

        while True:
            try:
                obj = recv()
            except zmq.Again:
                logger.error("ZMQ receive timed out during event loop")
                break

            if "end" in obj:
                logger.info("Received end-of-stream signal")
                break

            # Extract calibrated image and timestamp
            raw_ts: int = int(obj.get("timestamp", 0))
            ts_sec, ts_nsec = _decode_lcls_timestamp(raw_ts)
            ts_fid: int = raw_ts & 0x1FFFF  # lower 17 bits = fiducials

            img_data: Optional[npt.NDArray[np.float32]] = None
            for detname, det_data in obj.items():
                if detname == "timestamp":
                    continue
                arr = det_data.get("calib") if isinstance(det_data, dict) else None
                if arr is not None:
                    img_data = np.array(arr, dtype=np.float32)
                break

            if img_data is None:
                num_empty += 1
                continue

            img: npt.NDArray[np.float32] = img_data
            original_img = np.array(img, copy=True)

            # ── Lazy initialisation on first valid event ─────────────────────
            if alg is None:
                det_shape: Tuple[int, ...] = img.shape
                if img.ndim == 3:
                    det_shape = (img.shape[0] * img.shape[1], img.shape[2])

                mask = np.ones(det_shape, dtype=np.uint16)
                if zmq_mask is not None:
                    mask = zmq_mask.reshape(det_shape)
                if par.mask_file is not None:
                    with h5py.File(par.mask_file, "r") as fh:
                        mask *= fh["entry_1/data_1/mask"][:].astype(np.uint16)

                _zero_geom = np.zeros(img.shape, dtype=np.int64)
                file_writer = CxiWriter(
                    outdir=par.outdir,
                    rank=0,
                    exp=exp,
                    run=int(run),
                    n_events=par.nevents or 0,
                    det_shape=det_shape,
                    raw_det_shape=img.shape,
                    i_x=i_x if i_x is not None else _zero_geom,
                    i_y=i_y if i_y is not None else _zero_geom,
                    ipx=ipx,
                    ipy=ipy,
                    min_peaks=par.min_peaks,
                    max_peaks=par.max_peaks,
                    tag=tag,
                    algo="PyAlgos",
                )

                alg = True  # peak selection params passed directly to peaks_adaptive

                if par.compression is not None:
                    libpressio_config = generate_libpressio_configuration(
                        compressor=par.compression.compressor,
                        roi_window_size=par.compression.roi_window_size,
                        bin_size=par.compression.bin_size,
                        abs_error=par.compression.abs_error,
                        libpressio_mask=mask,
                    )

                powder_hits = np.zeros(det_shape, dtype=np.float64)
                powder_misses = np.zeros(det_shape, dtype=np.float64)

            # ── Optional DR stage (per panel) ────────────────────────────────
            if dr_algo is not None:
                if event_number == 0 and img.ndim == 3:
                    P, H, W = img.shape
                    k = min(int(max(1, round(0.2 * min(H, W)))), min(H, W) - 1)
                    par.n_components = k
                    dr_algo.set_params(n_components=k)

                P = img.shape[0]
                reconstructed_img = np.zeros_like(img)
                for p in range(P):
                    panel = img[p]
                    try:
                        dr_algo.fit(panel)
                        metrics_writer.write_event_metrics(
                            event_id=event_number * P + p,
                            metrics=dr_algo.compute_metrics(),
                            timestamp=ts_sec + ts_nsec * 1e-9,
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
                img = reconstructed_img
                metrics_writer.maybe_write_debug_panels(
                    event_id=event_number,
                    timestamp=ts_sec + ts_nsec * 1e-9,
                    original_img=original_img,
                    reconstructed_img=img,
                )

            event_number += 1

            # ── Peak finding ─────────────────────────────────────────────────
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
            peaks: npt.NDArray[np.float64] = (
                np.array([p.parameters() for p in _peak_list], dtype=np.float64)
                if _peak_list
                else np.empty((0, 17), dtype=np.float64)
            )
            num_events += 1

            if par.min_peaks <= peaks.shape[0] <= par.max_peaks:
                if par.compression is not None and libpressio_config is not None:
                    from libpressio import PressioCompressor  # type: ignore

                    cfg = add_peaks_to_libpressio_configuration(libpressio_config, peaks)
                    comp = PressioCompressor.from_config(cfg)
                    dec = np.zeros_like(img)
                    comp.decode(comp.encode(img), dec)
                    img = dec

                clen: float = (
                    par.camera_length_mm
                    if isinstance(par.camera_length_mm, float)
                    else 0.0
                )
                if isinstance(par.camera_length_mm, str) and num_hits == 1:
                    logger.warning(
                        f"camera_length_mm is a PV name ({par.camera_length_mm!r}) — "
                        "no epics access in psana2, writing 0.0 to CXI."
                    )
                file_writer.write_event(
                    img=img,
                    peaks=peaks,
                    timestamp_seconds=ts_sec,
                    timestamp_nanoseconds=ts_nsec,
                    timestamp_fiducials=ts_fid,
                    photon_energy=par.photon_energy_eV,
                    clen=clen,
                    algo="PyAlgos",
                )
                num_hits += 1

            flat_img = img.reshape(-1, img.shape[-1])
            if peaks.shape[0] >= par.min_peaks:
                powder_hits = np.maximum(powder_hits, flat_img)
            else:
                powder_misses = np.maximum(powder_misses, flat_img)

            if par.nevents and num_events >= par.nevents:
                break

        # ── 8. Teardown ──────────────────────────────────────────────────────
        zmq_socket.close()
        context.term()
        push_proc.wait(timeout=60)
        push_log.close()

        metrics_writer.write_summary(
            {
                "total_events": num_events,
                "total_hits": num_hits,
                "hit_rate": num_hits / num_events if num_events > 0 else 0.0,
                "empty_images": num_empty,
            }
        )
        metrics_writer.close()

        if file_writer is None or alg is None or powder_hits is None:
            logger.warning("No events were processed — no CXI output written.")
            self._result.task_status = TaskStatus.COMPLETED
            return

        file_writer.write_non_event_data(
            powder_hits=powder_hits,
            powder_misses=powder_misses,
            mask=mask,
        )
        file_writer.optimize_and_close_file(num_hits=num_hits, max_peaks=par.max_peaks, algo="PyAlgos")

        master_fname: Path = write_master_file(
            mpi_size=1,
            outdir=par.outdir,
            exp=exp,
            run=int(run),
            tag=tag,
            n_hits_per_rank=[num_hits],
            n_hits_total=num_hits,
        )
        with open(Path(par.out_file), "w") as fh:
            print(str(master_fname), file=fh)

        summary_path: Path = Path(par.outdir) / f"peakfinding{tag}.summary"
        with open(summary_path, "w") as fh:
            print(f"Number of events processed: {num_events}", file=fh)
            print(f"Number of hits found: {num_hits}", file=fh)
            if num_events:
                print(f"Fractional hit rate: {num_hits / num_events:.4f}", file=fh)

        logger.info(
            f"Done — {num_hits}/{num_events} hits. Master CXI: {master_fname}"
        )

    def _post_run(self) -> None:
        super()._post_run()
        self._result.task_status = TaskStatus.COMPLETED
