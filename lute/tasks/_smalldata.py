"""Base classes for working with SmallData HDF5 files.

Classes defined in this module provide an interface to extracting data from
SmallData files and analyzing it. They are subclassed in the main `smalldata`
module for implementation into runnable Tasks.

Classes:
    AnalyzeSmallData(Task): Analyze a smalldata file, with MPI support.
"""

__all__ = ["AnalyzeSmallData"]
__author__ = "Gabriel Dorlhiac"

import sys
import logging
from typing import List, Optional, Dict, Tuple, Union, cast, ClassVar

import h5py  # type: ignore
import holoviews as hv  # type: ignore
import numpy as np
import numpy.typing as npt
import panel as pn
import matplotlib.pyplot as plt  # type: ignore
from mpi4py import MPI
from scipy.signal import find_peaks  # type: ignore
from scipy.optimize import curve_fit  # type: ignore

from lute.execution.logging import get_logger
from lute.io.models.base import TaskParameters
from lute.io.models.smd import (
    AnalyzeSmallDataXASParameters,
    AnalyzeSmallDataXESParameters,
    AnalyzeSmallDataXSSParameters,
)
from lute.tasks.task import Task
from lute.tasks.math import gaussian, sigma_to_fwhm

logger: logging.Logger = get_logger(__name__)


