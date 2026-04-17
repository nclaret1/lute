#!/usr/bin/env bash
set -euo pipefail
#set -x
trap 'echo "FAILED at line $LINENO: $BASH_COMMAND" >&2' ERR

set +u
source /sdf/group/lcls/ds/ana/sw/conda1/rel/ami_current/setup_env_lcls1.sh
set -u

export PYTHONPATH="/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/decompy/src:${PYTHONPATH:-}"
export PYTHONPATH="/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute:${PYTHONPATH:-}"


# =========================
# User inputs
# =========================
EXPERIMENT="mfxx49820"
RUNS_TO_PROCESS=({16..17})

# List DR methods to run. Must match make_yaml_from_template.sh "METHODS" names.
#wavelet_mkt
DR_METHODS=( baseline wavelet_quant_zerotree_compress )
INCLUDE_S="${INCLUDE_S:-0}"

COMPONENTS=(X_hat)
if [[ "${INCLUDE_S}" == "1" ]]; then
  COMPONENTS+=(S)
fi

array_contains() {
  local needle="$1"; shift
  local item
  for item in "$@"; do
    [[ "$item" == "$needle" ]] && return 0
  done
  return 1
}

DO_BASELINE=0
if array_contains "baseline" "${DR_METHODS[@]}"; then
  DO_BASELINE=1
fi

DR_METHODS_NO_BASELINE=()
for m in "${DR_METHODS[@]}"; do
  [[ "$m" == "baseline" ]] && continue
  DR_METHODS_NO_BASELINE+=("$m")
done


# =========================
# Task names 
# =========================
TASK_NAME1="DrPeakFinderPyAlgos"
TASK_NAME2="CrystFELIndexer"
TASK_NAME3="StreamFileConcatenator"
TASK_NAME4="CleanupIntermediateFiles"
TASK_NAME5="PartialatorMerger"
TASK_NAME6="HKLComparer"

# =========================
# Paths
# =========================
MAKE_YAML_SCRIPT="/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute/config/dr_pyalgos_sfx/make_yaml_from_template.sh"
SUBMIT_SCRIPT="/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute/launch_scripts/submit_slurm.sh"

CONFIG_DIR="/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute/config/_auto"
LUTE_OUTPUT_DIR="/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute_output/Wavelet/test/"
mkdir -p "${CONFIG_DIR}" "${LUTE_OUTPUT_DIR}"

GEOM_FILE="/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/geom/r0016.geom"

GLOBAL_TEMPLATE="/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute/config/dr_pyalgos_sfx/template_concat_merge_compare.yaml"
RUN_TEMPLATE="/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute/config/dr_pyalgos_sfx/template_peakfind_index.yaml"

# =========================
# Slurm 
# =========================
PARTITION="milano"
ACCOUNT="lcls:mfxx49820"
NODES=1
CORES_PER_NODE=115
NTASKS=$((NODES * CORES_PER_NODE))
STREAMCAT_NTASKS=$NTASKS
MERGE_NTASKS=$NTASKS
DEP_POLICY="afterok"

SBATCH_INVARIANT_ARGS=(
  --partition="${PARTITION}" --account="${ACCOUNT}" 
  --cpus-per-task=1 --hint=nomultithread --exclusive
)

sbatch_layout() {
  local ntasks="$1"; local tpn="$CORES_PER_NODE"
  if (( ntasks < tpn )); then tpn="$ntasks"; fi
  local nodes=$(( (ntasks + tpn - 1) / tpn ))
  printf -- "--nodes=%d --ntasks=%d --ntasks-per-node=%d" "$nodes" "$ntasks" "$tpn"
}

export SLURM_CPU_BIND=cores
export SLURM_DISTRIBUTION=block:block

export_nprocs_flag() {
  local desired_ranks="$1"; local nprocs_plus_one=$(( desired_ranks + 1 ))
  printf -- "--export=ALL,SLURM_NPROCS=%d" "${nprocs_plus_one}"
}


# =========================
# Helpers 
# =========================

parse_method_and_component() {
  local fname="$1"
  local core="${fname#dr_}"
  core="${core%.yaml}"
  local comp="${core##*_}"    
  local method="${core%_*}"   
  printf "%s %s\n" "$method" "$comp"
}


