from unittest.mock import MagicMock

from lute.execution.ipc import Message, PipeCommunicator, Party


def test_pipe_communicator_non_utf():
    """Verify that PipeCommunicator handles non-UTF-8 bytes on stderr gracefully."""
    comm: PipeCommunicator = PipeCommunicator(party=Party.EXECUTOR, use_pickle=False)

    # Mock subprocess.Popen
    mock_proc: MagicMock = MagicMock()

    # Simulate junk on stderr that is not valid UTF-8 - we saw 0x94 before
    bad_bytes: bytes = b"Some signal \x94 and more text"
    mock_proc.stderr.read.return_value = bad_bytes
    mock_proc.stdout.read.return_value = b"Normal stdout content"

    msg: Message = comm.read(mock_proc)

    # Non LUTE_SIGNAL should be moved into msg.contents
    assert msg.signal is None
    # \ufffd is the replacement character used for invalid UTF-8
    assert "Some signal \ufffd and more text" in msg.contents
    assert "Normal stdout content" in msg.contents


def test_pipe_communicator_no_signals():
    """Verify standard read without signals."""
    comm: PipeCommunicator = PipeCommunicator(party=Party.EXECUTOR, use_pickle=False)
    mock_proc: MagicMock = MagicMock()
    mock_proc.stderr.read.return_value = b""
    mock_proc.stdout.read.return_value = b"Hello World"

    msg: Message = comm.read(mock_proc)
    assert msg.signal == "" or msg.signal is None
    assert msg.contents == "Hello World"
