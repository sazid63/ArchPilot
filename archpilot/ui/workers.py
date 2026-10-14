"""Off-thread work.

A FreeCAD build takes a second or more and an AI call can take tens of seconds; both must run
away from the UI thread or the window stops repainting. Everything long-running goes through
:class:`Worker`, and every failure arrives as a message a non-technical user can act on — an
:class:`~archpilot.core.errors.ArchPilotError` brings its own wording, anything else is
reported as an unexpected fault with the traceback kept for the log.
"""
from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from archpilot.core.errors import ArchPilotError

ProgressFn = Callable[[str], None]


class Worker(QThread):
    """Runs one callable on a background thread and reports the outcome by signal.

    The callable receives a ``progress`` function it may call with short status strings; those
    are forwarded to the UI. It must not touch widgets.
    """

    succeeded = Signal(object)
    failed = Signal(str, str)          # user-facing message, log detail
    progressed = Signal(str)

    def __init__(self, work: Callable[[ProgressFn], Any], parent: QObject | None = None):
        super().__init__(parent)
        self._work = work
        self._cancelled = False

    def cancel(self) -> None:
        """Ask the job to stop at its next opportunity. Best-effort, never a hard kill."""
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def _progress(self, text: str) -> None:
        self.progressed.emit(text)

    def run(self) -> None:                      # pragma: no cover - exercised via the GUI test
        try:
            result = self._work(self._progress)
        except ArchPilotError as exc:
            self.failed.emit(exc.user_message(), str(exc))
        except Exception as exc:
            self.failed.emit(
                f"An unexpected problem stopped this step: {exc}",
                traceback.format_exc())
        else:
            if not self._cancelled:
                self.succeeded.emit(result)


class JobRunner(QObject):
    """Owns at most one worker at a time for a window, so two builds cannot overlap.

    Keeping the reference is also what stops the thread being garbage-collected mid-run.
    """

    busy_changed = Signal(bool)
    progressed = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._worker: Worker | None = None
        self._on_ok: Callable[[Any], None] | None = None
        self._on_error: Callable[[str, str], None] | None = None
        self._finished_cb: Callable[[], None] | None = None

    @property
    def busy(self) -> bool:
        return self._worker is not None

    def run(self, work: Callable[[ProgressFn], Any], *,
            on_success: Callable[[Any], None],
            on_error: Callable[[str, str], None],
            on_progress: ProgressFn | None = None,
            on_finished: Callable[[], None] | None = None) -> bool:
        """Start ``work``. Returns False (and does nothing) if a job is already running."""
        if self.busy:
            return False
        self._on_ok = on_success
        self._on_error = on_error
        self._finished_cb = on_finished
        worker = Worker(work, self)
        self._worker = worker
        worker.succeeded.connect(self._handle_ok)
        worker.failed.connect(self._handle_error)
        worker.progressed.connect(self._handle_progress)
        worker.finished.connect(self._handle_finished)
        if on_progress is not None:
            worker.progressed.connect(on_progress)
        self.busy_changed.emit(True)
        worker.start()
        return True

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def wait_for_idle(self, timeout_ms: int = 30_000) -> bool:
        """Block until the current job finishes. Used when a window is closing."""
        if self._worker is None:
            return True
        return self._worker.wait(timeout_ms)

    # -- internal ------------------------------------------------------------
    def _handle_ok(self, result: Any) -> None:
        if self._on_ok is not None:
            self._on_ok(result)

    def _handle_error(self, message: str, detail: str) -> None:
        if self._on_error is not None:
            self._on_error(message, detail)

    def _handle_progress(self, text: str) -> None:
        self.progressed.emit(text)

    def _handle_finished(self) -> None:
        self._worker = None
        self.busy_changed.emit(False)
        if self._finished_cb is not None:
            self._finished_cb()
