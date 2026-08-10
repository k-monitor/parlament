"""A lockfile so two scrape runs never collide (requirements SCR-1).

Used as a context manager around a whole run. The guard is an **flock on the
file**, not the PID recorded inside it: the kernel releases an flock when the
holding process dies, however it dies, so a crashed — or container-killed — run
cannot wedge the scheduler, and no liveness check has to be correct for that to
hold.

That distinction is the whole point of not using the PID. The lockfile lives in
the data directory, a bind mount shared with the container that scrapes, so the
PID written into it belongs to *another PID namespace*: ``os.kill(pid, 0)`` here
asks whether **this** namespace has a process 10, which says nothing about the
run that wrote the file — and answers "alive" as soon as the number is reused. A
deploy that recreates the sync container mid-scrape left exactly that behind, and
every later pass then read the lock as held and skipped the scrape indefinitely.
The PID is still written, for a human reading the file; never for a decision.

The file is deliberately **never unlinked**, because an flock belongs to the
inode: removing it would let the next run lock a fresh inode while the previous
one still holds the old one, and both would run. So the file's *existence* means
nothing on its own — only the flock does. Its contents are emptied on release, so
a PID in there means a run genuinely holds the lock right now.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path

try:                                    # POSIX only; the guard degrades to a
    import fcntl                        # no-op where it is unavailable.
except ImportError:                     # pragma: no cover - non-POSIX
    fcntl = None

logger = logging.getLogger(__name__)


class LockBusy(RuntimeError):
    pass


def _holder(lockfile: Path) -> str:
    """What the file claims about its writer — for the error message only. Empty
    when nobody holds the lock (see the module docstring on why this is not
    trusted for anything)."""
    try:
        return str(json.loads(lockfile.read_text()).get("pid"))
    except (ValueError, OSError):
        return "unknown"


@contextmanager
def acquire(lockfile: Path, *, force: bool = False):
    """Hold ``lockfile`` for the duration of the block.

    Raises :class:`LockBusy` when another run holds it, unless ``force`` — which
    proceeds without the lock (``--force-lock``: the operator asserts nothing else
    is running). Best-effort, like the loader's writer lock: if the file cannot be
    opened at all (read-only data dir, no fcntl) the run proceeds unguarded with a
    warning rather than failing.
    """
    lockfile = Path(lockfile)
    lockfile.parent.mkdir(parents=True, exist_ok=True)

    if fcntl is None:                   # pragma: no cover - non-POSIX
        logger.warning("No fcntl on this platform; running without the %s guard",
                       lockfile.name)
        yield
        return

    try:
        fh = open(lockfile, "a+")
    except OSError as e:
        logger.warning("Cannot open lockfile %s (%s); running unguarded", lockfile, e)
        yield
        return

    held = False
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if not force:
                raise LockBusy(
                    f"Another run holds {lockfile} (pid {_holder(lockfile)})") from None
            logger.warning("Lockfile %s is held (pid %s) — proceeding anyway "
                           "(--force-lock)", lockfile, _holder(lockfile))
        else:
            held = True
            # Who holds it, for a human reading the file. Truncate first: the
            # handle is in append mode, so writes would otherwise stack up.
            fh.seek(0)
            fh.truncate()
            fh.write(json.dumps({"pid": os.getpid()}))
            fh.flush()
        yield
    finally:
        if held:
            # Empty it before releasing, so a stale PID never outlives the run
            # that wrote it. Failure here is harmless — the flock is what counts.
            try:
                fh.seek(0)
                fh.truncate()
                fh.flush()
            except OSError:              # pragma: no cover - unwritable mid-run
                pass
        fh.close()                      # releases the flock, if this run held it
