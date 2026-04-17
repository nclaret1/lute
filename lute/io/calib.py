"""Tools for mapping sources and groups to a detector type.

The current implementation tries to mimic the PSCalib source code
from CalibFileFinder.py (https://github.com/lcls-psana/PSCalib/blob/master/src/CalibFileFinder.py).

Functions:
    group_from_det_type(det_type: str) -> str: Retrieve the group string

    source_from_det_info(det_type: str, hutch: str) -> str: Retrieve the source string

Exceptions:
"""

import os

from typing import Optional

__all__ = ["group_from_det_type", "source_from_det_info"]
__author__ = "Louis Conreux"

calib_groups = (
    "UNDEFINED",
    "CsPad::CalibV1",
    "CsPad2x2::CalibV1",
    "Princeton::CalibV1",
    "PNCCD::CalibV1",
    "Camera::CalibV1",
    "Camera::CalibV1",
    "Camera::CalibV1",
    "Camera::CalibV1",
    "Camera::CalibV1",
    "Camera::CalibV1",
    "Epix::CalibV1",
    "Epix10ka::CalibV1",
    "Epix100a::CalibV1",
    "Camera::CalibV1",
    "Andor::CalibV1",
    "Acqiris::CalibV1",
    "Imp::CalibV1",
    "Camera::CalibV1",
    "Camera::CalibV1",
    "EvrData::CalibV1",
    "Camera::CalibV1",
    "Timepix::CalibV1",
    "Fli::CalibV1",
    "Pimax::CalibV1",
    "Andor3d::CalibV1",
    "Jungfrau::CalibV1",
    "Jungfrau::CalibV1",
    "Jungfrau::CalibV1",
    "Camera::CalibV1",
    "Camera::CalibV1",
    "Epix10ka::CalibV1",
    "Uxi::CalibV1",
    "Pixis::CalibV1",
    "Epix10ka2M::CalibV1",
    "Epix10kaQuad::CalibV1",
    "Epix10kaQuad::CalibV1",
    "Epix10kaQuad::CalibV1",
    "Epix10kaQuad::CalibV1",
    "Camera::CalibV1",
    "Camera::CalibV1",
    "Camera::CalibV1",
    "Camera::CalibV1",
)

psana_det_names = (
    "UNDEFINED",
    "Cspad",
    "Cspad2x2",
    "Princeton",
    "pnCCD",
    "Tm6740",
    "Opal1000",
    "Opal2000",
    "Opal4000",
    "Opal8000",
    "OrcaFl40",
    "Epix",
    "Epix10k",
    "Epix100a",
    "Fccd960",
    "Andor",
    "Acqiris",
    "Imp",
    "Quartz4A150",
    "Rayonix",
    "Evr",
    "Fccd",
    "Timepix",
    "Fli",
    "Pimax",
    "Andor3d",
    "Jungfrau",
    "Jungfrau1M",
    "Jungfrau4M",
    "Zyla",
    "ControlsCamera",
    "Epix10ka",
    "Uxi",
    "Pixis",
    "Epix10k2M",
    "Epix10kaQuad0",
    "Epix10kaQuad1",
    "Epix10kaQuad2",
    "Epix10kaQuad3",
    "Streak",
    "Archon",
    "iStar",
    "Alvium",
)

psana_det_names_lower = tuple(name.lower() for name in psana_det_names)

calib_det_names = (
    "UNDEFINED",
    "Cspad",
    "Cspad2x2",
    "Princeton",
    "pnCCD",
    "Tm6740",
    "Opal1000",
    "Opal2000",
    "Opal4000",
    "Opal8000",
    "OrcaFl40",
    "Epix",
    "Epix10k",
    "Epix100a",
    "Fccd960",
    "Andor",
    "Acqiris",
    "Imp",
    "Quartz4A150",
    "Rayonix",
    "Evr",
    "Fccd",
    "Timepix",
    "Fli",
    "Pimax",
    "Andor3d",
    "Jungfrau",
    "Jungfrau",
    "Jungfrau",
    "Zyla",
    "ControlsCamera",
    "Epix10ka",
    "Uxi",
    "Pixis",
    "Epix10ka2M",
    "Epix10kaQuad.0",
    "Epix10kaQuad.1",
    "Epix10kaQuad.2",
    "Epix10kaQuad.3",
    "Streak",
    "Archon",
    "iStar",
    "Alvium",
)

hutches = (
    "UNDEFINED",
    "XPP",
    "XCS",
    "CXI",
    "MEC",
    "MFX",
)

stations = (
    "UNDEFINED",
    "XppEndstation.0",
    "XcsEndstation.0",
    "CxiDs1.0",
    "MecTargetChamber.0",
    "MfxEndstation.0",
)


psana_to_calib_det_name = dict(zip(psana_det_names_lower, calib_det_names))

det_to_group = dict(zip(calib_det_names, calib_groups))

hutch_to_station = dict(zip(hutches, stations))


def group_from_det_type(det_type: str) -> Optional[str]:
    """Retrieve the group string from the detector type."""
    det_type_lower = det_type.lower()
    det_name = psana_to_calib_det_name.get(det_type_lower, "UNDEFINED")
    if det_name == "UNDEFINED":
        raise ValueError(f"Unknown detector type: {det_type}")
    group = det_to_group.get(det_name)
    return group


def source_from_det_info(det_type: str, hutch: str) -> Optional[str]:
    """Retrieve the source string from the detector type and hutch."""
    hutch_upper = hutch.upper()
    station = hutch_to_station.get(hutch_upper, "UNDEFINED")
    if station == "UNDEFINED":
        raise ValueError(f"Unknown hutch: {hutch}")
    det_type_lower = det_type.lower()
    det_name = psana_to_calib_det_name.get(det_type_lower, "UNDEFINED")
    if det_name == "UNDEFINED":
        raise ValueError(f"Unknown detector type: {det_type}")
    if "Epix10kaQuad" in det_name:
        return f"{station}:{det_name}"
    return f"{station}:{det_name}.0"


def select_calib_file(calib_dir: str, run: int) -> Optional[str]:
    """Select the calibration file from the calibration directory and run number."""
    fnames = os.listdir(calib_dir)
    files = [os.path.join(calib_dir, fname) for fname in fnames]

    run_max = 9999
    run_files = []
    for file in files:
        f = os.path.basename(file)
        if f == "HISTORY":
            continue
        if os.path.splitext(f)[1] != ".data":
            continue
        basename = os.path.splitext(f)[0]
        fields = basename.split("-")
        begin, end = fields

        if begin.isdigit():
            begin_int = int(begin)
            if begin_int >= run_max:
                raise ValueError(
                    f"Begin run number {run} is too high for calibration directory {calib_dir}"
                )

        if end.isdigit():
            end_int = int(end)
            if end_int >= run_max:
                raise ValueError(
                    f"End run number {run} is too high for calibration directory {calib_dir}"
                )
        elif end == "end":
            end_int = run_max

        run_files.append((begin_int, end_int, file))
    run_files.sort(key=lambda x: int(x[0]))

    for run_file in run_files[::-1]:
        if run_file[0] <= run <= run_file[1]:
            return run_file[2]

    return ""
