"""Locate FreeCAD and run scripts against it as a subprocess — no in-process import.

ArchPilot's Python and FreeCAD's embedded Python are different interpreters (verified:
app 3.14, FreeCAD 3.11 conda-forge). Importing FreeCAD into the app would fail, so the CAD
layer always shells out to ``freecadcmd`` and exchanges JSON through files.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT_S = 180
ENV_OVERRIDE = "ARCHPILOT_FREECADCMD"

#: Sentinels the generated scripts print. Success is only believed when the matching
#: sentinel appears: freecadcmd's exit code is not trustworthy.
ERROR_PREFIX = "ARCHPILOT_ERROR"

#: Version strings, keyed by executable path. See :func:`_probe_version`.
_VERSION_CACHE: dict[str, str] = {}
BUILD_OK_PREFIX = "ARCHPILOT_BUILD_OK"
SYNC_OK_PREFIX = "ARCHPILOT_SYNC_OK"
PROBE_OK_PREFIX = "ARCHPILOT_PROBE_OK"
_TRACEBACK = "Traceback (most recent call last)"
#: freecadcmd's own report for a script that raised. Verified against FreeCAD 1.1.4: an
#: uncaught exception produces this on stderr and *still exits 0*, so the exit code and the
#: presence of a traceback are both useless as success signals.
SCRIPT_EXCEPTION = "Exception while processing file:"

# Fallback install locations, used only when the registry says nothing. FreeCAD normally records
# its install directory under SOFTWARE\FreeCAD<version> (see :func:`_registry_candidates`), which
# is the only version-agnostic source; these are the standard paths its installer offers, and they
# are checked most specific first. Deliberately no machine-specific paths here.
CANDIDATES = (
    r"C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe",
    r"C:\Program Files\FreeCAD\bin\freecadcmd.exe",
    r"C:\Program Files (x86)\FreeCAD\bin\freecadcmd.exe",
)


@dataclass(frozen=True)
class FreeCADInstall:
    executable: Path
    version: str
    source: str

    def describe(self) -> str:
        v = self.version.splitlines()[0] if self.version else "version unknown"
        return f"{self.executable}  ({v})"


class FreeCADClientError(RuntimeError):
    pass


def _registry_candidates() -> list[str]:
    """FreeCAD records its install dir under HKLM/HKCU\\SOFTWARE\\FreeCAD<ver>."""
    if os.name != "nt":
        return []
    found: list[str] = []
    try:
        import winreg
    except ImportError:
        return []
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, "SOFTWARE") as software:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(software, i)
                    except OSError:
                        break
                    i += 1
                    if not sub.lower().startswith("freecad"):
                        continue
                    try:
                        with winreg.OpenKey(software, sub) as key:
                            # FreeCAD writes its install dir to the *unnamed* default value
                            # (verified on 1.1.4: the key holds `(Default)` and `Version`).
                            # That value is read with an empty value name. A value literally
                            # named "Default" is tried as well, so a build that uses that name
                            # is still found.
                            for value_name in ("", "Default"):
                                try:
                                    base, _ = winreg.QueryValueEx(key, value_name)
                                except OSError:
                                    continue
                                if isinstance(base, str) and base.strip():
                                    found.append(
                                        str(Path(base.strip()) / "bin" / "freecadcmd.exe")
                                    )
                                    break
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def candidate_paths() -> list[Path]:
    seen: list[Path] = []
    override = os.environ.get(ENV_OVERRIDE, "").strip()
    raw = ([override] if override else []) + _registry_candidates() + list(CANDIDATES)
    for item in raw:
        if not item:
            continue
        p = Path(item)
        if p not in seen:
            seen.append(p)
    return seen


def detect(extra_candidates: list[str] | None = None) -> FreeCADInstall | None:
    """Return the first usable freecadcmd, or None. Never raises."""
    paths = list(candidate_paths())
    for item in extra_candidates or []:
        if item and Path(item) not in paths:
            paths.insert(0, Path(item))
    for p in paths:
        if p.is_file():
            return FreeCADInstall(p, _probe_version(p), str(p))
    return None


def _probe_version(exe: Path) -> str:
    """The ``--version`` line, cached per executable path.

    Asking FreeCAD for its version starts a process, which is far too expensive to repeat on
    every screen refresh. The path is the cache key, so a FreeCAD installed or pointed at later
    is still probed on first sight; only a version already known for that exact path is reused.
    """
    key = str(exe)
    cached = _VERSION_CACHE.get(key)
    if cached is not None:
        return cached
    version = ""
    try:
        r = subprocess.run([str(exe), "--version"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=45)
        out = (r.stdout or "") + (r.stderr or "")
        for line in out.splitlines():
            if line.strip():
                version = line.strip()
                break
    except (OSError, subprocess.SubprocessError):
        version = ""
    _VERSION_CACHE[key] = version
    return version


def forget_detection() -> None:
    """Drop the cached version probes so the next :func:`detect` re-runs FreeCAD.

    Used by the 'Check again' buttons, for the case where FreeCAD was upgraded in place.
    """
    _VERSION_CACHE.clear()


def test_connection(exe: Path, workdir: Path, timeout_s: int = 60) -> tuple[bool, str]:
    """Create, save and reopen an empty document to prove the install really works."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    probe = workdir / "_connection_probe.py"
    target = workdir / "_connection_probe.FCStd"
    probe.write_text(
        "import sys, traceback\n"
        "try:\n"
        "    import FreeCAD as App\n"
        "    if 'ProbeDoc' in App.listDocuments():\n"
        "        App.closeDocument('ProbeDoc')\n"
        "    d = App.newDocument('ProbeDoc')\n"
        "    b = d.addObject('Part::Box', 'Probe')\n"
        "    d.recompute()\n"
        f"    d.saveAs({str(target)!a})\n"
        "    print('ARCHPILOT_PROBE_OK', App.Version()[0], App.Version()[1])\n"
        "except Exception:\n"
        "    traceback.print_exc()\n"
        "    sys.exit(1)\n",
        encoding="utf-8",
    )
    result = run_script(exe, probe, timeout_s=timeout_s)
    ok = result.has_sentinel(PROBE_OK_PREFIX) and target.is_file()
    if ok:
        line = next((row for row in result.stdout.splitlines() if PROBE_OK_PREFIX in row), "")
        return True, f"Connected — FreeCAD {line.replace(PROBE_OK_PREFIX, '').strip()}"
    return False, result.summary()


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    launcher_error: str = ""

    @property
    def crashed(self) -> bool:
        """True if the script did not run to completion.

        FreeCAD 1.1.4 reports an uncaught exception as
        ``Exception while processing file: <script> [<message>]`` on stderr while still
        exiting 0, so the exit code cannot be trusted. A traceback is also treated as a
        crash, which covers scripts that print their own.
        """
        blob = self.stdout + "\n" + self.stderr
        return SCRIPT_EXCEPTION in self.stderr or _TRACEBACK in blob

    def freecad_exception(self) -> str:
        """The message FreeCAD gave for a script that raised, if there was one."""
        for line in self.stderr.splitlines():
            if SCRIPT_EXCEPTION in line:
                tail = line.split(SCRIPT_EXCEPTION, 1)[1].strip()
                if tail.endswith("]") and "[" in tail:
                    inner = tail[tail.rindex("[") + 1:-1].strip()
                    return inner or tail
                return tail
        return ""

    @property
    def reported_error(self) -> bool:
        return any(line.startswith(ERROR_PREFIX) for line in self.stdout.splitlines())

    @property
    def ok(self) -> bool:
        return (self.returncode == 0 and not self.timed_out and not self.launcher_error
                and not self.crashed and not self.reported_error)

    def sentinel_count(self, prefix: str) -> int:
        """Read the integer after ``prefix``, or -1 when the sentinel is absent.

        Generated scripts print exactly one sentinel on success; callers must require it
        rather than trusting the exit code.
        """
        for line in self.stdout.splitlines():
            if line.startswith(prefix):
                try:
                    return int(line[len(prefix):].strip().split()[0])
                except (ValueError, IndexError):
                    return -1
        return -1

    def has_sentinel(self, prefix: str) -> bool:
        return self.sentinel_count(prefix) >= 0

    def summary(self, limit: int = 1600) -> str:
        if self.launcher_error:
            return f"Could not start FreeCAD: {self.launcher_error}"
        if self.timed_out:
            return ("FreeCAD did not finish in time. Large models can take a while — "
                    "try again, or simplify the design.")
        for line in self.stdout.splitlines():
            if line.startswith(ERROR_PREFIX):
                return line.replace(ERROR_PREFIX, "FreeCAD reported:").strip()
        if self.crashed:
            reason = self.freecad_exception()
            if reason:
                return f"FreeCAD reported: {reason}"
            tail = (self.stdout + "\n" + self.stderr).strip()
            return f"FreeCAD stopped with an unexpected error:\n{tail[-limit:]}"
        text = (self.stdout + "\n" + self.stderr).strip()
        return text[-limit:] if text else "FreeCAD produced no output."


def run_script(exe: Path, script: Path, timeout_s: int = DEFAULT_TIMEOUT_S) -> RunResult:
    """Run a script file through freecadcmd. Never raises for FreeCAD-side failures."""
    exe, script = Path(exe), Path(script)
    try:
        proc = subprocess.run(
            [str(exe), str(script)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout_s, cwd=str(script.parent),
        )
    except subprocess.TimeoutExpired:
        return RunResult(124, "", "", timed_out=True)
    except OSError as e:
        return RunResult(1, "", "", launcher_error=str(e))
    return RunResult(proc.returncode, proc.stdout or "", proc.stderr or "")
