import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from cocbot import config


class InstanceTests(unittest.TestCase):
    def test_current_process_is_alive(self):
        self.assertTrue(config._pid_alive(os.getpid()))

    def test_lock_excludes_other_process_and_releases_after_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "bot.lock"
            script = (
                "from pathlib import Path; from cocbot.config import bot_lock; "
                "import sys; "
                "\nwith bot_lock(Path(sys.argv[1])) as acquired:"
                "\n print(acquired, flush=True)"
                "\n if acquired: sys.stdin.readline()"
            )
            def launch():
                return subprocess.Popen(
                    [sys.executable, "-c", script, str(lock)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True,
                )
            first = launch()
            try:
                self.assertEqual(first.stdout.readline().strip(), "True")
                second = launch()
                out, err = second.communicate(timeout=10)
                self.assertEqual(second.returncode, 0, err)
                self.assertEqual(out.strip(), "False")
                # A forced exit must release the OS lock, even without cleanup.
                first.kill()
                first.communicate(timeout=10)
                third = launch()
                out, err = third.communicate(input="\n", timeout=10)
                self.assertEqual(third.returncode, 0, err)
                self.assertEqual(out.strip(), "True")
            finally:
                if first.poll() is None:
                    first.kill()
                first.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()
