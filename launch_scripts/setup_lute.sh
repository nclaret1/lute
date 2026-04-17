#!/usr/bin/env bash
set -euo pipefail

EXPERIMENT="${EXPERIMENT:-mfxx49820}"
OUT_ROOT="${OUT_ROOT:-/sdf/data/lcls/ds/mfx/${EXPERIMENT}/results/nclaret}"
CONFIG_ROOT="${CONFIG_ROOT:-${OUT_ROOT}/lute/config/_auto}"
BASE_YAML="${BASE_YAML:-${OUT_ROOT}/lute/config/dr_pyalgos_sfx/template.yaml}"
GENERATOR="${GENERATOR:-${OUT_ROOT}/lute/config/dr_pyalgos_sfx/make_yaml_from_template.sh}"
RUN_FOR_TEMPLATE="${RUN_FOR_TEMPLATE:-16}"
CONFIGS="${CONFIGS:-}"

log(){
    printf '[%(%F %T)T] %s\n' -1 "$*" >&2
}

discover_configs(){
  find "$CONFIG_ROOT" -maxdepth 1 -type f -name 'dr_*.yaml' ! -name '*_CCstar.yaml' ! -name '*_Rsplit.yaml' | sort
}

[[ -f "$BASE_YAML" ]] || { log "ERROR: Base template YAML not found: $BASE_YAML"; exit 1; }
[[ -f "$GENERATOR" ]] || { log "ERROR: YAML generator script not found: $GENERATOR"; exit 1; }
[[ -x "$GENERATOR" ]] || log "WARN: Generator script is not executable. Will try running with 'bash' anyway."

log "Running generator to create/update combo YAMLs from template: ${GENERATOR}"
RUN_PAD=$(printf "%04d" "$RUN_FOR_TEMPLATE")

env EXPERIMENT="$EXPERIMENT" RUN="$RUN_FOR_TEMPLATE" RUN_PAD="$RUN_PAD" OUT_ROOT="$OUT_ROOT" BASE_YAML="$BASE_YAML" \
bash "$GENERATOR" || { log "ERROR: YAML generator script failed."; exit 1; }

mapfile -t BASE_CONFIGS < <( [[ -n "$CONFIGS" ]] && printf '%s\n' $CONFIGS || discover_configs )
[[ ${#BASE_CONFIGS[@]} -gt 0 ]] || { log "ERROR: No base configs found in $CONFIG_ROOT and CONFIGS variable was not set."; exit 1; }

log "Found ${#BASE_CONFIGS[@]} combos. Creating directories..."
for c in "${BASE_CONFIGS[@]}"; do log " - $(basename "$c")"; done

for CFG in "${BASE_CONFIGS[@]}"; do
    CFG_BASE="$(basename "$CFG" .yaml)"
    COMBO="${CFG_BASE#dr_}"
    COMBO_DIR="${OUT_ROOT}/lute_output/DrComp/${COMBO}"
    
    log "Ensuring directory exists: ${COMBO_DIR}"
    mkdir -p "$COMBO_DIR"
done

log "All requested directories have been created."