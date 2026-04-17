#!/usr/bin/env bash
set -eo pipefail

set +u
source /sdf/group/lcls/ds/ana/sw/conda1/rel/ami_current/setup_env_lcls1.sh
set -u

EXPERIMENT="mfxx49820"
RUNS_TO_PROCESS=({16..17})

DR_METHOD="baseline"
DR_COMPONENT="None"
DIR_NAME="baseline"
METHOD_PART_FOR_YAML="None"
COMP_PART_FOR_YAML="baseline"

TASK_NAME1="DrPeakFinderPyAlgos"
TASK_NAME2="CrystFELIndexer"
TASK_NAME4="CleanupIntermediateFiles"
TASK_NAME5="PartialatorMerger"
TASK_NAME6="HKLComparer"

MAKE_YAML_SCRIPT="/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute/config/dr_pyalgos_sfx/make_yaml_from_template.sh"
SUBMIT_SCRIPT="/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute/launch_scripts/submit_slurm.sh"
CONFIG_DIR="/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute/config/_auto"
LUTE_OUTPUT_DIR="/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute_output/DrComp"
mkdir -p "${CONFIG_DIR}" "${LUTE_OUTPUT_DIR}"

GEOM_FILE="/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/geom/r0016.geom"
GLOBAL_TEMPLATE="/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute/config/dr_pyalgos_sfx/template_concat_merge_compare.yaml"
RUN_TEMPLATE="/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret/lute/config/dr_pyalgos_sfx/template_peakfind_index.yaml"

PARTITION="milano"
ACCOUNT="lcls:mfxx49820"
NODES=1
CORES_PER_NODE=115
NTASKS=$((NODES * CORES_PER_NODE))
STREAMCAT_NTASKS=$NTASKS
MERGE_NTASKS=$NTASKS
DEP_POLICY="afterok"