class AnalyzeSmallData(Task):
    """Base class for analyzing a SmallData HDF5 file with MPI support."""

    _LARGE_DETNAMES: ClassVar[List[str]] = ["epix10k2M", "Rayonix", "Jungfrau4M"]
    _SMALL_DETNAMES: ClassVar[List[str]] = ["epix_1", "epix_2"]

    def __init__(self, *, params: TaskParameters, use_mpi: bool = True) -> None:
        super().__init__(params=params, use_mpi=use_mpi)
        self._task_parameters = cast(
            Union[
                AnalyzeSmallDataXASParameters,
                AnalyzeSmallDataXESParameters,
                AnalyzeSmallDataXSSParameters,
            ],
            self._task_parameters,
        )
        hv.extension("bokeh")
        pn.extension()
        self._mpi_comm: MPI.Intracomm = MPI.COMM_WORLD
        self._mpi_rank: int = self._mpi_comm.Get_rank()
        self._mpi_size: int = self._mpi_comm.Get_size()
        try:
            self._smd_h5: h5py.File = h5py.File(self._task_parameters.smd_path, "r")
        except Exception:
            if self._mpi_rank == 0:
                logger.error(f"Failed to open file: {self._task_parameters.smd_path}!")
            self._mpi_comm.Barrier()
            sys.exit(-1)

        self._events_per_rank: npt.NDArray[np.int64]
        self._start_indices_per_rank: npt.NDArray[np.int64]
        if self._mpi_rank == 0:
            try:
                self._total_num_events: int = len(self._smd_h5["event_time"][()])
            except KeyError:
                self._total_num_events = len(self._smd_h5["timestamp"])
            quotient: int
            remainder: int
            quotient, remainder = divmod(self._total_num_events, self._mpi_size)
            self._events_per_rank = np.array(
                [
                    quotient + 1 if rank < remainder else quotient
                    for rank in range(self._mpi_size)
                ]
            )
            self._start_indices_per_rank = np.zeros(self._mpi_size, dtype=np.int64)
            self._start_indices_per_rank[1:] = np.cumsum(self._events_per_rank[:-1])
        else:
            self._events_per_rank = np.zeros(self._mpi_size, dtype=np.int64)
            self._start_indices_per_rank = np.zeros(self._mpi_size, dtype=np.int64)
        self._mpi_comm.Bcast(self._events_per_rank, root=0)
        self._mpi_comm.Bcast(self._start_indices_per_rank, root=0)
        self._xss_detname: str
        if self._task_parameters.xss_detname is None:
            for key in self._smd_h5.keys():
                if key in AnalyzeSmallData._LARGE_DETNAMES:
                    self._xss_detname = key
                    logger.debug(f"Assuming XSS detector is: {key}.")
                    break
            else:
                logger.error("No XSS detname provided and could not determine detname!")
                sys.exit(-1)
        else:
            self._xss_detname = self._task_parameters.xss_detname

        # Basic RANK-LOCAL variables - Each rank holds a subset of data
        ################################################################
        self._num_events: int = self._events_per_rank[self._mpi_rank]
        self._start_idx: int = self._start_indices_per_rank[self._mpi_rank]
        self._stop_idx: int = self._start_idx + self._num_events
        logger.debug(
            f"Rank {self._mpi_rank}: Processing {self._num_events} events from "
            f"event {self._start_idx}."
        )

        # Generic filtering variables
        self._filter_dict: Dict[
            str, Union[npt.NDArray[np.float64], npt.NDArray[np.bool_]]
        ] = {}
        self._xray_intensity: npt.NDArray[np.float64]
        self._integrated_intensity: npt.NDArray[np.float64]

        # Scan vars
        self._scan_var_name: Optional[str] = None
        self._scan_values: npt.NDArray[np.float64]

    def _pre_run(self) -> None: ...

    def _run(self) -> None: ...

    def _post_run(self) -> None: ...

    def _extract_standard_data(self) -> None:
        """Setup up stored attributes by taking data from the smalldata hdf5 file."""

        self._task_parameters = cast(
            Union[
                AnalyzeSmallDataXASParameters,
                AnalyzeSmallDataXESParameters,
                AnalyzeSmallDataXSSParameters,
            ],
            self._task_parameters,
        )
        self._extract_az_int()
        try:
            self._xray_intensity = self._smd_h5[self._task_parameters.ipm_var][
                self._start_idx : self._stop_idx
            ]
        except KeyError:
            logger.error("ipm data not found! Check config!")
        self._integrated_intensity = np.nansum(self._az_int, axis=(1, 2))
        self._setup_std_filters()

        if isinstance(self._task_parameters.scan_var, str):
            scan_var: str = self._task_parameters.scan_var
            try:
                self._scan_values = self._smd_h5[f"scan/{scan_var}"][
                    self._start_idx : self._stop_idx
                ]
                self._scan_var_name = scan_var
            except KeyError:
                logger.error(f"Scan variable {scan_var} not found!")
        elif isinstance(self._task_parameters.scan_var, list):
            for scan_var in self._task_parameters.scan_var:
                try:
                    self._scan_values = self._smd_h5[f"scan/{scan_var}"][
                        self._start_idx : self._stop_idx
                    ]
                    self._scan_var_name = scan_var
                    break
                except KeyError:
                    logger.debug(f"Scan variable {scan_var} not found!")
                    continue
        if not hasattr(self, "_scan_values"):
            # Create a set of scan values if none of the above scan
            # variables are found, either lxt_fast or linear set.
            try:
                self._scan_values = self._smd_h5["enc/lasDelay"][
                    self._start_idx : self._stop_idx
                ]
                self._scan_var_name = "lxt_fast"
                logger.debug("Using scan variable lxt_fast")
            except KeyError:
                logger.debug("Scan variable lxt_fast not found!")
                self._scan_values = np.linspace(
                    self._start_idx, self._stop_idx, self._num_events
                )
                logger.debug(
                    "No scans found, using linearly spaced data for _scan_values."
                )

    def _extract_az_int(self) -> None:
        """Try to extract the azimuthal integration data from HDF5 file.

        This internal method will search first to see if a detector has been
        provided as the scattering detector. If not, it will attempt to guess
        which detector to use. It will attempt to extract both the internal
        integration data and PyFAI integrated data (which have different
        interfaces). If both are present, it will only extract the internal
        algorithm data.
        """
        detname: str = self._xss_detname
        try:
            self._extract_az_int_smd_internal(detname)
            logger.debug(
                f"Extracted internal azimuthal integration data for {detname}."
            )
        except Exception:
            try:
                self._extract_az_int_pyfai(detname)
                logger.debug(
                    f"Extracted PyFAI azimuthal integration data for {detname}."
                )
            except Exception:
                logger.error("No azimuthal integration data found.")
                self._mpi_comm.Barrier()
                sys.exit(-1)

    def _extract_az_int_smd_internal(self, detname: str) -> None:
        """Extract stored data from SmallData's azimuthal integration algorithm.

        Args:
            detname (str): The detector name to extract data for.
        """

        self._az_int = self._smd_h5[f"{detname}/azav_azav"][
            self._start_idx : self._stop_idx
        ]  # 3D
        self._q_vals = self._smd_h5[f"UserDataCfg/{detname}/azav__azav_q"][()]
        self._phi_vals = self._smd_h5[f"UserDataCfg/{detname}/azav__azav_phiVec"][()]

    def _extract_az_int_pyfai(self, detname: str) -> None:
        """Extract stored data from PyFAI's azimuthal integration algorithm.

        Args:
            detname (str): The detector name to extract data for.
        """

        self._az_int = self._smd_h5[f"{detname}/pyfai_azav"][
            self._start_idx : self._stop_idx
        ]  # 3D
        self._q_vals = self._smd_h5[f"{detname}/pyfai_q"][()][0]
        self._phi_vals = self._smd_h5[f"{detname}/pyfai_az"][()][0]

    def _setup_std_filters(self) -> None:
        """Setup the individual standard event filters.

        Sets up light status (X-ray and laser) filters, and adjustable filters
        based on, e.g., thresholds.
        """
        # For filtering based on event type
        self._filter_dict["xray on"] = (
            self._smd_h5["lightStatus/xray"][self._start_idx : self._stop_idx] == 1
        )
        self._filter_dict["laser on"] = (
            self._smd_h5["lightStatus/laser"][self._start_idx : self._stop_idx] == 1
        )

        self._filter_dict["xray off"] = (
            self._smd_h5["lightStatus/xray"][self._start_idx : self._stop_idx] == 0
        )
        self._filter_dict["laser off"] = (
            self._smd_h5["lightStatus/laser"][self._start_idx : self._stop_idx] == 0
        )

        # Create scattering and ipm thresholds for first time
        self._update_filters()

    def _update_filters(self) -> None:
        """Update stored event filters that are based on adjustable parameters.

        E.g. update the minimum scattering intensity to use in analyses.
        """
        self._task_parameters = cast(
            Union[
                AnalyzeSmallDataXASParameters,
                AnalyzeSmallDataXESParameters,
                AnalyzeSmallDataXSSParameters,
            ],
            self._task_parameters,
        )
        scattering_thresh: float = self._task_parameters.thresholds.min_Iscat
        ipm_thresh: float = self._task_parameters.thresholds.min_ipm
        self._filter_dict["total scattering"] = (
            self._integrated_intensity > scattering_thresh
        )
        if hasattr(self, "_xray_intensity"):
            self._filter_dict["ipm"] = self._xray_intensity > ipm_thresh
        else:
            self._filter_dict["ipm"] = np.ones([self._num_events], dtype=bool)

    def _calc_norm_by_qrange(
        self, q_limits: Tuple[float, float] = (0.9, 3.5)
    ) -> npt.NDArray[np.float64]:
        """Calculate a normalization factor by averaging over a range of Q values.

        Args:
            q_limits (Tuple[float, float]): The lower and upper limit of the
                Q-range to average.

        Returns:
            norm (npt.NDArray[np.float64]): The 2D norm with shape
                (num_events, num_phi)
        """
        norm_range: npt.NDArray[np.bool_] = self._q_vals > q_limits[0]
        norm_range &= self._q_vals < q_limits[1]
        return np.nanmean(self._az_int[..., norm_range], axis=-1)

    def _calc_norm_by_max(self) -> npt.NDArray[np.float64]:
        """Calculate a normalization factor by taking the maximum of each profile.

        Returns:
        norm (npt.NDArray[np.float64]): The 1D norm with shape (num_events)
        """
        return np.nanmax(self._az_int, axis=(1, 2))

    def _calc_1d_water_norm(self) -> npt.NDArray[np.float64]:
        """Calculate normalization factors by integrating the water ring.

        https://www.osti.gov/servlets/purl/1760438 says to use 1.5-3.5 A-1
        """
        # _ = self.find_solvent_argmax(self.scattering_laser_on_mean)
        # min_q_idx: int = self._solvent_peak_idx - 10
        # max_q_idx: int = self._solvent_peak_idx + 1
        # return np.nanmean(
        #    self.calc_norm_by_qrange((self.q_vals[min_q_idx], self.q_vals[max_q_idx])),
        #    axis=-1,
        # )
        return np.nanmean(self._calc_norm_by_qrange((1.5, 3.5)), axis=-1)

    def _aggregate_filters(
        self, filter_vars: str = ("xray on, laser on, total scattering, ipm")
    ) -> npt.NDArray[np.bool_]:
        """Combine a number of possible data filters.

        Takes a string of filters to be applied in the order specified. The
        order matters for some filters, e.g. if "percentage" is selected it
        should be applied after total scattering. See below for a list of
        filters.

        Possible filters:
            "xray on": Take shots where X-rays are on,
            "xray off": Take shots where X-rays are off,
            "laser on": Take shots where laser is on,
            "laser off": Take shots where laser is off,
            "ipm": Take shots with X-ray intensity above a value (ipm value),
            "total scattering": Take shots with minimum integrated scattering,
            "percentage": Extract this percentage of data centered on the max
                scattering intensity. Should be applied after total scatteirng.
                E.g. Include 50% of shots centered around the Q-value with
                maximum scattering.

        Args:
            filter_vars (str): A string of comma-seperated filters to apply,
                taken from the list above. E.g. "xray on, laser on".

        Returns:
            total_filter (npt.NDArray[np.bool]): A 1D boolean array with a shape
                of (num_events) which can be used to index and filter a data array.
        """
        filters: List[str] = [f.strip() for f in filter_vars.split(",")]
        total_filter: npt.NDArray = np.ones([self._num_events], dtype=bool)

        for data_filter in filters:
            try:
                total_filter &= self._filter_dict[data_filter]
            except KeyError:
                logger.debug(
                    f"Unrecognized filter requested: {data_filter}. "
                    "Ignoring and continuing processing."
                )
                continue

        return total_filter

    def _calc_xss_dark_mean(
        self, profiles: npt.NDArray[np.float64]
    ) -> npt.NDArray[np.float64]:
        """Calculate the dark (X-ray off) mean of a set of XSS profiles.

        Args:
            profiles (npt.NDArray[np.float64]): Set of profiles to calculate the
                dark mean from. Shape: (n_events, q_bins)
        Returns:
            dark_mean (npt.NDArray[np.float64]): The calculated dark mean.
                Shape: (q_bins)
        """
        dark_mean: npt.NDArray[np.float64]
        try:
            dark_mean = np.nanmean(profiles[self._filter_dict["xray off"]], axis=0)
            dark_mean = self._mpi_comm.allreduce(dark_mean, op=MPI.SUM)
            dark_mean /= self._mpi_size
        except KeyError:
            logger.debug(
                "No X-ray off event information for filtering. "
                "Dark mean will be all zeros."
            )
            dark_mean = np.zeros([self._num_events])
        return dark_mean

    def _find_solvent_argmax(self, corrected_profile: npt.NDArray) -> int:
        """Find the index of the solvent ring maximum.

        Currently just a hack around SciPy find_peaks.

        Args:
            corrected_profile (npt.NDArray[np.float64]): 1D normalized and dark
                mean corrected, laser on, scattering profile.

        Returns:
            peak_idx (int): The index where the solvent maximum is located.
        """
        res: Tuple[npt.NDArray[np.int64], Dict[str, npt.NDArray[np.float64]]] = (
            find_peaks(corrected_profile, 1)
        )
        try:
            peak_indices: npt.NDArray = res[0]
            peak_heights: npt.NDArray = res[1]["peak_heights"]
        except KeyError:
            logger.debug("No peaks found")
            return cast(int, np.argmax(corrected_profile))

        peak_idx: int = peak_indices[
            np.argmax(peak_heights)
        ]  # peak_indices[0]  # peak_indices[peak_select - 1]
        self._solvent_peak_idx = peak_idx
        return peak_idx

    def _fit_overlap(
        self,
        laser_on: npt.NDArray[np.float64],
        bins: npt.NDArray[np.float64],
        diff: npt.NDArray[np.float64],
        guess: Optional[List[float]] = None,
    ) -> Tuple[
        npt.NDArray[np.float64], npt.NDArray[np.float64], npt.NDArray[np.float64]
    ]:
        """Fit overlap based on scattering difference signal to a Gaussian.

        Args:
            laser_on (npt.NDArray[np.float64]): 1D corrected average laser on
                scattering profile.

            bins (npt.NDArray[np.float64]): 1D set of bins used for difference
                signal.

            diff (npt.NDArray[np.float64]): 2D difference signal of shape
                (q_bins, scan_bins).

            guess (Optional[List[float]]): A list of initial parameter guesses
                for Gaussian fit in the order (amplitude, x0, sigma, background
                offset)

        Returns:
            raw_curve (npt.NDArray[np.float64]): 1D difference slice at a specific
                Q value used for calculating the overlap.

            opt (npt.NDArray[np.float64]): Optimized parameters.

            res (npt.NDArray[np.float64]): Covariances.
        """
        Tuple[npt.NDArray[np.float64], npt.NDArray[np.float64], npt.NDArray[np.float64]]
        max_pt: int = self._find_solvent_argmax(laser_on)
        idx_peak_q: int
        try:
            idx_peak_q = max_pt + 10
        except IndexError as e:
            logger.debug(f"{e}: No non-nan values found.")
            idx_peak_q = 15

        raw_curve: npt.NDArray = diff[idx_peak_q]
        if not guess:
            max_val: int = raw_curve.max()
            min_val: int = raw_curve.min()
            # guess = [amplitude, center, stddev, bkgnd]
            guess = [0, 0, 0, 0]
            x0: float
            if max_val > np.abs(min_val):
                x0 = bins[raw_curve.argmax()]
                guess[0] = max_val
            else:
                x0 = bins[raw_curve.argmin()]
                guess[0] = min_val
            guess[1] = x0
            guess[2] = np.abs(bins[-1] - bins[0]) / 20
            guess[3] = np.min(raw_curve)
        try:
            opt, res = curve_fit(gaussian, bins, raw_curve, p0=guess, maxfev=999999)
        except ValueError as e:
            logger.debug(f"{e}:\n No non-nan values...")
            opt = guess
            res = None

        return raw_curve, opt, res

    def _fit_convolution_fwhm(
        self, trace: npt.NDArray, bins: npt.NDArray[np.float64]
    ) -> float:
        """Calculate the FWHM of a convolution signal.

        Args:
            trace (npt.NDArray[np.float64]): 1D convolution trace.

            bins (npt.NDArray[np.float64]): 1D set of bins used.

        Returns:
            fwhm (float): Calculated FWHM.
        """
        from scipy.interpolate import UnivariateSpline  # type: ignore

        x = np.linspace(0, len(trace) - 1, len(trace))
        spline = UnivariateSpline(x, (trace - np.max(trace) / 2), s=0)
        roots = spline.roots()
        if len(roots) == 2:
            lb, rb = roots
            fwhm = rb - lb
        else:
            max_val: int = trace.max()
            min_val: int = trace.min()
            guess: List[Union[float, int]] = [0, 0, 0, 0]
            x0: Union[float, int]
            if max_val > np.abs(min_val):
                x0 = bins[trace.argmax()]
                guess[0] = max_val
            else:
                x0 = bins[trace.argmin()]
                guess[0] = min_val
            guess[1] = x0
            guess[2] = np.abs(bins[-1] - bins[0]) / 20
            guess[3] = np.min(trace)
            opt, res = curve_fit(
                gaussian, bins, np.nan_to_num(trace), p0=guess, maxfev=999999
            )
            fwhm = sigma_to_fwhm(opt[2])
        return fwhm

    def _convolution_fit(
        self,
        laser_on: npt.NDArray[np.float64],
        bins: npt.NDArray[np.float64],
        diff: npt.NDArray[np.float64],
    ) -> Tuple[npt.NDArray[np.float64], npt.NDArray[np.float64], int, float]:
        """Fits a time scan through convolution with Heaviside kernel.

        Args:
            laser_on (npt.NDArray[np.float64]): 1D corrected average laser on
                scattering profile.

            bins (npt.NDArray[np.float64]): 1D set of bins used for difference
                signal.

            diff (npt.NDArray[np.float64]): 2D difference signal of shape
                (q_bins, scan_bins).

        Returns:
            raw_curve (npt.NDArray[np.float64]): 1D difference slice at a specific
                Q value used for calculating the overlap.

            trace (npt.NDArray[np.float64]): 1D convolution trace.

            center (int): The index of the center from the convolution.

            fwhm (float): Width of the convlution signal (fwhm).
        """
        from scipy.signal import fftconvolve  # type: ignore

        max_pt: int = self._find_solvent_argmax(laser_on)
        raw_curve: npt.NDArray = np.nan_to_num(diff[max_pt + 10])
        npts: int = len(raw_curve)
        kernel: npt.NDArray = np.zeros([npts])
        kernel[: npts // 2] = 1
        trace: npt.NDArray = fftconvolve(raw_curve, kernel, mode="same")
        center: int = cast(int, trace.argmax())
        fwhm: float = self._fit_convolution_fwhm(trace, bins)
        return raw_curve, trace, center, fwhm

    # XAS - Extraction and TR difference
    ############################################################################
    def _extract_xas(self, detname: str) -> None:
        """Extract XAS specific data.

        Extracts the integrated sum of an ROI as well as an CCM position data.
        Will search for both the readback value PV and, if present, the setpoint
        PV.

        Args:
            detname (str): The detector name to extract data for.
        """
        assert isinstance(self._task_parameters, AnalyzeSmallDataXASParameters)
        self._xas_raw = self._smd_h5[f"{detname}/ROI_0_sum"][
            self._start_idx : self._stop_idx
        ]
        self._ccm_E = self._smd_h5[self._task_parameters.ccm][
            self._start_idx : self._stop_idx
        ]
        if self._task_parameters.ccm_set is not None:
            try:
                self._ccm_E_set_pt = self._smd_h5[self._task_parameters.ccm_set][
                    self._start_idx : self._stop_idx
                ]
            except KeyError:
                logger.error("No ccm_E_setpoint data. Will use fallback binning.")
        self._element: Optional[str] = self._task_parameters.element

    def _calc_binned_difference_xas(
        self,
    ) -> Tuple[
        Optional[npt.NDArray[np.float64]],
        Optional[npt.NDArray[np.float64]],
        Optional[npt.NDArray[np.float64]],
        Optional[npt.NDArray[np.float64]],
    ]:
        """Calculate the binned difference absorption.

        Calculates the 1D difference absorption for a set of CCM bins.
        Final difference shape is 1D: (ccm_bins). Also returns bins and
        the laser on/off profiles.

        Returns None for all values if the final number of CCM bins is small (<=2).

        Returns:
            bins (Optional[npt.NDArray[np.float64]]): 1D array of ccm bins used.

            diff (Optional[npt.NDArray[np.float64]]): 1D binned difference absorption
                of shape (ccm_bins)

            laser_on (Optional[npt.NDArray[np.float64]]): 1D laser on absorption
                profiles of shape (ccm_bins)

            laser_off (Optional[npt.NDArray[np.float64]]): 1D laser off absorption
                profiles of shape (ccm_bins)
        """
        nbins: int
        b_edges: npt.NDArray[np.float64]
        if self._ccm_E_set_pt is not None:
            # nbins = len(self.ccm_E_set_pt)
            nbins, b_edges = self._calc_ccm_bins_by_set_pt()
        else:
            nbins, b_edges = self._calc_ccm_bins_by_unique()

        if nbins <= 2:
            return None, None, None, None

        filter_las_on: npt.NDArray[np.bool_] = self._aggregate_filters()
        filter_las_off: npt.NDArray[np.bool_] = self._aggregate_filters(
            filter_vars="xray on, laser off, ipm, total scattering"
        )
        norm: npt.NDArray[np.float64] = self._calc_1d_water_norm()
        if (norm < 0).any():
            norm = self._xray_intensity
        xas_norm: npt.NDArray[np.float64] = self._xas_raw / norm

        xas_laser_on: npt.NDArray = np.zeros(nbins)
        xas_laser_off: npt.NDArray = np.zeros(nbins)
        # bins are [lower, upper) except for last which is [lower, upper]
        # _, b_edges = np.histogram(self.ccm_E_unique, bins=nbins)
        bins: npt.NDArray = np.zeros([nbins])
        i: int = 0
        while i < nbins:  # - win_size + 1
            win = b_edges[i : i + 2]
            bins[i] = win.mean()
            i += 1

        for i in range(nbins):
            lower: int = b_edges[i]
            upper: int = b_edges[i + 1]
            # Prepare CCM_E bin
            energy_filt: npt.NDArray
            if i == nbins - 1:
                # upper edge inclusive bin
                energy_filt = self._ccm_E <= upper
            else:
                energy_filt = self._ccm_E < upper
            energy_filt &= self._ccm_E >= lower
            full_filt_on = filter_las_on & energy_filt
            full_filt_off = filter_las_off & energy_filt
            xas_laser_on[i] = np.nanmean(xas_norm[full_filt_on])
            xas_laser_off[i] = np.nanmean(xas_norm[full_filt_off])

        return (
            bins,
            np.nan_to_num(xas_laser_on - xas_laser_off),
            np.nan_to_num(xas_laser_on),
            np.nan_to_num(xas_laser_off),
        )

    # XES - Extraction and TR difference
    ############################################################################
    def _extract_xes(self, detname: str) -> None:
        """Extract XAS specific data.

        Extracts individual ROIs and applys projections to extract the XES
        spectra. Depending on input TaskParameters, it will optionally rotate
        each ROI by some degrees prior to projection.

        By default, this method will read all ROIs into memory simultaneously
        and then project them. Alternatively, e.g. if encountering memory issues,
        input parameters can be changed to switch to reading in batches. Set the
        `batch_size` parameter to indicate the number of events to read into memory
        at once.

        Args:
            detname (str): The detector name to extract data for.
        """
        assert isinstance(self._task_parameters, AnalyzeSmallDataXESParameters)
        # Lets assume they set up the ROI nicley?
        # By xcsl1004821
        # proj0 should be spatial distribution
        # proj1 should be XES (unless invert == True)
        spatial_axis: int
        spectral_axis: int
        if not self._task_parameters.invert_xes_axes:
            spatial_axis = 0
            spectral_axis = 1
        else:
            spatial_axis = 1
            spectral_axis = 0

        self._xes: npt.NDArray[np.float64]
        spatial_dist: npt.NDArray[np.float64]
        guess_idx: int
        if self._task_parameters.batch_size:
            # Read data in batches for OOM issues
            size: int = self._task_parameters.batch_size

            for start in range(self._start_idx, self._stop_idx, size):
                end: int
                if start + size > self._stop_idx:
                    end = self._stop_idx
                else:
                    end = start + size
                xes_roi = self._smd_h5[f"{detname}/ROI_0_area"][start:end]
                if start == self._start_idx:
                    self._xes = np.zeros(
                        (self._num_events, xes_roi.shape[spatial_axis + 1])
                    )
                if self._task_parameters.rot_angle is not None:
                    from scipy.ndimage import rotate  # type: ignore

                    xes_roi = rotate(
                        xes_roi, angle=self._task_parameters.rot_angle, axes=(2, 1)
                    )
                spatial_dist = np.nansum(xes_roi, axis=(0, spatial_axis + 1))
                guess_idx = cast(int, np.argmax(spatial_dist))
                if not self._task_parameters.invert_xes_axes:
                    self._xes[start:end] = np.nansum(
                        xes_roi[..., guess_idx - 5 : guess_idx + 5],
                        axis=spectral_axis + 1,
                    )
                else:
                    self._xes[start:end] = np.nansum(
                        xes_roi[:, guess_idx - 5 : guess_idx + 5, :],
                        axis=spectral_axis + 1,
                    )
        else:
            xes_roi = self._smd_h5[f"{detname}/ROI_0_area"][
                self._start_idx : self._stop_idx
            ]
            if self._task_parameters.rot_angle is not None:
                from scipy.ndimage import rotate

                xes_roi = rotate(
                    xes_roi, angle=self._task_parameters.rot_angle, axes=(2, 1)
                )

            spatial_dist = np.nansum(xes_roi, axis=(0, spatial_axis + 1))
            guess_idx = cast(int, np.argmax(spatial_dist))
            if not self._task_parameters.invert_xes_axes:
                self._xes = np.nansum(
                    xes_roi[..., guess_idx - 5 : guess_idx + 5], axis=spectral_axis + 1
                )
            else:
                self._xes = np.nansum(
                    xes_roi[:, guess_idx - 5 : guess_idx + 5, :], axis=spectral_axis + 1
                )

    def _calc_avg_difference_xes(
        self,
    ) -> Tuple[
        npt.NDArray[np.float64],
        npt.NDArray[np.float64],
        npt.NDArray[np.float64],
    ]:
        """Calculate the average difference XES.

        Calculates the 1D difference emission spectra in pixels.
        Final difference shape is 1D: (pixels). Also returns the laser on/off
        profiles. The number of pixels is determined by the projection axis after
        image rotation if that is requested (see _extract_xes).

        Returns:
            diff (npt.NDArray[np.float64]): 1D binned difference emission of shape
                (pixels)

            laser_on (npt.NDArray[np.float64]): 1D laser on emission profiles
                of shape (pixels)

            laser_off (npt.NDArray[np.float64]): 1D laser off emission profiles
                of shape (pixels)
        """

        filter_las_on: npt.NDArray[np.bool_] = self._aggregate_filters()
        filter_las_off: npt.NDArray[np.bool_] = self._aggregate_filters(
            filter_vars="xray on, laser off, ipm, total scattering"
        )

        xes_on: npt.NDArray[np.float64] = np.nanmean(self._xes[filter_las_on], axis=0)
        xes_off: npt.NDArray[np.float64] = np.nanmean(self._xes[filter_las_off], axis=0)

        diff: npt.NDArray[np.float64] = xes_on - xes_off

        return diff, xes_on, xes_off

    # Binning
    ############################################################################
    def _calc_ccm_bins_by_set_pt(self) -> Tuple[int, npt.NDArray[np.float64]]:
        """Caculate bin edges based on the provided CCM_E set points.

        Returns:
            bins (npt.NDArray[np.float64]): 1D array of ccm bins used.
        """
        if self._ccm_E_set_pt is not None:
            all_set_pts: npt.NDArray[np.float64] = np.zeros(
                np.sum(self._events_per_rank), dtype=np.float64
            )
            self._mpi_comm.Allgatherv(
                self._ccm_E_set_pt,
                [
                    all_set_pts,
                    self._events_per_rank,
                    self._start_indices_per_rank,
                    MPI.DOUBLE,
                ],
            )
            bin_centers: npt.NDArray[np.float64] = np.sort(np.unique(all_set_pts))
            nbins: int = len(bin_centers)
        else:
            raise RuntimeError("Set points not provided/not found!")

        edges: npt.NDArray[np.float64] = np.empty(nbins + 1)
        edges[1:-1] = (bin_centers[1:] + bin_centers[:-1]) / 2
        # How to handle the ends?
        # Lower bin inclusive, upper not (except last)
        edges[0] = np.min(bin_centers)
        edges[-1] = np.max(bin_centers)

        return nbins, edges

    def _calc_ccm_bins_by_unique(
        self, nbins: int = 50
    ) -> Tuple[int, npt.NDArray[np.float64]]:
        """Calculate bin edges based on unique CCM_E recorded values.

        Args:
            nbins (int): Number of bins to create. 50-100 is empirically useful.

        Returns:
            nbins (int): Number of bins.

            b_edges (npt.NDArray[np.float64]): Bin edges.
        """
        all_ccm_values: npt.NDArray[np.float64] = np.zeros(
            np.sum(self._events_per_rank), dtype=np.float64
        )
        self._mpi_comm.Allgatherv(
            self._ccm_E,
            [
                all_ccm_values,
                self._events_per_rank,
                self._start_indices_per_rank,
                MPI.DOUBLE,
            ],
        )
        unique: npt.NDArray[np.float64] = np.unique(all_ccm_values)
        del all_ccm_values
        nbins = np.min([nbins, len(unique)])
        b_edges: npt.NDArray[np.float64] = np.histogram_bin_edges(unique, bins=nbins)
        return nbins, b_edges

    def _calc_scan_bins(self, nbins: int = 51) -> npt.NDArray[np.float64]:
        """Calculate a set of scan bins.

        Args:
            nbins (int): Number of bins to create.

        Returns:
            scan_bins (npt.NDArray[np.float64]): 1D set of scan bins.
        """
        # Create a full set of scan values
        all_scan_values: npt.NDArray[np.float64] = np.zeros(
            np.sum(self._events_per_rank), dtype=np.float64
        )
        self._mpi_comm.Allgatherv(
            self._scan_values,
            [
                all_scan_values,
                self._events_per_rank,
                self._start_indices_per_rank,
                MPI.DOUBLE,
            ],
        )
        all_scan_values = np.nan_to_num(all_scan_values)
        scan_bins: npt.NDArray[np.float64]
        if self._scan_var_name is not None and "lxt_fast" in self._scan_var_name:
            scan_bins = np.histogram_bin_edges(np.unique(all_scan_values), bins=nbins)
        elif self._scan_var_name is not None:
            scan_bins = np.unique(all_scan_values)
        else:
            scan_bins = np.ones([1])
        return scan_bins

    # Differences by scan
    ############################################################################

    def _calc_scan_binned_difference_xss(
        self,
    ) -> Tuple[
        npt.NDArray[np.float64], npt.NDArray[np.float64], npt.NDArray[np.float64]
    ]:
        """Calculate the binned difference scattering.

        Calculates the 1D difference scattering for each bin of a scan variable.
        Final difference shape is 2D: (q_bins, scan_bins). Also returns bins and
        the laser on profiles.

        Returns:
            bins (npt.NDArray[np.float64]): 1D array of scan bins used.

            diff (npt.NDArray[np.float64]): 2D binned difference scattering of shape
                (q_bins, scan_bins)

            laser_on (npt.NDArray[np.float64]): 2D laser on scattering profiles
                of shape (n_events_las_on, q_bins)
        """
        profiles: npt.NDArray[np.float64] = np.nansum(self._az_int, axis=1)
        dark_mean: npt.NDArray[np.float64] = self._calc_xss_dark_mean(profiles)
        if len(np.unique(dark_mean)) > 1:
            # Can be len == 1 if all nan
            profiles -= dark_mean
        norm: npt.NDArray[np.float64] = self._calc_norm_by_qrange()
        profiles = (profiles.T / np.nanmean(norm, axis=-1).T).T
        del dark_mean
        del norm

        filter_las_on: npt.NDArray[np.bool_] = self._aggregate_filters()
        filter_las_off: npt.NDArray[np.bool_] = self._aggregate_filters(
            filter_vars="xray on, laser off, ipm, total scattering"
        )
        normed_xss_las_on: npt.NDArray[np.float64] = profiles[filter_las_on]
        normed_xss_las_off: npt.NDArray[np.float64] = profiles[filter_las_off]

        bins: npt.NDArray[np.float64] = self._calc_scan_bins()
        binned_on: npt.NDArray[np.float64] = np.zeros((len(self._q_vals), len(bins)))
        binned_off: npt.NDArray[np.float64] = np.zeros((len(self._q_vals), len(bins)))
        scanvals_las_on: npt.NDArray[np.float64] = self._scan_values[filter_las_on]
        scanvals_las_off: npt.NDArray[np.float64] = self._scan_values[filter_las_off]

        idx: int
        scan_bin: float
        for idx, scan_bin in enumerate(bins):
            if self._scan_var_name is not None and "lxt_fast" in self._scan_var_name:
                if idx == len(bins) - 1:
                    continue
                mask_on: npt.NDArray[np.bool_] = (scanvals_las_on >= scan_bin) * (
                    scanvals_las_on < bins[idx + 1]
                )
                mask_off: npt.NDArray[np.bool_] = (scanvals_las_off >= scan_bin) * (
                    scanvals_las_off < bins[idx + 1]
                )
                binned_on[:, idx] = np.nanmean(normed_xss_las_on[mask_on], axis=0)
                binned_off[:, idx] = np.nanmean(normed_xss_las_off[mask_off], axis=0)
            else:
                binned_on[:, idx] = np.nanmean(
                    normed_xss_las_on[(scanvals_las_on == scan_bin)], axis=0
                )
                binned_off[:, idx] = np.nanmean(
                    normed_xss_las_off[(scanvals_las_off == scan_bin)], axis=0
                )
        diff: npt.NDArray[np.float64] = np.nan_to_num(binned_on) - np.nan_to_num(
            binned_off
        )
        return bins, diff, normed_xss_las_on

    def _calc_scan_binned_difference_xas(
        self,
    ) -> Tuple[
        Optional[npt.NDArray[np.float64]],
        Optional[npt.NDArray[np.float64]],
        Optional[npt.NDArray[np.float64]],
        Optional[npt.NDArray[np.float64]],
    ]:
        """Calculate the binned difference absorption.

        Calculates the difference absorption for each bin of a scan variable.
        Final difference shape is 1D: (scan_bins)

        Returns None for all values if the final number of bins is small (<=2).

        Returns:
            bins (Optional[npt.NDArray[np.float64]]): 1D array of scan bins used.

            diff (Optional[npt.NDArray[np.float64]]): 1D difference absorption.

            laser_off (Optional[npt.NDArray[np.float64]]): 1D laser on absorption.

            laser_off (Optional[npt.NDArray[np.float64]]): 1D laser off absorption.
        """
        bins: npt.NDArray[np.float64] = self._calc_scan_bins()
        if len(bins) <= 2:
            return None, None, None, None
        filter_las_on: npt.NDArray[np.bool_] = self._aggregate_filters()
        filter_las_off: npt.NDArray[np.bool_] = self._aggregate_filters(
            filter_vars="xray on, laser off, ipm, total scattering"
        )
        norm: npt.NDArray[np.float64] = self._xray_intensity
        normed_xas: npt.NDArray[np.float64] = self._xas_raw / norm
        normed_xas_las_on: npt.NDArray[np.float64] = normed_xas[filter_las_on]
        normed_xas_las_off: npt.NDArray[np.float64] = normed_xas[filter_las_off]

        scan_vals_las_on: npt.NDArray[np.float64] = self._scan_values[filter_las_on]
        scan_vals_las_off: npt.NDArray[np.float64] = self._scan_values[filter_las_off]

        lxt_fast_scan: bool = (
            self._scan_var_name is not None and "lxt_fast" in self._scan_var_name
        )
        binned_xas_las_on: npt.NDArray[np.float64]
        binned_xas_las_off: npt.NDArray[np.float64]
        if lxt_fast_scan:
            binned_xas_las_on = np.zeros(len(bins) - 1)
            binned_xas_las_off = np.zeros(len(bins) - 1)
        else:
            binned_xas_las_on = np.zeros(len(bins))
            binned_xas_las_off = np.zeros(len(bins))

        for idx, bin_or_bin_edge in enumerate(bins):
            if lxt_fast_scan:
                if idx == len(bins) - 1:
                    continue
                mask_on: npt.NDArray[np.float64] = (
                    scan_vals_las_on >= bin_or_bin_edge
                ) * (scan_vals_las_on < bins[idx + 1])
                mask_off: npt.NDArray[np.float64] = (
                    scan_vals_las_off >= bin_or_bin_edge
                ) * (scan_vals_las_off < bins[idx + 1])
                binned_xas_las_on[idx] = np.mean(normed_xas_las_on[mask_on])
                binned_xas_las_off[idx] = np.mean(normed_xas_las_off[mask_off])
            else:
                binned_xas_las_on[idx] = np.mean(
                    normed_xas_las_on[scan_vals_las_on == bin_or_bin_edge]
                )
                binned_xas_las_off[idx] = np.mean(
                    normed_xas_las_off[scan_vals_las_off == bin_or_bin_edge]
                )

        diff: npt.NDArray[np.float64] = binned_xas_las_on - binned_xas_las_off
        return bins, diff, binned_xas_las_on, binned_xas_las_off

    def _calc_scan_binned_difference_xes(
        self,
    ) -> Tuple[
        npt.NDArray[np.float64],
        npt.NDArray[np.float64],
        npt.NDArray[np.float64],
        npt.NDArray[np.float64],
    ]:
        """Calculate the binned difference emission.

        Calculates the difference emission for each bin of a scan variable.
        Final difference shape is 2D: (pixels, scan_bins) where the pixel
        axis is energy.

        Returns:
            bins (npt.NDArray[np.float64]): 1D array of scan bins used.

            diff (npt.NDArray[np.float64]): 2D difference emission.

            laser_off (npt.NDArray[np.float64]): 2D laser on emission.

            laser_off (npt.NDArray[np.float64]): 2D laser off emission.
        """
        filter_las_on: npt.NDArray[np.bool_] = self._aggregate_filters()
        filter_las_off: npt.NDArray[np.bool_] = self._aggregate_filters(
            filter_vars="xray on, laser off, ipm, total scattering"
        )
        norm: npt.NDArray[np.float64] = self._xray_intensity
        normed_xes: npt.NDArray[np.float64] = (self._xes.T / norm).T
        normed_xes_las_on: npt.NDArray[np.float64] = normed_xes[filter_las_on]
        normed_xes_las_off: npt.NDArray[np.float64] = normed_xes[filter_las_off]

        scan_vals_las_on: npt.NDArray[np.float64] = self._scan_values[filter_las_on]
        scan_vals_las_off: npt.NDArray[np.float64] = self._scan_values[filter_las_off]
        bins: npt.NDArray[np.float64] = self._calc_scan_bins()

        lxt_fast_scan: bool = (
            self._scan_var_name is not None and "lxt_fast" in self._scan_var_name
        )
        binned_xes_las_on: npt.NDArray[np.float64]
        binned_xes_las_off: npt.NDArray[np.float64]
        if lxt_fast_scan:
            binned_xes_las_on = np.zeros((normed_xes_las_on.shape[1], len(bins) - 1))
            binned_xes_las_off = np.zeros((normed_xes_las_off.shape[1], len(bins) - 1))
        else:
            binned_xes_las_on = np.zeros((normed_xes_las_on.shape[1], len(bins)))
            binned_xes_las_off = np.zeros((normed_xes_las_off.shape[1], len(bins)))

        for idx, bin_or_bin_edge in enumerate(bins):
            if lxt_fast_scan:
                if idx == len(bins) - 1:
                    continue
                mask_on: npt.NDArray[np.float64] = (
                    scan_vals_las_on >= bin_or_bin_edge
                ) * (scan_vals_las_on < bins[idx + 1])
                mask_off: npt.NDArray[np.float64] = (
                    scan_vals_las_off >= bin_or_bin_edge
                ) * (scan_vals_las_off < bins[idx + 1])
                binned_xes_las_on[:, idx] = np.mean(normed_xes_las_on[mask_on], axis=0)
                binned_xes_las_off[:, idx] = np.mean(
                    normed_xes_las_off[mask_off], axis=0
                )
            else:
                binned_xes_las_on[:, idx] = np.mean(
                    normed_xes_las_on[scan_vals_las_on == bin_or_bin_edge], axis=0
                )
                binned_xes_las_off[:, idx] = np.mean(
                    normed_xes_las_off[scan_vals_las_off == bin_or_bin_edge], axis=0
                )

        diff: npt.NDArray[np.float64] = np.nan_to_num(
            binned_xes_las_on
        ) - np.nan_to_num(binned_xes_las_off)
        return bins, diff, binned_xes_las_on, binned_xes_las_off

    # Plots
    ############################################################################

    # XSS
    def plot_avg_xss(
        self,
        laser_on: npt.NDArray[np.float64],
        q_vals: npt.NDArray[np.float64],
        phis: npt.NDArray[np.float64],
    ) -> plt.Figure: ...

    def plot_difference_xss_hv(
        self,
        bins: npt.NDArray[np.float64],
        q_vals: npt.NDArray[np.float64],
        diff: npt.NDArray[np.float64],
        scan_var_name: str,
    ) -> pn.GridSpec:
        """Plot the binned difference scattering.

        Args:
            bins (npt.NDArray[np.float64]): 1D set of bins used for difference
                signal.

            q_vals (npt.NDArray[np.float64]): 1D set of Q bins.

            diff (npt.NDArray[np.float64]): 2D difference signal of shape
                (q_bins, scan_bins).

            scan_var_name (str): Name of the scan variable (for titles, etc.).

        Returns:
            plot (pn.GridSpec): Plotted binned difference.
        """
        grid: pn.GridSpec = pn.GridSpec(name="Difference Profiles")
        xdim: hv.core.dimension.Dimension = hv.Dimension(
            ("scan_var", f"Scan Var {scan_var_name}")
        )
        ydim: hv.core.dimension.Dimension = hv.Dimension(("Q", "Q"))
        diff_img: hv.Image = hv.Image(
            (bins, q_vals, diff),
            kdims=[xdim, ydim],
        ).opts(shared_axes=False)
        contours = diff_img + hv.operation.contours(diff_img, levels=5)
        contours.opts(
            hv.opts.Contours(cmap="fire", colorbar=True, tools=["hover"], width=325)
        ).opts(shared_axes=False)
        grid[:2, :2] = contours
        xdim = hv.Dimension(("Q", "Q"))
        ydim = hv.Dimension(("dS", "dS"))
        diff_curves: hv.Overlay
        diff_curves = hv.Curve((q_vals, diff[:, 0]))
        for i, _ in enumerate(bins):
            if i == 0:
                continue
            else:
                diff_curves *= hv.Curve(
                    (
                        q_vals,
                        diff[:, i],
                    )
                ).opts(xlabel=xdim.label, ylabel=ydim.label)

        grid[2:4, :] = diff_curves.opts(shared_axes=False)
        return grid

    def plot_xss_overlap_fit(
        self,
        laser_on: npt.NDArray[np.float64],
        bins: npt.NDArray[np.float64],
        diff: npt.NDArray[np.float64],
    ) -> plt.Figure:
        """Plot the overlap fit to a slice of the binned difference.

        Args:
            laser_on (npt.NDArray[np.float64]): 1D corrected average laser on
                scattering profile.

            bins (npt.NDArray[np.float64]): 1D set of bins used for difference
                signal.

            diff (npt.NDArray[np.float64]): 2D difference signal of shape
                (q_bins, scan_bins).

        Returns:
            plot (plt.Figure): Plotted overlap fit.
        """
        fig, ax = plt.subplots(1, 1, figsize=(6, 3), dpi=200)
        raw_curve: npt.NDArray[np.float64]
        msg: str
        if self._scan_var_name is not None and "lxt" in self._scan_var_name:
            trace: npt.NDArray[np.float64]
            center: int
            fwhm: float
            raw_curve, trace, center, fwhm = self._convolution_fit(laser_on, bins, diff)
            msg = (
                f"Scan Var: {self._scan_var_name}\n"
                f"Overlap Position: {bins[center]}\n"
                f"FWHM: {fwhm}"
            )
            logger.info(msg)
            ax.plot(bins, raw_curve, label="Raw")
            ax2 = ax.twinx()
            ax2.plot(bins, trace, color="orange", label="Convolution")
            ax.set_title(msg)
            ax.set_xlabel(f"Scan Variable ({self._scan_var_name})")
            ax.set_ylabel(r"$\Delta$S")
            plt.legend(loc=0, frameon=False)
        else:
            opt: npt.NDArray[np.float64]
            raw_curve, opt, _ = self._fit_overlap(laser_on, bins, diff)
            msg = (
                f"Scan Var: {self._scan_var_name}\n"
                f"Overlap Position: {opt[1]}\n"
                f"FWHM: {sigma_to_fwhm(opt[2])}"
            )
            logger.info(msg)
            ax.plot(bins, raw_curve)
            ax.plot(bins, gaussian(bins, *opt))
            ax.set_title(msg)
            ax.set_xlabel(f"Scan Variable ({self._scan_var_name})")
            ax.set_ylabel(r"$\Delta$S")
        fig.tight_layout()
        return fig

    def plot_all_xss(
        self,
        laser_on: npt.NDArray[np.float64],
        bins: npt.NDArray[np.float64],
        diff: npt.NDArray[np.float64],
        scan_var_name: str,
    ) -> pn.Tabs:
        """Plot all relevant scattering plots.

        Args:
            laser_on (npt.NDArray[np.float64]): 1D corrected average laser on
                scattering profile.

            bins (npt.NDArray[np.float64]): 1D set of bins used for difference
                signal.

            diff (npt.NDArray[np.float64]): 2D difference signal of shape
                (q_bins, scan_bins).

            scan_var_name (str): Name of the scan variable (for titles, etc.).

        Returns:
            plot (pn.Tabs): All plots in separated tabs in a pn.Tabs object.
        """
        # avg_fig = self.plot_avg_xss()
        overlap_fig = self.plot_xss_overlap_fit(laser_on, bins, diff)

        # avg_grid = pn.GridSpec(name="TR X-ray Scattering")
        # avg_grid[:2, :2] = avg_fig

        diff_grid = self.plot_difference_xss_hv(bins, self._q_vals, diff, scan_var_name)
        overlap_grid = pn.GridSpec(name="Overlap Fit")
        overlap_grid[:2, :2] = overlap_fig

        tabbed_display = pn.Tabs(diff_grid)
        tabbed_display.append(overlap_grid)
        # tabbed_display.append(avg_grid)

        return tabbed_display

    # XAS
    def plot_all_xas(
        self,
        laser_on: npt.NDArray[np.float64],
        laser_off: npt.NDArray[np.float64],
        ccm_bins: npt.NDArray[np.float64],
        diff: npt.NDArray[np.float64],
    ) -> pn.Tabs:
        """Plot XAS and optionally EXAFS

        Args:
            laser_on (npt.NDArray[np.float64]): 1D corrected average laser on
                absorption spectrum.

            laser_off (npt.NDArray[np.float64]): 1D corrected average laser off
                absorption spectrum.

            ccm_bins (npt.NDArray[np.float64]): 1D set of bins used for difference
                signal.

            diff (npt.NDArray[np.float64]): 1D difference absorption.

        Returns:
            plot (pn.Tabs): All plots in separated tabs in a pn.Tabs object.
        """
        std_xas_grid: pn.GridSpec = self.plot_std_xas_hv(
            laser_on, laser_off, ccm_bins, diff
        )
        tabs: pn.Tabs = pn.Tabs(std_xas_grid)
        if (
            hasattr(self._task_parameters, "analyze_exafs")
            and self._task_parameters.analyze_exafs
        ):
            ...
            # exafs_grid: pn.GridSpec = self.plot_exafs_hv()
            # tabs.append(exafs_grid)

        return tabs

    def plot_std_xas_hv(
        self,
        laser_on: npt.NDArray[np.float64],
        laser_off: npt.NDArray[np.float64],
        ccm_bins: npt.NDArray[np.float64],
        diff: npt.NDArray[np.float64],
    ) -> pn.GridSpec:
        """Plot relevant XAS plots.

        Args:
            laser_on (npt.NDArray[np.float64]): 1D corrected average laser on
                absorption spectrum.

            laser_off (npt.NDArray[np.float64]): 1D corrected average laser off
                absorption spectrum.

            ccm_bins (npt.NDArray[np.float64]): 1D set of bins used for difference
                signal.

            diff (npt.NDArray[np.float64]): 1D difference absorption.

        Returns:
            plot (pn.GridSpec): Laser on/off and difference XAS plots.
        """
        xdim: hv.core.dimension.Dimension = hv.Dimension(("ccm_E", "Energy"))
        ydim: hv.core.dimension.Dimension = hv.Dimension(("A", "A"))

        on_pts: hv.Points = hv.Points(
            (ccm_bins, laser_on), kdims=[xdim, ydim], label="Laser on"
        ).opts(size=5, color="green")
        on_curve: hv.Curve = hv.Curve((ccm_bins, laser_on), kdims=[xdim, ydim]).opts(
            color="green"
        )
        off_pts: hv.Points = hv.Points(
            (ccm_bins, laser_off), kdims=[xdim, ydim], label="Laser off"
        ).opts(size=5, color="orange")
        off_curve: hv.Curve = hv.Curve((ccm_bins, laser_off), kdims=[xdim, ydim]).opts(
            color="orange"
        )
        xas_plots = on_pts * on_curve * off_pts * off_curve

        ydim = hv.Dimension(("diff A", "dA/dE"))

        diff_pts: hv.Points = hv.Points((ccm_bins, diff), kdims=[xdim, ydim]).opts(
            size=5
        )
        diff_curve: hv.Curve = hv.Curve((ccm_bins, diff), kdims=[xdim, ydim])
        diff_plot: hv.Overlay = hv.Overlay([diff_pts, diff_curve])

        grid: pn.GridSpec = pn.GridSpec(name="XAS")
        grid[:2, :2] = xas_plots
        grid[2:4, :2] = diff_plot
        return grid

    def plot_xas_scan_hv(
        self,
        laser_on: npt.NDArray[np.float64],
        laser_off: npt.NDArray[np.float64],
        scan_bins: npt.NDArray[np.float64],
        diff: npt.NDArray[np.float64],
    ) -> Optional[pn.Tabs]:
        """Plot scan binned XAS data.

        Currently handles lxe_opa power titration and t0 (lxt_fast) XAS scans.

        Args:
            laser_on (npt.NDArray[np.float64]): 1D corrected average laser on
                absorption spectrum.

            laser_off (npt.NDArray[np.float64]): 1D corrected average laser off
                absorption spectrum.

            scan_bins (npt.NDArray[np.float64]): 1D set of bins used for difference
                signal.

            diff (npt.NDArray[np.float64]): 1D difference absorption.

        Returns:
            plot (pn.Tabs): Plotted binned difference.
        """
        if self._scan_var_name is None:
            logger.error("Skipping scan plots - requested scan variables not found.")
            return None
        if "lxe_opa" in self._scan_var_name:
            return self.plot_xas_power_titration_scan(
                laser_on, laser_off, scan_bins, diff
            )
        elif "lxt_fast" in self._scan_var_name:
            return self.plot_xas_lxt_fast_scan(laser_on, laser_off, scan_bins, diff)
        return None

    def plot_xas_lxt_fast_scan(
        self,
        laser_on: npt.NDArray[np.float64],
        laser_off: npt.NDArray[np.float64],
        scan_bins: npt.NDArray[np.float64],
        diff: npt.NDArray[np.float64],
    ) -> pn.Tabs:
        """Produce a plot of an lxt_fast scan for time zero (or real signal).

        Args:
            laser_on (npt.NDArray[np.float64]): 1D corrected average laser on
                absorption spectrum.

            laser_off (npt.NDArray[np.float64]): 1D corrected average laser off
                absorption spectrum.

            scan_bins (npt.NDArray[np.float64]): 1D set of bins used for difference
                signal.

            diff (npt.NDArray[np.float64]): 1D difference absorption.

        Returns:
            plot (pn.Tabs): Plotted binned difference.
        """
        xdim: hv.core.dimension.Dimension = hv.Dimension(("lxt_fast", "lxt_fast"))
        ydim: hv.core.dimension.Dimension = hv.Dimension(
            ("Normalized A", "Normalized A")
        )

        bin_centers: npt.NDArray[np.float64] = (scan_bins[:-1] + scan_bins[1:]) / 2

        on_pts: hv.Points = hv.Points(
            (bin_centers, laser_on), kdims=[xdim, ydim], label="Laser on"
        ).opts(size=5, color="blue")
        on_curve: hv.Curve = hv.Curve((bin_centers, laser_on), kdims=[xdim, ydim]).opts(
            color="blue"
        )
        off_pts: hv.Points = hv.Points(
            (bin_centers, laser_off), kdims=[xdim, ydim], label="Laser off"
        ).opts(size=5, color="orange")
        off_curve: hv.Curve = hv.Curve(
            (bin_centers, laser_off), kdims=[xdim, ydim]
        ).opts(color="orange")
        xas_plots: hv.Overlay = on_pts * on_curve * off_pts * off_curve

        ydim = hv.Dimension(("diff A", "diff A"))
        diff_pts: hv.Points = hv.Points(
            (bin_centers, diff),
            kdims=[xdim, ydim],
            label="Laser on - Laser off",
        ).opts(size=5, color="green")
        diff_curve: hv.Curve = hv.Curve((bin_centers, diff), kdims=[xdim, ydim]).opts(
            color="green"
        )
        diff_plot: hv.Overlay = diff_pts * diff_curve

        grid: pn.GridSpec = pn.GridSpec(name="lxt_fast T0 scan")
        grid[:2, :2] = xas_plots.opts(axiswise=True, shared_axes=False)
        grid[:2, 2:4] = diff_plot.opts(axiswise=True, shared_axes=False)
        tabs: pn.Tabs = pn.Tabs(grid)
        return tabs

    def plot_xas_power_titration_scan(
        self,
        laser_on: npt.NDArray[np.float64],
        laser_off: npt.NDArray[np.float64],
        scan_bins: npt.NDArray[np.float64],
        diff: npt.NDArray[np.float64],
    ) -> pn.Tabs:
        """Produce a plot of the lxe_opa power titration.

        Args:
            laser_on (npt.NDArray[np.float64]): 1D corrected average laser on
                absorption spectrum.

            laser_off (npt.NDArray[np.float64]): 1D corrected average laser off
                absorption spectrum.

            scan_bins (npt.NDArray[np.float64]): 1D set of bins used for difference
                signal.

            diff (npt.NDArray[np.float64]): 1D difference absorption.

        Returns:
            plot (pn.Tabs): Plotted binned difference.
        """
        xdim: hv.core.dimension.Dimension = hv.Dimension(("lxe_opa", "lxe_opa"))
        ydim: hv.core.dimension.Dimension = hv.Dimension(
            ("Normalized A", "Normalized A")
        )

        on_pts: hv.Points = hv.Points(
            (scan_bins, laser_on), kdims=[xdim, ydim], label="Laser on"
        ).opts(size=5, color="blue")
        on_curve: hv.Curve = hv.Curve((scan_bins, laser_on), kdims=[xdim, ydim]).opts(
            color="blue"
        )
        off_pts: hv.Points = hv.Points(
            (scan_bins, laser_off), kdims=[xdim, ydim], label="Laser off"
        ).opts(size=5, color="orange")
        off_curve: hv.Curve = hv.Curve((scan_bins, laser_off), kdims=[xdim, ydim]).opts(
            color="orange"
        )
        xas_plots: hv.Overlay = on_pts * on_curve * off_pts * off_curve

        ydim = hv.Dimension(("diff A", "diff A"))
        diff_pts: hv.Points = hv.Points(
            (scan_bins, diff),
            kdims=[xdim, ydim],
            label="Laser on - Laser off",
        ).opts(size=5, color="green")
        diff_curve: hv.Curve = hv.Curve((scan_bins, diff), kdims=[xdim, ydim]).opts(
            color="green"
        )
        diff_plot: hv.Overlay = diff_pts * diff_curve

        grid: pn.GridSpec = pn.GridSpec(name="Power Titration")
        grid[:2, :2] = xas_plots.opts(axiswise=True, shared_axes=False)
        grid[:2, 2:4] = diff_plot.opts(axiswise=True, shared_axes=False)
        tabs: pn.Tabs = pn.Tabs(grid)
        return tabs

    # XES
    def plot_xes_hv(
        self,
        laser_on: npt.NDArray[np.float64],
        laser_off: npt.NDArray[np.float64],
        energy_bins: Optional[npt.NDArray[np.float64]],
        diff: npt.NDArray[np.float64],
    ) -> pn.Tabs:
        """Plot XES and difference XES. In the future will provide other plots.

        Args:
            laser_on (npt.NDArray[np.float64]): 1D corrected average laser on
                emission spectrum.

            laser_off (npt.NDArray[np.float64]): 1D corrected average laser off
                emission spectrum.

            energy_bins (Optional[npt.NDArray[np.float64]]): 1D set of bins used
                for the energy axis. If None, will just use pixels for that axis.

            diff (npt.NDArray[np.float64]): 1D difference emission.

        Returns:
            plot (pn.Tabs): Plotted binned difference.
        """

        std_xes_grid: pn.GridSpec = self.plot_std_xes_hv(
            laser_on, laser_off, energy_bins, diff
        )
        tabs: pn.Tabs = pn.Tabs(std_xes_grid)

        return tabs

    def plot_std_xes_hv(
        self,
        laser_on: npt.NDArray[np.float64],
        laser_off: npt.NDArray[np.float64],
        energy_bins: Optional[npt.NDArray[np.float64]],
        diff: npt.NDArray[np.float64],
    ) -> pn.GridSpec:
        """Plot XES and difference XES.

        Args:
            laser_on (npt.NDArray[np.float64]): 1D corrected average laser on
                emission spectrum.

            laser_off (npt.NDArray[np.float64]): 1D corrected average laser off
                emission spectrum.

            energy_bins (Optional[npt.NDArray[np.float64]]): 1D set of bins used
                for the energy axis. If None, will just use pixels for that axis.

            diff (npt.NDArray[np.float64]): 1D difference emission.

        Returns:
            plot (pn.Tabs): Plotted binned difference.
        """
        xdim: hv.core.dimension.Dimension = hv.Dimension(("Energy", "Energy"))
        ydim: hv.core.dimension.Dimension = hv.Dimension(("I", "I"))

        bins: npt.NDArray[Union[np.int64, np.float64]]
        if energy_bins is None:
            bins = np.arange(len(laser_on))
        else:
            # May be float, above is int
            bins = energy_bins

        on_pts: hv.Points = hv.Points(
            (bins, laser_on), kdims=[xdim, ydim], label="Laser on"
        ).opts(size=5, color="green")
        on_curve: hv.Curve = hv.Curve((bins, laser_on), kdims=[xdim, ydim]).opts(
            color="green"
        )
        off_pts: hv.Points = hv.Points(
            (bins, laser_off), kdims=[xdim, ydim], label="Laser off"
        ).opts(size=5, color="orange")
        off_curve: hv.Curve = hv.Curve((bins, laser_off), kdims=[xdim, ydim]).opts(
            color="orange"
        )
        xes_plots = on_pts * on_curve * off_pts * off_curve

        ydim = hv.Dimension(("diff I", "dI"))

        diff_pts: hv.Points = hv.Points((bins, diff), kdims=[xdim, ydim]).opts(size=5)
        diff_curve: hv.Curve = hv.Curve((bins, diff), kdims=[xdim, ydim])
        diff_plot: hv.Overlay = hv.Overlay([diff_pts, diff_curve])

        grid: pn.GridSpec = pn.GridSpec(name="XES")
        grid[:2, :2] = xes_plots
        grid[2:4, :2] = diff_plot
        return grid

    def plot_xes_scan_hv(
        self,
        laser_on: npt.NDArray[np.float64],
        laser_off: npt.NDArray[np.float64],
        scan_bins: npt.NDArray[np.float64],
        diff: npt.NDArray[np.float64],
    ) -> Optional[pn.Tabs]:
        """Plot scan binned XES data.

        Currently handles lxt and lxt_fast XES scans.

        Args:
            laser_on (npt.NDArray[np.float64]): 1D corrected average laser on
                emission spectrum.

            laser_off (npt.NDArray[np.float64]): 1D corrected average laser off
                emission spectrum.

            scan_bins (npt.NDArray[np.float64]): 1D set of bins used for difference
                signal.

            diff (npt.NDArray[np.float64]): 1D difference emission.

        Returns:
            plot (Optional[pn.Tabs]): Plotted binned difference. Returns None if
                the scan variable is unknown/unrecognized.
        """
        if self._scan_var_name is None:
            logger.info("Skipping scan plots - requested scan variables not found.")
            return None
        if "lxt_fast" in self._scan_var_name:
            return self.plot_xes_lxt_fast_scan(laser_on, laser_off, scan_bins, diff)
        elif "lxt" in self._scan_var_name:
            return self.plot_xes_lxt_scan(laser_on, laser_off, scan_bins, diff)
        return None

    def plot_xes_lxt_fast_scan(
        self,
        laser_on: npt.NDArray[np.float64],
        laser_off: npt.NDArray[np.float64],
        scan_bins: npt.NDArray[np.float64],
        diff: npt.NDArray[np.float64],
    ) -> pn.Tabs:
        """Plot lxt_fast scan binned XES data.

        Args:
            laser_on (npt.NDArray[np.float64]): 1D corrected average laser on
                emission spectrum.

            laser_off (npt.NDArray[np.float64]): 1D corrected average laser off
                emission spectrum.

            scan_bins (npt.NDArray[np.float64]): 1D set of bins used for difference
                signal.

            diff (npt.NDArray[np.float64]): 1D difference emission.

        Returns:
            plot (pn.Tabs): Plotted binned difference.
        """
        grid: pn.GridSpec = pn.GridSpec(name="Difference XES")
        xdim: hv.core.dimension.Dimension = hv.Dimension(("lxt_fast", "lxt_fast"))
        ydim: hv.core.dimension.Dimension = hv.Dimension(
            ("Energy (Pixel)", "Energy (Pixel)")
        )

        bin_centers: npt.NDArray[np.float64] = (scan_bins[:1] + scan_bins[1:]) / 2
        diff_img: hv.Image = hv.Image(
            (
                bin_centers,
                np.linspace(0, len(laser_on[:, 0]) - 1, len(laser_on[:, 0])),
                diff,
            ),
            kdims=[xdim, ydim],
        ).opts(shared_axes=False)
        contours: hv.Contours = hv.operation.contours(diff_img, levels=5)
        contours.opts(
            hv.opts.Contours(cmap="fire", colorbar=True, tools=["hover"], width=325)
        )
        shared: hv.Overlay = diff_img + contours
        shared.opts(shared_axes=False)
        grid[:2, :2] = shared

        xdim = hv.Dimension(("Energy (Pixel)", "Energy (Pixel)"))
        ydim = hv.Dimension(("dI", "dI"))

        diff_curves: hv.Curve = hv.Curve((range(len(diff[:, 0])), diff[:, 0]))
        for idx, _ in enumerate(bin_centers):
            if idx == 0:
                continue
            else:
                diff_curves *= hv.Curve((range(len(diff[:, 0])), diff[:, idx])).opts(
                    xlabel=xdim.label, ylabel=ydim.label
                )
        grid[2:4, :] = diff_curves.opts(shared_axes=False)
        return pn.Tabs(grid)

    def plot_xes_lxt_scan(
        self,
        laser_on: npt.NDArray[np.float64],
        laser_off: npt.NDArray[np.float64],
        scan_bins: npt.NDArray[np.float64],
        diff: npt.NDArray[np.float64],
    ) -> pn.Tabs:
        """Plot lxt scan binned XES data.

        Args:
            laser_on (npt.NDArray[np.float64]): 1D corrected average laser on
                emission spectrum.

            laser_off (npt.NDArray[np.float64]): 1D corrected average laser off
                emission spectrum.

            scan_bins (npt.NDArray[np.float64]): 1D set of bins used for difference
                signal.

            diff (npt.NDArray[np.float64]): 1D difference emission.

        Returns:
            plot (pn.Tabs): Plotted binned difference.
        """
        grid: pn.GridSpec = pn.GridSpec(name="Difference XES")
        xdim: hv.core.dimension.Dimension = hv.Dimension(("lxt", "lxt"))
        ydim: hv.core.dimension.Dimension = hv.Dimension(
            ("Energy (Pixel)", "Energy (Pixel)")
        )

        diff_img: hv.Image = hv.Image(
            (
                scan_bins,
                np.linspace(0, len(laser_on[:, 0]) - 1, len(laser_on[:, 0])),
                diff,
            ),
            kdims=[xdim, ydim],
        ).opts(shared_axes=False)
        contours: hv.Contours = hv.operation.contours(diff_img, levels=5)
        contours.opts(
            hv.opts.Contours(cmap="fire", colorbar=True, tools=["hover"], width=325)
        )
        shared: hv.Overlay = diff_img + contours
        shared.opts(shared_axes=False)
        grid[:2, :2] = shared

        xdim = hv.Dimension(("Energy (Pixel)", "Energy (Pixel)"))
        ydim = hv.Dimension(("dI", "dI"))

        diff_curves: hv.Curve = hv.Curve((range(len(diff[:, 0])), diff[:, 0]))
        for idx, _ in enumerate(scan_bins):
            if idx == 0:
                continue
            else:
                diff_curves *= hv.Curve((range(len(diff[:, 0])), diff[:, idx])).opts(
                    xlabel=xdim.label, ylabel=ydim.label
                )
        grid[2:4, :] = diff_curves.opts(shared_axes=False)
        return pn.Tabs(grid)
