"""Experimental screenrecord stream; screenshot fallback remains available."""
import os
import subprocess
import threading
import time
from .config import NO_WINDOW

class VideoStream:
    def __init__(self, adb, device):
        self.adb, self.device = adb, device
        self.lock, self.stop_event = threading.Lock(), threading.Event()
        self.frame, self.timestamp, self.process = None, 0.0, None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        try:
            import av
        except ImportError:
            return
        while not self.stop_event.is_set():
            try:
                args = [self.adb, '-s', self.device, 'exec-out', 'screenrecord', '--output-format=h264',
                        '--bit-rate', '8M', '--time-limit', '180', '-']
                self.process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                                creationflags=NO_WINDOW)
                decoder = av.CodecContext.create('h264', 'r')
                while not self.stop_event.is_set():
                    chunk = os.read(self.process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    for packet in decoder.parse(chunk):
                        for frame in decoder.decode(packet):
                            image = frame.to_ndarray(format='bgr24')
                            with self.lock:
                                self.frame, self.timestamp = image, time.monotonic()
            except (OSError, ValueError, RuntimeError):
                pass
            finally:
                with self.lock:
                    self.frame, self.timestamp = None, 0.0
                process, self.process = self.process, None
                if process:
                    if process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            self.stop_event.wait(1)

    def latest(self, after=0, max_age=0.5):
        with self.lock:
            if self.frame is None or self.timestamp <= after or time.monotonic() - self.timestamp > max_age:
                return None
            return self.frame.copy()

    def close(self):
        self.stop_event.set()
        process = self.process
        if process and process.poll() is None:
            process.terminate()
        self.thread.join(timeout=8)
