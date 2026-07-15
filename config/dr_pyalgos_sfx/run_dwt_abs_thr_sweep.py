#!/usr/bin/env python3
"""Orchestrate DWT absolute-threshold sweep.

For each threshold in DWT_PEAK_ABS_THR:
  1. Create output directory
  2. Write a YAML config in lute/config/_auto/
  3. Submit the SLURM peakfinder job and wait for completion
  4. Run the Bokeh visualisation; save HTML next to the CXI/H5 output

Run with the psconda + lute-install environment active:
  source /sdf/group/lcls/ds/ana/sw/conda1/manage/bin/psconda.sh
  source /sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute/install/bin/activate_installation
  python3 run_dwt_abs_thr_sweep.py

Based on the abs_thr=10 run, the DWT peak count per event by threshold is roughly:
  thr= 75 →  ~2 745 peaks/event  (above max_peaks=2048)
  thr=100 →    ~282 peaks/event  (well within max_peaks)
  thr=125 →    ~100 peaks/event  (estimated)
  thr=150 →     ~14 peaks/event  (near min_peaks=10)
  thr=175 →     ~12 peaks/event
  thr=200 →     ~11 peaks/event
  thr=300 →      ~7 peaks/event  (below min_peaks)
  thr=500 →      ~5 peaks/event
"""

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# ── configuration ──────────────────────────────────────────────────────────────
DWT_PEAK_ABS_THR = [75, 100, 125, 150, 175, 200, 300, 500]

SWEEP_BASE = (
    "/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute_output/Wavelet"
    "/dionisio_DWT_peakfinding/abs_threshold_sweep"
)
YAML_DIR = (
    "/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute/config/_auto"
)
VIS_SCRIPT = (
    "/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/analysis"
    "/viewing_scripts/view_dwt_abs_thr_sweep.py"
)

PSCONDA   = "/sdf/group/lcls/ds/ana/sw/conda1/manage/bin/psconda.sh"
LUTE_ACT  = ("/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute"
             "/install/bin/activate_installation")

SLURM_POLL_INTERVAL = 30   # seconds between squeue checks

# ── YAML template ──────────────────────────────────────────────────────────────
# Use {{ }} for Jinja2 placeholders (Python str.format escapes them as {{ }})
YAML_TEMPLATE = """\
%YAML 1.3
---
# ========= HEADER (global) =========
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
DWT_PEAK_ABS_THR: {thr}
DWT_PEAK_THR: 10.0

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
  use_dwt_abs_thr: True
  dwt_peak_find_abs_thr: "{{{{ DWT_PEAK_ABS_THR }}}}"
  dwt_peak_find_sig_factor: "{{{{ DWT_PEAK_THR }}}}"

...
"""


# ── helpers ────────────────────────────────────────────────────────────────────
def thr_tag(thr: float) -> str:
    """Integer string for use in directory / file names."""
    return str(int(thr))


