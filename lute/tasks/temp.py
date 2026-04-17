"""
Classes for peak finding tasks in SFX.

Classes:
    DrFindPeaksPyAlgos: peak finding using psana's PyAlgos algorithm. Optional data
        compression and decompression with libpressio for data reduction tests.
"""

__all__ = ["DrFindPeaksPyAlgos"]
__author__ = "Noemie Claret"

import sys
from pathlib import Path
from typing import Any, Dict, List, Literal, TextIO, Tuple, Optional, cast, Union
import os

import h5py 
import numpy
import panel as pn
from mpi4py.MPI import COMM_WORLD, SUM
from numpy.typing import NDArray
from psalgos.pypsalgos import PyAlgos  # type: ignore
from psana import Detector, EventId, MPIDataSource  # type: ignore
from PSCalib import GeometryAccess  # type: ignore


####
import numpy as np
import random
import json

####

from lute.execution.ipc import Message
from lute.io.models.sfx_dr_find_peaks import DrFindPeaksPyAlgosParameters
from lute.tasks.task import Task
from lute.tasks.dataclasses import TaskStatus, ElogSummaryPlots
from lute.tasks.sfx_find_peaks import CxiWriter, write_master_file, generate_libpressio_configuration, add_peaks_to_libpressio_configuration

from lute.DrAlgo import import_dr_algo


