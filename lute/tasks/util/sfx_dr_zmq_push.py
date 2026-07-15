"""SFX DR peak-finding ZMQ data sender. Runs in psana1 (conda1) environment.

Reads XTC1 detector data using psana1 and sends all fields required by
SfxDrFindPeaksZmq over ZMQ PUSH, mirroring the per-event access pattern of
DrFindPeaksPyAlgos:
  - init message: pixel index maps, centre indices, optional psana mask
  - per-event: calibrated image, timestamp (sec/ns/fid), EVR event codes,
    photon energy, camera length
  - end message
"""

import argparse
import csv
import pickle
import zlib
from typing import Any, Dict, List, Optional

import numpy as np
import psana  # type: ignore
import zmq


class ZmqSender:
    def __init__(self, socket_str: str) -> None:
        context: zmq.Context = zmq.Context()
        self.zmq_socket: zmq.Socket = context.socket(zmq.PUSH)
        self.zmq_socket.bind(socket_str)

    def send_zipped_pickle(self, obj: Any, protocol: int = -1) -> None:
        try:
            p: bytes = pickle.dumps(obj, protocol)
            z: bytes = zlib.compress(p)
            self.zmq_socket.send(z)
        except Exception as e:
            print(f"[SFX DR Sender]: send error: {e}", flush=True)

    def close(self) -> None:
        self.zmq_socket.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="SFX DR XTC1 ZMQ Sender")
    parser.add_argument("-e", "--exp", type=str, required=True)
    parser.add_argument("-r", "--run", type=str, required=True)
    parser.add_argument("-p", "--port", type=int, default=5557)
    parser.add_argument("-d", "--det-name", type=str, required=True)
    parser.add_argument("--event-receiver", type=str, default="evr0")
    parser.add_argument(
        "--pv-camera-length",
        type=str,
        default="",
        help="Camera length as a float (e.g. '0.1') or an EPICS PV name.",
    )
    parser.add_argument(
        "--psana-mask",
        action="store_true",
        default=False,
        help="Include the psana pixel-status mask in the init message.",
    )
    parser.add_argument(
        "-n", "--nevents", type=int, default=0,
        help="Number of events to send (0 = all from --start-event).",
    )
    parser.add_argument(
        "-f", "--eventfile", type=str, default="",
        help="CSV file listing event indices; supersedes --nevents and --start-event.",
    )
    parser.add_argument(
        "--n-workers", type=int, default=1,
        help="Number of ZMQ PULL receivers (MPI ranks). Init and end messages are sent N times so each rank receives one via PUSH round-robin.",
    )
    parser.add_argument(
        "--start-event", type=int, default=0,
        help="Index of first event to send. Ignored when --eventfile is set.",
    )
    parser.add_argument(
        "--stream", type=int, default=-1,
        help="XTC stream index to read (0, 1, 2, ...). -1 = all streams (default).",
    )
    args = parser.parse_args()

    zmq_send: ZmqSender = ZmqSender(f"tcp://*:{args.port}")

    run_num: int = int(args.run)
    if args.stream >= 0:
        ds: psana.DataSource = psana.DataSource(
            f"exp={args.exp}:run={args.run}:stream={args.stream}:idx"
        )
    else:
        ds: psana.DataSource = psana.DataSource(f"exp={args.exp}:run={args.run}:idx")
    run_obj = next(ds.runs())
    timestamps: tuple = run_obj.times()

    det = psana.Detector(args.det_name)
    det.do_reshape_2d_to_3d(flag=True)
    evr = psana.Detector(args.event_receiver)
    ebeam_det = psana.Detector("EBeam")

    # Pixel index maps and beam-centre — computed once per run
    i_x = det.indexes_x(run_num).astype(np.int64)
    i_y = det.indexes_y(run_num).astype(np.int64)
    ipx, ipy = det.point_indexes(run_num, pxy_um=(0, 0))

    psana_mask: Optional[np.ndarray] = None
    if args.psana_mask:
        psana_mask = det.mask(
            run_num,
            calib=False,
            status=True,
            edges=False,
            centra=False,
            unbond=False,
            unbondnbrs=False,
        )

    init_msg = {
        "init": True,
        "i_x": i_x,
        "i_y": i_y,
        "ipx": ipx,
        "ipy": ipy,
        "psana_mask": psana_mask,
    }
    for _ in range(args.n_workers):
        zmq_send.send_zipped_pickle(init_msg)
    print(f"[SFX DR Sender]: init sent x{args.n_workers} (pixel maps, mask)", flush=True)

    # Event list
    event_num_list: List[int]
    start: int = args.start_event
    if args.eventfile:
        event_num_list = []
        try:
            with open(args.eventfile, newline="") as csvfile:
                for row in csv.reader(csvfile):
                    event_num_list += list(map(int, row))
        except FileNotFoundError:
            print(f"[SFX DR Sender]: eventfile not found: {args.eventfile}", flush=True)
            event_num_list = list(range(start, len(timestamps)))
    elif args.nevents:
        event_num_list = list(range(start, min(start + args.nevents, len(timestamps))))
    else:
        event_num_list = list(range(start, len(timestamps)))

    # Parse camera-length argument once
    clen_fixed: Optional[float] = None
    clen_pv: Optional[str] = None
    if args.pv_camera_length:
        try:
            clen_fixed = float(args.pv_camera_length)
        except ValueError:
            clen_pv = args.pv_camera_length

    for event_num in event_num_list:
        ts = timestamps[int(event_num)]
        evt = run_obj.event(ts)

        img = det.calib(evt)
        if img is None:
            zmq_send.send_zipped_pickle({"empty": True})
            continue

        evt_id = evt.get(psana.EventId)
        ts_seconds: int
        ts_nanoseconds: int
        ts_seconds, ts_nanoseconds = evt_id.time()
        ts_fiducials: int = evt_id.fiducials()

        event_codes: List[int] = list(evr.eventCodes(evt) or [])

        clen: float = 0.0
        if clen_fixed is not None:
            clen = clen_fixed
        elif clen_pv is not None:
            try:
                clen = float(ds.env().epicsStore().value(clen_pv))
            except Exception:
                pass

        photon_energy: float = 0.0
        try:
            ebeam = ebeam_det.get(evt)
            if ebeam is not None:
                photon_energy = float(ebeam.ebeamPhotonEnergy())
                if not np.isfinite(photon_energy):
                    raise ValueError("non-finite")
        except Exception:
            try:
                wl = float(ds.env().epicsStore().value("SIOC:SYS0:ML00:AO192"))
                photon_energy = (1.23984197386209e-06 / wl) * 1e9
            except Exception:
                photon_energy = 0.0

        zmq_send.send_zipped_pickle(
            {
                "img": img,
                "timestamp_seconds": ts_seconds,
                "timestamp_nanoseconds": ts_nanoseconds,
                "timestamp_fiducials": ts_fiducials,
                "event_codes": event_codes,
                "photon_energy": photon_energy,
                "clen": clen,
            }
        )
        print(
            f"[SFX DR Sender]: event {event_num}  ts={ts_seconds}.{ts_nanoseconds:09d}",
            flush=True,
        )

    for _ in range(args.n_workers):
        zmq_send.send_zipped_pickle({"end": True})
    print("[SFX DR Sender]: complete.", flush=True)
    zmq_send.close()


if __name__ == "__main__":
    main()
