#!/usr/bin/env python3
"""Run CrystFEL indexing for three DWT scenarios on the DWT reconstructed image.

Scenario 1 — v3r3_on_dwt   (1 run):
    DWT image + v3r3 peaks found ON the DWT reconstructed image.

Scenario 2 — abs_indexed   (one run per abs threshold):
    DWT image + DWT absolute-threshold peaks.

Scenario 3 — sig_indexed   (one run per sig threshold):
    DWT image + DWT sigma-threshold peaks.

All share the same DWT reconstructed image from rank-0 CXI files.
CXI peak format:  fast-scan X = col,  slow-scan Y = panel*352 + local_row.

Steps:
    1. Build synthetic CXI files (locally, fast).
    2. Submit all SLURM indexing jobs simultaneously.
    3. Wait for all jobs and report.

Output directories:
    <DWT_BASE>/dwt_indexed/v3r3_on_dwt/r0016/
    <DWT_BASE>/dwt_indexed/abs_thr_{N}/r0016/
    <DWT_BASE>/dwt_indexed/sig_thr_{N}/r0016/
"""

import re
import subprocess
import time
from pathlib import Path
from typing import Optional

import h5py
import numpy as np

# ── config ─────────────────────────────────────────────────────────────────────
DWT_BASE = Path("/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret"
                "/lute_output/Wavelet/dionisio_DWT_peakfinding")

SIG_SWEEP  = DWT_BASE / "sig_threshold_sweep"
ABS_SWEEP  = DWT_BASE / "abs_threshold_sweep"
OUT_BASE   = DWT_BASE / "dwt_indexed"
YAML_DIR   = Path("/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute/config/_auto")

ABS_THRS = [100, 125, 150, 175, 200, 300, 500]
SIG_THRS = [5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17, 19, 21, 23, 25, 27, 29, 30, 50]

GEOM        = "/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/geom/r0016.geom"
CELL        = "/sdf/data/lcls/ds/mfx/mfxx49820/results/btx_pypca/cell/reference.cell"
CRYSTFEL_BIN= "/sdf/group/lcls/ds/tools/crystfel/0.10.2/bin/indexamajig"

PSCONDA  = "/sdf/group/lcls/ds/ana/sw/conda1/manage/bin/psconda.sh"
LUTE_ACT = ("/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute"
            "/install/bin/activate_installation")

ROWS = 352; COLS = 384; N_PANELS = 16; MAX_CXI_PEAKS = 2048
POLL = 30

# ── YAML template for CrystFELIndexer (no peakfinding block needed) ──────────
YAML_TEMPLATE = """\
%YAML 1.3
---
title: "DWT indexed sweep — {label}"
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
geom_file: "{geom}"

IndexCrystFEL:
  in_file: "{list_file}"
  executable: "{crystfel_bin}"
  out_file: "{stream_file}"
  geometry: "{geom}"
  no_revalidate: True
  multi: True
  indexing: "mosflm,xds"
  int_radius: "3,4,5"
  profile: True
  peaks: "cxi"
  tolerance: "5,5,5,1.5"
  cell_file: "{cell}"

...
"""


# ── helpers ────────────────────────────────────────────────────────────────────

def bash_capture(cmd):
    return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)


def parse_job_id(text):
    for line in text.splitlines():
        m = re.search(r"Submitted batch job\s+(\d+)", line)
        if m:
            return m.group(1)
    return None


def stream_is_done(path):
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return False
    try:
        with open(path) as f:
            for line in f:
                if "----- End chunk -----" in line:
                    return True
    except Exception:
        return False
    return False


