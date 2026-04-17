set -euo pipefail

EXPERIMENT="${EXPERIMENT:-mfxx49820}"
RUN="${RUN:-16}"
RUN_PAD=$(printf "%04d" "$RUN")

METHODS=(${METHODS:-rpca_altproj median_bg morph_open pysz_codec baseline})
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
BASE_YAML="${BASE_YAML:-${SCRIPT_DIR}/template.yaml}"
OUT_ROOT="${OUT_ROOT:-/sdf/data/lcls/ds/mfx/mfxx49820/results/nclaret}"
TMP_DIR="${TMP_DIR:-${OUT_ROOT}/lute/config/_auto}"
mkdir -p "$TMP_DIR"

[[ -f "$BASE_YAML" ]] || { echo "ERROR: Template YAML not found: $BASE_YAML" >&2; exit 1; }


need_token() {
    local tok="$1"
    if ! grep -q "$tok" "$BASE_YAML"; then
        echo "ERROR: Template missing $tok" >&2
        exit 1
    fi
}
need_token 'DR_METHOD'
need_token 'DR_COMPONENTSAFE'
need_token 'DR_COMPONENT'
need_token 'RUN_PAD_PLACEHOLDER'
need_token 'RUN_PLACEHOLDER'


if grep -q 'METRIC' "$BASE_YAML"; then
    TEMPLATE_KIND="global"
else
    TEMPLATE_KIND="run"
fi

echo "make_yaml_from_template: BASE_YAML=$BASE_YAML kind=$TEMPLATE_KIND RUN=$RUN METHODS=${METHODS[*]}"

components_for_method() {
    local method="$1"
    local include_s="${INCLUDE_S:-0}" 

    case "$method" in
        rpca_altproj|rsvd_density_power|median_bg|morph_open)
            if [[ "$include_s" == "1" || "$include_s" == "true" ]]; then
                echo "L+S S"
            else
                echo "L+S"
            fi
            ;;
        pysz_codec)
            echo "X_hat"
            ;;
        *)
            echo "X_hat"
            ;;
    esac
}

write_final_yamls() {
    local base_yaml="$1"
    local base_name
    base_name=$(basename "$base_yaml")

    if [[ "$TEMPLATE_KIND" == "global" ]]; then
        local yaml_r="${base_yaml%.yaml}_Rsplit.yaml"
        local yaml_c="${base_yaml%.yaml}_CCstar.yaml"
        perl -0777 -pe "s/METRIC/Rsplit/g" "$base_yaml" > "$yaml_r"
        perl -0777 -pe "s/METRIC/CCstar/g" "$base_yaml" > "$yaml_c"
        echo "Wrote: $(basename "$yaml_r"), $(basename "$yaml_c")"
        rm "$base_yaml"
    else
        echo "Wrote: $base_name"
    fi
}

make_one() {
    local method="$1"
    local comp="$2"
    local safe_comp
    safe_comp="${comp//+/p}"
    local out_yaml_base="${TMP_DIR}/dr_${method}_${safe_comp}.yaml"

    perl -0777 -pe "
        s/DR_METHOD/$method/g;
        s/DR_COMPONENTSAFE/$safe_comp/g;
        s/DR_COMPONENT/$comp/g;
        s/RUN_PAD_PLACEHOLDER/$RUN_PAD/g;
        s/RUN_PLACEHOLDER/$RUN/g;
    " "$BASE_YAML" > "$out_yaml_base"

    write_final_yamls "$out_yaml_base"
}

make_baseline() {
    local method_for_yaml="None"
    local comp_for_yaml="None"
    local safe_comp_for_name="baseline"
    local out_yaml_base="${TMP_DIR}/dr_${method_for_yaml}_${safe_comp_for_name}.yaml"

    perl -0777 -pe "
        s/DR_METHOD/$method_for_yaml/g;
        s/DR_COMPONENTSAFE/$safe_comp_for_name/g;
        s/DR_COMPONENT/$comp_for_yaml/g;
        s/RUN_PAD_PLACEHOLDER/$RUN_PAD/g;
        s/RUN_PLACEHOLDER/$RUN/g;
    " "$BASE_YAML" > "$out_yaml_base" 


    sed -i -E \
        -e 's/^([[:space:]]*)dr_method:[[:space:]]*"None"/\1dr_method: null/' \
        -e 's/^([[:space:]]*)dr_component:[[:space:]]*"None"/\1dr_component: null/' \
        "$out_yaml_base"


    write_final_yamls "$out_yaml_base"
}


for method in "${METHODS[@]}"; do
    if [[ "$method" == "baseline" ]]; then
        make_baseline
    else
        comps="$(components_for_method "$method")"
        for comp in $comps; do
            make_one "$method" "$comp"
        done
    fi
done