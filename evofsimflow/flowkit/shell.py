"""Running external programs, and a stand-in that needs none of them.

Everything that leaves this process goes through Executor, so the whole
pipeline can be exercised on a machine with no EDA tools installed.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


@dataclass
class Completed:
    argv: list[str]
    rc: int
    seconds: float
    log: Path | None
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.rc == 0 and not self.timed_out


class Executor:
    def run(
        self,
        argv: Sequence[str],
        cwd: Path,
        log: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        show: bool = False,
    ) -> Completed:
        """show: with no log, let the output through to ours."""
        raise NotImplementedError

    def which(self, name: str) -> str | None:
        return shutil.which(name)


class LocalExecutor(Executor):
    """Real subprocesses.

    stdin is always /dev/null: a tool that lands on an interactive prompt
    (UCLI, a confirmation) would otherwise wait forever in an unattended run.
    """

    def __init__(self, new_session: bool = True) -> None:
        # A new session per child lets kill_all reach everything it forked.
        # An interactive command keeps the child in its own process group
        # instead, so Ctrl-C reaches both, exactly as it would from a shell.
        self.new_session = new_session
        self._live: set[subprocess.Popen] = set()
        self._lock = threading.Lock()

    def kill_all(self) -> int:
        """Leave nothing behind holding cores when the run is torn down."""
        with self._lock:
            live = list(self._live)
        for proc in live:
            if proc.poll() is None:
                _kill_group(proc)
        return len(live)

    def run(
        self,
        argv: Sequence[str],
        cwd: Path,
        log: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        show: bool = False,
    ) -> Completed:
        argv = [str(a) for a in argv]
        cwd.mkdir(parents=True, exist_ok=True)
        full_env = dict(os.environ)
        if env:
            full_env.update(env)
        if log:
            out = open(log, "wb")
        else:
            out = None if show else subprocess.DEVNULL
        t0 = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                env=full_env,
                start_new_session=self.new_session,
            )
            with self._lock:
                self._live.add(proc)
            try:
                rc = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_group(proc)
                rc = proc.wait(timeout=30)
            except KeyboardInterrupt:
                self._interrupted(proc)
                raise
            finally:
                with self._lock:
                    self._live.discard(proc)
        except FileNotFoundError:
            return Completed(argv, 127, time.monotonic() - t0, log)
        finally:
            if log:
                out.close()
        return Completed(argv, rc, time.monotonic() - t0, log, timed_out)

    def _interrupted(self, proc: subprocess.Popen) -> None:
        """Never leave a child running behind an interrupted command. In its
        own session it did not see the Ctrl-C; in ours it did, and is given
        the time to exit cleanly that a shell would give it."""
        if self.new_session:
            _kill_group(proc)
            return
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _kill_group(proc: subprocess.Popen) -> None:
    """Kill the whole process group: these tools fork children of their own."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=10)
            return
        except subprocess.TimeoutExpired:
            continue


Handler = Callable[[list[str], Path, Path | None], int]


class FakeExecutor(Executor):
    """Answers with a scripted handler instead of running anything.

    Handlers are matched on the basename of argv[0]; the fallback succeeds.
    Used by the selftest to drive the full pipeline with no tools present.
    """

    def __init__(self, handlers: dict[str, Handler] | None = None, delay: float = 0.0):
        self.handlers = handlers or {}
        self.delay = delay
        self.calls: list[list[str]] = []

    def which(self, name: str) -> str | None:
        return name

    def run(
        self,
        argv: Sequence[str],
        cwd: Path,
        log: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        show: bool = False,
    ) -> Completed:
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        cwd.mkdir(parents=True, exist_ok=True)
        if log:
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("")
        t0 = time.monotonic()
        if self.delay:
            time.sleep(self.delay)
        handler = self.handlers.get(Path(argv[0]).name)
        rc = handler(argv, cwd, log) if handler else 0
        return Completed(argv, rc, time.monotonic() - t0, log)


def safe_rmtree(path: Path, root: Path, ignore_errors: bool = False) -> bool:
    """The only way anything here is deleted.

    Removes path -- a file, a link or a whole tree -- only if it lies inside
    root. A link is removed, never followed, wherever it points. Returns
    whether there was anything to remove. ignore_errors covers the removal
    itself failing, never a path outside root.
    """
    root = Path(root).resolve()
    path = Path(path)
    if path.name in ("", ".", ".."):
        raise ValueError(f"refusing to remove {path}: not a plain entry")
    target = path.parent.resolve() / path.name
    if root not in target.parents:
        raise ValueError(f"refusing to remove {target}: it is not inside {root}")
    try:
        if target.is_symlink() or target.is_file():
            target.unlink()
            return True
        if target.is_dir():
            shutil.rmtree(target)
            return True
    except OSError:
        if not ignore_errors:
            raise
    return False
