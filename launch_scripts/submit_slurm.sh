#!/bin/bash
usage()
{
    cat << EOF
$(basename "$0"):
    Submit a LUTE managed Task using SLURM on S3DF.
    Options:
        -c|--config
          ABSOLUTE path to the LUTE configuration YAML. Must be absolute.
        --debug
          Whether to run in debug mode (verbose printing).
        -h|--help
          Display this message.
        -t|--taskname
          Name of the LUTE managed Task to run.
        --psana2
          Use Psana2 for the base environment. The core infrastructure runs in
          Both psana1 and psana2.
        --psana1
          Use Psana1 for the base environment. The core infrastructure runs in
          Both psana1 and psana2. This argument takes precedence over --psana2.
          This is because we now default to psana2 if we cannot determine what
          kind of DAQ was used for a run (like with xpptut15) experiments. By
          allowing this one to take precedence, the psana1 environment can be
          forced without adding additional complexity to the auto-environment
          determination that takes place.

    NOTE: This script does not parse SLURM arguments, but a number of them are
          mandatory. All additional arguments are transparently passed to SLURM.
          You will need to provide at least the queue and account using, e.g.:
                  --partition=milano --account=lcls:<experiment>

    Additional options:
    You can also optionally provide experiment and run number. Do this only when
    NOT running from the eLog using the ARP. These options set environment
    variables EXPERIMENT and RUN, which can alternatively be set directly.
        -e|--experiment
          Experiment name.
        -r|--run
          Run number.
        -K|--KERB
          Kerberos cache file variable. This should NOT be set manually!
EOF
}

POSITIONAL=()

while [[ $# -gt 0 ]]
do
    flag="$1"

    case $flag in
    -c|--config)
        CONFIGPATH="$2"
        shift
        shift
        ;;
    -e|--experiment)
        EXP_PARAM="$2"
        shift
        shift
        ;;
    -r|--run)
        RUN_PARAM="$2"
        shift
        shift
        ;;
    -K|--KERB)
        KERB_CACHE="$2"
        shift
        shift
        ;;
    -h|--help)
        usage
        exit
        ;;
    -t|--taskname)
        TASK="$2"
        shift
        shift
        ;;
    --debug)
        DEBUG=1
        shift
        ;;
    --psana2)
        USE_PSANA2=1
        shift
        ;;
    --psana1)
        USE_PSANA1=1
        shift
        ;;
    *)
        POS+=("$1")
        shift
        ;;
    esac
done
set -- "${POS[@]}"

if [[ -z ${CONFIGPATH} || -z ${TASK} ]]; then
    echo "Path to LUTE config and Task name are required!"
    usage
    exit
fi

# Assume all other arguments are for SLURM
SLURM_ARGS=$@

if [[ -v KERB_CACHE ]]; then
    export KRB5CCNAME=$KERB_CACHE
fi

if [[ -v EXP_PARAM ]]; then
    EXPERIMENT=$EXP_PARAM
fi
export EXPERIMENT
# Setup logfile names - $EXPERIMENT and $RUN_NUM will be available if ARP submitted
# RUN_NUM is actually in format RUN_DATETIME
RUN_TIME_ARR=(${RUN_NUM//_/ })
RUN="${RUN_TIME_ARR[0]}"
if [[ -v RUN_PARAM ]]; then
    RUN_NUM=$RUN_PARAM
    RUN=$RUN_NUM
fi
export RUN_NUM=$RUN
FORMAT_RUN=$(printf "%04d" ${RUN:-0})
LOG_FILE="${TASK}_${EXPERIMENT:-$EXP}_r${FORMAT_RUN}_$(date +'%Y-%m-%d_%H-%M-%S')"
SLURM_ARGS+=" --output=${LOG_FILE}_%J.out"
SLURM_ARGS+=" --error=${LOG_FILE}_%J.out"

if [[ -z ${LUTE_USE_TCP} || ${LUTE_USE_TCP} != 0 ]]; then
    echo "Using TCP"
    export LUTE_USE_TCP=1
else
    echo "Using Unix sockets"
    unset LUTE_USE_TCP
    export LUTE_SOCKET="/tmp/lute_${RANDOM}.sock"
fi

# By default source the psana environment since most Tasks will use it.
if [[ ${USE_PSANA1} ]]; then
    echo "Using a Psana1 base environment."
    source /sdf/group/lcls/ds/ana/sw/conda1/manage/bin/psconda.sh
elif [[ ${USE_PSANA2} ]]; then
    echo "Using a Psana2 base environment."
    source /sdf/group/lcls/ds/ana/sw/conda2/manage/bin/psconda.sh
else
    echo "Using a Psana1 base environment."
    source /sdf/group/lcls/ds/ana/sw/conda1/manage/bin/psconda.sh
fi

SCRIPT_DIR="$( readlink -f "$( dirname "${BASH_SOURCE[0]}" )" )"
export LUTE_PATH="$( echo $SCRIPT_DIR | sed s/launch_scripts//g | sed s/bin//g )"
EXECUTABLE="run_task.py"

if [[ $SCRIPT_DIR == *"launch_scripts"* ]]; then
    EXECUTABLE="${LUTE_PATH}run_task.py"
else
    # Running from installation
    source "${SCRIPT_DIR}/activate_installation"
    EXECUTABLE="$(which run_task)"
    export LUTE_PATH="${LUTE_PATH}/lib/python3.9/site-packages"
fi


if [[ ${DEBUG} ]]; then
    echo "Running in debug mode - verbose logging."
    CMD="python -B ${EXECUTABLE} -c ${CONFIGPATH} -t ${TASK}"
else
    echo "Running in standard mode."
    CMD="python -OB ${EXECUTABLE} -c ${CONFIGPATH} -t ${TASK}"
fi

echo "Submitting task ${TASK}"
if [[ $DEBUG ]]; then
    echo "Running ${TASK} with SLURM arguments: ${SLURM_ARGS}"
    echo "Using socket ${LUTE_SOCKET}"
    echo "${CMD}"
fi

sbatch $SLURM_ARGS --wrap "${CMD}"