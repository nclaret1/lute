#!/usr/bin/env python3
"""Run DWT sigma-threshold peakfinding (no indexing, no visualisation).

Submits DrPeakFinderPyAlgos SLURM jobs for each sig threshold, waits for
completion, then deletes non-rank-0 CXI files to save disk space.

Skips thresholds where rank000.h5 already has valid peak_comparison data.
"""

import re
import subprocess
import time
from pathlib import Path
from typing import Optional

NEW_SIG_THRS = [21]

SWEEP_BASE = (
    "/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute_output/Wavelet"
    "/dionisio_DWT_peakfinding/sig_threshold_sweep"
)
YAML_DIR = (
    "/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute/config/_auto"
)

PSCONDA  = "/sdf/group/lcls/ds/ana/sw/conda1/manage/bin/psconda.sh"
LUTE_ACT = ("/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute"
            "/install/bin/activate_installation")

SLURM_POLL_INTERVAL = 30

YAML_TEMPLATE = """\
%YAML 1.3
---
title: "SFX: Pipeline for Dr + PeakFinding"
experiment: "mfxx49820"
run: "16"
run_pad: "0016"
date: "2025/10/15"
lute_version: 0.1
task_timeout: 259200

work_dir: "{work_dir}"
...
---
detector: "epix10k2M"
out_root:  "{{{{ work_dir }}}}"
pv_camera_length: "MFX:ROB:CONT:POS:Z"
event_receiver: "evr0"
geom_file: "/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/geom/r0016.geom"
DWT_PEAK_SIG_THR: {sig_thr}
DWT_PEAK_ABS_THR: 0.0

DrFindPeaksPyAlgos:
  outdir: "{{{{ out_root }}}}"
  det_name: "{{{{ detector }}}}"
  event_receiver: "{{{{ event_receiver }}}}"
  pv_camera_length: "{{{{ pv_camera_length }}}}"

  n_events: 0
  psana_mask: false
  min_peaks: 10
  max_peaks: 2048
  npix_min: 2
  npix_max: 30
  amax_thr: 40
  atot_thr: 180
  son_min: 10
  peak_rank: 3
  r0: 3.0
  dr: 2.0
  nsigm: 10.0
  tol: 1e-3
  max_iter: 500
  dr_component: "X_hat"
  dr_method: "wavelet_dionisio"
  size: 7
  post_threshold_scale: 0
  use_dwt_abs_thr: False
  use_dwt_sig_factor: True
  dwt_peak_find_abs_thr: "{{{{ DWT_PEAK_ABS_THR }}}}"
  dwt_peak_find_sig_factor: "{{{{ DWT_PEAK_SIG_THR }}}}"

...
"""


def bash_capture(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)


def wait_for_job(job_id: str) -> None:
    print(f"  Waiting for SLURM job {job_id} ...", flush=True)
    while True:
        result = bash_capture(f"squeue -j {job_id} -h 2>/dev/null")
        if not result.stdout.strip():
            break
        time.sleep(SLURM_POLL_INTERVAL)
    print(f"  Job {job_id} finished.", flush=True)


def parse_job_id(text: str) -> Optional[str]:
    for line in text.splitlines():
        m = re.search(r"Submitted batch job\s+(\d+)", line)
        if m:
            return m.group(1)
    return None


def h5_has_peaks(h5_path: str) -> bool:
    import h5py
    if not Path(h5_path).exists():
        return False
    try:
        with h5py.File(h5_path, "r") as f:
            return "peak_comparison" in f and len(f["peak_comparison"]) > 0
    except Exception:
        return False


def cleanup_rank_cxi(work_dir: str) -> None:
    deleted = 0
    for cxi in Path(work_dir).glob("mfxx49820_r0016_[1-9]*.cxi"):
        cxi.unlink()
        deleted += 1
    # also remove the VDS file (broken once per-rank files are gone)
    vds = Path(work_dir) / "mfxx49820_r0016.cxi"
    if vds.exists():
        vds.unlink()
        deleted += 1
    if deleted:
        print(f"  Cleaned up {deleted} CXI files.", flush=True)


def main():
    print(f"Running peakfinding for sig thresholds: {NEW_SIG_THRS}\n")
    for sig_thr in NEW_SIG_THRS:
        tag      = str(int(sig_thr))
        work_dir = f"{SWEEP_BASE}/sig_thr_{tag}/r0016"
        yaml_path = f"{YAML_DIR}/dionisio_dwt_peakfind_sig_thr{tag}.yaml"
        h5_path  = f"{work_dir}/r0016_metrics_rank000.h5"

        print(f"\n{'='*55}", flush=True)
        print(f"sig_thr = {sig_thr}", flush=True)
        Path(work_dir).mkdir(parents=True, exist_ok=True)

        if h5_has_peaks(h5_path):
            print(f"  H5 already complete — skipping.", flush=True)
            cleanup_rank_cxi(work_dir)
            continue

        print(f"  Submitting peakfinder ...", flush=True)
        yaml_content = YAML_TEMPLATE.format(work_dir=work_dir, sig_thr=sig_thr)
        Path(yaml_path).write_text(yaml_content)

        submit_cmd = (
            f"source {PSCONDA} && "
            f"source {LUTE_ACT} && "
            f"submit_slurm "
            f"  -t DrPeakFinderPyAlgos "
            f"  -c {yaml_path} "
            f"  -e mfxx49820 "
            f"  -r 16 "
            f"  --partition=milano "
            f"  --account=lcls:mfxx49820 "
            f"  --ntasks=100"
        )
        result = bash_capture(submit_cmd)
        print(result.stdout, end="", flush=True)
        if result.stderr:
            print(result.stderr, end="", flush=True)

        job_id = parse_job_id(result.stdout + result.stderr)
        if job_id:
            wait_for_job(job_id)
        else:
            print("  Could not parse SLURM job ID — polling for H5 ...", flush=True)
            for _ in range(120):
                if Path(h5_path).exists():
                    break
                time.sleep(30)
            else:
                print(f"  WARNING: {h5_path} not found after 60 min, skipping.",
                      flush=True)
                continue

        if h5_has_peaks(h5_path):
            print(f"  Done: {h5_path}", flush=True)
        else:
            print(f"  WARNING: {h5_path} missing or empty after job.", flush=True)

        cleanup_rank_cxi(work_dir)

    print(f"\n{'='*55}", flush=True)
    print("All done.", flush=True)


if __name__ == "__main__":
    main()
