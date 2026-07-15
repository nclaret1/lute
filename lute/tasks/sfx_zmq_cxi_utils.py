"""Psana2-safe CXI writing utilities for ZMQ-streaming SFX tasks.

Contains copies of CxiWriter, write_master_file, generate_libpressio_configuration,
add_peaks_to_libpressio_configuration (from sfx_find_peaks.py) and MetricsWriter,
psqrt_roundtrip_fracbits_gain_equalized (from sfx_dr_find_peaks.py) with all
psana1 / _peakfinders_ext imports removed.

Imported by sfx_dr_find_peaks_zmq.py and sfx_stream_find_peaks.py so those tasks
can run in the psana2 (conda2) environment without triggering the Python-version-
mismatched _peakfinders_ext C extension.
"""

import sys
from collections import namedtuple
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, TypedDict, Union

import h5py
import numpy as np
import numpy.typing as npt

# ── TypedDicts for peak lists ─────────────────────────────────────────────────


class Peakfinder8PeakList(TypedDict):
    num_peaks: int
    fs: List[float]
    ss: List[float]
    intensity: List[float]
    num_pixels: List[float]
    max_pixel_intensity: List[float]
    snr: List[float]


class Peakfinder8_v2PeakList(TypedDict):
    num_peaks: int
    fs: List[float]
    ss: List[float]
    intensity: List[float]
    num_pixels: List[float]
    max_pixel_intensity: List[float]
    snr: List[float]
    panel_number: List[int]


# ── CxiWriter ─────────────────────────────────────────────────────────────────


