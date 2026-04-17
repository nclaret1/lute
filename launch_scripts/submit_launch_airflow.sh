#!/bin/bash

# Need to capture partition and account for SLURM
while [[ $# -gt 0 ]]
do
    case "$1" in
        --partition=*)
            PARTITION="${1#*=}"
            shift
            ;;
        --account=*)
            ACCOUNT="${1#*=}"
            shift
            ;;
        *)
            POS+=("$1")
            shift
            ;;
    esac
done
set -- "${POS[@]}"

# Bodge Kerberos credentials
# These duplicates are removed later by the workflow process
KERB_CACHE_PATH=$(klist -l | awk -F"FILE:" '{printf (NF>1)? $NF : ""}')
if [[ ! -d $HOME/.tmp_cache ]]; then
    mkdir $HOME/.tmp_cache
fi
cp $KERB_CACHE_PATH $HOME/.tmp_cache/kerbcache
echo $?
export KRB5CCNAME="FILE:${HOME}/.tmp_cache/kerbcache"

LUTE_BIN_PATH="$(cd "$(dirname ${BASH_SOURCE[0]})" &> /dev/null && pwd)"
source "${LUTE_BIN_PATH}/activate_installation"

CMD="${@}"
CMD="${CMD} --partition=${PARTITION} --account=${ACCOUNT}"
echo $CMD
SLURM_ARGS="--partition=${PARTITION} --account=${ACCOUNT} --ntasks=1"
echo "Running ${CMD} with ${SLURM_ARGS}"
sbatch $SLURM_ARGS --wrap "${CMD}"
export KRB5CCNAME="FILE:${KERB_CACHE_PATH}"
