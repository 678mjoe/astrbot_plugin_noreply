"""Opt-in private JSONL records, with bounded disk use and no provider config."""

import datetime
import json
import os
import stat
import uuid
from pathlib import Path


class Recorder:
    def __init__(self, base: Path, *, max_bytes: int = 2 * 1024 * 1024):
        directory = base / "diagnostics"
        if directory.is_symlink():
            raise OSError("Diagnostic directory must not be a symlink")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        if sum(1 for _ in directory.glob("trial-*.jsonl")) >= 5:
            raise OSError("Archive diagnostic files before another trial")
        self.path = directory / ("trial-" + uuid.uuid4().hex + ".jsonl")
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        self.max_bytes = max_bytes

    def write(self, kind: str, event_id: str, **fields):
        payload = (
            json.dumps(
                {
                    "time": datetime.datetime.now().astimezone().isoformat(),
                    "kind": kind,
                    "event_id": event_id,
                    **fields,
                },
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        with os.fdopen(fd, "ab") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("Invalid diagnostic file")
            if info.st_size + len(payload) > self.max_bytes:
                raise OSError("Diagnostic capacity reached")
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