class CxiWriter:

    def __init__(
        self,
        outdir: str,
        rank: int,
        exp: str,
        run: int,
        n_events: int,
        det_shape: Tuple[int, ...],
        raw_det_shape: Tuple[int, ...],
        min_peaks: int,
        max_peaks: int,
        i_x: Any,
        i_y: Any,
        ipx: Any,
        ipy: Any,
        tag: str,
        algo: Literal["Peakfinder8", "Peakfinder8_v2", "PyAlgos"] = "Peakfinder8",
    ):
        self._det_shape: Tuple[int, ...] = det_shape
        self._raw_det_shape: Tuple[int, ...] = raw_det_shape
        self._i_x: Any = i_x
        self._i_y: Any = i_y
        self._ipx: Any = ipx
        self._ipy: Any = ipy
        self._index: int = 0

        fname: str = f"{exp}_r{run:0>4}_{rank}{tag}.cxi"
        Path(outdir).mkdir(exist_ok=True)
        self._outh5: Any = h5py.File(Path(outdir) / fname, "w")

        entry_1: Any = self._outh5.create_group("entry_1")

        keys: List[str]
        if "Peakfinder8" in algo:
            keys = [
                "nPeaks",
                "peakXPosRaw",
                "peakYPosRaw",
                "peakNPixels",
                "peakTotalIntensity",
                "peakMaxIntensity",
                "peakSNR",
            ]
            if algo == "Peakfinder8_v2":
                keys.append("peakPanelNumRaw")
        else:
            keys = [
                "nPeaks",
                "peakXPosRaw",
                "peakYPosRaw",
                "rcent",
                "ccent",
                "rmin",
                "rmax",
                "cmin",
                "cmax",
                "peakTotalIntensity",
                "peakMaxIntensity",
                "peakRadius",
            ]

        ds_expId: Any = entry_1.create_dataset(
            "experimental_identifier", (n_events,), maxshape=(None,), dtype=int
        )
        ds_expId.attrs["axes"] = "experiment_identifier"
        data_1: Any = entry_1.create_dataset(
            "/entry_1/data_1/data",
            (n_events, det_shape[0], det_shape[1]),
            chunks=(1, det_shape[0], det_shape[1]),
            maxshape=(None, det_shape[0], det_shape[1]),
            dtype=np.float32,
        )
        data_1.attrs["axes"] = "experiment_identifier"

        key: str
        for key in ["powderHits", "powderMisses", "mask"]:
            entry_1.create_dataset(
                f"/entry_1/data_1/{key}",
                det_shape,
                chunks=det_shape,
                maxshape=det_shape,
                dtype=float,
            )

        ds_x: Any
        for key in keys:
            if key == "nPeaks":
                ds_x = self._outh5.create_dataset(
                    f"/entry_1/result_1/{key}",
                    (n_events,),
                    maxshape=(None,),
                    dtype=int,
                )
                ds_x.attrs["minPeaks"] = min_peaks
                ds_x.attrs["maxPeaks"] = max_peaks
            else:
                ds_x = self._outh5.create_dataset(
                    f"/entry_1/result_1/{key}",
                    (n_events, max_peaks),
                    maxshape=(None, max_peaks),
                    chunks=(1, max_peaks),
                    dtype=float,
                )
            ds_x.attrs["axes"] = "experiment_identifier:peaks"

        lcls_1: Any = self._outh5.create_group("LCLS")
        keys = [
            "eventNumber",
            "machineTime",
            "machineTimeNanoSeconds",
            "fiducial",
            "photon_energy_eV",
        ]
        for key in keys:
            if key == "photon_energy_eV":
                ds_x = lcls_1.create_dataset(
                    f"{key}", (n_events,), maxshape=(None,), dtype=float
                )
            else:
                ds_x = lcls_1.create_dataset(
                    f"{key}", (n_events,), maxshape=(None,), dtype=int
                )
            ds_x.attrs["axes"] = "experiment_identifier"

        ds_x = self._outh5.create_dataset(
            "/LCLS/detector_1/EncoderValue", (n_events,), maxshape=(None,), dtype=float
        )
        ds_x.attrs["axes"] = "experiment_identifier"

    def write_event(
        self,
        img: npt.NDArray[np.float32],
        peaks: Any,
        timestamp_seconds: int,
        timestamp_nanoseconds: int,
        timestamp_fiducials: int,
        photon_energy: float,
        clen: float,
        algo: Literal["Peakfinder8", "Peakfinder8_v2", "PyAlgos"] = "Peakfinder8",
    ):
        if algo == "PyAlgos":
            self._write_event_pyalgos(
                img=img,
                peaks=peaks,
                timestamp_seconds=timestamp_seconds,
                timestamp_nanoseconds=timestamp_nanoseconds,
                timestamp_fiducials=timestamp_fiducials,
                photon_energy=photon_energy,
                clen=clen,
            )
        elif algo not in ("Peakfinder8", "Peakfinder8_v2"):
            raise ValueError(f"Unsupported peakfinding algorithm {algo}!")
        else:
            self._write_event_peakfinder8(
                img=img,
                peaks=peaks,
                timestamp_seconds=timestamp_seconds,
                timestamp_nanoseconds=timestamp_nanoseconds,
                timestamp_fiducials=timestamp_fiducials,
                photon_energy=photon_energy,
                clen=clen,
                v2=(algo == "Peakfinder8_v2"),
            )

    def _write_event_peakfinder8(
        self,
        img: npt.NDArray[np.float32],
        peaks: Union[Peakfinder8PeakList, Peakfinder8_v2PeakList],
        timestamp_seconds: int,
        timestamp_nanoseconds: int,
        timestamp_fiducials: int,
        photon_energy: float,
        clen: float,
        v2: bool = False,
    ) -> None:
        if self._outh5["/entry_1/data_1/data"].shape[0] <= self._index:
            self._outh5["entry_1/data_1/data"].resize(self._index + 1, axis=0)
            ds_key: str
            for ds_key in self._outh5["/entry_1/result_1"].keys():
                self._outh5[f"/entry_1/result_1/{ds_key}"].resize(
                    self._index + 1, axis=0
                )
            for ds_key in (
                "machineTime",
                "machineTimeNanoSeconds",
                "fiducial",
                "photon_energy_eV",
                "detector_1/EncoderValue",
            ):
                self._outh5[f"/LCLS/{ds_key}"].resize(self._index + 1, axis=0)

        self._outh5["/entry_1/data_1/data"][self._index, :, :] = img.reshape(
            -1, img.shape[-1]
        )
        num_peaks: int = peaks["num_peaks"]
        self._outh5["/entry_1/result_1/nPeaks"][self._index] = num_peaks
        self._outh5["/entry_1/result_1/peakXPosRaw"][self._index, :num_peaks] = (
            np.array(peaks["fs"], dtype=np.float32)
        )
        self._outh5["/entry_1/result_1/peakYPosRaw"][self._index, :num_peaks] = (
            np.array(peaks["ss"], dtype=np.float32)
        )
        if v2:
            self._outh5["/entry_1/result_1/peakPanelNumRaw"][
                self._index, :num_peaks
            ] = np.array(peaks["panel_number"], dtype=np.float32)
        self._outh5["/entry_1/result_1/peakNPixels"][self._index, :num_peaks] = (
            np.array(peaks["num_pixels"], dtype=np.float32)
        )
        self._outh5["/entry_1/result_1/peakTotalIntensity"][self._index, :num_peaks] = (
            np.array(peaks["intensity"], dtype=np.float32)
        )
        self._outh5["/entry_1/result_1/peakMaxIntensity"][self._index, :num_peaks] = (
            np.array(peaks["max_pixel_intensity"], dtype=np.float32)
        )
        self._outh5["/entry_1/result_1/peakSNR"][self._index, :num_peaks] = np.array(
            peaks["snr"], dtype=np.float32
        )

        self._outh5["/LCLS/machineTime"][self._index] = timestamp_seconds
        self._outh5["/LCLS/machineTimeNanoSeconds"][self._index] = timestamp_nanoseconds
        self._outh5["/LCLS/fiducial"][self._index] = timestamp_fiducials
        self._outh5["/LCLS/photon_energy_eV"][self._index] = photon_energy
        self._outh5["/LCLS/detector_1/EncoderValue"][self._index] = clen
        self._index += 1

    def _write_event_pyalgos(
        self,
        img: npt.NDArray[np.float32],
        peaks: Any,
        timestamp_seconds: int,
        timestamp_nanoseconds: int,
        timestamp_fiducials: int,
        photon_energy: float,
        clen: float,
    ) -> None:
        ch_rows: npt.NDArray[np.float64] = (
            peaks[:, 0] * self._raw_det_shape[-2] + peaks[:, 1]
        )
        ch_cols: npt.NDArray[np.float64] = peaks[:, 2]

        if self._outh5["/entry_1/data_1/data"].shape[0] <= self._index:
            self._outh5["entry_1/data_1/data"].resize(self._index + 1, axis=0)
            ds_key: str
            for ds_key in self._outh5["/entry_1/result_1"].keys():
                self._outh5[f"/entry_1/result_1/{ds_key}"].resize(
                    self._index + 1, axis=0
                )
            for ds_key in (
                "machineTime",
                "machineTimeNanoSeconds",
                "fiducial",
                "photon_energy_eV",
                "detector_1/EncoderValue",
            ):
                self._outh5[f"/LCLS/{ds_key}"].resize(self._index + 1, axis=0)

        self._outh5["/entry_1/data_1/data"][self._index, :, :] = img.reshape(
            -1, img.shape[-1]
        )
        self._outh5["/entry_1/result_1/nPeaks"][self._index] = peaks.shape[0]
        self._outh5["/entry_1/result_1/peakXPosRaw"][self._index, : peaks.shape[0]] = (
            ch_cols.astype("int")
        )
        self._outh5["/entry_1/result_1/peakYPosRaw"][self._index, : peaks.shape[0]] = (
            ch_rows.astype("int")
        )
        self._outh5["/entry_1/result_1/rcent"][self._index, : peaks.shape[0]] = peaks[
            :, 6
        ]
        self._outh5["/entry_1/result_1/ccent"][self._index, : peaks.shape[0]] = peaks[
            :, 7
        ]
        self._outh5["/entry_1/result_1/rmin"][self._index, : peaks.shape[0]] = peaks[
            :, 10
        ]
        self._outh5["/entry_1/result_1/rmax"][self._index, : peaks.shape[0]] = peaks[
            :, 11
        ]
        self._outh5["/entry_1/result_1/cmin"][self._index, : peaks.shape[0]] = peaks[
            :, 12
        ]
        self._outh5["/entry_1/result_1/cmax"][self._index, : peaks.shape[0]] = peaks[
            :, 13
        ]
        self._outh5["/entry_1/result_1/peakTotalIntensity"][
            self._index, : peaks.shape[0]
        ] = peaks[:, 5]
        self._outh5["/entry_1/result_1/peakMaxIntensity"][
            self._index, : peaks.shape[0]
        ] = peaks[:, 4]

        peaks_cenx: npt.NDArray[np.float64] = (
            self._i_x[
                np.array(peaks[:, 0], dtype=np.int64),
                np.array(peaks[:, 1], dtype=np.int64),
                np.array(peaks[:, 2], dtype=np.int64),
            ]
            + 0.5
            - self._ipx
        )
        peaks_ceny: npt.NDArray[np.float64] = (
            self._i_y[
                np.array(peaks[:, 0], dtype=np.int64),
                np.array(peaks[:, 1], dtype=np.int64),
                np.array(peaks[:, 2], dtype=np.int64),
            ]
            + 0.5
            - self._ipy
        )
        peak_radius: npt.NDArray[np.float64] = np.sqrt(peaks_cenx**2 + peaks_ceny**2)
        self._outh5["/entry_1/result_1/peakRadius"][
            self._index, : peaks.shape[0]
        ] = peak_radius

        self._outh5["/LCLS/machineTime"][self._index] = timestamp_seconds
        self._outh5["/LCLS/machineTimeNanoSeconds"][self._index] = timestamp_nanoseconds
        self._outh5["/LCLS/fiducial"][self._index] = timestamp_fiducials
        self._outh5["/LCLS/photon_energy_eV"][self._index] = photon_energy
        self._outh5["/LCLS/detector_1/EncoderValue"][self._index] = clen
        self._index += 1

    def write_non_event_data(
        self,
        powder_hits: npt.NDArray[np.float64],
        powder_misses: npt.NDArray[np.float64],
        mask: npt.NDArray[np.uint8],
    ):
        self._outh5["/entry_1/data_1/powderHits"][:] = powder_hits
        self._outh5["/entry_1/data_1/powderMisses"][:] = powder_misses
        self._outh5["/entry_1/data_1/mask"][:] = (1 - mask).reshape(-1, mask.shape[-1])

    def optimize_and_close_file(
        self,
        num_hits: int,
        max_peaks: int,
        algo: Literal["PyAlgos", "Peakfinder8", "Peakfinder8_v2"] = "Peakfinder8",
    ):
        data_shape: Tuple[int, ...] = self._outh5["/entry_1/data_1/data"].shape
        self._outh5["/entry_1/data_1/data"].resize(
            (num_hits, data_shape[1], data_shape[2])
        )
        self._outh5["/entry_1/result_1/nPeaks"].resize((num_hits,))

        keys: List[str]
        if "Peakfinder8" in algo:
            keys = [
                "peakXPosRaw",
                "peakYPosRaw",
                "peakNPixels",
                "peakTotalIntensity",
                "peakMaxIntensity",
                "peakSNR",
            ]
            if algo == "Peakfinder8_v2":
                keys.append("peakPanelNumRaw")
        else:
            keys = [
                "peakXPosRaw",
                "peakYPosRaw",
                "rcent",
                "ccent",
                "rmin",
                "rmax",
                "cmin",
                "cmax",
                "peakTotalIntensity",
                "peakMaxIntensity",
                "peakRadius",
            ]

        key: str
        for key in keys:
            self._outh5[f"/entry_1/result_1/{key}"].resize((num_hits, max_peaks))

        for key in [
            "eventNumber",
            "machineTime",
            "machineTimeNanoSeconds",
            "fiducial",
            "detector_1/EncoderValue",
            "photon_energy_eV",
        ]:
            self._outh5[f"/LCLS/{key}"].resize((num_hits,))
        self._outh5.close()


