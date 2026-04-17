import argparse
from unittest.mock import MagicMock, patch

from lute.execution.launch import (
    get_base_launch_parser,
    setup_launch_env,
    retrieve_run_info,
    get_lute_launch_config,
)


def test_get_base_launch_parser():
    parser = get_base_launch_parser("Test Description")
    args = parser.parse_args(["-c", "config.yaml", "-e", "exp123", "-r", "run456"])
    assert args.config == "config.yaml"
    assert args.experiment == "exp123"
    assert args.run == "run456"


@patch("lute.execution.launch.request_arp_token")
@patch("os.getenv")
def test_setup_launch_env_arp(mock_getenv, mock_request_token):
    # Simulate ARP environment
    mock_getenv.side_effect = lambda k, default=None: {
        "EXPERIMENT": "exp123",
        "RUN_NUM": "456",
        "Authorization": "Bearer token",
        "ARP_JOB_ID": "jobid789",
    }.get(k, default)

    args = MagicMock(spec=argparse.Namespace)
    env = setup_launch_env(args)

    assert env["experiment"] == "exp123"
    assert env["run_num"] == "456"
    assert env["authorization"] == "Bearer token"
    assert env["arp_job_id"] == "jobid789"
    mock_request_token.assert_not_called()


@patch("lute.execution.launch.requests.get")
def test_retrieve_run_info(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "value": {"type": "CALIB", "params": {"DAQ Detectors/drp/det1": {}}}
    }
    mock_get.return_value = mock_resp

    run_type, is_daq2 = retrieve_run_info("exp", "run", "auth")
    assert run_type == "CALIB"
    assert is_daq2 is True


def test_get_lute_launch_config():
    launch_info = {
        "experiment": "exp",
        "run_num": "1234",
        "authorization": "auth",
        "arp_job_id": "jobid",
        "kerb_file": "kerb",
    }
    config = get_lute_launch_config(
        launch_info=launch_info,
        run_type="TEST",
        is_daq2=False,
        lute_params={"config": "abcd", "debug": False},
        slurm_params=["--qos=low"],
    )
    assert config["experiment"] == "exp"
    assert config["run_type"] == "TEST"
    assert config["is_daq2"] is False
    assert config["lute_params"] == {"config": "abcd", "debug": False}
    assert config["slurm_params"] == ["--qos=low"]