algo_out_dir() {
  local method="$1" comp="$2"
  if [[ "$method" == "None" && "$comp" == "baseline" ]]; then
    printf "%s" "${LUTE_OUTPUT_DIR}/baseline"
  else
    printf "%s" "${LUTE_OUTPUT_DIR}/${method}/${comp}"
  fi
}

set_yaml_workdir() {
  local yaml="$1" workdir="$2"
  [[ "${workdir}" != */ ]] && workdir="${workdir}/"
  sed -i -E "s|^[[:space:]]*work_dir:.*|work_dir: \"${workdir}\"|" "$yaml"
}

submit_task_get_jobid() {
  local submit_out
  submit_out="$("$@")"
  local jobid
  jobid="$(echo "${submit_out}" | awk '/Submitted batch job/{print $4}')"
  [[ -z "${jobid}" ]] && jobid="$(echo "${submit_out}" | grep -oE '[0-9]+$' || true)"
  if [[ -z "${jobid}" || ! "${jobid}" =~ ^[0-9]+$ ]]; then
    echo "ERROR: Failed to submit. Output was:" >&2
    echo "${submit_out}" >&2
    return 1
  fi
  echo "${jobid}"
}

submit_mkdir_job() {
  local workdir="$1"
  local label="$2"

  submit_task_get_jobid \
    sbatch \
      --job-name="MKDIR_${label}" \
      --output="${LUTE_OUTPUT_DIR}/mkdir-${label}-%j.out" \
      --error="${LUTE_OUTPUT_DIR}/mkdir-${label}-%j.err" \
      --partition=milano \
      --account="${ACCOUNT}" \
      --cpus-per-task=1 \
      $(sbatch_layout 1) \
      --wrap="mkdir -p '${workdir}'"
}