# ── write_master_file ─────────────────────────────────────────────────────────


def write_master_file(
    mpi_size: int,
    outdir: str,
    exp: str,
    run: int,
    tag: str,
    n_hits_per_rank: List[int],
    n_hits_total: int,
) -> Path:
    fnames: List[Path] = []
    ranks_with_hits: List[int] = []
    for fi in range(mpi_size):
        if n_hits_per_rank[fi] > 0:
            fnames.append(Path(outdir) / f"{exp}_r{run:0>4}_{fi}{tag}.cxi")
            ranks_with_hits.append(fi)
    if len(fnames) == 0:
        sys.exit("No hits found")

    dname_list, key_list, shape_list, dtype_list = [], [], [], []
    datasets = ["/entry_1/result_1", "/LCLS/detector_1", "/LCLS", "/entry_1/data_1"]
    f = h5py.File(fnames[0], "r")
    for dname in datasets:
        dset = f[dname]
        for key in dset.keys():
            if f"{dname}/{key}" not in datasets:
                dname_list.append(dname)
                key_list.append(key)
                shape_list.append(dset[key].shape)
                dtype_list.append(dset[key].dtype)
    f.close()

    mask: Optional[npt.NDArray[np.uint8]] = None
    powder_hits: Optional[npt.NDArray[np.float64]] = None
    powder_misses: Optional[npt.NDArray[np.float64]] = None
    for fn in fnames:
        f = h5py.File(fn, "r")
        if mask is None:
            mask = f["entry_1/data_1/mask"][:].copy()
        if powder_hits is None:
            powder_hits = f["entry_1/data_1/powderHits"][:].copy()
            powder_misses = f["entry_1/data_1/powderMisses"][:].copy()
        else:
            assert powder_misses is not None
            powder_hits = np.maximum(
                powder_hits, f["entry_1/data_1/powderHits"][:].copy()
            )
            powder_misses = np.maximum(
                powder_misses, f["entry_1/data_1/powderMisses"][:].copy()
            )
        f.close()

    vfname: Path = Path(outdir) / f"{exp}_r{run:0>4}{tag}.cxi"
    with h5py.File(vfname, "w") as vdf:
        for dnum in range(len(dname_list)):
            dname = f"{dname_list[dnum]}/{key_list[dnum]}"
            if key_list[dnum] not in ["mask", "powderHits", "powderMisses"]:
                layout = h5py.VirtualLayout(
                    shape=(n_hits_total,) + shape_list[dnum][1:], dtype=dtype_list[dnum]
                )
                cursor = 0
                for i, fn in enumerate(fnames):
                    rank_idx: int = ranks_with_hits[i]
                    vsrc = h5py.VirtualSource(
                        fn,
                        dname,
                        shape=(n_hits_per_rank[rank_idx],) + shape_list[dnum][1:],
                    )
                    if len(shape_list[dnum]) == 1:
                        layout[cursor : cursor + n_hits_per_rank[rank_idx]] = vsrc
                    else:
                        layout[cursor : cursor + n_hits_per_rank[rank_idx], :] = vsrc
                    cursor += n_hits_per_rank[rank_idx]
                vdf.create_virtual_dataset(dname, layout, fillvalue=-1)

        vdf["entry_1/data_1/powderHits"] = powder_hits
        vdf["entry_1/data_1/powderMisses"] = powder_misses
        vdf["entry_1/data_1/mask"] = mask

    return vfname


