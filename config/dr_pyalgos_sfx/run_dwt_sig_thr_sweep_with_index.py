#!/usr/bin/env python3
"""Orchestrate DWT adaptive (sigma-factor) threshold sweep WITH CrystFEL indexing.

For each sigma factor in DWT_PEAK_SIG_THR:
  1. Create output directory
  2. Write a YAML config
  3. Submit the SLURM peakfinder job and wait for completion
  4. Build a synthetic CXI that keeps the DWT images but replaces the peak list
     with baseline v3r3 peaks (original-frame peaks as CrystFEL anchors)
  5. Submit CrystFELIndexer on the synthetic CXI and wait
  6. Run the Bokeh visualisation (with indexed reflections overlay)
  7. Clean up rank 1+ CXI files

Stream Event //N maps directly to the Nth rank-0 hit in H5 order.

Run with the psconda + lute-install environment active:
  source /sdf/group/lcls/ds/ana/sw/conda1/manage/bin/psconda.sh
  source /sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute/install/bin/activate_installation
  python3 run_dwt_sig_thr_sweep_with_index.py
"""

import re
import subprocess
import time
from pathlib import Path
from typing import Optional

# ── configuration ──────────────────────────────────────────────────────────────
DWT_PEAK_SIG_THR = [3, 5, 7, 10, 15, 20, 30, 50]

SWEEP_BASE = (
    "/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute_output/Wavelet"
    "/dionisio_DWT_peakfinding/sig_threshold_sweep"
)
YAML_DIR = (
    "/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute/config/_auto"
)
VIS_SCRIPT = (
    "/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/analysis"
    "/viewing_scripts/view_dwt_abs_thr_sweep.py"
)
GEOM   = "/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/geom/r0016.geom"
CELL   = "/sdf/data/lcls/ds/mfx/mfxx49820/results/btx_pypca/cell/reference.cell"
CRYSTFEL_BIN = "/sdf/group/lcls/ds/tools/crystfel/0.10.2/bin/indexamajig"

PSCONDA  = "/sdf/group/lcls/ds/ana/sw/conda1/manage/bin/psconda.sh"
LUTE_ACT = ("/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute"
            "/install/bin/activate_installation")

BASELINE_CXI = (
    "/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute_output"
    "/ZmqStream/baseline_peakfinder8/r0016/mfxx49820_r0016.cxi"
)
BASELINE_STREAM = (
    "/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute_output"
    "/ZmqStream/baseline_peakfinder8/r0016/stream_16.stream"
)

SLURM_POLL_INTERVAL = 30
N_PANELS = 16
ROWS     = 352
COLS     = 384
MAX_CXI_PEAKS = 2048

# ── YAML template ──────────────────────────────────────────────────────────────
YAML_TEMPLATE = """\
%YAML 1.3
---
title: "SFX: DWT sig_thr peakfind + baseline-v3r3 indexing"
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

IndexCrystFEL:
  in_file: "{work_dir}/mfxx49820_16_v3r3baseline.list"
  executable: "{crystfel_bin}"
  out_file: "{work_dir}/stream_16.stream"
  geometry: "{geom}"
  no_revalidate: True
  multi: True
  indexing: "mosflm,xds"
  int_radius: '3,4,5'
  profile: True
  peaks: "cxi"
  tolerance: "5,5,5,1.5"
  cell_file: "{cell}"

...
"""


# ── helpers ────────────────────────────────────────────────────────────────────
def thr_tag(thr: float) -> str:
    return str(int(thr))


def bash_capture(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)


def bash(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", cmd], capture_output=False,
                          text=True, check=check)


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


def stream_is_done(stream_path: str) -> bool:
    p = Path(stream_path)
    if not p.exists() or p.stat().st_size == 0:
        return False
    try:
        with open(stream_path) as f:
            for line in f:
                if "----- End chunk -----" in line:
                    return True
    except Exception:
        return False
    return False


def cleanup_rank_cxi(work_dir: str) -> None:
    deleted = 0
    for cxi in Path(work_dir).glob("mfxx49820_r0016_[1-9]*.cxi"):
        cxi.unlink()
        deleted += 1
    if deleted:
        print(f"  Deleted {deleted} rank CXI files.", flush=True)


