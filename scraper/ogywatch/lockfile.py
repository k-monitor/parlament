"""A simple PID lockfile so two scrape runs never collide (requirements SCR-1).

Used as a context manager around a whole run. A stale lock (the recorded PID is
no longer alive) is reclaimed automatically so a crashed run does not wedge the
scheduler.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


class LockBusy(RuntimeError):
    pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    return True


@contextmanager
def acquire(lockfile: Path, *, force: bool = False):
    lockfile = Path(lockfile)
    lockfile.parent.mkdir(parents=True, exist_ok=True)
    if lockfile.exists() and not force:
        try:
            held = json.loads(lockfile.read_text()).get("pid")
        except (ValueError, OSError):
            held = None
        if held and _alive(int(held)):
            raise LockBusy(f"Another run holds {lockfile} (pid {held})")
        logger.warning("Reclaiming stale lockfile %s (pid %s)", lockfile, held)
    lockfile.write_text(json.dumps({"pid": os.getpid()}))
    try:
        yield
    finally:
        try:
            lockfile.unlink()
        except FileNotFoundError:
            pass
