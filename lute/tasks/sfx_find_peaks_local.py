"""
Classes for peak finding on local simulation HDF5 files in SFX.

Classes:
    CxiWriter: utility class for writing peak finding results to CXI files.

    FindPeaksSFXLocal: peak finding using the Peakfinder8 algorithm on
        locally stored HDF5 simulation frames. No psana data source required;
        detector geometry is extracted from the dxtbx_detector_json attribute
        embedded in each HDF5 file.
"""

__all__ = ["CxiWriter", "FindPeaksSFXLocal"]
__author__ = "Valerio Mariani, Gabriel Dorlhiac"

import glob
import json
import logging
import random
import sys
from collections import namedtuple
from pathlib import Path
from typing import (
    Any,
    Dict,
    Generator,
    List,
    Literal,
    Optional,
    TextIO,
    Tuple,
    TypedDict,
    TYPE_CHECKING,
    Union,
    cast,
)

try:
    from typing import TypeAlias  # type: ignore
except ImportError:
    from typing_extensions import TypeAlias

import h5py  # type: ignore
import holoviews as hv  # type: ignore
import numpy as np
import numpy.typing as npt
import panel as pn
from mpi4py.MPI import COMM_WORLD, SUM

from lute.execution.logging import get_logger
from lute.execution.ipc import Message
from lute.tasks.algorithms import _peakfinders_ext  # type: ignore
from lute.tasks.task import Task
from lute.tasks.dataclasses import TaskStatus, ElogSummaryPlots

if TYPE_CHECKING:
    from lute.io.models.sfx_find_peaks_local import FindPeaksSFXLocalParameters
else:
    from lute.io.parameters import TaskParameters

    FindPeaksSFXLocalParameters = TaskParameters


hv.extension("bokeh")
pn.extension()


logger: logging.Logger = get_logger("FindPeaksSFXLocal", is_task=True)


class RadialStatistics(TypedDict):
    rstats_pixel_index: npt.NDArray[np.int32]
    rstats_radius: npt.NDArray[np.int32]
    rstats_num_pix: int
    radial_map: npt.NDArray[np.float64]


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


EventMaskData = namedtuple(
    "EventMaskData",
    ["data", "mask", "seconds", "nanoseconds", "fiducials", "clen", "photon_energy"],
)

EventData = namedtuple(
    "EventData",
    ["data", "seconds", "nanoseconds", "fiducials", "clen", "photon_energy"],
)

DetectorGeomInfo = namedtuple("DetectorGeomInfo", ["i_x", "i_y", "ipx", "ipy"])

