"""
A script to compare the impact of data precision (float32 vs. float16)
on SFX peak finding results.

This script processes each event twice: once after casting the calibrated data
to float32, and once after casting to float16. It generates two separate sets of
CXI files and produces a final summary report comparing key statistics like
hit rate, peak counts, image fidelity (MSE), and final file size.

Classes:
    CxiWriter: (Unchanged from /tasks/sfx_find_peaks.py) Utility class for writing peak finding results.
    ComparePeakFindingPrecision: The main task class for performing the comparison.
"""

__all__ = ["CxiWriter", "ComparePeakFindingPrecision"]
__author__ = "Noemie Claret"

import sys
import os
from pathlib import Path
from typing import Any, Dict, List, TextIO, Tuple, Optional, cast, Union

import h5py  # type: ignore
import holoviews as hv  # type: ignore
import numpy
import panel as pn
from mpi4py.MPI import COMM_WORLD, SUM
from numpy.typing import NDArray
from psalgos.pypsalgos import PyAlgos  # type: ignore
from psana import Detector, EventId, MPIDataSource  # type: ignore
from PSCalib import GeometryAccess  # type: ignore

from lute.execution.ipc import Message
from lute.io.models.sfx_find_peaks import FindPeaksPyAlgosParameters
from lute.tasks.task import Task
from lute.tasks.dataclasses import TaskStatus, ElogSummaryPlots