def create_v3r3_input_cxi(work_dir: str, h5_path: str, rank0_cxi: str) -> Optional[str]:
    """Build a CXI using DWT images but baseline v3r3 peaks as the CrystFEL input.

    Returns path to the new CXI, or None if prerequisites are missing.
    Event //N in the resulting stream maps to all_hits[N] in the H5.
    """
    import h5py
    import numpy as np

    out_cxi = f"{work_dir}/mfxx49820_r0016_v3r3baseline.cxi"
    if Path(out_cxi).exists():
        print(f"  Synthetic CXI already exists: {out_cxi}", flush=True)
        return out_cxi

    if not Path(rank0_cxi).exists() or not Path(h5_path).exists():
        print(f"  WARNING: Missing rank-0 CXI or H5 — cannot build synthetic CXI.",
              flush=True)
        return None

    print(f"  Building synthetic CXI (DWT images + v3r3-baseline peaks) ...", flush=True)

    # ── collect hit event IDs and their baseline peaks from H5 ───────────────
    with h5py.File(h5_path, "r") as f:
        pc = f["peak_comparison"]
        ev_ids = sorted(int(k.split("_")[1]) for k in pc.keys())
        hits = [
            i for i in ev_ids
            if 10 <= int(pc[f"event_{i}"].attrs.get("dwt_n_peaks", 0)) <= 2048
        ]
        baseline_peaks = []
        for ev in hits:
            grp = pc[f"event_{ev}"]
            if "baseline_v3r3_peaks" in grp:
                bp = grp["baseline_v3r3_peaks"][:]   # (M, 17)
            else:
                bp = np.empty((0, 17), dtype=np.float32)
            baseline_peaks.append(bp)

    n_hits = len(hits)
    if n_hits == 0:
        print(f"  No hits in H5 — skipping synthetic CXI.", flush=True)
        return None

    # ── load DWT images for the hit CXI rows ────────────────────────────────
    try:
        with h5py.File(rank0_cxi, "r") as f:
            dset      = f["entry_1/data_1/data"]
            n_cxi     = dset.shape[0]
            lcls_fid       = f["LCLS/fiducial"][:] if "LCLS/fiducial" in f else None
            lcls_time      = f["LCLS/machineTime"][:] if "LCLS/machineTime" in f else None
            lcls_photon_ev = f["LCLS/photon_energy_eV"][:] if "LCLS/photon_energy_eV" in f else None
            lcls_clen      = f["LCLS/detector_1/EncoderValue"][:] if "LCLS/detector_1/EncoderValue" in f else None
            n_use          = min(n_hits, n_cxi)
            images    = dset[:n_use]   # (n_use, 5632, 384)
    except OSError as e:
        print(f"  WARNING: Cannot read rank-0 CXI ({e}) — skipping synthetic CXI.",
              flush=True)
        return None

    n_hits = n_use
    baseline_peaks = baseline_peaks[:n_hits]
    images_arr = images.astype(np.float32)

    # ── build peak arrays ────────────────────────────────────────────────────
    npeaks_arr = np.zeros(n_hits, dtype=np.int32)
    peak_x_arr = np.zeros((n_hits, MAX_CXI_PEAKS), dtype=np.float32)
    peak_y_arr = np.zeros((n_hits, MAX_CXI_PEAKS), dtype=np.float32)
    peak_i_arr = np.zeros((n_hits, MAX_CXI_PEAKS), dtype=np.float32)

    for i, bp in enumerate(baseline_peaks):
        if bp.shape[0] == 0:
            continue
        n = min(bp.shape[0], MAX_CXI_PEAKS)
        npeaks_arr[i]   = n
        peak_x_arr[i, :n] = bp[:n, 2]                      # col → fast-scan
        peak_y_arr[i, :n] = bp[:n, 0] * ROWS + bp[:n, 1]  # global slow-scan
        peak_i_arr[i, :n] = bp[:n, 5]                      # atot

    # ── write CXI ────────────────────────────────────────────────────────────
    # libver='earliest' ensures HDF5 1.12.x (CrystFEL) can read the file
    with h5py.File(out_cxi, "w", libver='earliest') as f:
        f.create_dataset("entry_1/data_1/data", data=images_arr,
                         compression="gzip", compression_opts=1)
        # All-zero mask: mask_good=0x0000 in geom means zero = good pixel
        f["entry_1/data_1/mask"]               = np.zeros((N_PANELS * ROWS, COLS), dtype=np.float64)
        f["entry_1/result_1/nPeaks"]              = npeaks_arr
        f["entry_1/result_1/peakXPosRaw"]         = peak_x_arr
        f["entry_1/result_1/peakYPosRaw"]         = peak_y_arr
        f["entry_1/result_1/peakTotalIntensity"]  = peak_i_arr
        f["entry_1/result_1/peakMaxIntensity"]    = peak_i_arr
        if lcls_fid is not None:
            f["LCLS/fiducial"]                = lcls_fid[:n_hits]
        if lcls_time is not None:
            f["LCLS/machineTime"]             = lcls_time[:n_hits]
        if lcls_photon_ev is not None:
            f["LCLS/photon_energy_eV"]        = lcls_photon_ev[:n_hits]
        if lcls_clen is not None:
            f["LCLS/detector_1/EncoderValue"] = lcls_clen[:n_hits]

    print(f"  Synthetic CXI: {out_cxi}  ({n_hits} events, "
          f"avg {npeaks_arr.mean():.1f} v3r3-baseline peaks/event)", flush=True)
    return out_cxi