SBATCH_INVARIANT_ARGS=(
    --partition="${PARTITION}" --account="${ACCOUNT}" --ntasks-per-core=1
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

echo "Generating global config files for ${DIR_NAME}..."
BASE_YAML="$GLOBAL_TEMPLATE" RUN=0 METHODS="${DR_METHOD}" "${MAKE_YAML_SCRIPT}"

GLOBAL_RSPLIT_CONFIG="${CONFIG_DIR}/dr_${METHOD_PART_FOR_YAML}_${COMP_PART_FOR_YAML}_Rsplit.yaml"
GLOBAL_CCSTAR_CONFIG="${CONFIG_DIR}/dr_${METHOD_PART_FOR_YAML}_${COMP_PART_FOR_YAML}_CCstar.yaml"

sed -i -e "s|/DrComp/None_baseline/|/DrComp/baseline/|g" \
    "${GLOBAL_RSPLIT_CONFIG}" "${GLOBAL_CCSTAR_CONFIG}"

GLOBAL_WORK_DIR="${LUTE_OUTPUT_DIR}/${DIR_NAME}"
mkdir -p "${GLOBAL_WORK_DIR}"


echo "Submitting per-run jobs SEQUENTIALLY..."
for run in "${RUNS_TO_PROCESS[@]}"; do
    run_pad=$(printf "%04d" "$run")
    echo "--- Starting processing for run ${run} ---"
    
    BASE_YAML="${RUN_TEMPLATE}" RUN=${run} METHODS="${DR_METHOD}" "${MAKE_YAML_SCRIPT}"
    SRC_RUN_YAML="${CONFIG_DIR}/dr_${METHOD_PART_FOR_YAML}_${COMP_PART_FOR_YAML}.yaml"
    CONFIG_FILE_FOR_RUN="${CONFIG_DIR}/dr_${METHOD_PART_FOR_YAML}_${COMP_PART_FOR_YAML}_r${run_pad}.yaml"

    if [[ ! -f "${SRC_RUN_YAML}" ]]; then
        echo "ERROR: Expected run YAML not found: ${SRC_RUN_YAML}" >&2; exit 1;
    fi
    
    cp "${SRC_RUN_YAML}" "${CONFIG_FILE_FOR_RUN}"
    
    sed -i \
        -e "s|^geom_file:.*|geom_file: \"${GEOM_FILE}\"|" \
        -e "s|/DrComp/None_baseline/|/DrComp/baseline/|" \
        "${CONFIG_FILE_FOR_RUN}"

    RUN_OUTPUT_DIR="${LUTE_OUTPUT_DIR}/${DIR_NAME}/r${run_pad}"
    mkdir -p "${RUN_OUTPUT_DIR}"

    echo "Submitting Peakfinder for run ${run}..."
    JOB1_SUBMIT_OUTPUT=$(${SUBMIT_SCRIPT} -t "${TASK_NAME1}" -c "${CONFIG_FILE_FOR_RUN}" -e "${EXPERIMENT}" -r "${run}" $(sbatch_layout "${NTASKS}") "$(export_nprocs_flag "${NTASKS}")" "${SBATCH_INVARIANT_ARGS[@]}")
    JOB1_ID=$(echo "${JOB1_SUBMIT_OUTPUT}" | awk '/Submitted batch job/{print $4}')
    [[ -z "${JOB1_ID}" ]] && JOB1_ID=$(echo "${JOB1_SUBMIT_OUTPUT}" | grep -oE '[0-9]+$')

    if [[ -z "${JOB1_ID}" || ! "${JOB1_ID}" =~ ^[0-9]+$ ]]; then
        echo "ERROR: Failed to submit Peakfinder for run ${run}. sbatch said:"
        echo "${JOB1_SUBMIT_OUTPUT}"
        exit 1
    fi
    echo "Peakfinder for run ${run} submitted with Job ID: ${JOB1_ID}"

    DEP_OPT=(--dependency=${DEP_POLICY}:${JOB1_ID})
    echo "Submitting Indexer for run ${run} (depends on ${JOB1_ID})..."
    JOB2_SUBMIT_OUTPUT=$(${SUBMIT_SCRIPT} -t "${TASK_NAME2}" -c "${CONFIG_FILE_FOR_RUN}" -e "${EXPERIMENT}" -r "${run}" $(sbatch_layout "${NTASKS}") "$(export_nprocs_flag "${NTASKS}")" "${DEP_OPT[@]}" "${SBATCH_INVARIANT_ARGS[@]}")
    JOB2_ID=$(echo "${JOB2_SUBMIT_OUTPUT}" | awk '/Submitted batch job/{print $4}')
    [[ -z "${JOB2_ID}" ]] && JOB2_ID=$(echo "${JOB2_SUBMIT_OUTPUT}" | grep -oE '[0-9]+$')
    
    if [[ -z "${JOB2_ID}" || ! "${JOB2_ID}" =~ ^[0-9]+$ ]]; then
        echo "ERROR: Failed to submit Indexer for run ${run}. sbatch said:"
        echo "${JOB2_SUBMIT_OUTPUT}"
        exit 1
    fi
    echo "Indexer for run ${run} submitted with Job ID: ${JOB2_ID}"
    
    CLEANUP_COMMAND="find '${RUN_OUTPUT_DIR}' -type f -name '*_[0-9]*.cxi' -print -delete"
    CLEANUP_DEP=(--dependency=${DEP_POLICY}:${JOB2_ID})
    
    echo "Submitting Cleanup for run ${run} (depends on ${JOB2_ID}) and WAITING for completion..."
    sbatch --job-name="${TASK_NAME4}_r${run}" \
           --output="${RUN_OUTPUT_DIR}/cleanup-slurm-%j.out" \
           --error="${RUN_OUTPUT_DIR}/cleanup-slurm-%j.err" \
           --wait \
           "${CLEANUP_DEP[@]}" $(sbatch_layout 1) "${SBATCH_INVARIANT_ARGS[@]}" --wrap="${CLEANUP_COMMAND}"

    echo "--- Run ${run} processing is COMPLETE. Moving to next run. ---"
done

echo "All per-run jobs have completed sequentially."
echo "Submitting global concat/merge/compare jobs..."

TASK_NAME3="StreamFileConcatenator"
CONCAT_SUBMIT_OUTPUT=$(${SUBMIT_SCRIPT} -t "${TASK_NAME3}" -c "${GLOBAL_RSPLIT_CONFIG}" -e "${EXPERIMENT}" -r "0" $(sbatch_layout "${STREAMCAT_NTASKS}") "$(export_nprocs_flag "${STREAMCAT_NTASKS}")" "${SBATCH_INVARIANT_ARGS[@]}")
CONCAT_JOB_ID=$(echo "${CONCAT_SUBMIT_OUTPUT}" | awk '/Submitted batch job/{print $4}')
[[ -z "${CONCAT_JOB_ID}" ]] && CONCAT_JOB_ID=$(echo "${CONCAT_SUBMIT_OUTPUT}" | grep -oE '[0-9]+$')

if [[ -z "${CONCAT_JOB_ID}" || ! "${CONCAT_JOB_ID}" =~ ^[0-9]+$ ]]; then
    echo "ERROR: Failed to submit global concatenator. Exiting."
    exit 1
fi
echo "Global concatenator submitted with Job ID: ${CONCAT_JOB_ID}"

MERGE_DEP=(--dependency=${DEP_POLICY}:${CONCAT_JOB_ID})
MERGE_SUBMIT_OUTPUT=$(${SUBMIT_SCRIPT} -t "${TASK_NAME5}" -c "${GLOBAL_RSPLIT_CONFIG}" -e "${EXPERIMENT}" -r "0" $(sbatch_layout "${MERGE_NTASKS}") "$(export_nprocs_flag "${MERGE_NTASKS}")" "${MERGE_DEP[@]}" "${SBATCH_INVARIANT_ARGS[@]}")
MERGE_JOB_ID=$(echo "${MERGE_SUBMIT_OUTPUT}" | awk '/Submitted batch job/{print $4}')
[[ -z "${MERGE_JOB_ID}" ]] && MERGE_JOB_ID=$(echo "${MERGE_SUBMIT_OUTPUT}" | grep -oE '[0-9]+$')

if [[ -z "${MERGE_JOB_ID}" || ! "${MERGE_JOB_ID}" =~ ^[0-9]+$ ]]; then
    echo "ERROR: Failed to submit global merger. Exiting."
    exit 1
fi
echo "Global merger submitted with Job ID: ${MERGE_JOB_ID}"

RSPLIT_DEP=(--dependency=${DEP_POLICY}:${MERGE_JOB_ID})
${SUBMIT_SCRIPT} -t "${TASK_NAME6}" -c "${GLOBAL_RSPLIT_CONFIG}" -e "${EXPERIMENT}" -r "0" $(sbatch_layout 10) "$(export_nprocs_flag 10)" "${RSPLIT_DEP[@]}" "${SBATCH_INVARIANT_ARGS[@]}"
${SUBMIT_SCRIPT} -t "${TASK_NAME6}" -c "${GLOBAL_CCSTAR_CONFIG}" -e "${EXPERIMENT}" -r "0" $(sbatch_layout 10) "$(export_nprocs_flag 10)" "${RSPLIT_DEP[@]}" "${SBATCH_INVARIANT_ARGS[@]}"

echo "Done: All submissions issued."