def _write_cxi(out_cxi: Path, images: np.ndarray, npeaks, px, py, pi,
               lcls_fid=None, lcls_time=None, lcls_ev=None, lcls_clen=None):
    with h5py.File(out_cxi, "w", libver="earliest") as f:
        f.create_dataset("entry_1/data_1/data", data=images.astype(np.float32),
                         compression="gzip", compression_opts=1)
        f["entry_1/data_1/mask"]              = np.zeros((N_PANELS*ROWS, COLS),
                                                          dtype=np.float64)
        f["entry_1/result_1/nPeaks"]             = npeaks
        f["entry_1/result_1/peakXPosRaw"]        = px
        f["entry_1/result_1/peakYPosRaw"]        = py
        f["entry_1/result_1/peakTotalIntensity"] = pi
        f["entry_1/result_1/peakMaxIntensity"]   = pi
        if lcls_fid  is not None: f["LCLS/fiducial"]                = lcls_fid
        if lcls_time is not None: f["LCLS/machineTime"]             = lcls_time
        if lcls_ev   is not None: f["LCLS/photon_energy_eV"]        = lcls_ev
        if lcls_clen is not None: f["LCLS/detector_1/EncoderValue"] = lcls_clen


def load_rank0_images_and_lcls(rank0_cxi: Path, n_hits: int):
    """Return (images, fids, times, evs, clens), each cropped to n_hits."""
    with h5py.File(rank0_cxi, "r") as f:
        n_cxi  = f["entry_1/data_1/data"].shape[0]
        n_use  = min(n_hits, n_cxi)
        images = f["entry_1/data_1/data"][:n_use]
        fids   = f["LCLS/fiducial"][:n_use]   if "LCLS/fiducial"              in f else None
        times  = f["LCLS/machineTime"][:n_use] if "LCLS/machineTime"          in f else None
        evs    = f["LCLS/photon_energy_eV"][:n_use] if "LCLS/photon_energy_eV" in f else None
        clens  = f["LCLS/detector_1/EncoderValue"][:n_use] \
                 if "LCLS/detector_1/EncoderValue" in f else None
    return images, fids, times, evs, clens, n_use


def peaks_to_cxi_arrays(peak_list, col_x=2, col_y_panel=0, col_y_row=1, col_i=4):
    """Convert list of (M, K) peak arrays to CXI-ready arrays.

    For DWT peaks (14 cols):    col_x=2, col_y_panel=0, col_y_row=1, col_i=4
    For v3r3/bl peaks (17 cols):col_x=2, col_y_panel=0, col_y_row=1, col_i=5
    """
    n = len(peak_list)
    npeaks = np.zeros(n, dtype=np.int32)
    px     = np.zeros((n, MAX_CXI_PEAKS), dtype=np.float32)
    py     = np.zeros((n, MAX_CXI_PEAKS), dtype=np.float32)
    pi     = np.zeros((n, MAX_CXI_PEAKS), dtype=np.float32)
    for i, bp in enumerate(peak_list):
        if bp.shape[0] == 0:
            continue
        k = min(bp.shape[0], MAX_CXI_PEAKS)
        npeaks[i]  = k
        px[i, :k]  = bp[:k, col_x]
        py[i, :k]  = bp[:k, col_y_panel] * ROWS + bp[:k, col_y_row]
        pi[i, :k]  = bp[:k, col_i]
    return npeaks, px, py, pi


