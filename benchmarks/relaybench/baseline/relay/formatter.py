import json
import threading


class BatchFormatter:
    def __init__(self):
        self._lock = threading.Lock()

    def render(self, rows):
        self._lock.acquire()
        try:
            return json.dumps(rows, sort_keys=True)
        finally:
            self._lock.release()