def bash(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a multi-line bash snippet (sources are supported)."""
    return subprocess.run(
        ["bash", "-c", cmd],
        capture_output=False,
        text=True,
        check=check,
    )


def bash_capture(cmd: str) -> subprocess.CompletedProcess:
    """Run bash and capture stdout + stderr."""
    return subprocess.run(
        ["bash", "-c", cmd],
        capture_output=True,
        text=True,
    )


def wait_for_job(job_id: str) -> None:
    """Poll squeue until the SLURM job is no longer listed."""
    print(f"  Waiting for SLURM job {job_id} ...", flush=True)
    while True:
        result = bash_capture(f"squeue -j {job_id} -h 2>/dev/null")
        if not result.stdout.strip():
            break
        time.sleep(SLURM_POLL_INTERVAL)
    print(f"  Job {job_id} finished.", flush=True)


def parse_job_id(text: str) -> Optional[str]:
    """Extract SLURM job ID from 'Submitted batch job XXXXXXX'."""
    for line in text.splitlines():
        m = re.search(r"Submitted batch job\s+(\d+)", line)
        if m:
            return m.group(1)
    return None


def h5_has_peaks(h5_path: str) -> bool:
    """Return True if H5 exists and contains a valid peak_comparison group."""
    import h5py
    if not Path(h5_path).exists():
        return False
    try:
        with h5py.File(h5_path, "r") as f:
            return "peak_comparison" in f and len(f["peak_comparison"]) > 0
    except Exception:
        return False


def cleanup_rank_cxi(work_dir: str) -> None:
    """Delete rank 1+ CXI files, keeping only _0.cxi."""
    deleted = 0
    for cxi in Path(work_dir).glob("mfxx49820_r0016_[1-9]*.cxi"):
        cxi.unlink()
        deleted += 1
    if deleted:
        print(f"  Deleted {deleted} rank CXI files.", flush=True)


# ── main sweep ─────────────────────────────────────────────────────────────────
def main():
    for thr in DWT_PEAK_ABS_THR:
        tag      = thr_tag(thr)
        work_dir = f"{SWEEP_BASE}/abs_thr_{tag}/r0016"
        yaml_path = f"{YAML_DIR}/dionisio_dwt_peakfind_thr{tag}.yaml"
        h5_path   = f"{work_dir}/r0016_metrics_rank000.h5"
        cxi_path  = (f"{work_dir}/mfxx49820_r0016_.cxi"
                     if Path(f"{work_dir}/mfxx49820_r0016_.cxi").exists()
                     else f"{work_dir}/mfxx49820_r0016_0.cxi")
        html_out  = f"{work_dir}/dwt_abs_thr{tag}_peakfinder_compare_r0016.html"

        print(f"\n{'='*60}", flush=True)
        print(f"abs_thr = {thr}", flush=True)

        # ── 1. create output directory ────────────────────────────────────────
        Path(work_dir).mkdir(parents=True, exist_ok=True)
        print(f"  Output dir: {work_dir}", flush=True)

        # ── 2/3. skip SLURM if H5 already has valid peak data ─────────────────
        if h5_has_peaks(h5_path):
            print(f"  H5 already complete — skipping SLURM submission.", flush=True)
        else:
            # write YAML and submit
            yaml_content = YAML_TEMPLATE.format(work_dir=work_dir, thr=thr)
            Path(yaml_path).write_text(yaml_content)
            print(f"  YAML:       {yaml_path}", flush=True)

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
            print(f"  Submitting job ...", flush=True)
            result = bash_capture(submit_cmd)
            print(result.stdout, end="", flush=True)
            if result.stderr:
                print(result.stderr, end="", flush=True)

            job_id = parse_job_id(result.stdout + result.stderr)
            if job_id:
                wait_for_job(job_id)
            else:
                print("  Could not parse SLURM job ID — waiting for H5 output ...",
                      flush=True)
                for _ in range(120):
                    if Path(h5_path).exists():
                        break
                    time.sleep(30)
                else:
                    print(f"  WARNING: {h5_path} not found after 60 min, skipping.",
                          flush=True)
                    continue

        # ── 4. run visualization ──────────────────────────────────────────────
        if not Path(h5_path).exists():
            print(f"  WARNING: {h5_path} not found — skipping visualisation.",
                  flush=True)
            continue

        cxi_arg = f"--cxi {cxi_path}" if Path(cxi_path).exists() else ""
        vis_cmd = (
            f"source {PSCONDA} && "
            f"python3 {VIS_SCRIPT} "
            f"  --h5 {h5_path} "
            f"  {cxi_arg} "
            f"  --out {html_out} "
            f"  --thr {thr}"
        )
        print(f"  Running visualisation ...", flush=True)
        bash(vis_cmd, check=False)
        print(f"  HTML: {html_out}", flush=True)

        # ── 5. clean up rank 1+ CXI files ─────────────────────────────────────
        cleanup_rank_cxi(work_dir)

    print(f"\n{'='*60}", flush=True)
    print("Sweep complete.", flush=True)


if __name__ == "__main__":
    main()