def build_cxi(out_cxi: Path, rank0_cxi: Path, h5_path: Path,
              peak_key: str, col_i: int, label: str) -> Optional[int]:
    """Build synthetic CXI with DWT images + specified peaks from H5.

    peak_key : 'dwt_peaks' | 'v3r3_peaks' | 'baseline_v3r3_peaks'
    col_i    : intensity column index in that peak array
    Returns  : number of events written, or None on failure.
    """
    if out_cxi.exists():
        print(f"    CXI already exists: {out_cxi.name}", flush=True)
        return None  # already done

    if not rank0_cxi.exists() or not h5_path.exists():
        print(f"    MISSING: {rank0_cxi.name} or {h5_path.name}", flush=True)
        return None

    # collect hits + peaks from H5
    with h5py.File(h5_path, "r") as f:
        pc     = f["peak_comparison"]
        ev_ids = sorted(int(k.split("_")[1]) for k in pc.keys())
        hits   = [i for i in ev_ids
                  if 10 <= int(pc[f"event_{i}"].attrs.get("dwt_n_peaks", 0)) <= 2048]
        peak_list = []
        for ev in hits:
            grp = pc[f"event_{ev}"]
            bp = grp[peak_key][:] if peak_key in grp else np.empty((0, 14), dtype=np.float32)
            peak_list.append(bp)

    n_hits = len(hits)
    if n_hits == 0:
        print(f"    No hits in H5.", flush=True)
        return None

    try:
        images, fids, times, evs, clens, n_use = load_rank0_images_and_lcls(
            rank0_cxi, n_hits)
    except OSError as e:
        print(f"    CXI read error: {e}", flush=True)
        return None

    n_hits     = n_use
    peak_list  = peak_list[:n_hits]

    col_x_val = 2; col_yp = 0; col_yr = 1
    npeaks, px, py, pi = peaks_to_cxi_arrays(
        peak_list, col_x=col_x_val, col_y_panel=col_yp, col_y_row=col_yr, col_i=col_i)

    _write_cxi(out_cxi, images, npeaks, px, py, pi,
               lcls_fid=fids, lcls_time=times, lcls_ev=evs, lcls_clen=clens)

    avg_n = float(npeaks.mean()) if n_hits > 0 else 0.0
    print(f"    Built {label} CXI: {n_hits} events, "
          f"avg {avg_n:.1f} peaks/evt → {out_cxi.name}", flush=True)
    return n_hits


def submit_indexer(work_dir: Path, list_file: Path, stream_file: Path,
                   label: str) -> Optional[str]:
    """Write YAML and submit CrystFELIndexer via LUTE. Returns SLURM job id."""
    if stream_is_done(str(stream_file)):
        print(f"    Stream already complete — skipping.", flush=True)
        return None

    yaml_path = YAML_DIR / f"dwt_index_{label}.yaml"
    yaml_path.write_text(YAML_TEMPLATE.format(
        label=label, work_dir=str(work_dir),
        geom=GEOM, list_file=str(list_file),
        stream_file=str(stream_file), crystfel_bin=CRYSTFEL_BIN, cell=CELL,
    ))

    if stream_file.exists():
        stream_file.unlink()

    cmd = (f"source {PSCONDA} && source {LUTE_ACT} && "
           f"submit_slurm -t CrystFELIndexer -c {yaml_path} "
           f"-e mfxx49820 -r 16 --partition=milano "
           f"--account=lcls:mfxx49820 --ntasks=100")
    r = bash_capture(cmd)
    print(r.stdout, end="", flush=True)
    if r.stderr:
        print(r.stderr, end="", flush=True)
    job_id = parse_job_id(r.stdout + r.stderr)
    if job_id:
        print(f"    Submitted SLURM job {job_id}", flush=True)
    return job_id


# ── scenario builders ─────────────────────────────────────────────────────────

def build_v3r3_on_dwt():
    """Case 1: DWT image + v3r3 peaks found on DWT reconstructed image."""
    # Use sig_thr_15 as source (stable, well-populated)
    src_h5  = SIG_SWEEP / "sig_thr_15" / "r0016" / "r0016_metrics_rank000.h5"
    src_cxi = SIG_SWEEP / "sig_thr_15" / "r0016" / "mfxx49820_r0016_0.cxi"
    out_dir = OUT_BASE / "v3r3_on_dwt" / "r0016"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("\n=== Case 1: v3r3 peaks on DWT image ===", flush=True)
    out_cxi    = out_dir / "peaks_input.cxi"
    list_file  = out_dir / "input.list"
    stream_out = out_dir / "stream_16.stream"

    build_cxi(out_cxi, src_cxi, src_h5,
              peak_key="v3r3_peaks", col_i=5, label="v3r3_on_dwt")
    if out_cxi.exists():
        list_file.write_text(str(out_cxi) + "\n")
        return [("v3r3_on_dwt", out_dir, list_file, stream_out)]
    return []