# ── libpressio helpers ────────────────────────────────────────────────────────


def generate_libpressio_configuration(
    compressor: Literal["sz3", "qoz"],
    roi_window_size: int,
    bin_size: int,
    abs_error: float,
    libpressio_mask: npt.NDArray,
) -> Dict[str, Any]:
    if compressor == "qoz":
        pressio_opts: Dict[str, Any] = {
            "pressio:abs": abs_error,
            "qoz": {"qoz:stride": 8},
        }
    elif compressor == "sz3":
        pressio_opts = {"pressio:abs": abs_error}

    ndims: int = len(libpressio_mask.shape)
    other_dims: List[int] = [1] * (ndims - 2)
    roi_size: List[int] = other_dims * (ndims - 2) + [roi_window_size, roi_window_size]
    bin_dims: List[int] = other_dims * (ndims - 2) + [bin_size, bin_size]

    if len(roi_size) < 4:
        other_dims = [1] * (4 - len(roi_size))
        roi_size = other_dims + roi_size
        bin_dims = other_dims + bin_dims

    lp_json: Dict[str, Any] = {
        "compressor_id": "pressio",
        "early_config": {
            "pressio": {
                "pressio:compressor": "roibin",
                "roibin": {
                    "roibin:metric": "composite",
                    "roibin:background": "mask_binning",
                    "roibin:roi": "fpzip",
                    "background": {
                        "binning:compressor": "pressio",
                        "mask_binning:compressor": "pressio",
                        "pressio": {"pressio:compressor": compressor},
                    },
                    "composite": {
                        "composite:plugins": [
                            "size",
                            "time",
                            "input_stats",
                            "error_stat",
                        ],
                    },
                },
            }
        },
        "compressor_config": {
            "pressio": {
                "roibin": {
                    "roibin:roi_size": roi_size,
                    "roibin:centers": None,
                    "roibin:roi_strategy": "coordinates",
                    "roibin:nthreads": 1,
                    "roi": {"fpzip:prec": 0},
                    "background": {
                        "mask_binning:mask": None,
                        "mask_binning:shape": bin_dims,
                        "mask_binning:nthreads": 4,
                        "pressio": pressio_opts,
                    },
                }
            }
        },
        "name": "pressio",
    }

    reshaped_mask: npt.NDArray[np.uint8]
    if libpressio_mask.ndim < 4:
        new_shape: Tuple[int, ...] = (
            (1,) * (4 - libpressio_mask.ndim)
        ) + libpressio_mask.shape
        reshaped_mask = libpressio_mask.reshape(new_shape)
    else:
        reshaped_mask = libpressio_mask

    lp_json["compressor_config"]["pressio"]["roibin"]["background"][
        "mask_binning:mask"
    ] = (1 - reshaped_mask)
    return lp_json


