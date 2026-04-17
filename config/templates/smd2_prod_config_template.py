{%- macro step_parameters(dict_name, detector, data, add_to_ret=True) %}
{%- if data is mapping %}
{%- for param_name, param_value in data.items() %}
        {% if param_value is none -%}
        {{ dict_name }}["{{ param_name }}"] = None
        {% else -%}
        {{ dict_name }}["{{ param_name }}"] = {{ param_value | pprint }}
        {% endif %}
{%- endfor %}
        {% if add_to_ret -%}
        ret_dict["{{ detector }}"] = {{ dict_name }}
        {% endif %}
{%- else %}
        # Create list of dicts for {{ detector }}
        {{ dict_name }}s = []

{%- for data_dict in data %}
        # Create a dict for this set
        {{ dict_name }} = {}

{{- step_parameters(dict_name, detector, data_dict, False) }}
        {{ dict_name }}s.append({{ dict_name }})

{%- endfor %}

        # Add list of dicts for {{ detector }} to total dictionary
        ret_dict["{{ detector }}"] = {{ dict_name }}s
{%- endif %}
{%- endmacro -%}
import numpy as np

{%- if detnames is defined and detnames %}
detectors = {{ detnames }}
{% else %}
detectors = []
{% endif %}
{%- if xdetectors is defined and xdetectors %}
xdetectors = {{ xdetectors }}
{% else %}
{% endif %}
{%- if integrating_detectors is defined and integrating_detectors %}
integrating_detectors = {{ integrating_detectors }}
{% endif %}

{%- if IntgParams is defined and IntgParams %}
def get_intg(run):
    """
    Returns
    -------
    intg_main (str):  This detector ill be passed to the psana datasource. It should be the SLOWEST of
                all integrating detectors in the data
    intg_addl (list of str): The detectors in this list will be analyzed as integrating detectors. It is
                             important that the readout frequency of these detectors is commensurate and
                             in-phase with intg_main.
    """
    run = int(run)
    intg_main = ""
    intg_addl = []
    if run > 0:
{%- if get_intg is defined and get_intg %}
    {%- if get_intg["intg_main"] and get_intg["intg_main"] %}
        intg_main = "{{ get_intg['intg_main'] }}"
    {% endif %}
    {%- if get_intg["intg_addl"] and get_intg["intg_addl"] %}
        {%- for det in get_intg["intg_addl"] %}
        intg_addl.append("{{ det }}")
        {% endfor %}
    {% endif %}
{% else %}
        ...
{% endif %}
    return intg_main, intg_addl
{% endif %}

{%- if getROIs is defined and getROIs %}
def getROIs(run):
    ret_dict = {}

    if run > 0:
        roi_dict = {}
{% for detector, params in getROIs.items() %}
{{- step_parameters("roi_dict", detector, params) }}
{% endfor %}
    return ret_dict
{% endif %}

{%- if getDetImages is defined and getDetImages %}
def getDetImages(run):
    ...
{% endif %}

{%- if getWfIntegrate is defined and getWfIntegrate %}
def get_wf_integrate(run):
    ...
{% endif %}

{%- if getWfHitfinder is defined and getWfIntegrate %}
def get_wf_hitfinder(run):
    ...
{% endif %}


{%- if getDroplet2Photons is defined and getDroplet2Photons %}
def get_droplet2photon(run):
    ret_dict = {}

    if run > 0:
        d2p_dict = {}
{% for detector, params in getDroplet2Photons.items() %}
        d2p_dict["droplet"] = {
            "threshold": {{ params["droplet"]["threshold"] }},
            "thresholdLow": {{ params["droplet"]["thresholdLow"] }},
            "thresADU": {{ params["droplet"]["thresADU"] }},
            "useRms": {{ params["droplet"]["useRms"] }},
        }
        d2p_dict["d2p"] = {
            "aduspphot": 20,
            "cputime": {{ params["cputime"] }},
        }
        ret_dict["{{ detector }}"] = {{ params }}
        d2p_dict["nData"] = None
        d2p_dict["get_photon_img"] = False

        ret_dict["{{ detector }}"] = d2p_dict
{% endfor %}
    return ret_dict
{% endif %}