def build_abs_sweep():
    """Case 2: DWT image + DWT abs-threshold peaks."""
    jobs = []
    print("\n=== Case 2: DWT abs peaks on DWT image ===", flush=True)
    for thr in ABS_THRS:
        label   = f"abs_{thr}"
        src_h5  = ABS_SWEEP / f"abs_thr_{thr}" / "r0016" / "r0016_metrics_rank000.h5"
        src_cxi = ABS_SWEEP / f"abs_thr_{thr}" / "r0016" / "mfxx49820_r0016_0.cxi"
        out_dir = OUT_BASE / "abs_threshold_sweep" / f"abs_thr_{thr}" / "r0016"
        out_dir.mkdir(parents=True, exist_ok=True)

        out_cxi    = out_dir / "peaks_input.cxi"
        list_file  = out_dir / "input.list"
        stream_out = out_dir / "stream_16.stream"

        print(f"  {label}:", flush=True)
        build_cxi(out_cxi, src_cxi, src_h5,
                  peak_key="dwt_peaks", col_i=4, label=label)
        if out_cxi.exists():
            list_file.write_text(str(out_cxi) + "\n")
            jobs.append((label, out_dir, list_file, stream_out))
    return jobs


def build_sig_sweep():
    """Case 3: DWT image + DWT sig-threshold peaks."""
    jobs = []
    print("\n=== Case 3: DWT sig peaks on DWT image ===", flush=True)
    for thr in SIG_THRS:
        label   = f"sig_{thr}"
        src_h5  = SIG_SWEEP / f"sig_thr_{thr}" / "r0016" / "r0016_metrics_rank000.h5"
        src_cxi = SIG_SWEEP / f"sig_thr_{thr}" / "r0016" / "mfxx49820_r0016_0.cxi"
        out_dir = OUT_BASE / "sig_threshold_sweep" / f"sig_thr_{thr}" / "r0016"
        out_dir.mkdir(parents=True, exist_ok=True)

        out_cxi    = out_dir / "peaks_input.cxi"
        list_file  = out_dir / "input.list"
        stream_out = out_dir / "stream_16.stream"

        print(f"  {label}:", flush=True)
        build_cxi(out_cxi, src_cxi, src_h5,
                  peak_key="dwt_peaks", col_i=4, label=label)
        if out_cxi.exists():
            list_file.write_text(str(out_cxi) + "\n")
            jobs.append((label, out_dir, list_file, stream_out))
    return jobs


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    print("=== DWT Indexed Sweep ===\n")
    print("Step 1: Building synthetic CXI files ...", flush=True)

    all_jobs = []
    all_jobs.extend(build_v3r3_on_dwt())
    all_jobs.extend(build_abs_sweep())
    all_jobs.extend(build_sig_sweep())

    print(f"\nStep 2: Submitting {len(all_jobs)} indexing jobs simultaneously ...",
          flush=True)

    pending = {}
    for label, out_dir, list_file, stream_out in all_jobs:
        if not list_file.exists():
            print(f"  {label}: list file missing — skipping.", flush=True)
            continue
        job_id = submit_indexer(out_dir, list_file, stream_out, label)
        if job_id:
            pending[job_id] = (label, stream_out)

    if not pending:
        print("No jobs submitted (all already done or skipped).", flush=True)
        return

    print(f"\nStep 3: Waiting for {len(pending)} jobs ...", flush=True)
    while pending:
        done = []
        for job_id, (label, stream_out) in pending.items():
            r = bash_capture(f"squeue -j {job_id} -h 2>/dev/null")
            if not r.stdout.strip():
                ok = stream_is_done(str(stream_out))
                print(f"  {label} (job {job_id}): "
                      f"{'DONE ✓' if ok else 'FINISHED (stream empty?)'}", flush=True)
                done.append(job_id)
        for j in done:
            del pending[j]
        if pending:
            print(f"  {len(pending)} jobs still running ...", flush=True)
            time.sleep(POLL)

    # Summary
    print("\n=== Summary ===")
    for label, out_dir, list_file, stream_out in all_jobs:
        ok = stream_is_done(str(stream_out))
        print(f"  {label}: {'OK' if ok else 'MISSING/EMPTY'} — {stream_out}")


if __name__ == "__main__":
    main()