# ── main sweep ─────────────────────────────────────────────────────────────────
def main():
    for sig_thr in DWT_PEAK_SIG_THR:
        tag         = thr_tag(sig_thr)
        work_dir    = f"{SWEEP_BASE}/sig_thr_{tag}/r0016"
        yaml_path   = f"{YAML_DIR}/dionisio_dwt_peakfind_sig_thr{tag}_indexed.yaml"
        h5_path     = f"{work_dir}/r0016_metrics_rank000.h5"
        rank0_cxi   = f"{work_dir}/mfxx49820_r0016_0.cxi"
        cxi_path    = (f"{work_dir}/mfxx49820_r0016_.cxi"
                       if Path(f"{work_dir}/mfxx49820_r0016_.cxi").exists()
                       else rank0_cxi)
        stream_path = f"{work_dir}/stream_16.stream"
        html_out    = f"{work_dir}/dwt_sig_thr{tag}_peakfinder_indexed_r0016.html"

        print(f"\n{'='*60}", flush=True)
        print(f"sig_thr = {sig_thr}", flush=True)

        Path(work_dir).mkdir(parents=True, exist_ok=True)
        print(f"  Output dir: {work_dir}", flush=True)

        # ── 2/3. peakfinding ─────────────────────────────────────────────────
        if h5_has_peaks(h5_path):
            print(f"  H5 already complete — skipping peakfinder.", flush=True)
        else:
            yaml_content = YAML_TEMPLATE.format(
                work_dir=work_dir, sig_thr=sig_thr,
                geom=GEOM, cell=CELL, crystfel_bin=CRYSTFEL_BIN,
            )
            Path(yaml_path).write_text(yaml_content)

            submit_cmd = (
                f"source {PSCONDA} && source {LUTE_ACT} && "
                f"submit_slurm -t DrPeakFinderPyAlgos -c {yaml_path} "
                f"-e mfxx49820 -r 16 --partition=milano "
                f"--account=lcls:mfxx49820 --ntasks=100"
            )
            print(f"  Submitting peakfinder ...", flush=True)
            result = bash_capture(submit_cmd)
            print(result.stdout, end="", flush=True)
            if result.stderr:
                print(result.stderr, end="", flush=True)
            job_id = parse_job_id(result.stdout + result.stderr)
            if job_id:
                wait_for_job(job_id)
            else:
                for _ in range(120):
                    if Path(h5_path).exists():
                        break
                    time.sleep(30)
                else:
                    print(f"  WARNING: {h5_path} not found — skipping.", flush=True)
                    continue

        if not Path(h5_path).exists():
            print(f"  WARNING: {h5_path} missing — skipping.", flush=True)
            continue

        # ── 4. build synthetic CXI + submit indexer ──────────────────────────
        if stream_is_done(stream_path):
            print(f"  Stream already complete — skipping indexer.", flush=True)
        else:
            synth_cxi = create_v3r3_input_cxi(work_dir, h5_path, rank0_cxi)
            if synth_cxi is None:
                print(f"  Skipping indexer (no synthetic CXI).", flush=True)
            else:
                yaml_content = YAML_TEMPLATE.format(
                    work_dir=work_dir, sig_thr=sig_thr,
                    geom=GEOM, cell=CELL, crystfel_bin=CRYSTFEL_BIN,
                )
                Path(yaml_path).write_text(yaml_content)
                v3r3_list = f"{work_dir}/mfxx49820_16_v3r3baseline.list"
                Path(v3r3_list).write_text(synth_cxi + "\n")

                # CrystFEL refuses to overwrite an existing stream; delete first
                if Path(stream_path).exists():
                    Path(stream_path).unlink()
                    print(f"  Deleted existing stream for overwrite.", flush=True)

                submit_cmd = (
                    f"source {PSCONDA} && source {LUTE_ACT} && "
                    f"submit_slurm -t CrystFELIndexer -c {yaml_path} "
                    f"-e mfxx49820 -r 16 --partition=milano "
                    f"--account=lcls:mfxx49820 --ntasks=100"
                )
                print(f"  Submitting indexer (v3r3-baseline anchors) ...", flush=True)
                result = bash_capture(submit_cmd)
                print(result.stdout, end="", flush=True)
                if result.stderr:
                    print(result.stderr, end="", flush=True)
                job_id = parse_job_id(result.stdout + result.stderr)
                if job_id:
                    wait_for_job(job_id)
                else:
                    for _ in range(240):
                        if stream_is_done(stream_path):
                            break
                        time.sleep(30)
                    else:
                        print(f"  WARNING: stream not complete — skipping viz.", flush=True)
                        continue

        # ── 5. visualisation ──────────────────────────────────────────────────
        cxi_arg    = f"--cxi {rank0_cxi}" if Path(rank0_cxi).exists() else ""
        bl_cxi_arg = f"--baseline-cxi {BASELINE_CXI}" if Path(BASELINE_CXI).exists() else ""
        stream_arg = f"--stream {BASELINE_STREAM}" if Path(BASELINE_STREAM).exists() else ""
        vis_cmd = (
            f"source {PSCONDA} && "
            f"python3 {VIS_SCRIPT} "
            f"  --h5 {h5_path} {cxi_arg} {bl_cxi_arg} {stream_arg} "
            f"  --out {html_out} --thr {sig_thr}"
        )
        print(f"  Running visualisation ...", flush=True)
        bash(vis_cmd, check=False)
        print(f"  HTML: {html_out}", flush=True)

        cleanup_rank_cxi(work_dir)

    print(f"\n{'='*60}", flush=True)
    print("Sweep complete.", flush=True)


if __name__ == "__main__":
    main()
