import subprocess
import unittest
from unittest.mock import MagicMock, patch

from lute.execution.ipc import (
    Message,
    PipeCommunicator,
    Party,
)


class TestBidirectionalIPC(unittest.TestCase):

    def test_pipe_bidirectional(self):
        """Verify PipeCommunicator can send/read in both directions.

        Executor reads from stdout and stderr on the process.
        It writes to stdin on the process.

        Task reads from stdin (sys).
        It writes to stdout and stderr.
        """
        exec_comm: PipeCommunicator = PipeCommunicator(party=Party.EXECUTOR)
        task_comm: PipeCommunicator = PipeCommunicator(party=Party.TASK)

        mock_proc: MagicMock = MagicMock(spec=subprocess.Popen)
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()

        # Send Message from Task to Executor (our standard operation)
        msg_t2e: Message = Message(contents="Hello Executor", signal="TASK_STARTED")

        with patch("sys.stdout.buffer.write") as mock_stdout:
            with patch("sys.stderr.buffer.write") as mock_stderr:
                task_comm.write(msg_t2e)
                # Just verify it calls writes
                self.assertTrue(mock_stdout.called)
                self.assertTrue(mock_stderr.called)

        # See that Executor can write back to Task
        msg_e2t = Message(contents="Go Task!", signal="TASK_RESPONSE")
        exec_comm.write(msg_e2t, proc=mock_proc)

        # Verify Executor wrote on proc.stdin
        self.assertTrue(mock_proc.stdin.write.called)

        # Verify Task read it
        with patch("sys.stdin.buffer.read") as mock_stdin:
            import pickle

            mock_stdin.return_value = pickle.dumps(msg_e2t)
            read_msg = task_comm.read()
            self.assertEqual(read_msg.contents, msg_e2t.contents)
            self.assertEqual(read_msg.signal, msg_e2t.signal)


if __name__ == "__main__":
    unittest.main()