def _libpressio_config_pyalgos(lp_json: Dict[str, Any], peaks: Any) -> Dict[str, Any]:
    peaks_new: npt.NDArray[np.uint64] = np.zeros((len(peaks), 4), dtype=np.uint64)
    peaks_new[:, 1:] = np.ascontiguousarray(np.uint64(peaks[:, [0, 1, 2]])).astype(
        np.uint64
    )
    lp_json["compressor_config"]["pressio"]["roibin"]["roibin:centers"] = peaks_new
    return lp_json


def _libpressio_config_pf8(
    lp_json: Dict[str, Any], peaks: Peakfinder8PeakList
) -> Dict[str, Any]:
    peaks_rotated: npt.NDArray[np.uint64] = np.zeros(
        (peaks["num_peaks"], 4), dtype=np.uint64
    )
    peaks_rotated[:, 2] = np.array(peaks["ss"]).astype(np.uint64)
    peaks_rotated[:, 3] = np.array(peaks["fs"]).astype(np.uint64)
    lp_json["compressor_config"]["pressio"]["roibin"]["roibin:centers"] = peaks_rotated
    return lp_json


def add_peaks_to_libpressio_configuration(
    lp_json: Dict[str, Any],
    peaks: Any,
    algo: Literal["PyAlgos", "Peakfinder8", "Peakfinder8_v2"] = "PyAlgos",
) -> Dict[str, Any]:
    if "Peakfinder8" in algo:
        return _libpressio_config_pf8(lp_json=lp_json, peaks=peaks)
    return _libpressio_config_pyalgos(lp_json=lp_json, peaks=peaks)