class DrFindPeaksPyAlgos(Task):
    """
    Task that compresses images, benchmarks speed/bandwidth and performs peak finding using the PyAlgos peak finding algorithms and
    writes the peak information to CXI files.
    """

    def __init__(
        self, *, 
        params: DrFindPeaksPyAlgosParameters, 
        use_mpi: bool = True
    ) -> None:
        super().__init__(params=params, use_mpi=use_mpi)
    

    def _run(self) -> None:
        self._task_parameters = cast(DrFindPeaksPyAlgosParameters, self._task_parameters)
        ENABLE_ELOG: bool = False#os.getenv("LUTE_ENABLE_ELOG", "0") == "1"
        ds: Any = MPIDataSource(
            f"exp={self._task_parameters.lute_config.experiment}:"
            f"run={self._task_parameters.lute_config.run}:smd"
        )
        if self._task_parameters.n_events != 0:
            ds.break_after(self._task_parameters.n_events)

        det: Any = Detector(self._task_parameters.det_name)
        det.do_reshape_2d_to_3d(flag=True)

        evr: Any = Detector(self._task_parameters.event_receiver)

        i_x: Any = det.indexes_x(self._task_parameters.lute_config.run).astype(
            numpy.int64
        )
        i_y: Any = det.indexes_y(self._task_parameters.lute_config.run).astype(
            numpy.int64
        )
        ipx: Any
        ipy: Any
        ipx, ipy = det.point_indexes(
            self._task_parameters.lute_config.run, pxy_um=(0, 0)
        )

        alg: Any = None
        num_hits: int = 0
        num_events: int = 0
        num_empty_images: int = 0
        tag: str = self._task_parameters.tag
        if (tag != "") and (tag[0] != "_"):
            tag = "_" + tag

        

        ##############
        rank_fracs = np.arange(0.01, 0.1001, 0.01)
        total_events_target: int = 2
        max_saved_events = 10
        saved_event_indices: List[int] = []
        ##############
        RECON_FULL_IMAGE = False
        evt: Any
        for evt in ds.events():
            ########
            
            num_events += 1
            print("adding one event")

            # default: do not save this event
            save_this_event = False

            if num_events > total_events_target:
                print(f"breaking after {total_events_target} events for testing")
                break

            # reservoir-type sampling over the first total_events_target events
            if len(saved_event_indices) < max_saved_events:
                remaining_slots = max_saved_events - len(saved_event_indices)
                remaining_events = total_events_target - num_events + 1
                prob = remaining_slots / remaining_events
                if random.random() < prob:
                    save_this_event = True
                    saved_event_indices.append(num_events)

            print(f"event {num_events}: save_this_event={save_this_event}")

    ######################

            evt_id: Any = evt.get(EventId)
            timestamp_seconds: int = evt_id.time()[0]
            timestamp_nanoseconds: int = evt_id.time()[1]
            timestamp_fiducials: int = evt_id.fiducials()
            event_codes: Any = evr.eventCodes(evt)

            if isinstance(self._task_parameters.pv_camera_length, float):
                clen: float = self._task_parameters.pv_camera_length
            else:
                clen = (
                    ds.env().epicsStore().value(self._task_parameters.pv_camera_length)
                )

            if self._task_parameters.event_logic:
                if self._task_parameters.event_code not in event_codes:
                    continue
            
            img: Any = det.calib(evt)

            #if img is not None:
                #img = np.asarray(img, dtype=numpy.float16)
                #img = img.astype(numpy.float32, copy=False)

            if img is None:
                num_empty_images += 1
                continue
            dr_method : Any = None
            if self._task_parameters.dr_method:
                AlgoClass = import_dr_algo(self._task_parameters.dr_method)
                dr_algo = AlgoClass()
                dr_algo.set_params(
                    n_components=self._task_parameters.n_components,
                    tol=self._task_parameters.tol,
                    gamma=self._task_parameters.gamma,
                    max_iter=self._task_parameters.max_iter,
                    size=self._task_parameters.size,
                    dr_component=self._task_parameters.dr_component
                )
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
                        status=False,
                        edges=True,
                        central=True,
                        unbond=True,
                        unbondnbrs=False
                    ).astype(numpy.uint16)
                    

            #set target rank
            #####
            for r_frac in rank_fracs:
                P, H, W = img.shape
                desired = int(max(1, round(r_frac * min(H, W))))
                k = min(desired, min(H, W) - 1)  
                self._task_parameters.n_components = k

                recon_img_full: Any = numpy.zeros(img.shape)
                orig_img_full: Any = numpy.zeros(img.shape)
                S_img_full: Any = numpy.zeros(img.shape)
                r_tag = f"{r_frac:.2f}".replace(".", "p")
                base_root = "/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/analysis/DrCompTest3"
                base_dir = os.path.join(base_root, f"saved_rpca_r{r_tag}")
                print(f"target rank fraction r={r_frac:.2f}, k={k}")
                print(f"[Event {evt_id}] P={P}, H={H}, W={W}")


                #######


                reconstructed_img: Any = numpy.zeros(img.shape)
                comp = self._task_parameters.dr_component
                assert img.ndim == 3
                P, H, W = img.shape

                for p in range(P):
                    try: 
                        ####
                            
                        img[p, :, :]
                        '''
                        dr_algo.fit(img[p, :, :])

                        if comp == "S":
                            reconstructed_img[p] = dr_algo.sparse_
                        elif comp == "L+S":
                            reconstructed_img[p] = dr_algo.reconstruct()
                        '''

                        #########
                        if mask.ndim == 3:
                            panel_mask = mask[p, :, :]
                        else:
                            panel_mask = mask

                        panel_mask_bool = (panel_mask == 1)

                        panel_mask_bool = (panel_mask == 1)

                        # original panel (keep full frame)
                        orig_img = img[p, :, :].astype(np.float32, copy=False)

                        # rows/cols that contain at least one good pixel
                        row_good = panel_mask_bool.any(axis=1)  # shape (H,)
                        col_good = panel_mask_bool.any(axis=0)  # shape (W,)

                        # convert to index lists
                        row_idx = np.where(row_good)[0]
                        col_idx = np.where(col_good)[0]

                        # if nothing or too little is good, skip DR on this panel
                        if row_idx.size < 2 or col_idx.size < 2:
                            print(f"[Frame {p}] Skipping DR: not enough unmasked rows/cols")
                            orig_img_full[p] = orig_img
                            recon_img_full[p] = orig_img
                            S_img_full[p] = np.zeros_like(orig_img)
                            continue

                        # compacted image: all unmasked rows/cols stitched together
                        comp_img = orig_img[np.ix_(row_idx, col_idx)]
                        H_comp, W_comp = comp_img.shape

                        base_beta = 1.0 / (2.0 * (H_comp * W_comp) ** 0.25)
                        beta_factors = [40]
                        betas = [round(base_beta * f, 12) for f in beta_factors]

                        for beta in betas:

                            beta_tag = f"b{beta:.3g}".replace(".", "p").replace("-", "m")
                            save_dir = os.path.join(base_dir, f"beta_{beta_tag}")
                            os.makedirs(save_dir, exist_ok=True)

                            #adding this to save raw frames
                            save_orig_img_path = os.path.join(
                                    save_dir, f"orig_frame_{p}_{evt_id}.npy"
                                )

                            if self._task_parameters.dr_method:
                                AlgoClass = import_dr_algo(self._task_parameters.dr_method)
                                dr_algo = AlgoClass()

                                # effective rank must satisfy 0 < k < min(comp_img.shape)
                                k_global = self._task_parameters.n_components
                                min_dim = min(H_comp, W_comp)
                                k_eff = min(k_global, max(1, min_dim - 1))

                                dr_algo.set_params(
                                    n_components=k_eff,
                                    tol=self._task_parameters.tol,
                                    gamma=self._task_parameters.gamma,
                                    max_iter=self._task_parameters.max_iter,
                                    size=self._task_parameters.size,
                                    dr_component=self._task_parameters.dr_component,
                                    beta=beta,
                                )

                            # --- DR on compacted good region only ---
                            dr_algo.fit(comp_img)

                            recon_comp = dr_algo.reconstruct()
                            S_comp = dr_algo.sparse_
                            L_comp = dr_algo.low_rank_



                            # --- build back full-size images ---
                            if RECON_FULL_IMAGE:
                                # Keep original values in masked region
                                recon_full = orig_img.copy()
                            else:
                                # Zero (or NaN) out masked region; only unmasked region has DR content
                                recon_full = np.zeros_like(orig_img)
                                # or, if you prefer NaNs:
                                # recon_full = np.full_like(orig_img, np.nan)

                            S_full = np.zeros_like(orig_img)
                            L_full = np.zeros_like(orig_img)

                            # Fill only the unmasked rows/cols
                            recon_full[np.ix_(row_idx, col_idx)] = recon_comp
                            if S_comp is not None:
                                S_full[np.ix_(row_idx, col_idx)] = S_comp
                            if L_comp is not None:
                                L_full[np.ix_(row_idx, col_idx)] = L_comp

                            # store per-frame outputs
                            recon_img_full[p] = recon_full
                            S_img_full[p]     = S_full
                            L_img             = L_full  # for stats/saving below


                            U = dr_algo.U_
                            V = dr_algo.V_
                            Sigma = dr_algo.Sigma_

                            if save_this_event:
                                save_orig_img_path = os.path.join(
                                    save_dir, f"orig_frame_{p}_{evt_id}.npy"
                                )
                                save_L_img_path = os.path.join(
                                    save_dir, f"L_frame_{p}_{evt_id}.npy"
                                )
                                save_S_img_path = os.path.join(
                                    save_dir, f"S_frame_{p}_{evt_id}.npy"
                                )
                                save_recon_img_path = os.path.join(
                                    save_dir, f"recon_frame_{p}_{evt_id}.npy"
                                )
                                np.save(save_orig_img_path, orig_img)
                                np.save(save_recon_img_path, recon_full)
                                np.save(save_S_img_path, S_full)
                                np.save(save_L_img_path, L_full)

                            sparse_stats = None
                            if S_full is not None:
                                nnz = int(np.count_nonzero(S_full))
                                val_bytes = nnz * S_full.dtype.itemsize

                                index_dtype = np.uint16
                                index_bytes = nnz * (np.dtype(index_dtype).itemsize * 2)
                                sparse_stats = {
                                    "shape": list(S_full.shape),
                                    "dtype": str(S_full.dtype),
                                    "nnz": nnz,
                                    "value_bytes": int(val_bytes),
                                    "index_bytes_estimate": int(index_bytes),
                                    "total_sparse_bytes_estimate": int(val_bytes + index_bytes),
                                    "density": float(nnz / S_full.size) if S_full.size else 0.0,
                                }
                                save_S_stats_path = os.path.join(
                                    save_dir, f"S_stats_{p}_{evt_id}.json"
                                )
                                with open(save_S_stats_path, "w") as fh:
                                    json.dump(sparse_stats, fh, indent=2)


                            if L_img is not None:
                                lr_stats = None
                                if (U is not None) and (Sigma is not None) and (V is not None):
                                    bytes_U = U.size * U.dtype.itemsize
                                    bytes_V = V.size * V.dtype.itemsize
                                    bytes_S = Sigma.size * Sigma.dtype.itemsize
                                    lr_stats = {
                                        "type": "USV",
                                        "U_shape": list(U.shape),
                                        "Sigma_shape": list(Sigma.shape),
                                        "V_shape": list(V.shape),
                                        "U_bytes": int(bytes_U),
                                        "Sigma_bytes": int(bytes_S),
                                        "V_bytes": int(bytes_V),
                                        "total_lr_bytes": int(bytes_U + bytes_V + bytes_S),
                                        "rank": int(Sigma.shape[0]),
                                        "dtype_U": str(U.dtype),
                                        "dtype_V": str(V.dtype),
                                        "dtype_Sigma": str(Sigma.dtype),
                                    }
                                    save_L_stats_path = os.path.join(
                                        save_dir, f"L_stats_{p}_{evt_id}.json"
                                    )
                                    with open(save_L_stats_path, "w") as fh:
                                        json.dump(lr_stats, fh, indent=2)

                                print(f"p is :{p}")

                            
                                hdffh: Any
                                if self._task_parameters.mask_file is not None:
                                    with h5py.File(self._task_parameters.mask_file, "r") as hdffh:
                                        loaded_mask: NDArray[numpy.int64] = hdffh[
                                            "entry_1/data_1/mask"
                                        ][:]
                                        mask *= loaded_mask.astype(numpy.uint16)

                                
                                alg = PyAlgos(mask=mask, pbits=0)  # pbits controls verbosity
                                alg.set_peak_selection_pars(
                                    npix_min=self._task_parameters.npix_min,
                                    npix_max=self._task_parameters.npix_max,
                                    amax_thr=self._task_parameters.amax_thr,
                                    atot_thr=self._task_parameters.atot_thr,
                                    son_min=self._task_parameters.son_min,
                                )

                            peaks_orig: Any = alg.peak_finder_v3r3(
                                orig_img_full,
                                rank=self._task_parameters.peak_rank,
                                r0=self._task_parameters.r0,
                                dr=self._task_parameters.dr,
                                nsigm=self._task_parameters.nsigm,
                            )

                            peaks_S: Any = alg.peak_finder_v3r3(
                                S_img_full,
                                rank=self._task_parameters.peak_rank,
                                r0=self._task_parameters.r0,
                                dr=self._task_parameters.dr,
                                nsigm=self._task_parameters.nsigm,
                            )

                            peaks_recon: Any = alg.peak_finder_v3r3(
                                recon_img_full,
                                rank=self._task_parameters.peak_rank,
                                r0=self._task_parameters.r0,
                                dr=self._task_parameters.dr,
                                nsigm=self._task_parameters.nsigm,
                            )
                            
                            def _to_yx(pk: Any) -> np.ndarray:
                                pk = np.asarray(pk)
                                if pk.size == 0:
                                    return np.zeros((0, 2), dtype=np.int64)
                                if pk.ndim == 1:
                                    pk = pk.reshape(1, -1)
                                if pk.shape[1] >= 3:          
                                    yx = pk[:, 1:3]
                                else:                       
                                    yx = pk[:, 0:2]
                                return yx.astype(np.int64, copy=False)

                            if save_this_event:
                                np.save(
                                    os.path.join(save_dir, f"peaks_orig_frame_{p}_{evt_id}.npy"),
                                    _to_yx(peaks_orig),
                                )
                                np.save(
                                    os.path.join(save_dir, f"peaks_S_frame_{p}_{evt_id}.npy"),
                                    _to_yx(peaks_S),
                                )
                                np.save(
                                    os.path.join(save_dir, f"peaks_recon_frame_{p}_{evt_id}.npy"),
                                    _to_yx(peaks_recon),
                                )
                        print(f"p is :{p}")
                        ###########


                    except Exception as e:  
                        print(f"[Frame {p}] Error during fit: {e}")
                        reconstructed_img[p] = img[p, :, :]
                        save_dir = "/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/analysis/DrComp/failed_frames"
                        os.makedirs(save_dir, exist_ok=True)
                        bad_img_path = os.path.join(save_dir, f"failed_frame_{p}.npy")
                        np.save(bad_img_path, img[p, :, :])
                        print(f"Saved failed frame to {bad_img_path}")

        '''

                img = reconstructed_img

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

                hdffh: Any
                if self._task_parameters.mask_file is not None:
                    with h5py.File(self._task_parameters.mask_file, "r") as hdffh:
                        loaded_mask: NDArray[numpy.int64] = hdffh[
                            "entry_1/data_1/mask"
                        ][:]
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
                alg = PyAlgos(mask=mask, pbits=0)  # pbits controls verbosity
                alg.set_peak_selection_pars(
                    npix_min=self._task_parameters.npix_min,
                    npix_max=self._task_parameters.npix_max,
                    amax_thr=self._task_parameters.amax_thr,
                    atot_thr=self._task_parameters.atot_thr,
                    son_min=self._task_parameters.son_min,
                )

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

                if self._task_parameters.compression is not None:
                    from libpressio import PressioCompressor  # type: ignore

                    libpressio_config_with_peaks = (
                        add_peaks_to_libpressio_configuration(libpressio_config, peaks)
                    )
                    compressor = PressioCompressor.from_config(
                        libpressio_config_with_peaks
                    )
                    compressed_img = compressor.encode(img)
                    decompressed_img = numpy.zeros_like(img)
                    _ = compressor.decode(compressed_img, decompressed_img)
                    img = decompressed_img

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

            # TODO: Fix bug here
            # generate / update powders
            if peaks.shape[0] >= self._task_parameters.min_peaks:
                powder_hits = numpy.maximum(
                    powder_hits,
                    img.reshape(-1, img.shape[-1]),
                )
            else:
                powder_misses = numpy.maximum(
                    powder_misses,
                    img.reshape(-1, img.shape[-1]),
                )

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

        file_writer.optimize_and_close_file(
            num_hits=num_hits, max_peaks=self._task_parameters.max_peaks
        )

        COMM_WORLD.Barrier()

        num_hits_per_rank: List[int] = cast(
            List[int], COMM_WORLD.gather(num_hits, root=0)
        )
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
                final_powder_hits: NDArray[numpy.float64] = f[
                    "entry_1/data_1/powderHits"
                ][:]
                final_powder_misses: NDArray[numpy.float64] = f[
                    "entry_1/data_1/powderMisses"
                ][:]
                f.close()

            if ENABLE_ELOG:
                powder_plots: pn.Tabs = self._create_powder_plots(
                    det, final_powder_hits, final_powder_misses
                )
                text_summary: Dict[str, str] = {
                    "Number of events processed": str(num_events_total),
                    "Number of hits found": str(num_hits_total),
                    "Fractional hit rate": f"{num_hits_total/num_events_total:.2f}",
                }
                self._result.summary = (
                    text_summary,
                    ElogSummaryPlots(
                        f"r{self._task_parameters.lute_config.run}/powders", powder_plots
                    ),
                )
            with open(Path(self._task_parameters.out_file), "w") as f:
                print(f"{master_fname}", file=f)

            # Write out_file 
            '''

    def _post_run(self) -> None:
        super()._post_run()
        self._result.task_status = TaskStatus.COMPLETED

    def _assemble_image(
        self, det: Detector, img: NDArray[numpy.float64]
    ) -> NDArray[numpy.float64]:
        """Assemble an image based on psana geometry.

        Args:
            det (psana.Detector): The detector object for the associated image.
                Used to access the geometry.

            img (numpy.ndarray[np.float64]): The image to assemble. Should
                generally be of shape (n_panels, ss, fs)

        Returns:
            assembled_img(numpy.ndarray[np.float64]): Assembled 2D image.
        """
        geom: GeometryAccess = det.geometry(self._task_parameters.lute_config.run)
        tmp: Tuple[NDArray[numpy.uint64], ...] = geom.get_pixel_coord_indexes()
        pixel_map: NDArray[numpy.uint64] = numpy.zeros(
            tmp[0].shape[1:] + (2,), dtype=numpy.uint64
        )
        pixel_map[..., 0] = tmp[0][0]
        pixel_map[..., 1] = tmp[1][0]
        unflattened_img: NDArray[numpy.float64] = img.reshape(pixel_map.shape[:-1])
        idx_max_y: int = int(numpy.max(pixel_map[..., 0]) + 1)  # Adding one
        idx_max_x: int = int(numpy.max(pixel_map[..., 1]) + 1)  # casts to float
        assembled_img: NDArray[numpy.float64] = numpy.zeros((idx_max_y, idx_max_x))
        assembled_img[pixel_map[..., 0], pixel_map[..., 1]] = unflattened_img

        return assembled_img

    def _create_powder_plots(
        self,
        det: Detector,
        powder_hits: NDArray[numpy.float64],
        powder_misses: NDArray[numpy.float64],
    ) -> Any:
        """Create a tabbed display of hits and misses 'powder' plots.

        Args:
            det (psana.Detector): The detector object for the associated image.
                Used to access the geometry.

            powder_hits (numpy.ndarray[np.float64]): Total max/sum projection of
                hits across the run.

            powder_misses (numpy.ndarray[np.float64]): Total max/sum projection of
                misses across the run.

        Returns:
            tabs (pn.Tabs): Tabbed display of the image plots.
        """
        self._task_parameters = cast(DrFindPeaksPyAlgosParameters, self._task_parameters)

        from mpi4py import MPI

        if MPI.COMM_WORLD.Get_rank() != 0:
            return None

        import holoviews as hv  # type: ignore
        import panel as pn      # type: ignore
        hv.extension("bokeh")
        pn.extension()

        assembled_powder_hits: NDArray[numpy.float64] = self._assemble_image(
            det, powder_hits
        )
        assembled_powder_misses: NDArray[numpy.float64] = self._assemble_image(
            det, powder_misses
        )

        grid_hits: pn.GridSpec = pn.GridSpec(
            sizing_mode="stretch_both",
            max_width=700,
            name=f"{self._task_parameters.det_name} - Hits",
        )
        dim: hv.Dimension = hv.Dimension(
            ("image", "Hits"),
            range=(
                numpy.nanpercentile(assembled_powder_hits, 1),
                numpy.nanpercentile(assembled_powder_hits, 99),
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
            name=f"{self._task_parameters.det_name} - Misses",
        )
        dim = hv.Dimension(
            ("image", "Misses"),
            range=(
                numpy.nanpercentile(assembled_powder_misses, 1),
                numpy.nanpercentile(assembled_powder_misses, 99),
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