PyAlgos: TypeAlias = Any


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
        i_x: Any,  # Not typed becomes it comes from psana
        i_y: Any,  # Not typed becomes it comes from psana
        ipx: Any,  # Not typed becomes it comes from psana
        ipy: Any,  # Not typed becomes it comes from psana
        tag: str,
        algo: Literal["Peakfinder8", "Peakfinder8_v2", "PyAlgos"] = "Peakfinder8",
    ):
        """
        Set up the CXI files to which peak finding results will be saved.

        Parameters:

            outdir (str): Output directory for cxi file.

            rank (int): MPI rank of the caller.

            exp (str): Experiment string.

            run (int): Experimental run.

            n_events (int): Number of events to process.

            det_shape (Tuple[int, int]): Shape of the numpy array storing the detector
                data. This must be aCheetah-stile 2D array.

            raw_det_shape (Tuple[int, ...]): Shape of the numpy array storing the
                detector in raw unassembled form. Length = 2, 3, or 4.

            min_peaks (int): Minimum number of peaks per image.

            max_peaks (int): Maximum number of peaks per image.

            i_x (Any): Array of pixel indexes along x

            i_y (Any): Array of pixel indexes along y

            ipx (Any): Pixel indexes with respect to detector origin (x component)

            ipy (Any): Pixel indexes with respect to detector origin (y component)

            tag (str): Tag to append to cxi file names.

            algo (str): The algorithm being used - either Peakfinder8 or PyAlgos.
        """
        self._det_shape: Tuple[int, ...] = det_shape
        self._raw_det_shape: Tuple[int, ...] = raw_det_shape
        self._i_x: Any = i_x
        self._i_y: Any = i_y
        self._ipx: Any = ipx
        self._ipy: Any = ipy
        self._index: int = 0

        # Create and open the HDF5 file
        fname: str = f"{exp}_r{run:0>4}_{rank}{tag}.cxi"
        Path(outdir).mkdir(exist_ok=True)
        self._outh5: Any = h5py.File(Path(outdir) / fname, "w")

        # Entry_1 entry for processing with CrystFEL
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

        # Peak-related entries
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

        # Timestamp entries
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
        peaks: Any,  # Not typed becomes it comes from psana
        timestamp_seconds: int,
        timestamp_nanoseconds: int,
        timestamp_fiducials: int,
        photon_energy: float,
        clen: float,
        algo: Literal["Peakfinder8", "Peakfinder8_v2", "PyAlgos"] = "Peakfinder8",
    ):
        """
        Write peak finding results for an event into the HDF5 file.

        Parameters:

            img (npt.NDArray[np.float32]): Detector data for the event

            peaks: (Any): Peak information for the event, as recovered from the PyAlgos
                algorithm

            timestamp_seconds (int): Second part of the event's timestamp information

            timestamp_nanoseconds (int): Nanosecond part of the event's timestamp
                information

            timestamp_fiducials (int): Fiducials part of the event's timestamp
                information

            photon_energy (float): Photon energy for the event

            clen (float): Camera length/detector distance.
        """
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
            v2: bool = False
            if algo == "Peakfinder8":
                v2 = False
            elif algo == "Peakfinder8_v2":
                v2 = True

            self._write_event_peakfinder8(
                img=img,
                peaks=peaks,
                timestamp_seconds=timestamp_seconds,
                timestamp_nanoseconds=timestamp_nanoseconds,
                timestamp_fiducials=timestamp_fiducials,
                photon_energy=photon_energy,
                clen=clen,
                v2=v2,
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

        # Entry_1 entry for processing with CrystFEL
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
            peaks = cast(Peakfinder8_v2PeakList, peaks)
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

        # LCLS entry dataset
        self._outh5["/LCLS/machineTime"][self._index] = timestamp_seconds
        self._outh5["/LCLS/machineTimeNanoSeconds"][self._index] = timestamp_nanoseconds
        self._outh5["/LCLS/fiducial"][self._index] = timestamp_fiducials
        self._outh5["/LCLS/photon_energy_eV"][self._index] = photon_energy

        # Add clen distance
        self._outh5["/LCLS/detector_1/EncoderValue"][self._index] = clen
        self._index += 1

    def _write_event_pyalgos(
        self,
        img: npt.NDArray[np.float32],
        peaks: Any,  # Not typed becomes it comes from psana
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

        # Entry_1 entry for processing with CrystFEL
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

        # Calculate and write pixel radius
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
        peak_radius: npt.NDArray[np.float64] = np.sqrt(
            (peaks_cenx**2) + (peaks_ceny**2)
        )
        self._outh5["/entry_1/result_1/peakRadius"][
            self._index, : peaks.shape[0]
        ] = peak_radius

        # LCLS entry dataset
        self._outh5["/LCLS/machineTime"][self._index] = timestamp_seconds
        self._outh5["/LCLS/machineTimeNanoSeconds"][self._index] = timestamp_nanoseconds
        self._outh5["/LCLS/fiducial"][self._index] = timestamp_fiducials
        self._outh5["/LCLS/photon_energy_eV"][self._index] = photon_energy

        # Add clen distance
        self._outh5["/LCLS/detector_1/EncoderValue"][self._index] = clen
        self._index += 1

    def write_non_event_data(
        self,
        powder_hits: npt.NDArray[np.float64],
        powder_misses: npt.NDArray[np.float64],
        mask: npt.NDArray[np.uint8],
    ):
        """
        Write to the file data that is not related to a specific event (masks, powders)

        Parameters:

            powder_hits (npt.NDArray[np.float64]): Virtual powder pattern from hits

            powder_misses (npt.NDArray[np.float64]): Virtual powder pattern from hits

            mask: (npt.NDArray[np.uint16]): Pixel ask to write into the file

        """
        # Add powders and mask to files, reshaping them to match the crystfel
        # convention
        self._outh5["/entry_1/data_1/powderHits"][:] = powder_hits
        self._outh5["/entry_1/data_1/powderMisses"][:] = powder_misses
        self._outh5["/entry_1/data_1/mask"][:] = (1 - mask).reshape(
            -1, mask.shape[-1]
        )  # Crystfel expects inverted values

    def optimize_and_close_file(
        self,
        num_hits: int,
        max_peaks: int,
        algo: Literal["PyAlgos", "Peakfinder8", "Peakfinder8_v2"] = "Peakfinder8",
    ):
        """
        Resize data blocks and write additional information to the file

        Parameters:

            num_hits (int): Number of hits for which information has been saved to the
                file

            max_peaks (int): Maximum number of peaks (per event) for which information
                can be written into the file

            algo (Literal["PyAlgos", "Peakfinder8", "Peakfinder8_v2"]): The algorithm
                being used for peakfinding. This affects the exact keys that are
                saved.
        """

        # Resize the entry_1 entry
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

        # Resize LCLS entry
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


def write_master_file(
    mpi_size: int,
    outdir: str,
    exp: str,
    run: int,
    tag: str,
    n_hits_per_rank: List[int],
    n_hits_total: int,
) -> Path:
    """
    Generate a virtual dataset to map all individual files for this run.

    Parameters:

        mpi_size (int): Number of ranks in the MPI pool.

        outdir (str): Output directory for cxi file.

        exp (str): Experiment string.

        run (int): Experimental run.

        tag (str): Tag to append to cxi file names.

        n_hits_per_rank (List[int]): Array containing the number of hits found on each
            node processing data.

        n_hits_total (int): Total number of hits found across all nodes.

    Returns:

        The path to the the written master file
    """
    # Retrieve paths to the files containing data
    fnames: List[Path] = []
    # Need to keep track of these for indexing later during master file creation
    ranks_with_hits: List[int] = []
    for fi in range(mpi_size):
        if n_hits_per_rank[fi] > 0:
            fnames.append(Path(outdir) / f"{exp}_r{run:0>4}_{fi}{tag}.cxi")
            ranks_with_hits.append(fi)
    if len(fnames) == 0:
        sys.exit("No hits found")

    # Retrieve list of entries to populate in the virtual hdf5 file
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

    # Compute cumulative powder hits and misses for all files
    # Copy mask as well
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
        # Write the virtual hdf5 file
        for dnum in range(len(dname_list)):
            dname = f"{dname_list[dnum]}/{key_list[dnum]}"
            if key_list[dnum] not in ["mask", "powderHits", "powderMisses"]:
                layout = h5py.VirtualLayout(
                    shape=(n_hits_total,) + shape_list[dnum][1:], dtype=dtype_list[dnum]
                )
                cursor = 0
                for i, fn in enumerate(fnames):
                    # Use the correct rank hit count or you get a malformed VDS
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


class FindPeaksSFXLocal(Task):
    """
    Task that performs Peakfinder8 peak finding on local simulation HDF5 files
    and writes the peak information to CXI files.

    Detector geometry (beam center, radius map) is derived from the
    dxtbx_detector_json attribute embedded in each HDF5 frame file. No psana
    data source is required.
    """

    def __init__(
        self, *, params: FindPeaksSFXLocalParameters, use_mpi: bool = True, row_ids=None
    ) -> None:
        self._task_parameters: FindPeaksSFXLocalParameters = params
        super().__init__(params=params, use_mpi=use_mpi, row_ids=row_ids)

    def _compute_num_radial_bins(
        self, indices: Tuple[slice, ...], radius_map: npt.NDArray[np.float64]
    ) -> int:
        return int(np.ceil(radius_map[indices].max()) + 1)

    def compute_radial_statistics(
        self,
        radius_map: npt.NDArray[np.float64],
        shape: Tuple[int, ...],
        num_bins: int = 100,
    ) -> RadialStatistics:
        radius_map_int: npt.NDArray[np.int8] = np.rint(radius_map).astype(int).ravel()
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

        rstats_pixel_index: npt.NDArray[np.int32] = np.array(peak_index).astype(
            np.int32
        )
        rstats_radius: npt.NDArray[np.int32] = np.array(radius).astype(np.int32)

        logger.debug(
            f"Computed radial statistics for {num_bins} bins. "
            f"rstats_num_pix: {rstats_pixel_index.size}"
        )

        return {
            "rstats_pixel_index": rstats_pixel_index,
            "rstats_radius": rstats_radius,
            "rstats_num_pix": rstats_pixel_index.size,
            "radial_map": radius_map.reshape(shape),
        }

    def _get_geometry_from_h5(
        self, filepath: str
    ) -> Tuple[DetectorGeomInfo, npt.NDArray[np.float64], float]:
        """Derive pixel maps, radius map, and camera length from a frame file.

        Reads the dxtbx_detector_json attribute embedded in the HDF5 file and
        computes all geometry quantities needed by Peakfinder8 and CxiWriter.

        Parameters:
            filepath: Path to any frame HDF5 file from the simulation.

        Returns:
            Tuple of (DetectorGeomInfo, radius_map, clen_mm).
        """
        with h5py.File(filepath, "r") as f:
            det: Dict[str, Any] = json.loads(f.attrs["dxtbx_detector_json"])

        panel: Dict[str, Any] = det["panels"][0]
        origin: List[float] = panel["origin"]  # [x_mm, y_mm, z_mm]
        ps: List[float] = panel["pixel_size"]  # [fs_mm, ss_mm]
        nx: int
        ny: int
        nx, ny = panel["image_size"]  # [n_fast, n_slow]

        # Beam center in pixel coordinates
        # x_lab(j) = origin[0] + j*ps[0]*fast[0]  (fast=[1,0,0])
        # y_lab(i) = origin[1] + i*ps[1]*slow[1]  (slow=[0,-1,0])
        # Set to zero: bcx = -origin[0]/ps[0], bcy = origin[1]/ps[1]
        bcx: float = -origin[0] / ps[0]  # col (fast axis) of beam center
        bcy: float = origin[1] / ps[1]  # row (slow axis) of beam center
        clen: float = abs(origin[2])  # detector distance in mm

        cols: npt.NDArray[np.float64]
        rows: npt.NDArray[np.float64]
        cols, rows = np.meshgrid(
            np.arange(nx, dtype=np.float64),
            np.arange(ny, dtype=np.float64),
        )
        radius_map: npt.NDArray[np.float32] = np.sqrt(
            (cols - bcx) ** 2 + (rows - bcy) ** 2
        ).astype(np.float32)

        # Identity pixel maps: panel is already a 2D slab, assembly is trivial
        i_x: npt.NDArray[np.int64] = rows.astype(np.int64)
        i_y: npt.NDArray[np.int64] = cols.astype(np.int64)

        return (
            DetectorGeomInfo(i_x=i_x, i_y=i_y, ipx=int(bcx), ipy=int(bcy)),
            radius_map,
            clen,
        )

    def _event_generator_local(
        self,
        local_files: List[str],
        clen: float,
    ) -> Generator[Union[EventMaskData, EventData], None, None]:
        """Yield calibrated detector frames from local HDF5 files.

        Parameters:
            local_files: Ordered list of HDF5 file paths assigned to this rank.
            clen: Camera length in mm (constant for all frames).
        """
        # hc in eV·m
        HC: float = 1.23984193e-6

        first_event: bool = True
        filepath: str
        for idx, filepath in enumerate(local_files):
            with h5py.File(filepath, "r") as f:
                img: npt.NDArray[np.float32] = f["panel_0"][:].astype(np.float32)
                beam: Dict[str, Any] = json.loads(f.attrs["dxtbx_beam_json"])

            wavelength_A: float = float(beam["wavelength"])
            photon_energy: float = HC / (wavelength_A * 1e-10)  # eV

            # Timestamps: use file index as a fake integer second; no real
            # LCLS timing available for simulated data.
            seconds: int = idx
            nanoseconds: int = 0
            fiducials: int = 0

            if first_event:
                mask: npt.NDArray[np.uint8] = np.ones(img.shape, dtype=np.uint8)
                first_event = False
                yield EventMaskData(
                    img, mask, seconds, nanoseconds, fiducials, clen, photon_energy
                )
            else:
                yield EventData(
                    img, seconds, nanoseconds, fiducials, clen, photon_energy
                )

    def _run(self) -> None:
        rank: int = COMM_WORLD.Get_rank()
        size: int = COMM_WORLD.Get_size()

        # Build the full file list, apply optional frame cap, split across ranks
        all_files: List[str] = sorted(
            glob.glob(
                str(
                    Path(self._task_parameters.input_dir)
                    / self._task_parameters.file_pattern
                )
            )
        )
        if self._task_parameters.n_events > 0:
            all_files = all_files[: self._task_parameters.n_events]
        local_files: List[str] = all_files[rank::size]

        # Derive geometry once from the first file (same detector for all frames)
        geom: DetectorGeomInfo
        radius_map: npt.NDArray[np.float64]
        clen: float
        geom, radius_map, clen = self._get_geometry_from_h5(all_files[0])
        i_x, i_y, ipx, ipy = geom.i_x, geom.i_y, geom.ipx, geom.ipy

        alg: Optional[_peakfinders_ext.peakfinder_8] = None
        num_hits: int = 0
        num_events: int = 0
        num_empty_images: int = 0
        tag: str = self._task_parameters.tag

        if (tag != "") and (tag[0] != "_"):
            tag = "_" + tag

        first_event: bool = True
        mask: Optional[npt.NDArray[np.uint8]] = None
        file_writer: Optional[CxiWriter] = None
        powder_hits: Optional[npt.NDArray[np.float64]] = None
        powder_misses: Optional[npt.NDArray[np.float64]] = None

        for event_data in self._event_generator_local(local_files, clen):
            if first_event:
                assert isinstance(event_data, EventMaskData)
                (
                    img,
                    mask,
                    timestamp_seconds,
                    timestamp_nanoseconds,
                    timestamp_fiducials,
                    clen,
                    photon_energy,
                ) = event_data

                first_event = False
            else:
                assert isinstance(event_data, EventData)
                (
                    img,
                    timestamp_seconds,
                    timestamp_nanoseconds,
                    timestamp_fiducials,
                    clen,
                    photon_energy,
                ) = event_data
            if img is None:
                num_empty_images += 1
                continue

            # Images from local H5 files are always 2D slabs
            det_shape: Tuple[int, ...] = img.shape
            img_reshaped: npt.NDArray[np.float32] = img
            mask_reshaped: npt.NDArray[np.uint8]

            radial_stats: Optional[RadialStatistics] = None
            base_peakfinder_options: Dict[str, Any]
            if alg is None:
                # First event: initialise algorithm and file writer
                assert mask is not None
                mask_reshaped = mask

                adc_thresh: float = self._task_parameters.amax_thr
                hitfinder_min_snr: float = self._task_parameters.son_min
                hitfinder_min_pix_count: int = self._task_parameters.npix_min
                hitfinder_max_pix_count: int = self._task_parameters.npix_max
                local_bg_radius: float = self._task_parameters.r0

                alg = _peakfinders_ext.peakfinder_8

                num_radial_bins: int = self._compute_num_radial_bins(
                    indices=tuple(slice(None, dim) for dim in radius_map.shape),
                    radius_map=radius_map,
                )
                radial_stats = self.compute_radial_statistics(
                    radius_map=radius_map,
                    shape=det_shape,
                    num_bins=num_radial_bins,
                )

                # Single monolithic panel: asic == full image
                asic_nx: int = img.shape[1]  # fast-scan size
                asic_ny: int = img.shape[0]  # slow-scan size
                nasics_x: int = 1
                nasics_y: int = 1

                base_peakfinder_options = {
                    "max_num_peaks": self._task_parameters.max_peaks,
                    "data": img_reshaped,  # updated each event below
                    "mask": mask_reshaped.astype(np.int8),
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
                    "hitfinder_local_bg_radius": int(local_bg_radius),  # Must be int
                }

                hdffh: Any
                if self._task_parameters.mask_file is not None:
                    with h5py.File(self._task_parameters.mask_file, "r") as hdffh:
                        loaded_mask: npt.NDArray[np.int64] = hdffh[
                            "entry_1/data_1/mask"
                        ][:]
                        mask *= loaded_mask.astype(np.uint8)

                file_writer = CxiWriter(
                    outdir=self._task_parameters.outdir,
                    rank=rank,
                    exp=self._task_parameters.exp_label,
                    run=self._task_parameters.run_label,
                    n_events=len(local_files),
                    det_shape=det_shape,
                    raw_det_shape=img.shape,
                    i_x=i_x,
                    i_y=i_y,
                    ipx=ipx,
                    ipy=ipy,
                    min_peaks=self._task_parameters.min_peaks,
                    max_peaks=self._task_parameters.max_peaks,
                    tag=tag,
                    algo="Peakfinder8",
                )

                powder_hits = np.zeros(det_shape)
                powder_misses = np.zeros(det_shape)

            peaks: Peakfinder8PeakList
            num_peaks: int = 0
            assert alg

            # Update data pointer and run Peakfinder8
            base_peakfinder_options["data"] = img_reshaped
            raw_pf8_lists: Tuple[
                List[float],
                List[float],
                List[float],
                List[int],
                List[float],
                List[float],
                List[float],
                List[float],
            ] = alg(**base_peakfinder_options)

            pf8_peaks: Peakfinder8PeakList = {
                "num_peaks": len(raw_pf8_lists[0]),
                "fs": raw_pf8_lists[0],
                "ss": raw_pf8_lists[1],
                "intensity": raw_pf8_lists[2],
                "num_pixels": raw_pf8_lists[4],
                "max_pixel_intensity": raw_pf8_lists[5],
                "snr": raw_pf8_lists[6],
            }

            num_peaks = pf8_peaks["num_peaks"]
            peaks = pf8_peaks

            num_events += 1
            if (num_peaks >= self._task_parameters.min_peaks) and (
                num_peaks <= self._task_parameters.max_peaks
            ):
                assert file_writer is not None
                file_writer.write_event(
                    img=img_reshaped,
                    peaks=peaks,
                    timestamp_seconds=timestamp_seconds,
                    timestamp_nanoseconds=timestamp_nanoseconds,
                    timestamp_fiducials=timestamp_fiducials,
                    photon_energy=photon_energy,
                    clen=clen,
                    algo="Peakfinder8",
                )
                num_hits += 1

            if num_peaks >= self._task_parameters.min_peaks:
                assert powder_hits is not None
                powder_hits = np.maximum(
                    powder_hits,
                    img.reshape(det_shape),
                )
            else:
                assert powder_misses is not None
                powder_misses = np.maximum(
                    powder_misses,
                    img.reshape(det_shape),
                )

        if num_empty_images != 0:
            msg: Message = Message(
                contents=f"Rank {rank} encountered {num_empty_images} empty images."
            )
            self._report_to_executor(msg)

        if file_writer is not None:
            assert mask is not None
            assert powder_hits is not None
            assert powder_misses is not None
            file_writer.write_non_event_data(
                powder_hits=powder_hits,
                powder_misses=powder_misses,
                mask=mask,
            )

            file_writer.optimize_and_close_file(
                num_hits=num_hits,
                max_peaks=self._task_parameters.max_peaks,
                algo="Peakfinder8",
            )

        COMM_WORLD.Barrier()

        num_hits_per_rank: List[int] = cast(
            List[int], COMM_WORLD.gather(num_hits, root=0)
        )
        num_hits_total: int = cast(int, COMM_WORLD.reduce(num_hits, SUM))
        num_events_total: int = cast(int, COMM_WORLD.reduce(num_events, SUM))

        if rank == 0:
            master_fname: Path = write_master_file(
                mpi_size=size,
                outdir=self._task_parameters.outdir,
                exp=self._task_parameters.exp_label,
                run=self._task_parameters.run_label,
                tag=tag,
                n_hits_per_rank=num_hits_per_rank,
                n_hits_total=num_hits_total,
            )

            # Write final summary file
            f: Union[TextIO, h5py.File]
            with open(
                Path(self._task_parameters.outdir) / f"peakfinding{tag}.summary", "w"
            ) as f:
                print(f"Number of events processed: {num_events_total}", file=f)
                print(f"Number of hits found: {num_hits_total}", file=f)
                print(
                    "Fractional hit rate: " f"{(num_hits_total/num_events_total):.2f}",
                    file=f,
                )
                print(f"No. hits per rank: {num_hits_per_rank}", file=f)

            with h5py.File(master_fname, "r") as f:
                final_powder_hits: npt.NDArray[np.float64] = f[
                    "entry_1/data_1/powderHits"
                ][:]
                final_powder_misses: npt.NDArray[np.float64] = f[
                    "entry_1/data_1/powderMisses"
                ][:]
                f.close()

            text_summary: Dict[str, str] = {
                "Number of events processed": str(num_events_total),
                "Number of hits found": str(num_hits_total),
                "Fractional hit rate": f"{num_hits_total/num_events_total:.2f}",
            }

            task_summary: Union[Dict[str, str], Tuple[Dict[str, str], ElogSummaryPlots]]
            if self._task_parameters.make_powder_plots:
                powder_plots: pn.Tabs = self._create_powder_plots(
                    powder_hits=final_powder_hits.astype(np.float64),
                    powder_misses=final_powder_misses.astype(np.float64),
                    i_x=i_x,
                    i_y=i_y,
                )
                task_summary = (
                    text_summary,
                    ElogSummaryPlots(
                        f"r{self._task_parameters.run_label}/powders",
                        powder_plots,
                    ),
                )
            else:
                task_summary = text_summary

            self._result.summary = task_summary
            with open(Path(self._task_parameters.out_file), "w") as f:
                print(f"{master_fname}", file=f)

    def _post_run(self) -> None:
        super()._post_run()
        self._result.task_status = TaskStatus.COMPLETED

    def _assemble_image(
        self,
        img: npt.NDArray[np.float64],
        i_x: npt.NDArray[np.uint64],
        i_y: npt.NDArray[np.uint64],
    ) -> npt.NDArray[np.float64]:
        """Assemble an image based on detector geometry pixel maps.

        Args:
            img (np.ndarray[np.float64]): The image to assemble.

            i_x (Any): Array of pixel indexes along x

            i_y (Any): Array of pixel indexes along y

        Returns:
            assembled_img(np.ndarray[np.float64]): Assembled 2D image.
        """
        img_reshaped: npt.NDArray[np.float64] = img.reshape(i_x.shape)
        idx_max_y: int = int(np.max(i_x) + 1)
        idx_max_x: int = int(np.max(i_y) + 1)
        assembled_img: npt.NDArray[np.float64] = np.zeros(
            (idx_max_y, idx_max_x), dtype=np.float64
        )
        assembled_img[i_x, i_y] = img_reshaped

        return assembled_img

    def _create_powder_plots(
        self,
        powder_hits: npt.NDArray[np.float64],
        powder_misses: npt.NDArray[np.float64],
        i_x: npt.NDArray[np.uint64],
        i_y: npt.NDArray[np.uint64],
    ) -> pn.Tabs:
        """Create a tabbed display of hits and misses 'powder' plots.

        Args:
            powder_hits (np.ndarray[np.float64]): Total max/sum projection of
                hits across the run.

            powder_misses (np.ndarray[np.float64]): Total max/sum projection of
                misses across the run.

            i_x (Any): Array of pixel indexes along x

            i_y (Any): Array of pixel indexes along y

        Returns:
            tabs (pn.Tabs): Tabbed display of the image plots.
        """
        assembled_powder_hits: npt.NDArray[np.float64] = self._assemble_image(
            img=powder_hits, i_x=i_x, i_y=i_y
        )
        assembled_powder_misses: npt.NDArray[np.float64] = self._assemble_image(
            img=powder_misses, i_x=i_x, i_y=i_y
        )

        grid_hits: pn.GridSpec = pn.GridSpec(
            sizing_mode="stretch_both",
            max_width=700,
            name="epix_4m - Hits",
        )
        dim: hv.Dimension = hv.Dimension(
            ("image", "Hits"),
            range=(
                np.nanpercentile(assembled_powder_hits, 1),
                np.nanpercentile(assembled_powder_hits, 99),
            ),
        )
        grid_hits[0, 0] = pn.Row(
            hv.Image(assembled_powder_hits, vdims=[dim], name=dim.label).options(
                colorbar=True, cmap="rainbow"
            )
        )

        grid_misses: pn.GridSpec = pn.GridSpec(
            sizing_mode="stretch_both",
            max_width=700,
            name="epix_4m - Misses",
        )
        dim = hv.Dimension(
            ("image", "Misses"),
            range=(
                np.nanpercentile(assembled_powder_misses, 1),
                np.nanpercentile(assembled_powder_misses, 99),
            ),
        )
        grid_misses[0, 0] = pn.Row(
            hv.Image(assembled_powder_misses, vdims=[dim], name=dim.label).options(
                colorbar=True, cmap="rainbow"
            )
        )

        tabs: pn.Tabs = pn.Tabs(grid_hits)
        tabs.append(grid_misses)
        return tabs