submit_cancel_unless_completed() {
  local upstream_jobid="$1"; shift
  local label="$1"; shift
  local to_cancel=("$@")

  local cancel_list=""
  if (( ${#to_cancel[@]} > 0 )); then
    cancel_list="${to_cancel[*]}"
  fi

  sbatch \
    --job-name="WATCH_${label}_${upstream_jobid}" \
    --output="${LUTE_OUTPUT_DIR}/watch-${label}-${upstream_jobid}-%j.out" \
    --error="${LUTE_OUTPUT_DIR}/watch-${label}-${upstream_jobid}-%j.err" \
    --dependency=afterany:${upstream_jobid} \
    $(sbatch_layout 1) "${SBATCH_INVARIANT_ARGS[@]}" \
    --wrap="state=\$(sacct -j ${upstream_jobid} --format=State -n -P | head -n1 | cut -d'|' -f1 | tr -d ' '); \
            if [[ \"\$state\" != COMPLETED ]]; then \
              echo \"[ERROR] Upstream ${upstream_jobid} finished with state=\$state for ${label}. Cancelling: ${cancel_list}\" >&2; \
              if [[ -n \"${cancel_list}\" ]]; then scancel ${cancel_list} || true; fi; \
            else \
              echo \"[OK] Upstream ${upstream_jobid} COMPLETED for ${label}.\" >&2; \
            fi; \
            exit 0" >/dev/null
}


# =========================
# PER-RUN: generate yamls + run peakfind/index for ALL methods/components
# =========================
echo "Processing method/component blocks..."

declare -A MERGE_JOB_BY_PAIR=()
BASELINE_PAIR_KEY="None|baseline"

if (( DO_BASELINE == 1 )); then
  method="None"
  comp="baseline"
  key="${method}|${comp}"

  echo "=============================="
  echo "BASELINE: ${method}/${comp}"
  echo "=============================="

  for run in "${RUNS_TO_PROCESS[@]}"; do
    run_pad="$(printf "%04d" "$run")"
    echo "=== RUN ${run} (baseline) ==="

    BASE_YAML="${RUN_TEMPLATE}" RUN="${run}" METHODS="baseline" INCLUDE_S="${INCLUDE_S}" "${MAKE_YAML_SCRIPT}"

    src_yaml="${CONFIG_DIR}/dr_${method}_${comp}.yaml"
    if [[ ! -f "${src_yaml}" ]]; then
      echo "ERROR: Expected baseline run yaml not found: ${src_yaml}" >&2
      exit 1
    fi

    cfg="${CONFIG_DIR}/dr_${method}_${comp}_r${run_pad}.yaml"
    cp "${src_yaml}" "${cfg}"

    sed -i -E "s|^geom_file:.*|geom_file: \"${GEOM_FILE}\"|" "${cfg}"

    run_out_dir="$(algo_out_dir "${method}" "${comp}")/r${run_pad}"
    set_yaml_workdir "${cfg}" "${run_out_dir}"
    mkdir_id="$(submit_mkdir_job "${run_out_dir}" "${method}_${comp}_r${run_pad}")"
    mkdir_dep=(--dependency=${DEP_POLICY}:${mkdir_id})

    echo "--- baseline: Peakfinder (run ${run}) ---"
    job1_id="$(submit_task_get_jobid \
      "${SUBMIT_SCRIPT}" -t "${TASK_NAME1}" -c "${cfg}" -e "${EXPERIMENT}" -r "${run}" \
      $(sbatch_layout "${NTASKS}") "$(export_nprocs_flag "${NTASKS}")" \
      "${mkdir_dep[@]}" \
      "${SBATCH_INVARIANT_ARGS[@]}")"
    echo "Peakfinder submitted: ${job1_id}"

    echo "--- baseline: Indexer (after ${job1_id}) ---"
    dep1=(--dependency=${DEP_POLICY}:${job1_id})
    job2_id="$(submit_task_get_jobid \
      "${SUBMIT_SCRIPT}" -t "${TASK_NAME2}" -c "${cfg}" -e "${EXPERIMENT}" -r "${run}" \
      $(sbatch_layout "${NTASKS}") "$(export_nprocs_flag "${NTASKS}")" \
      "${mkdir_dep[@]}"\
      "${dep1[@]}" "${SBATCH_INVARIANT_ARGS[@]}")"
    echo "Indexer submitted: ${job2_id}"
    submit_cancel_unless_completed "${job1_id}" "${method}_${comp}_r${run_pad}_peakfind" "${job2_id}"

    echo "--- baseline: Cleanup (after ${job2_id}) and WAIT ---"
    cleanup_cmd="find '${run_out_dir}' -type f -name '*_[0-9]*.cxi' -print -delete"
    dep2=(--dependency=afterany:${job2_id})
    sbatch --job-name="${TASK_NAME4}_r${run}_${method}_${comp}" \
      --output="${run_out_dir}/cleanup-slurm-%j.out" \
      --error="${run_out_dir}/cleanup-slurm-%j.err" \
      --wait \
      "${dep2[@]}" $(sbatch_layout 1) "${SBATCH_INVARIANT_ARGS[@]}" \
      --wrap="${cleanup_cmd}"
  done

# =========================
# GLOBAL
# =========================


  echo "Generating GLOBAL configs for baseline ..."
  BASE_YAML="${GLOBAL_TEMPLATE}" RUN=0 METHODS="baseline" INCLUDE_S="${INCLUDE_S}" "${MAKE_YAML_SCRIPT}"

  rsplit_yaml="${CONFIG_DIR}/dr_${method}_${comp}_Rsplit.yaml"
  ccstar_yaml="${CONFIG_DIR}/dr_${method}_${comp}_CCstar.yaml"

  if [[ ! -f "${rsplit_yaml}" ]]; then
    echo "ERROR: Missing baseline global Rsplit yaml: ${rsplit_yaml}" >&2
    exit 1
  fi

  global_out_dir="$(algo_out_dir "${method}" "${comp}")"
  #mkdir -p "${global_out_dir}"
  set_yaml_workdir "${rsplit_yaml}" "${global_out_dir}"
  [[ -f "${ccstar_yaml}" ]] && set_yaml_workdir "${ccstar_yaml}" "${global_out_dir}"

  global_mkdir_id="$(submit_mkdir_job "${global_out_dir}" "${method}_${comp}_global")"
  global_mkdir_dep=(--dependency=${DEP_POLICY}:${global_mkdir_id})

  echo ">>> GLOBAL baseline: concat"
  concat_id="$(submit_task_get_jobid \
  "${SUBMIT_SCRIPT}" -t "${TASK_NAME3}" -c "${rsplit_yaml}"  \
    $(sbatch_layout "${STREAMCAT_NTASKS}") "$(export_nprocs_flag "${STREAMCAT_NTASKS}")" \
    "${global_mkdir_dep[@]}" \
    "${SBATCH_INVARIANT_ARGS[@]}")"

  echo ">>> GLOBAL baseline: merge (after ${concat_id})"
  depc=(--dependency=${DEP_POLICY}:${concat_id})
  merge_id="$(submit_task_get_jobid \
  "${SUBMIT_SCRIPT}" -t "${TASK_NAME5}" -c "${rsplit_yaml}" \
    $(sbatch_layout "${MERGE_NTASKS}") "$(export_nprocs_flag "${MERGE_NTASKS}")" \
    "${global_mkdir_dep[@]}" "${depc[@]}" \
    "${SBATCH_INVARIANT_ARGS[@]}")"

  MERGE_JOB_BY_PAIR["${key}"]="${merge_id}"

  echo ">>> GLOBAL baseline: self-compare after ${merge_id}"
  depm=(--dependency=${DEP_POLICY}:${merge_id})
  "${SUBMIT_SCRIPT}" -t "${TASK_NAME6}" -c "${rsplit_yaml}" \
    $(sbatch_layout 10) "$(export_nprocs_flag 10)" \
    "${global_mkdir_dep[@]}" "${depm[@]}" \
    "${SBATCH_INVARIANT_ARGS[@]}"


  if [[ -f "${ccstar_yaml}" ]]; then
    "${SUBMIT_SCRIPT}" -t "${TASK_NAME6}" -c "${ccstar_yaml}" \
    $(sbatch_layout 10) "$(export_nprocs_flag 10)" \
    "${global_mkdir_dep[@]}" "${depm[@]}" \
    "${SBATCH_INVARIANT_ARGS[@]}"
  fi
fi


# -------------------------
# PER-RUN: peakfind + index
# -------------------------

echo "Processing method/component blocks (not baseline)"

for method in "${DR_METHODS_NO_BASELINE[@]}"; do
  for comp in "${COMPONENTS[@]}"; do

    echo "=============================="
    echo "METHOD=${method}  COMPONENT=${comp}"
    echo "=============================="
    for run in "${RUNS_TO_PROCESS[@]}"; do
      run_pad="$(printf "%04d" "$run")"
      echo "=== RUN ${run} (${method}/${comp}) ==="

      BASE_YAML="${RUN_TEMPLATE}" RUN="${run}" METHODS="${method}" INCLUDE_S="${INCLUDE_S}" "${MAKE_YAML_SCRIPT}"

      src_yaml="${CONFIG_DIR}/dr_${method}_${comp}.yaml"
      if [[ ! -f "${src_yaml}" ]]; then
        echo "ERROR: Expected run yaml not found: ${src_yaml}" >&2
        exit 1
      fi

      cfg="${CONFIG_DIR}/dr_${method}_${comp}_r${run_pad}.yaml"
      cp "${src_yaml}" "${cfg}"
      #TO-FIX If the generator always overwrites dr_${method}_${comp}.yaml, 
      #that’s fine because immediately copy it. 
      #But if the generator produces multiple files or you run multiple blocks 
      #concurrently in another shell, it could race. 

      sed -i -E "s|^geom_file:.*|geom_file: \"${GEOM_FILE}\"|" "${cfg}"

      run_out_dir="$(algo_out_dir "${method}" "${comp}")/r${run_pad}"
      set_yaml_workdir "${cfg}" "${run_out_dir}"
      mkdir_id="$(submit_mkdir_job "${run_out_dir}" "${method}_${comp}_r${run_pad}")"
      mkdir_dep=(--dependency=${DEP_POLICY}:${mkdir_id})

      echo "--- ${method}/${comp}: Peakfinder (run ${run}) ---"
      job1_id="$(submit_task_get_jobid \
        "${SUBMIT_SCRIPT}" -t "${TASK_NAME1}" -c "${cfg}" -e "${EXPERIMENT}" -r "${run}" \
        $(sbatch_layout "${NTASKS}") "$(export_nprocs_flag "${NTASKS}")" \
        "${mkdir_dep[@]}" \
        "${SBATCH_INVARIANT_ARGS[@]}")"
      echo "Peakfinder submitted: ${job1_id}"

      echo "--- ${method}/${comp}: Indexer (after ${job1_id}) ---"
      dep1=(--dependency=${DEP_POLICY}:${job1_id})
      job2_id="$(submit_task_get_jobid \
        "${SUBMIT_SCRIPT}" -t "${TASK_NAME2}" -c "${cfg}" -e "${EXPERIMENT}" -r "${run}" \
        $(sbatch_layout "${NTASKS}") "$(export_nprocs_flag "${NTASKS}")" \
        "${mkdir_dep[@]}" \
        "${dep1[@]}" "${SBATCH_INVARIANT_ARGS[@]}")"
      echo "Indexer submitted: ${job2_id}"

      echo "--- ${method}/${comp}: Cleanup (after ${job2_id}) and WAIT ---"
      cleanup_cmd="find '${run_out_dir}' -type f -name '*_[0-9]*.cxi' -print -delete"
      dep2=(--dependency=afterany:${job2_id})
      sbatch --job-name="${TASK_NAME4}_r${run}_${method}_${comp}" \
        --output="${run_out_dir}/cleanup-slurm-%j.out" \
        --error="${run_out_dir}/cleanup-slurm-%j.err" \
        --wait \
        "${dep2[@]}" $(sbatch_layout 1) "${SBATCH_INVARIANT_ARGS[@]}" \
        --wrap="${cleanup_cmd}"

      echo "=== RUN ${run} done (${method}/${comp}) ==="
    done

    # -------------------------
    # GLOBAL: concat + merge + compare for this (method,comp)
    # -------------------------
    echo "Generating GLOBAL configs for ${method}/${comp} ..."
    BASE_YAML="${GLOBAL_TEMPLATE}" RUN=0 METHODS="${method}" INCLUDE_S="${INCLUDE_S}" "${MAKE_YAML_SCRIPT}"

    rsplit_yaml="${CONFIG_DIR}/dr_${method}_${comp}_Rsplit.yaml"
    ccstar_yaml="${CONFIG_DIR}/dr_${method}_${comp}_CCstar.yaml"

    if [[ ! -f "${rsplit_yaml}" ]]; then
      echo "ERROR: Missing global Rsplit yaml: ${rsplit_yaml}" >&2
      exit 1
    fi

    global_out_dir="$(algo_out_dir "${method}" "${comp}")"
    #mkdir -p "${global_out_dir}"
    set_yaml_workdir "${rsplit_yaml}" "${global_out_dir}"
    [[ -f "${ccstar_yaml}" ]] && set_yaml_workdir "${ccstar_yaml}" "${global_out_dir}"

    global_mkdir_id="$(submit_mkdir_job "${global_out_dir}" "${method}_${comp}_global")"
    global_mkdir_dep=(--dependency=${DEP_POLICY}:${global_mkdir_id})

    echo ">>> GLOBAL ${method}/${comp}: concat"
    concat_id="$(submit_task_get_jobid \
      "${SUBMIT_SCRIPT}" -t "${TASK_NAME3}" -c "${rsplit_yaml}"\
      $(sbatch_layout "${STREAMCAT_NTASKS}") "$(export_nprocs_flag "${STREAMCAT_NTASKS}")" \
      "${global_mkdir_dep[@]}" \
      "${SBATCH_INVARIANT_ARGS[@]}")"
    echo "Concat submitted: ${concat_id}"

    echo ">>> GLOBAL ${method}/${comp}: merge (after ${concat_id})"
    depc=(--dependency=${DEP_POLICY}:${concat_id})
    merge_id="$(submit_task_get_jobid \
      "${SUBMIT_SCRIPT}" -t "${TASK_NAME5}" -c "${rsplit_yaml}" \
      $(sbatch_layout "${MERGE_NTASKS}") "$(export_nprocs_flag "${MERGE_NTASKS}")" \
      "${global_mkdir_dep[@]}" "${depc[@]}" \
      "${SBATCH_INVARIANT_ARGS[@]}")"

    MERGE_JOB_BY_PAIR["${method}|${comp}"]="${merge_id}"

    echo ">>> GLOBAL ${method}/${comp}: self-compare after ${merge_id}"
    depm=(--dependency=${DEP_POLICY}:${merge_id})
    "${SUBMIT_SCRIPT}" -t "${TASK_NAME6}" -c "${rsplit_yaml}" \
      $(sbatch_layout 10) "$(export_nprocs_flag 10)" \
      "${global_mkdir_dep[@]}" "${depm[@]}" \
      "${SBATCH_INVARIANT_ARGS[@]}"

    if [[ -f "${ccstar_yaml}" ]]; then
      "${SUBMIT_SCRIPT}" -t "${TASK_NAME6}" -c "${ccstar_yaml}" \
        $(sbatch_layout 10) "$(export_nprocs_flag 10)" \
        "${global_mkdir_dep[@]}" "${depm[@]}" \
        "${SBATCH_INVARIANT_ARGS[@]}"
    fi

  done
done


# =========================
# EXTRA: baseline vs each non-baseline method/component comparisons
# =========================
if (( DO_BASELINE == 1 )); then

  echo "Submitting baseline-vs-all comparisons under DrComp/ ..."

  baseline_merge="${MERGE_JOB_BY_PAIR[${BASELINE_PAIR_KEY}]:-}"
  baseline_workdir="${LUTE_OUTPUT_DIR}/baseline"
  baseline_hkl1="${baseline_workdir}/merged.hkl1"

  for key in "${!MERGE_JOB_BY_PAIR[@]}"; do
    [[ "${key}" == "${BASELINE_PAIR_KEY}" ]] && continue

    algo_merge_id="${MERGE_JOB_BY_PAIR[$key]}"
    method="${key%%|*}"
    comp="${key##*|}"

    algo_workdir="$(algo_out_dir "${method}" "${comp}")"
    algo_hkl2="${algo_workdir}/merged.hkl2"

    src_rsplit="${CONFIG_DIR}/dr_${method}_${comp}_Rsplit.yaml"
    if [[ ! -f "${src_rsplit}" ]]; then
      echo "WARN: Missing source compare yaml: ${src_rsplit} (skipping ${method}/${comp})"
      continue
    fi

    cmp_dir="${LUTE_OUTPUT_DIR}/compare_baseline_vs_${method}/${comp}"
    #mkdir -p "${cmp_dir}"
    mkdir_id="$(submit_mkdir_job "${cmp_dir}" "cmp_baseline_vs_${method}_${comp}")"
    mkdir_dep=(--dependency=${DEP_POLICY}:${mkdir_id})

    if [[ -n "${baseline_merge}" ]]; then
      cmp_dep=(--dependency=${DEP_POLICY}:${baseline_merge}:${algo_merge_id})
    else
      cmp_dep=(--dependency=${DEP_POLICY}:${algo_merge_id})
    fi



    cmp_yaml="${CONFIG_DIR}/compare_baseline_vs_${method}_${comp}_Rsplit.yaml"
    cp "${src_rsplit}" "${cmp_yaml}"

    set_yaml_workdir "${cmp_yaml}" "${cmp_dir}"
    sed -i -E "s|^([[:space:]]*in_files:).*|\1 \"${baseline_hkl1} ${algo_hkl2}\"|" "${cmp_yaml}"

    echo ">>> COMPARE baseline vs ${method}/${comp}"

    cmp_job_id="$(submit_task_get_jobid \
      "${SUBMIT_SCRIPT}" -t "${TASK_NAME6}" -c "${cmp_yaml}" \
      $(sbatch_layout 10) "$(export_nprocs_flag 10)" \
      "${mkdir_dep[@]}" "${cmp_dep[@]}" \
      "${SBATCH_INVARIANT_ARGS[@]}")"
    echo "Compare submitted: ${cmp_job_id}"

    src_ccstar="${CONFIG_DIR}/dr_${method}_${comp}_CCstar.yaml"
    if [[ -f "${src_ccstar}" ]]; then
      cmp_yaml="${CONFIG_DIR}/compare_baseline_vs_${method}_${comp}_CCstar.yaml"
      cp "${src_ccstar}" "${cmp_yaml}"

      set_yaml_workdir "${cmp_yaml}" "${cmp_dir}"
      sed -i -E "s|^([[:space:]]*in_files:).*|\1 \"${baseline_hkl1} ${algo_hkl2}\"|" "${cmp_yaml}"

      cmp_job_id="$(submit_task_get_jobid \
        "${SUBMIT_SCRIPT}" -t "${TASK_NAME6}" -c "${cmp_yaml}" \
        $(sbatch_layout 10) "$(export_nprocs_flag 10)" \
        "${mkdir_dep[@]}" "${cmp_dep[@]}" \
        "${SBATCH_INVARIANT_ARGS[@]}")"
      echo "Compare submitted: ${cmp_job_id}"
    else
      echo "WARN: Missing CCstar source yaml: ${src_ccstar} (skipping CCstar compare for ${method}/${comp})"
    fi




  done
fi