{%- if getWfSVD is defined and getWfIntegrate %}
def get_wf_svd(run):
    ...
{% endif %}

{%- if getDropletParams is defined and getDropletParams %}
def get_droplet(run):
    if isinstance(run,str):
        run=int(run)
    ret_dict = {}
    if run>0:
        droplet_dict = {}
{% for detector, params in getDropletParams.items() %}
{{- step_parameters("droplet_dict", detector, params) }}
{% endfor %}
    return ret_dict
{% endif %}

{%- if getAzIntParams is defined and getAzIntParams %}
def get_azav(run):
    if isinstance(run,str):
        run=int(run)
    ret_dict = {}
    if run>0:
        az_dict = {}
{% for detector, params in getAzIntParams.items() %}
        {%- if 'userMask' in params %}
        az_dict['userMask'] = np.load("{{ params['userMask'] }}")
        {{- step_parameters("az_dict", detector, params|rejectattr("userMask")) }}
        {% else %}
        {{- step_parameters("az_dict", detector, params) }}
        {% endif %}
{% endfor %}
    return ret_dict
{% endif %}

{%- if getAzIntPyFAIParams is defined and getAzIntPyFAIParams %}
def get_azav_pyfai(run):
    if isinstance(run,str):
        run=int(run)
    ret_dict = {}
    if run>0:
        az_dict = {}
{% for detector, params in getAzIntPyFAIParams.items() %}
        {{- step_parameters("az_dict", detector, params) }}
{% endfor %}
    return ret_dict
{% endif %}

{%- if getPolynomialCorrection is defined and getWfIntegrate %}
def get_polynomial_correction(run):
    ...
{% endif %}

{%- if detSumAlgos is defined and detSumAlgos %}
def get_sum_algos(run):
    ret_dict = {}
    if run > 0:
{% for detector, params in detSumAlgos.items() %}
        ret_dict['{{ detector }}'] = {{ params }}
{% endfor %}
    return ret_dict
{% endif %}

{%- if getPressioCompression is defined and getPressioCompression %}
def get_pressio_compression(run):
    if isinstance(run,str):
        run=int(run)
    ret_dict = {}
    if run>0:
        pressio_dict = {}
{% for detector, params in getPressioCompression.items() %}
        {%- if 'compressor_id' in params %}
        compressor_id = "{{ params['compressor_id'] }}"
        {%- if 'compressor_args' in params and 'abs_error_bound' in params['compressor_args'] %}
        abs_error_bound = {{ params['compressor_args']['abs_error_bound'] }}
        {%- else %}
        abs_error_bound = 10
        {% endif %}
        pressio_dict['pressio_config'] = {
            "compressor_id": compressor_id,
            "compressor_config": {
                f"{compressor_id}:abs_error_bound": abs_error_bound,
                f"{compressor_id}:metric": "size",
            }
        }
        ret_dict["{{ detector }}"] = pressio_dict
        {% endif %}
{% endfor %}
    return ret_dict
{% endif %}

##########################################################
# run independent parameters
##########################################################
# These lists are either PV names, aliases, or tuples with both.
# epicsPV = ['las_fs14_controller_time']
# epicsOncePV = ['m0c0_vset', ('TMO:PRO2:MPOD:01:M2:C3:VoltageMeasure', 'MyAlias'),
#               'IM4K4:PPM:SPM:VOLT_RBV', "FOO:BAR:BAZ", ("X:Y:Z", "MCBTest"), "A:B:C"]
{%- if epicsPV is defined %}
epicsPV = {{ epicsPV }}
{% else %}
epicsPV = []
{% endif %}
{%- if epicsOncePV is defined %}
epicsOncePV = {{ epicsOncePV }}
{% else %}
epicsOncePV = []
{% endif %}

##########################################################
# psplot config
##########################################################

import psplot