# ── MetricsWriter ─────────────────────────────────────────────────────────────


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
        self._flush_every: int = max(
            1, self.n_debug_events
        )  # flush once reservoir is full, then every n_debug_events replacements
        self._replacements_since_flush: int = 0

        self.outdir.mkdir(parents=True, exist_ok=True)
        self.filename = (
            self.outdir / f"r{self.run:04d}_metrics_rank{self.rank:03d}{self.tag}.h5"
        )
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

    def _write_metrics_recursive(
        self, group: h5py.Group, metrics: Dict[str, Any]
    ) -> None:
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
        if (
            (not self.save_debug_panels)
            or (self.n_debug_events <= 0)
            or (self.panels_per_event <= 0)
        ):
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
            "original": [
                np.asarray(original_img[int(p)], dtype=self.debug_dtype, order="C")
                for p in panel_ids
            ],
            "reconstructed": [
                np.asarray(reconstructed_img[int(p)], dtype=self.debug_dtype, order="C")
                for p in panel_ids
            ],
        }
        self._seen += 1
        if len(self._reservoir) < self.n_debug_events:
            self._reservoir.append(payload)
            if len(self._reservoir) == self.n_debug_events:
                # Reservoir just filled — flush immediately so a cancellation
                # before close() doesn't lose all panels.
                self._flush_debug_panels()
            return
        j = int(self._rng.integers(0, self._seen))
        if j < self.n_debug_events:
            self._reservoir[j] = payload
            self._replacements_since_flush += 1
            if self._replacements_since_flush >= self._flush_every:
                self._flush_debug_panels()
                self._replacements_since_flush = 0

    def _write_small_dataset(
        self, group: h5py.Group, name: str, arr2d: np.ndarray
    ) -> None:
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


# ── psqrt helper ──────────────────────────────────────────────────────────────


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
        return np.zeros_like(x0, dtype=np.float32) - np.float32(offset)

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

    x_max_use = float(np.max(xf[finitef])) if x_max is None else float(x_max)
    if x_max_use <= 0.0:
        return np.zeros_like(x0, dtype=np.float32) - np.float32(offset)

    k = (float(y_max) * float(y_max)) / x_max_use
    xc = np.clip(xf, 0.0, x_max_use)
    code = np.rint(np.sqrt(k * xc))
    code = np.clip(code, 0.0, float(y_max))
    x_rec = (code * code) / k
    return (x_rec - np.float32(offset)).astype(np.float32)