hv.extension("bokeh")
pn.extension()

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
        dtype: type = numpy.float32, # Allow specifying dtype
    ):
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
        keys: List[str] = [
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
            dtype=dtype,  # Use specified dtype
        )
        data_1.attrs["axes"] = "experiment_identifier"
        key: str
        for key in ["powderHits", "powderMisses", "mask"]:
            entry_1.create_dataset(
                f"/entry_1/data_1/{key}",
                (det_shape[0], det_shape[1]),
                chunks=(det_shape[0], det_shape[1]),
                maxshape=(det_shape[0], det_shape[1]),
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
        img: Union[NDArray[numpy.float32], NDArray[numpy.float16]],
        peaks: Any,  # Not typed becomes it comes from psana
        timestamp_seconds: int,
        timestamp_nanoseconds: int,
        timestamp_fiducials: int,
        photon_energy: float,
        clen: float,
    ):
        ch_rows: NDArray[numpy.float64] = (
            peaks[:, 0] * self._raw_det_shape[-2] + peaks[:, 1]
        )
        ch_cols: NDArray[numpy.float64] = peaks[:, 2]

        if self._outh5["/entry_1/data_1/data"].shape[0] <= self._index:
            self._outh5["entry_1/data_1/data"].resize(self._index + 1, axis=0)
            ds_key: str
            for ds_key in self._outh5["/entry_1/result_1"].keys():
                self._outh5[f"/entry_1/result_1/{ds_key}"].resize(
                    self._index + 1, axis=0
                )
            for ds_key in (
                "eventNumber",
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

        peaks_cenx: NDArray[numpy.float64] = (
            self._i_x[
                numpy.array(peaks[:, 0], dtype=numpy.int64),
                numpy.array(peaks[:, 1], dtype=numpy.int64),
                numpy.array(peaks[:, 2], dtype=numpy.int64),
            ]
            + 0.5
            - self._ipx
        )
        peaks_ceny: NDArray[numpy.float64] = (
            self._i_y[
                numpy.array(peaks[:, 0], dtype=numpy.int64),
                numpy.array(peaks[:, 1], dtype=numpy.int64),
                numpy.array(peaks[:, 2], dtype=numpy.int64),
            ]
            + 0.5
            - self._ipy
        )
        peak_radius: NDArray[numpy.float64] = numpy.sqrt(
            (peaks_cenx**2) + (peaks_ceny**2)
        )
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
        powder_hits: NDArray[numpy.float64],
        powder_misses: NDArray[numpy.float64],
        mask: NDArray[numpy.uint16],
    ):
        self._outh5["/entry_1/data_1/powderHits"][:] = powder_hits.reshape(
            -1, powder_hits.shape[-1]
        )
        self._outh5["/entry_1/data_1/powderMisses"][:] = powder_misses.reshape(
            -1, powder_misses.shape[-1]
        )
        self._outh5["/entry_1/data_1/mask"][:] = (1 - mask).reshape(
            -1, mask.shape[-1]
        )

    def optimize_and_close_file(
        self,
        num_hits: int,
        max_peaks: int,
    ):
        # Resize the entry_1 entry
        data_shape: Tuple[int, ...] = self._outh5["/entry_1/data_1/data"].shape
        self._outh5["/entry_1/data_1/data"].resize(
            (num_hits, data_shape[1], data_shape[2])
        )
        self._outh5["/entry_1/result_1/nPeaks"].resize((num_hits,))
        key: str
        for key in [
            "peakXPosRaw", "peakYPosRaw", "rcent", "ccent", "rmin", "rmax",
            "cmin", "cmax", "peakTotalIntensity", "peakMaxIntensity", "peakRadius",
        ]:
            self._outh5[f"/entry_1/result_1/{key}"].resize((num_hits, max_peaks))

        # Resize LCLS entry
        for key in [
            "eventNumber", "machineTime", "machineTimeNanoSeconds", "fiducial",
            "detector_1/EncoderValue", "photon_energy_eV",
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
    fnames: List[Path] = []
    fi: int
    for fi in range(mpi_size):
        if n_hits_per_rank[fi] > 0:
            fnames.append(Path(outdir) / f"{exp}_r{run:0>4}_{fi}{tag}.cxi")
    if len(fnames) == 0:
        print(f"Warning: No hits found for tag '{tag}'. No master file created.")
        return Path() # Return empty path

    dname_list, key_list, shape_list, dtype_list = [], [], [], []
    datasets = ["/entry_1/result_1", "/LCLS/detector_1", "/LCLS", "/entry_1/data_1"]
    f = h5py.File(fnames[0], "r")
    for dname in datasets:
        if dname not in f: continue
        dset = f[dname]
        for key in dset.keys():
            if f"{dname}/{key}" not in datasets:
                dname_list.append(dname)
                key_list.append(key)
                shape_list.append(dset[key].shape)
                dtype_list.append(dset[key].dtype)
    f.close()

    mask: Optional[NDArray[numpy.uint16]] = None
    powder_hits, powder_misses = None, None
    for fn in fnames:
        with h5py.File(fn, "r") as f:
            if mask is None:
                mask = f["entry_1/data_1/mask"][:].copy()
            if powder_hits is None:
                powder_hits = f["entry_1/data_1/powderHits"][:].copy()
                powder_misses = f["entry_1/data_1/powderMisses"][:].copy()
            else:
                powder_hits = numpy.maximum(
                    powder_hits, f["entry_1/data_1/powderHits"][:].copy()
                )
                powder_misses = numpy.maximum(
                    powder_misses, f["entry_1/data_1/powderMisses"][:].copy()
                )

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
                    vsrc = h5py.VirtualSource(
                        fn, dname, shape=(n_hits_per_rank[i],) + shape_list[dnum][1:]
                    )
                    if len(shape_list[dnum]) == 1:
                        layout[cursor : cursor + n_hits_per_rank[i]] = vsrc
                    else:
                        layout[cursor : cursor + n_hits_per_rank[i], :] = vsrc
                    cursor += n_hits_per_rank[i]
                vdf.create_virtual_dataset(dname, layout, fillvalue=-1)

        vdf["entry_1/data_1/powderHits"] = powder_hits
        vdf["entry_1/data_1/powderMisses"] = powder_misses
        vdf["entry_1/data_1/mask"] = mask

    return vfname



class ComparePeakFindingPrecision(Task):
    """
    Task that compares peak finding on float32 vs float16 data.

    It runs the PyAlgos peak finding algorithm on both data types for each
    event, saves the results to separate CXI files, and generates a
    summary report comparing hit rates, peak counts, and file sizes.
    """

    def __init__(
        self, *, params: FindPeaksPyAlgosParameters, use_mpi: bool = True
    ) -> None:
        params.tag = ""
        super().__init__(params=params, use_mpi=use_mpi)

    def total_shard_size_mb(
        self, outdir: str, exp: str, run: int, tag: str, n_hits_per_rank: list[int]
    ) -> float:
        """Return total size (in MiB) of all shard CXI files for a given tag."""
        total = 0
        for i, nh in enumerate(n_hits_per_rank):
            if nh > 0:
                p = Path(outdir) / f"{exp}_r{run:0>4}_{i}{tag}.cxi"
                if p.exists():
                    total += os.path.getsize(p)
        return total / (1024**2)

    def _run(self) -> None:
        self._task_parameters = cast(FindPeaksPyAlgosParameters, self._task_parameters)
        ds: Any = MPIDataSource(
            f"exp={self._task_parameters.lute_config.experiment}:"
            f"run={self._task_parameters.lute_config.run}:smd"
        )
        if self._task_parameters.n_events != 0:
            ds.break_after(self._task_parameters.n_events)

        det: Any = Detector(self._task_parameters.det_name)
        det.do_reshape_2d_to_3d(flag=True)
        evr: Any = Detector(self._task_parameters.event_receiver)

        i_x: Any = det.indexes_x(self._task_parameters.lute_config.run).astype(numpy.int64)
        i_y: Any = det.indexes_y(self._task_parameters.lute_config.run).astype(numpy.int64)
        ipx, ipy = det.point_indexes(self._task_parameters.lute_config.run, pxy_um=(0, 0))

        n_hits_f32: int = 0
        n_hits_f16: int = 0
        num_events: int = 0
        num_empty_images: int = 0
        total_mse = 0.0
        peak_count_diffs = []  

        alg: Optional[PyAlgos] = None
        writer_f32: Optional[CxiWriter] = None
        writer_f16: Optional[CxiWriter] = None
        mask: Optional[NDArray[numpy.uint16]] = None
        powder_hits_f32, powder_misses_f32 = None, None
        powder_hits_f16, powder_misses_f16 = None, None

        evt: Any
        for evt in ds.events():
            num_events += 1
            evt_id: Any = evt.get(EventId)
            timestamp_seconds: int = evt_id.time()[0]
            timestamp_nanoseconds: int = evt_id.time()[1]
            timestamp_fiducials: int = evt_id.fiducials()
            event_codes: Any = evr.eventCodes(evt)

            if isinstance(self._task_parameters.pv_camera_length, float):
                clen: float = self._task_parameters.pv_camera_length
            else:
                clen =(
                    ds.env().epicsStore().value(self._task_parameters.pv_camera_length)
                )

            if self._task_parameters.event_logic:
                if self._task_parameters.event_code not in event_codes:
                    continue

            img_calib: Any = det.calib(evt)

            if img_calib is None:
                num_empty_images += 1
                print(f"Warning: Empty image for event {num_events}, processed on {ds.rank}. Skipping.", file=sys.stderr)
                continue

            if alg is None:
                det_shape: Tuple[int, ...] = img_calib.shape
                if len(det_shape) == 3:
                    det_shape = (det_shape[0] * det_shape[1], det_shape[2])
                else:
                    det_shape = img_calib.shape

                mask: NDArray[numpy.uint16] = numpy.ones(det_shape).astype(numpy.uint16)

                if self._task_parameters.psana_mask:
                    mask = det.mask(
                        int(self._task_parameters.lute_config.run), 
                        calib=False, 
                        status=True,
                        edges=False, 
                        centra=False, 
                        unbond=False, 
                        unbondnbrs=False
                    ).astype(numpy.uint16)

                hdffh: Any
                if self._task_parameters.mask_file is not None:
                    with h5py.File(self._task_parameters.mask_file, "r") as hdffh:
                        loaded_mask: NDArray[numpy.int64] = hdffh[
                            "entry_1/data_1/mask"
                        ][:]
                        mask *= loaded_mask.astype(numpy.uint16)
                
                common_writer_args = {
                    "outdir": self._task_parameters.outdir,
                    "rank": ds.rank,
                    "exp": self._task_parameters.lute_config.experiment,
                    "run": int(self._task_parameters.lute_config.run),
                    "n_events": self._task_parameters.n_events,
                    "det_shape": det_shape,
                    "raw_det_shape": img_calib.shape,
                    "i_x": i_x, 
                    "i_y": i_y, 
                    "ipx": ipx, 
                    "ipy": ipy,
                    "min_peaks": self._task_parameters.min_peaks,
                    "max_peaks": self._task_parameters.max_peaks,
                }

                writer_f32 = CxiWriter(**common_writer_args, tag="_f32", dtype=numpy.float32)
                writer_f16 = CxiWriter(**common_writer_args, tag="_f16", dtype=numpy.float16)

                alg = PyAlgos(mask=mask, pbits=0)
                alg.set_peak_selection_pars(
                    npix_min=self._task_parameters.npix_min, 
                    npix_max=self._task_parameters.npix_max,
                    amax_thr=self._task_parameters.amax_thr, 
                    atot_thr=self._task_parameters.atot_thr,
                    son_min=self._task_parameters.son_min,
                )

                powder_hits_f32: NDArray[numpy.float64] = numpy.zeros(det_shape)
                powder_misses_f32: NDArray[numpy.float64] = numpy.zeros(det_shape)
                powder_hits_f16: NDArray[numpy.float64] = numpy.zeros(det_shape)
                powder_misses_f16: NDArray[numpy.float64] = numpy.zeros(det_shape)

            img_f32 = img_calib.astype(numpy.float32)
            img_f16 = img_calib.astype(numpy.float16)

            mse = numpy.mean((img_f32 - img_f16.astype(numpy.float32)) ** 2)
            total_mse += mse
            

            peaks_f32 = alg.peak_finder_v3r3(
                img_f32, 
                rank=self._task_parameters.peak_rank, 
                r0=self._task_parameters.r0,
                dr=self._task_parameters.dr,
                nsigm=self._task_parameters.nsigm
            )

            if (self._task_parameters.min_peaks <= peaks_f32.shape[0]) and (
                peaks_f32.shape[0] <= self._task_parameters.max_peaks
            ):
                photon_energy: float
                try:
                    photon_energy = Detector("EBeam").get(evt).ebeamPhotonEnergy() 
                    if numpy.isinf(photon_energy):
                        raise ValueError
                except (AttributeError, ValueError):
                    photon_energy = (
                        1.23984197386209e-06
                        / ds.env().epicsStore().value("SIOC:SYS0:ML00:AO192")
                    ) * 1e9

                writer_f32.write_event(
                    img=img_f32, 
                    peaks=peaks_f32, 
                    timestamp_seconds=timestamp_seconds,
                    timestamp_nanoseconds=timestamp_nanoseconds,
                    timestamp_fiducials=timestamp_fiducials,
                    photon_energy=photon_energy,
                    clen=clen
                )
                n_hits_f32 += 1

            reshaped_img: NDArray[numpy.float32] = None 
            reshaped_img = img_f32.reshape(-1, img_f32.shape[-1])

            if peaks_f32.shape[0] >= self._task_parameters.min_peaks:
                powder_hits_f32 = numpy.maximum(
                    powder_hits_f32, reshaped_img
                )
            else:
                powder_misses_f32 = numpy.maximum(
                    powder_misses_f32, reshaped_img
                )

            img_f16_cast_back: NDArray[numpy.float32] = img_f16.astype(numpy.float32)
            peaks_f16 = alg.peak_finder_v3r3(
                img_f16_cast_back,
                rank=self._task_parameters.peak_rank, 
                r0=self._task_parameters.r0,
                dr=self._task_parameters.dr,
                nsigm=self._task_parameters.nsigm
            )

            if (self._task_parameters.min_peaks <= peaks_f16.shape[0]) and (
                peaks_f16.shape[0] <= self._task_parameters.max_peaks
            ):
                photon_energy: float
                try:
                    photon_energy = Detector("EBeam").get(evt).ebeamPhotonEnergy() 
                    if numpy.isinf(photon_energy):
                        raise ValueError
                except (AttributeError, ValueError):
                    photon_energy = (
                        1.23984197386209e-06
                        / ds.env().epicsStore().value("SIOC:SYS0:ML00:AO192")
                    ) * 1e9

                writer_f16.write_event(
                    img=img_f16,
                    peaks=peaks_f16,
                    timestamp_seconds=timestamp_seconds,
                    timestamp_nanoseconds=timestamp_nanoseconds,
                    timestamp_fiducials=timestamp_fiducials,
                    photon_energy=photon_energy,
                    clen=clen
                )
                n_hits_f16 += 1

            reshaped_img: NDArray[numpy.float16] = None
            reshaped_img = img_f16.reshape(-1, img_f16.shape[-1])

            if peaks_f16.shape[0] >= self._task_parameters.min_peaks:
                powder_hits_f16 = numpy.maximum(
                    powder_hits_f16, reshaped_img
                )
            else:
                powder_misses_f16 = numpy.maximum(
                    powder_misses_f16, reshaped_img
                )

            
            peak_count_diffs.append(peaks_f32.shape[0] - peaks_f16.shape[0])

        if num_empty_images > 0:
            msg: Message = Message(
                contents=f"Rank {ds.rank} encountered {num_empty_images} empty images."
            )
            self._report_to_executor(msg)

        if writer_f32 and writer_f16:
            writer_f32.write_non_event_data(
                powder_hits=powder_hits_f32,
                powder_misses=powder_misses_f32,
                mask=mask
            )
            writer_f32.optimize_and_close_file(
                num_hits=n_hits_f32, 
                max_peaks=self._task_parameters.max_peaks
            )

            writer_f16.write_non_event_data(
                powder_hits=powder_hits_f16,
                powder_misses=powder_misses_f16,
                mask=mask
            )
            writer_f16.optimize_and_close_file(
                num_hits=n_hits_f16,
                max_peaks=self._task_parameters.max_peaks
            )

        COMM_WORLD.Barrier()

        num_hits_f32_per_rank: List[int] = cast(
            List[int], COMM_WORLD.gather(n_hits_f32, root=0)
        )
        num_hits_f16_per_rank: List[int] = cast(
            List[int], COMM_WORLD.gather(n_hits_f16, root=0)
        )
        total_hits_f32: int = cast(int, COMM_WORLD.reduce(n_hits_f32, SUM, root=0))
        total_hits_f16: int = cast(int, COMM_WORLD.reduce(n_hits_f16, SUM, root=0))

        total_events: int = cast(int, COMM_WORLD.reduce(num_events, SUM, root=0))
        total_mse_reduced: float = cast(float, COMM_WORLD.reduce(total_mse, SUM, root=0))
        all_peak_count_diffs: List[list] = cast(List[list], COMM_WORLD.gather(peak_count_diffs, root=0))


        if ds.rank == 0:
            master_f32 = write_master_file(
                mpi_size=ds.size, 
                outdir=self._task_parameters.outdir,
                exp=self._task_parameters.lute_config.experiment,
                run=int(self._task_parameters.lute_config.run), 
                tag="_f32",
                n_hits_per_rank=num_hits_f32_per_rank, 
                n_hits_total=total_hits_f32,
            )
            master_f16 = write_master_file(
                mpi_size=ds.size, 
                outdir=self._task_parameters.outdir,
                exp=self._task_parameters.lute_config.experiment,
                run=int(self._task_parameters.lute_config.run), 
                tag="_f16",
                n_hits_per_rank=num_hits_f16_per_rank,
                n_hits_total=total_hits_f16,
            )
        

        size_f32_local = self.total_shard_size_mb(
            self._task_parameters.outdir,
            self._task_parameters.lute_config.experiment,
            int(self._task_parameters.lute_config.run),
            "_f32",
            [n_hits_f32 if i == ds.rank else 0 for i in range(ds.size)]
        )
        size_f16_local = self.total_shard_size_mb(
            self._task_parameters.outdir,
            self._task_parameters.lute_config.experiment,
            int(self._task_parameters.lute_config.run),
            "_f16",
            [n_hits_f16 if i == ds.rank else 0 for i in range(ds.size)]
        )

        total_size_f32 = cast(float, COMM_WORLD.reduce(size_f32_local, op=SUM, root=0))
        total_size_f16 = cast(float, COMM_WORLD.reduce(size_f16_local, op=SUM, root=0))
       
        if ds.rank == 0:
            summary_path: str = Path(self._task_parameters.outdir) / "comparison.summary"
            f: TextIO
            with open(summary_path, "w") as f:
                print("=" * 50, file=f)
                print("Peak Finding Precision Comparison: float32 vs float16", file=f)
                print("=" * 50, file=f)
                print(file=f)  

                print(f"Total events processed: {total_events}", file=f)
                print(file=f)

                print("--- Hit Finding ---", file=f)
                print("               |  float32  |  float16  ", file=f)
                print("---------------|-----------|-----------", file=f)
                print(f"Total Hits     | {total_hits_f32:<9} | {total_hits_f16:<9}", file=f)

                hit_rate_f32: float = total_hits_f32 / total_events if total_events > 0 else 0.0
                hit_rate_f16: float = total_hits_f16 / total_events if total_events > 0 else 0.0
                print(f"Hit Rate       | {hit_rate_f32:<9.3f} | {hit_rate_f16:<9.3f}", file=f)
                print(file=f)

                print("--- Image Fidelity ---", file=f)
                avg_mse: float = total_mse_reduced / total_events if total_events > 0 else 0.0
                print(f"Average Mean Squared Error (f32 vs f16): {avg_mse:.6e}", file=f)
                print(file=f)

                print("--- Peak Count Difference (n_peaks_f32 - n_peaks_f16) ---", file=f)
                flat_diffs_list: List[int] = [diff for sublist in all_peak_count_diffs for diff in sublist]
                flat_diffs: NDArray[numpy.float32] = numpy.array(flat_diffs_list, dtype=numpy.float32)

                if len(flat_diffs) > 0:
                    mismatched_events: int = numpy.count_nonzero(flat_diffs)
                    print(f"Mean difference:   {numpy.mean(flat_diffs):.4f}", file=f)
                    print(f"Std deviation:     {numpy.std(flat_diffs):.4f}", file=f)
                    print(f"Min difference:    {numpy.min(flat_diffs):.0f}", file=f)
                    print(f"Max difference:    {numpy.max(flat_diffs):.0f}", file=f)
                    print(f"Events with different peak counts: {mismatched_events} "
                          f"({mismatched_events/total_events:.2%})", file=f)
                else:
                    print("No events processed to compute peak differences.", file=f)
                print(file=f)

                print("--- Data Volume ---", file=f)
                print(f"Master file size (float32): {total_size_f32:.2f} MiB", file=f)
                print(f"Master file size (float16): {total_size_f16:.2f} MiB", file=f)

                if total_size_f32 > 0:
                    reduction: float = (1 - total_size_f16 / total_size_f32) * 100
                    print(f"Size reduction with float16: {reduction:.1f}%", file=f)
                print(file=f)

                print(f"Output master file (f32): {master_f32}", file=f)
                print(f"Output master file (f16): {master_f16}", file=f)

            
            with open(Path(self._task_parameters.out_file), "w") as f:
                print(f"{master_f32}", file=f)
            
            
            with h5py.File(master_f32, "r") as f:
                final_powder_hits: NDArray[numpy.float64] = f[
                    "entry_1/data_1/powderHits"
                    ][:]
                final_powder_misses: NDArray[numpy.float64] = f[
                    "entry_1/data_1/powderMisses"
                    ][:]
                f.close()

            powder_plots: pn.Tabs = self._create_powder_plots(
                det, final_powder_hits, final_powder_misses
                )
            text_summary = {
                "f32 Hits": str(total_hits_f32), 
                "f16 Hits": str(total_hits_f16),
                "Hit Rate (f32)": f"{hit_rate_f32:.3f}", 
                "Hit Rate (f16)": f"{hit_rate_f16:.3f}",
                "Avg. MSE": f"{avg_mse:.4e}"
            }
            self._result.summary = (
                text_summary, 
                ElogSummaryPlots(
                    f"r{self._task_parameters.lute_config.run}/powders_f32", powder_plots
                )
            )

            with open(Path(self._task_parameters.out_file), "w") as f:
                print(f"{master_f32}", file=f)

            with open(Path(self._task_parameters.out_file), "w") as f:
                print(f"{master_f16}", file=f)

    def _post_run(self) -> None:
        super()._post_run()
        self._result.task_status = TaskStatus.COMPLETED

    # Unchanged.
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

    def _create_powder_plots(self, det: Detector, powder_hits: NDArray[numpy.float64], powder_misses: NDArray[numpy.float64]) -> pn.Tabs:
        self._task_parameters = cast(FindPeaksPyAlgosParameters, self._task_parameters)
        assembled_powder_hits: NDArray[numpy.float64] = self._assemble_image(det, powder_hits)
        assembled_powder_misses: NDArray[numpy.float64] = self._assemble_image(det, powder_misses)
        
        grid_hits = pn.GridSpec(sizing_mode="stretch_both", max_width=700, name=f"{self._task_parameters.det_name} - Hits")
        dim = hv.Dimension(("image", "Hits"), range=(numpy.nanpercentile(assembled_powder_hits, 1), numpy.nanpercentile(assembled_powder_hits, 99)))
        grid_hits[0, 0] = pn.Row(hv.Image(assembled_powder_hits, vdims=[dim], name=dim.label).options(colorbar=True, cmap="rainbow"))
        
        grid_misses = pn.GridSpec(sizing_mode="stretch_both", max_width=700, name=f"{self._task_parameters.det_name} - Misses")
        dim = hv.Dimension(("image", "Misses"), range=(numpy.nanpercentile(assembled_powder_misses, 1), numpy.nanpercentile(assembled_powder_misses, 99)))
        grid_misses[0, 0] = pn.Row(hv.Image(assembled_powder_misses, vdims=[dim], name=dim.label).options(colorbar=True, cmap="rainbow"))
        
        tabs = pn.Tabs(grid_hits)
        tabs.append(grid_misses)
        return tabs