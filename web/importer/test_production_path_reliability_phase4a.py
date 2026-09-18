"""Phase 4A rem — Network-free full-path regression (R1–R7).

Canonical stack proof: ``scripts/start_local.ps1`` (which resolves Python via
``Resolve-EasyImportsPythonExecutable`` and starts services through
``run_service.ps1``). PIDs printed by the launcher are retained and stopped with
process-tree kill + port-free proof before clear. Dual-process browser → Django
→ API on isolated LOCALAPPDATA with one unrelated hard-cut (v6) fixture retained.

Authority:
``mappings_2/.../production_hubspot_crm_duplicate_operator_path_reliability.md``
Phase **4A**.
"""

from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

import requests
from django.test import SimpleTestCase

from .crm_duplicate_merge_copy import (
    APPROVE_MERGE_PLAN_LABEL,
    AUTHORIZE_MERGE_STEP_LABEL,
    html_shows_unfrozen_merge_plan,
)
from .pending_copy import html_has_pending_mutation_surface


PAIR_COUNT = 1  # bounded two-record Company upload → one review group
V6_CONTRACT = "mappings_2.workflow_checkpoint.pickle.v6"
PRODUCT_CONFLICT = "Frozen product identity conflicts"
BASE_PRODUCT = "easyimports.duplicate_resolution"


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class ProductionPathReliabilityPhase4aDualProcessTests(SimpleTestCase):
    """Browser upload path through freeze/authorize via canonical local launcher."""

    databases = set()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.repo = Path(__file__).resolve().parents[2]
        cls.test_dir = tempfile.TemporaryDirectory(prefix="ei_web_p4a_")
        root = Path(cls.test_dir.name)
        cls.machine = root / "LocalAppData"
        cls.django_db = root / "django.sqlite3"
        cls.media_root = root / "media"
        cls.env_file = root / "phase4a.env"
        cls.machine.mkdir(parents=True, exist_ok=True)
        cls.media_root.mkdir(parents=True, exist_ok=True)

        cls.api_port = _free_port()
        cls.django_port = _free_port()
        cls.api_base = f"http://127.0.0.1:{cls.api_port}"
        cls.django_base = f"http://127.0.0.1:{cls.django_port}"

        # R1: resolve via canonical PowerShell resolver (prefer healthy .venv).
        cls.python_exe = cls._resolve_python_via_canonical_resolver()
        # Service listener PIDs only (Python processes bound to our ports).
        # Launcher host shells are tracked separately for best-effort kill.
        cls.owned_listener_pids: list[int] = []
        cls.owned_host_pids: list[int] = []
        cls._write_env_file()
        cls._seed_pairs()
        cls._start_stack_via_start_local()
        cls._plant_unrelated_v6_fixture()

    @classmethod
    def _resolve_python_via_canonical_resolver(cls) -> Path:
        helper = cls.repo / "scripts" / "lib" / "resolve_python_for_tests.ps1"
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(helper),
                "-RepoRoot",
                str(cls.repo),
            ],
            cwd=str(cls.repo),
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "Resolve-EasyImportsPythonExecutable failed:\n"
                f"stdout={completed.stdout}\nstderr={completed.stderr}"
            )
        line = (completed.stdout or "").strip().splitlines()[-1].strip()
        exe = Path(line)
        if not exe.is_file():
            raise RuntimeError(f"R1: resolver returned non-file: {line!r}")
        # Prefer repo .venv when present and healthy (resolver contract).
        venv_py = cls.repo / ".venv" / "Scripts" / "python.exe"
        if venv_py.is_file():
            if exe.resolve() != venv_py.resolve():
                # Still accept if resolver intentionally fell back, but prove
                # the path is absolute and fully qualified (never bare python).
                if exe.name.lower() in {"python", "python.exe", "py"} and not exe.is_absolute():
                    raise RuntimeError(f"R1: bare interpreter rejected: {exe}")
        if not exe.is_absolute():
            raise RuntimeError(f"R1: non-absolute interpreter: {exe}")
        probe = subprocess.run(
            [
                str(exe),
                "-c",
                "import pandas, fastapi, django, uvicorn; print('ok')",
            ],
            cwd=str(cls.repo),
            text=True,
            capture_output=True,
        )
        if probe.returncode != 0 or "ok" not in probe.stdout:
            raise RuntimeError(
                "R1: resolved interpreter missing local deps:\n"
                f"stdout={probe.stdout}\nstderr={probe.stderr}"
            )
        return exe.resolve()

    @classmethod
    def _write_env_file(cls) -> None:
        # Child service windows load this via start_local -EnvFile.
        lines = [
            f"EASYIMPORTS_API_BASE_URL={cls.api_base}",
            f"EASYIMPORTS_API_HOST=127.0.0.1",
            f"EASYIMPORTS_API_PORT={cls.api_port}",
            f"EASYIMPORTS_DB_PATH={cls.django_db}",
            f"EASYIMPORTS_MEDIA_ROOT={cls.media_root}",
            "EASYIMPORTS_DEBUG=1",
            "EASYIMPORTS_ALLOWED_HOSTS=127.0.0.1,localhost,testserver",
            "EASYIMPORTS_SECRET_STORE=file_insecure",
            "EASYIMPORTS_SF_OAUTH_EXCHANGE=synthetic",
        ]
        cls.env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @classmethod
    def _data_root(cls) -> Path:
        # Default local install: %LOCALAPPDATA%\EasyImports\data
        return cls.machine / "EasyImports" / "data"

    @classmethod
    def _api_state(cls) -> Path:
        return cls._data_root()

    @classmethod
    def _seed_pairs(cls) -> None:
        # Ensure data root exists before seed helper writes org.json.
        root = cls._api_state()
        root.mkdir(parents=True, exist_ok=True)
        helper = cls.repo / "web" / "tools" / "seed_fake_crm_org.py"
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(cls.repo)
        environment["LOCALAPPDATA"] = str(cls.machine)
        completed = subprocess.run(
            [
                str(cls.python_exe),
                str(helper),
                "--state-root",
                str(root),
                "--pair-count",
                str(PAIR_COUNT),
                "--clear-injections",
            ],
            cwd=str(cls.repo),
            env=environment,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "seed_fake_crm_org failed:\n"
                f"stdout={completed.stdout}\nstderr={completed.stderr}"
            )

    @classmethod
    def _launcher_env(cls) -> dict:
        environment = os.environ.copy()
        environment["LOCALAPPDATA"] = str(cls.machine)
        environment["XDG_DATA_HOME"] = str(cls.machine / "xdg")
        environment.pop("EASYIMPORTS_API_STATE_ROOT", None)
        environment["EASYIMPORTS_DB_PATH"] = str(cls.django_db)
        environment["EASYIMPORTS_MEDIA_ROOT"] = str(cls.media_root)
        environment["EASYIMPORTS_DEBUG"] = "1"
        environment["EASYIMPORTS_ALLOWED_HOSTS"] = "127.0.0.1,localhost,testserver"
        environment["EASYIMPORTS_SECRET_STORE"] = "file_insecure"
        environment["EASYIMPORTS_SF_OAUTH_EXCHANGE"] = "synthetic"
        environment["PYTHONPATH"] = str(cls.repo)
        return environment

    @classmethod
    def _ports_busy(cls) -> list[int]:
        busy: list[int] = []
        for port in (cls.api_port, cls.django_port):
            sock = socket.socket()
            try:
                sock.settimeout(0.2)
                sock.connect(("127.0.0.1", port))
                busy.append(port)
            except OSError:
                pass
            finally:
                sock.close()
        return busy

    @classmethod
    def _open_process_exists(cls, pid: int) -> bool | None:
        """Windows OpenProcess existence probe: True/False, or None if inconclusive."""

        if pid <= 0:
            return False
        if sys.platform != "win32":
            return None
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            SYNCHRONIZE = 0x00100000
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
                0,
                int(pid),
            )
            if handle:
                kernel32.CloseHandle(handle)
                return True
            err = int(kernel32.GetLastError() or 0)
            # ERROR_ACCESS_DENIED (5): process exists.
            if err == 5:
                return True
            # ERROR_INVALID_PARAMETER (87): typically no such process.
            if err in {87, 0x57}:
                return False
            return None
        except Exception:
            return None

    @classmethod
    def _pid_alive(cls, pid: int) -> bool:
        """Return True when the OS process exists (best-effort, fail open for listeners).

        Prefer ``OpenProcess`` over ``tasklist`` (Access denied false-negatives).
        Kill-wait loops must not use this alone — use port-free proof +
        ``_pid_gone_for_stop``.
        """

        if pid <= 0:
            return False
        if sys.platform == "win32":
            opened = cls._open_process_exists(pid)
            if opened is not None:
                return opened
            # Secondary: Get-Process.
            try:
                completed = subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        f"if (Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue) "
                        f"{{ 'alive' }} else {{ 'dead' }}",
                    ],
                    text=True,
                    capture_output=True,
                    timeout=8,
                )
                out = (completed.stdout or "").lower()
                if "alive" in out:
                    return True
                if "dead" in out:
                    return False
            except (subprocess.TimeoutExpired, OSError):
                pass
            # tasklist last resort — Access denied is inconclusive → False for
            # stop waits (port ownership is authoritative for listeners).
            try:
                completed = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                    text=True,
                    capture_output=True,
                    timeout=8,
                )
                out = (completed.stdout or "") + (completed.stderr or "")
                low = out.lower()
                if "access is denied" in low or "access denied" in low:
                    return False
                if "no tasks" in low:
                    return False
                return str(pid) in out
            except (subprocess.TimeoutExpired, OSError):
                return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    @classmethod
    def _pid_is_listening(cls, pid: int, ports: list[int] | None = None) -> bool:
        """True if netstat still shows pid LISTENING on the given (or class) ports."""

        check_ports = list(ports or [cls.api_port, cls.django_port])
        return int(pid) in set(cls._listener_pids_for_ports(check_ports))

    @classmethod
    def _pid_gone_for_stop(cls, pid: int) -> bool:
        """Stop success for one PID: not listening on our ports and not alive."""

        if pid <= 0:
            return True
        if cls._pid_is_listening(pid):
            return False
        # Host shells / residual processes: OpenProcess is enough.
        opened = cls._open_process_exists(pid)
        if opened is True:
            return False
        if opened is False:
            return True
        return not cls._pid_alive(pid)

    @classmethod
    def _listener_pids_for_ports(cls, ports: list[int]) -> list[int]:
        """Return PIDs listening on the given ports (Windows netstat; portable fallback).

        Prefer ``netstat -ano`` over Get-NetTCPConnection: the latter often returns
        empty under restricted/non-admin shells even when ports are live.
        """

        if not ports:
            return []
        want = {int(p) for p in ports}
        found: list[int] = []
        seen: set[int] = set()

        def _add(pid: int) -> None:
            if pid > 0 and pid not in seen:
                seen.add(pid)
                found.append(pid)

        # --- netstat (primary on Windows) ---
        try:
            completed = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                text=True,
                capture_output=True,
                timeout=30,
            )
            for line in (completed.stdout or "").splitlines():
                # TCP    127.0.0.1:62198    0.0.0.0:0    LISTENING    12345
                # TCP    0.0.0.0:62198      0.0.0.0:0    LISTENING    12345
                # TCP    [::]:62198         [::]:0       LISTENING    12345
                if "LISTENING" not in line.upper():
                    continue
                parts = line.split()
                if len(parts) < 4:
                    continue
                local = parts[1] if parts[0].upper() == "TCP" else parts[0]
                if ":" not in local:
                    continue
                try:
                    port = int(local.rsplit(":", 1)[-1])
                except ValueError:
                    continue
                if port not in want:
                    continue
                try:
                    pid = int(parts[-1])
                except ValueError:
                    continue
                _add(pid)
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
            pass

        if found:
            return found

        # --- PowerShell Get-NetTCPConnection (secondary) ---
        port_list = ",".join(str(p) for p in sorted(want))
        script = (
            f"$ports = @({port_list}); "
            "$ids = New-Object System.Collections.Generic.List[int]; "
            "foreach ($p in $ports) { "
            "  Get-NetTCPConnection -LocalPort $p -State Listen "
            "    -ErrorAction SilentlyContinue | ForEach-Object { "
            "      [void]$ids.Add([int]$_.OwningProcess) "
            "    } "
            "}; "
            "$ids | Sort-Object -Unique | ForEach-Object { Write-Output $_ }"
        )
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                text=True,
                capture_output=True,
                timeout=30,
            )
            for line in (completed.stdout or "").splitlines():
                line = line.strip()
                if line.isdigit():
                    _add(int(line))
        except (subprocess.TimeoutExpired, OSError):
            pass
        return found

    @classmethod
    def _child_pids(cls, pid: int) -> list[int]:
        """Best-effort direct children of pid (Windows CIM / Unix empty)."""

        if pid <= 0 or sys.platform != "win32":
            return []
        script = (
            f"Get-CimInstance Win32_Process -Filter \"ParentProcessId={int(pid)}\" "
            "-ErrorAction SilentlyContinue | "
            "ForEach-Object { $_.ProcessId }"
        )
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                text=True,
                capture_output=True,
                timeout=8,
            )
        except (subprocess.TimeoutExpired, OSError):
            return []
        kids: list[int] = []
        for line in (completed.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit():
                kids.append(int(line))
        return kids

    @classmethod
    def _terminate_pid_win(cls, pid: int) -> tuple[bool, str]:
        """Terminate one Windows process via TerminateProcess. (ok, detail)."""

        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_TERMINATE = 0x0001
            handle = kernel32.OpenProcess(PROCESS_TERMINATE, 0, int(pid))
            if not handle:
                err = int(kernel32.GetLastError() or 0)
                # Already gone.
                if err in {87, 0x57}:
                    return True, f"open_missing err={err}"
                return False, f"open_failed err={err}"
            try:
                if not kernel32.TerminateProcess(handle, 1):
                    err = int(kernel32.GetLastError() or 0)
                    return False, f"terminate_failed err={err}"
                return True, "terminated"
            finally:
                kernel32.CloseHandle(handle)
        except Exception as exc:  # noqa: BLE001
            return False, f"terminate_exc={type(exc).__name__}"

    @classmethod
    def _run_taskkill(cls, pid: int) -> tuple[int, str]:
        """Run taskkill /T /F; return (returncode, combined output)."""

        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                text=True,
                capture_output=True,
                timeout=10,
            )
            out = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
            return int(completed.returncode), out
        except subprocess.TimeoutExpired:
            return -1, "taskkill_timeout"
        except OSError as exc:
            return -1, f"taskkill_oserror={exc}"

    @classmethod
    def _kill_pid_tree(cls, pid: int) -> str:
        """Kill pid and children. Return gone|killed|failed (never hang long).

        Honors taskkill return codes. Success for service listeners is primarily
        **ports free of this pid**, not an infinite wait on ``_pid_alive``.
        """

        if pid <= 0:
            return "gone"
        if cls._pid_gone_for_stop(pid):
            return "gone"

        attempts: list[str] = []

        def _brief_wait(budget: float = 1.5) -> bool:
            deadline = time.monotonic() + budget
            while time.monotonic() < deadline:
                if cls._pid_gone_for_stop(pid):
                    return True
                time.sleep(0.05)
            return cls._pid_gone_for_stop(pid)

        if sys.platform == "win32":
            # Children first (uvicorn/django may nest under the window host).
            for child in cls._child_pids(pid):
                rc, out = cls._run_taskkill(child)
                attempts.append(f"child_taskkill:{child}:rc={rc}")
                if rc not in {0, 128}:
                    ok, detail = cls._terminate_pid_win(child)
                    attempts.append(f"child_term:{child}:{detail}")
                _brief_wait(0.6)

            rc, out = cls._run_taskkill(pid)
            attempts.append(f"taskkill:rc={rc}")
            # 0 = killed, 128 = not found (already gone).
            if rc in {0, 128}:
                if _brief_wait(2.0):
                    return "killed" if rc == 0 else "gone"
            else:
                low = out.lower()
                attempts.append(f"taskkill_out={out[:200]}")
                # Access denied / other failure: do not spin 12s on _pid_alive.
                if "access" in low and "denied" in low:
                    attempts.append("taskkill_access_denied")

            # PowerShell Stop-Process (tree not automatic; children already tried).
            try:
                completed = subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        f"Stop-Process -Id {int(pid)} -Force -ErrorAction SilentlyContinue; "
                        f"Get-CimInstance Win32_Process -Filter \"ParentProcessId={int(pid)}\" "
                        "-ErrorAction SilentlyContinue | "
                        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
                        "-ErrorAction SilentlyContinue }",
                    ],
                    text=True,
                    capture_output=True,
                    timeout=10,
                )
                attempts.append(f"stop_process:rc={completed.returncode}")
            except (subprocess.TimeoutExpired, OSError) as exc:
                attempts.append(f"stop_process_err={type(exc).__name__}")
            if _brief_wait(1.5):
                return "killed"

            ok, detail = cls._terminate_pid_win(pid)
            attempts.append(f"terminate:{detail}")
            if ok and _brief_wait(1.5):
                return "killed"
        else:
            for sig in (15, 9):
                try:
                    os.kill(pid, sig)
                except OSError:
                    pass
                if _brief_wait(1.0):
                    return "killed"

        if cls._pid_gone_for_stop(pid):
            return "killed"
        # Do not wait further — caller has an overall stop deadline.
        return "failed:" + ";".join(attempts[:8])

    @classmethod
    def _parse_launcher_pids(cls, stdout: str) -> list[int]:
        """Parse PowerShell host PIDs from start_local service window lines."""

        pids: list[int] = []
        for match in re.finditer(
            r"Starting\s+(?:API|Django)\b[^\n]*\bPID\s+(\d+)",
            stdout or "",
            flags=re.IGNORECASE,
        ):
            pids.append(int(match.group(1)))
        seen: set[int] = set()
        ordered: list[int] = []
        for pid in pids:
            if pid not in seen:
                seen.add(pid)
                ordered.append(pid)
        return ordered

    @classmethod
    def _kill_set_now(cls) -> list[int]:
        """Listeners first (authoritative), then host shells that still exist."""

        listeners = cls._listener_pids_for_ports([cls.api_port, cls.django_port])
        hosts = [
            p
            for p in (getattr(cls, "owned_host_pids", []) or [])
            if not cls._pid_gone_for_stop(p)
        ]
        ordered: list[int] = []
        seen: set[int] = set()
        for pid in listeners + hosts + list(getattr(cls, "owned_listener_pids", []) or []):
            if pid > 0 and pid not in seen:
                seen.add(pid)
                ordered.append(pid)
        return ordered

    @classmethod
    def _ports_and_listeners_clear(cls) -> bool:
        busy = cls._ports_busy()
        live = cls._listener_pids_for_ports([cls.api_port, cls.django_port])
        return not busy and not live

    @classmethod
    def _stop_stack(cls) -> None:
        """Kill service listeners (+ host shells); clear only when ports free.

        Bounded overall deadline. Does not spin forever on unkillable PIDs —
        taskkill return codes are checked and alternate terminate paths are
        tried. Success criterion is **ports free**, not every host shell gone.
        """

        recorded_listeners = list(getattr(cls, "owned_listener_pids", []) or [])
        recorded_hosts = list(getattr(cls, "owned_host_pids", []) or [])
        # Ports free is sufficient — lingering host shells are best-effort only.
        if cls._ports_and_listeners_clear():
            cls.owned_listener_pids = []
            cls.owned_host_pids = []
            return

        deadline = time.monotonic() + 28.0
        kill_results: dict[int, str] = {}
        pass_n = 0
        while time.monotonic() < deadline:
            pass_n += 1
            if cls._ports_and_listeners_clear():
                # Ports free: drop ownership even if a host shell lingers.
                cls.owned_listener_pids = []
                cls.owned_host_pids = []
                return
            targets = cls._kill_set_now()
            if not targets:
                # Nothing to kill but ports still busy — brief wait for TIME_WAIT-ish.
                time.sleep(0.2)
                if cls._ports_and_listeners_clear():
                    cls.owned_listener_pids = []
                    cls.owned_host_pids = []
                    return
                break
            for pid in targets:
                if time.monotonic() >= deadline:
                    break
                # Retry failed PIDs a few times with alternates inside _kill_pid_tree.
                status = cls._kill_pid_tree(pid)
                kill_results[pid] = status
            time.sleep(0.15)

        busy = cls._ports_busy()
        still = cls._listener_pids_for_ports([cls.api_port, cls.django_port])
        if not busy and not still:
            cls.owned_listener_pids = []
            cls.owned_host_pids = []
            return
        # Do NOT clear ownership on failure.
        raise RuntimeError(
            "Stack stop incomplete: "
            f"busy_ports={busy} live_listeners={still} "
            f"recorded_listeners={recorded_listeners} "
            f"recorded_hosts={recorded_hosts} "
            f"passes={pass_n} kill_results={kill_results}"
        )

    @classmethod
    def _start_stack_via_start_local(cls) -> None:
        """R1/R2: start stack through canonical scripts/start_local.ps1."""

        if getattr(cls, "owned_listener_pids", None) or getattr(
            cls, "owned_host_pids", None
        ):
            cls._stop_stack()
        launcher = cls.repo / "scripts" / "start_local.ps1"
        if not launcher.is_file():
            raise RuntimeError(f"Missing canonical launcher: {launcher}")
        # Preflight resolver identity (start_local will re-resolve the same way).
        resolved = cls._resolve_python_via_canonical_resolver()
        if resolved != cls.python_exe:
            raise RuntimeError(
                f"Resolver drift: initial={cls.python_exe} now={resolved}"
            )
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(launcher),
                "-RepoRoot",
                str(cls.repo),
                "-PythonExe",
                str(cls.python_exe),
                "-EnvFile",
                str(cls.env_file),
                "-SkipBrowser",
                "-ApiHost",
                "127.0.0.1",
                "-ApiPort",
                str(cls.api_port),
                "-DjangoHost",
                "127.0.0.1",
                "-DjangoPort",
                str(cls.django_port),
                "-ReadyTimeoutSeconds",
                "120",
            ],
            cwd=str(cls.repo),
            env=cls._launcher_env(),
            text=True,
            capture_output=True,
            timeout=200,
        )
        combined = (completed.stdout or "") + "\n" + (completed.stderr or "")
        if completed.returncode != 0:
            raise RuntimeError(
                "start_local.ps1 failed (canonical launcher):\n"
                f"stdout={completed.stdout}\nstderr={completed.stderr}"
            )
        if str(cls.python_exe).lower() not in combined.lower():
            raise RuntimeError(
                "start_local did not report resolved Python:\n" + combined[:2500]
            )
        host_pids = cls._parse_launcher_pids(combined)
        # Host PIDs are optional for ownership; listeners are required.
        cls.owned_host_pids = host_pids
        # Readiness already waited by start_local; re-check.
        for _ in range(40):
            try:
                if requests.get(f"{cls.api_base}/health", timeout=0.3).status_code == 200:
                    break
            except requests.RequestException:
                time.sleep(0.1)
        else:
            cls._stop_stack()
            raise RuntimeError("API not ready after start_local (R2).")
        for _ in range(40):
            try:
                r = requests.get(f"{cls.django_base}/", timeout=0.3)
                if r.status_code in {200, 302}:
                    break
            except requests.RequestException:
                time.sleep(0.1)
        else:
            cls._stop_stack()
            raise RuntimeError("Django not ready after start_local (R2).")
        # Authoritative ownership: PIDs bound to our ports (netstat primary).
        # A LISTENING row is proof the process owns the socket — do NOT filter
        # those PIDs through tasklist/_pid_alive (Access denied false-negatives).
        # Do not use launcher host PIDs for R1 — PowerShell hosts often exit
        # while Python listeners remain.
        listener_pids: list[int] = []
        expected_ports = {cls.api_port, cls.django_port}
        for attempt in range(40):
            busy = set(cls._ports_busy())
            listener_pids = cls._listener_pids_for_ports(
                [cls.api_port, cls.django_port]
            )
            # Both ports must be busy; prefer two PIDs (API + Django).
            if busy == expected_ports and listener_pids:
                if len(listener_pids) >= 2 or attempt >= 25:
                    break
            time.sleep(0.15)
        else:
            listener_pids = cls._listener_pids_for_ports(
                [cls.api_port, cls.django_port]
            )
        if not listener_pids:
            # Ports claim busy but no PID — still attempt stop and fail closed.
            cls.owned_listener_pids = []
            cls.owned_host_pids = list(host_pids)
            try:
                cls._stop_stack()
            except RuntimeError:
                pass
            raise RuntimeError(
                "No listener PIDs discovered via netstat (primary) / "
                "Get-NetTCPConnection (fallback) while "
                f"ports_busy={cls._ports_busy()} "
                f"api_port={cls.api_port} django_port={cls.django_port}."
            )
        # R1 ownership set is netstat listener PIDs only (never host shells).
        cls.owned_listener_pids = list(listener_pids)
        cls.owned_host_pids = list(host_pids)
        pid_file = Path(cls.test_dir.name) / "owned_pids.txt"
        pid_file.write_text(
            "listeners="
            + ",".join(str(p) for p in cls.owned_listener_pids)
            + "\nhosts="
            + ",".join(str(p) for p in host_pids)
            + "\n",
            encoding="utf-8",
        )

    @classmethod
    def _restart_stack_with_seed(cls) -> None:
        cls._stop_stack()
        cls._seed_pairs()
        cls._start_stack_via_start_local()
        cls._plant_unrelated_v6_fixture()

    @classmethod
    def _plant_unrelated_v6_fixture(cls) -> None:
        db_path = cls._api_state() / "workflows.sqlite3"
        # Wait briefly if API just created the DB.
        for _ in range(50):
            if db_path.is_file():
                break
            time.sleep(0.1)
        if not db_path.is_file():
            raise RuntimeError(f"workflows.sqlite3 missing at {db_path}")
        job_id = "run_unrelated_v6_phase4a"
        now = "2026-07-01T00:00:00.000000+00:00"
        with closing(sqlite3.connect(db_path)) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO checkpoint_jobs (
                    job_id, status, stage, revision, created_at, updated_at,
                    serialization_contract, workflow_key, workflow_version,
                    workflow_checkpoint_contract, state_blob
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    "running",
                    "synthetic",
                    1,
                    now,
                    now,
                    V6_CONTRACT,
                    BASE_PRODUCT,
                    4,
                    "easyimports.duplicate_resolution.checkpoint.v7",
                    b"opaque-legacy-bytes-phase4a",
                ),
            )
            connection.commit()
        journeys_path = cls._api_state() / "crm_journeys.json"
        if journeys_path.is_file():
            store = json.loads(journeys_path.read_text(encoding="utf-8"))
        else:
            store = {"journeys": {}}
        store.setdefault("journeys", {})["journey_unrelated_v6_phase4a"] = {
            "journey_id": "journey_unrelated_v6_phase4a",
            "run_id": job_id,
            "source_run_id": "run_july_unrelated_source_phase4a",
            "owner_session": "owner-unrelated-v6",
            "workflow_receipt": {"run_id": "run_july_unrelated_source_phase4a"},
            "status": "workflow_started",
            "entity_family": "company",
        }
        journeys_path.write_text(
            json.dumps(store, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        cls.v6_job_id = job_id

    @classmethod
    def tearDownClass(cls):
        try:
            cls._stop_stack()
        finally:
            cls.test_dir.cleanup()
            super().tearDownClass()

    def _browser(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({"User-Agent": "Phase4APathReliability/1.0"})
        return session

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return self.django_base + path

    def _extract_form_token(self, html: str) -> str:
        marker = 'name="form_token" value="'
        start = html.find(marker)
        self.assertNotEqual(start, -1, "form_token missing")
        start += len(marker)
        end = html.find('"', start)
        return html[start:end]

    def _extract_csrf(self, html: str) -> str:
        marker = 'name="csrfmiddlewaretoken" value="'
        start = html.find(marker)
        self.assertNotEqual(start, -1, "csrf missing")
        start += len(marker)
        end = html.find('"', start)
        return html[start:end]

    def _extract_hidden(self, html: str, name: str) -> str:
        pattern = re.compile(
            rf'name="{re.escape(name)}"\s+value="([^"]*)"',
            re.IGNORECASE,
        )
        match = pattern.search(html)
        if match:
            return match.group(1)
        pattern = re.compile(
            rf'value="([^"]*)"\s+name="{re.escape(name)}"',
            re.IGNORECASE,
        )
        match = pattern.search(html)
        self.assertIsNotNone(match, f"hidden {name!r} missing")
        return match.group(1)

    def _session_id_from_url(self, url: str) -> str:
        match = re.search(r"/sessions/([0-9a-fA-F-]{36})/", url)
        self.assertIsNotNone(match, url)
        return match.group(1)

    def _connection_id_from_start_page(self, html: str) -> str:
        match = re.search(r'<option[^>]+value="(crm_conn_[^"]+)"', html)
        self.assertIsNotNone(match, "no connected CRM")
        return match.group(1)

    def _get(self, browser: requests.Session, path: str) -> requests.Response:
        response = browser.get(self._url(path), timeout=60, allow_redirects=True)
        self.assertIn(response.status_code, {200, 302}, response.text[:500])
        return response

    def _post_form(
        self,
        browser: requests.Session,
        path: str,
        data: dict,
        *,
        files: dict | None = None,
        allow_redirects: bool = True,
    ) -> requests.Response:
        payload = dict(data)
        if "csrfmiddlewaretoken" not in payload:
            prior = browser.get(self._url(path), timeout=30, allow_redirects=True)
            if prior.status_code == 200 and "csrfmiddlewaretoken" in prior.text:
                payload["csrfmiddlewaretoken"] = self._extract_csrf(prior.text)
            elif "csrftoken" in browser.cookies:
                payload["csrfmiddlewaretoken"] = browser.cookies["csrftoken"]
        headers = {}
        if "csrftoken" in browser.cookies:
            headers["X-CSRFToken"] = browser.cookies["csrftoken"]
            headers["Referer"] = self._url(path)
        return browser.post(
            self._url(path),
            data=payload,
            files=files,
            headers=headers,
            timeout=120,
            allow_redirects=allow_redirects,
        )

    def _org_snapshot(self) -> dict:
        org_path = self._api_state() / "fake_crm" / "org.json"
        payload = json.loads(org_path.read_text(encoding="utf-8"))
        records = payload.get("records") or {}
        deleted = sorted(
            rid
            for rid, rec in records.items()
            if isinstance(rec, dict)
            and rec.get("object_type") == "Account"
            and rec.get("is_deleted")
        )
        return {
            "mutation_count": int(payload.get("mutation_count") or 0),
            "deleted_account_ids": deleted,
            "account_ids": sorted(
                rid
                for rid, rec in records.items()
                if isinstance(rec, dict) and rec.get("object_type") == "Account"
            ),
        }

    def _assert_v6_fixture_retained(self) -> None:
        journeys = json.loads(
            (self._api_state() / "crm_journeys.json").read_text(encoding="utf-8")
        )
        row = journeys["journeys"]["journey_unrelated_v6_phase4a"]
        self.assertEqual(row["run_id"], self.v6_job_id)
        db_path = self._api_state() / "workflows.sqlite3"
        with closing(sqlite3.connect(db_path)) as connection:
            contract = connection.execute(
                "SELECT serialization_contract FROM checkpoint_jobs WHERE job_id = ?",
                (self.v6_job_id,),
            ).fetchone()
        self.assertIsNotNone(contract)
        self.assertEqual(contract[0], V6_CONTRACT)

    def _session_id_keys(self, session_id: str) -> tuple[str, str]:
        dashed = str(session_id or "").strip()
        compact = dashed.replace("-", "")
        return dashed, compact

    def _django_workflows(self, session_id: str) -> list[dict]:
        dashed, compact = self._session_id_keys(session_id)
        with closing(sqlite3.connect(self.django_db)) as connection:
            rows = connection.execute(
                """
                SELECT w.id, w.run_id, w.role, w.workflow_key,
                       w.source_workflow_id, s.product_key, s.active_workflow_id
                FROM importer_apiworkflow w
                JOIN importer_importsession s ON s.id = w.session_id
                WHERE s.id = ? OR s.id = ? OR REPLACE(CAST(s.id AS TEXT), '-', '') = ?
                ORDER BY w.role, w.run_id
                """,
                (dashed, compact, compact),
            ).fetchall()
        return [
            {
                "id": r[0],
                "run_id": str(r[1] or ""),
                "role": str(r[2] or ""),
                "workflow_key": str(r[3] or ""),
                "source_workflow_id": r[4],
                "product_key": str(r[5] or ""),
                "active_workflow_id": r[6],
            }
            for r in rows
        ]

    def _assert_roles_lineage_and_active_primary(self, session_id: str) -> None:
        """Unconditional R5: product + roles/source lineage; active stays primary."""

        rows = self._django_workflows(session_id)
        self.assertTrue(rows, f"no ApiWorkflow rows for session {session_id}")
        product = rows[0]["product_key"]
        self.assertEqual(product, BASE_PRODUCT)
        by_role: dict[str, list[dict]] = {}
        for row in rows:
            by_role.setdefault(row["role"], []).append(row)
        self.assertIn("primary", by_role, rows)
        primaries = by_role["primary"]
        self.assertTrue(primaries)
        active_id = rows[0]["active_workflow_id"]
        active_row = next((r for r in rows if r["id"] == active_id), None)
        self.assertIsNotNone(active_row, rows)
        # Phase 3: active primary is preserved through authorize and reloads.
        self.assertEqual(active_row["role"], "primary", active_row)
        self.assertEqual(active_row["workflow_key"], BASE_PRODUCT, active_row)
        # When review exists, it must be REVIEW with source → primary.
        if "review" in by_role:
            for rev in by_role["review"]:
                self.assertEqual(rev["role"], "review")
                self.assertIsNotNone(rev["source_workflow_id"])
                self.assertIn(
                    rev["source_workflow_id"],
                    {p["id"] for p in primaries},
                )
                self.assertIn(
                    rev["workflow_key"],
                    {
                        "easyimports.duplicate_resolution.account_review",
                        "easyimports.duplicate_resolution.person_review",
                        BASE_PRODUCT,
                    },
                )
        # When continuation exists, CONTINUATION + source lineage.
        if "continuation" in by_role:
            for cont in by_role["continuation"]:
                self.assertEqual(cont["role"], "continuation")
                self.assertIsNotNone(cont["source_workflow_id"])
                self.assertIn(
                    cont["source_workflow_id"],
                    {p["id"] for p in primaries}
                    | {r["id"] for r in by_role.get("review", [])},
                )

    def _assert_terminal_accountability(self, html: str) -> None:
        """Unconditional terminal Groups processed + record-level accountability."""

        self.assertIn("Groups processed", html)
        self.assertRegex(
            html,
            rf'<div class="stat-value">\s*{PAIR_COUNT}\s*</div>\s*'
            r'<div class="stat-label">Groups processed</div>',
        )
        lower = html.lower()
        self.assertTrue(
            "survivor" in lower
            or "loser" in lower
            or "record" in lower
            or "member" in lower
            or "processed" in lower,
            html[:800],
        )
        self.assertNotIn(PRODUCT_CONFLICT, html)

    def _connect_fake(self, browser: requests.Session) -> None:
        page = self._get(browser, "/crm/connections/")
        csrf = (
            self._extract_csrf(page.text)
            if "csrfmiddlewaretoken" in page.text
            else browser.cookies.get("csrftoken", "")
        )
        self._post_form(
            browser,
            "/crm/connect/",
            {"provider_key": "fake", "csrfmiddlewaretoken": csrf},
        )
        connections = self._get(browser, "/crm/connections/")
        self.assertIn("fake", connections.text.lower())

    def _pair_csv_bytes(self) -> bytes:
        lines = ["Id,Name"]
        for index in range(PAIR_COUNT):
            name = f"Holdings{index:03d}"
            lines.append(f"P{index}L,{name}")
            lines.append(f"P{index}R,{name}")
        return ("\n".join(lines) + "\n").encode("utf-8")

    def _wait_for_review(self, browser: requests.Session, session_id: str) -> str:
        deadline = time.monotonic() + 90.0
        path = f"/sessions/{session_id}/crm-duplicates/progress/"
        last = ""
        while time.monotonic() < deadline:
            response = browser.get(self._url(path), timeout=60, allow_redirects=True)
            last = response.text
            if response.status_code != 200:
                time.sleep(0.15)
                continue
            if "Review duplicate groups" in last or "crm-duplicate-review-form" in last:
                return last
            if "Review merge plan" in last:
                return last
            if "No duplicate groups found" in last:
                self.fail(f"unexpected zero groups: {last[:600]}")
            time.sleep(0.2)
        self.fail(f"timed out waiting for review: {last[:800]}")

    def _submit_review_windows_until_merge(
        self, browser: requests.Session, session_id: str, first_html: str
    ) -> str:
        html = first_html
        for _ in range(8):
            if html_shows_unfrozen_merge_plan(html):
                return html
            if "crm-duplicate-review-form" not in html:
                merge = self._get(
                    browser, f"/sessions/{session_id}/crm-duplicates/merge/"
                )
                return merge.text
            group_order = self._extract_hidden(html, "group_order")
            group_ids = [g for g in group_order.split(",") if g.strip()]
            data = {
                "csrfmiddlewaretoken": self._extract_csrf(html),
                "form_token": self._extract_form_token(html),
                "window_id": self._extract_hidden(html, "window_id"),
                "window_digest": self._extract_hidden(html, "window_digest"),
                "expected_revision": self._extract_hidden(html, "expected_revision"),
                "group_order": group_order,
            }
            for gid in group_ids:
                data[f"group_revision_{gid}"] = self._extract_hidden(
                    html, f"group_revision_{gid}"
                )
                data[f"allowed_{gid}"] = self._extract_hidden(html, f"allowed_{gid}")
                data[f"recommended_{gid}"] = self._extract_hidden(
                    html, f"recommended_{gid}"
                )
                data[f"eligible_{gid}"] = self._extract_hidden(html, f"eligible_{gid}")
                data[f"advanced_{gid}"] = self._extract_hidden(html, f"advanced_{gid}")
                recommended = data[f"recommended_{gid}"]
                allowed = {
                    part.strip()
                    for part in data[f"allowed_{gid}"].split(",")
                    if part.strip()
                }
                if "approve" in allowed and recommended:
                    data[f"action_{gid}"] = "approve"
                    data[f"survivor_{gid}"] = recommended
                elif "decline" in allowed:
                    data[f"action_{gid}"] = "decline"
                else:
                    data[f"action_{gid}"] = next(iter(allowed))
            post = self._post_form(
                browser,
                f"/sessions/{session_id}/crm-duplicates/review/",
                data,
            )
            self.assertIn(post.status_code, {200, 302}, post.text[:800])
            html = post.text
            if post.url and "merge" in post.url:
                return html
        self.fail("did not reach merge after review")

    def _poll_mutation_status(self, browser: requests.Session, html: str) -> str:
        deadline = time.monotonic() + 90.0
        current = html
        while time.monotonic() < deadline:
            urls = re.findall(r'data-mutation-status-url="([^"]+)"', current)
            if not urls:
                if not html_has_pending_mutation_surface(current):
                    return current
            for rel in urls:
                try:
                    status = browser.get(self._url(rel), timeout=20)
                    if status.status_code != 200:
                        continue
                    body = status.json()
                    if body.get("status") in {"completed", "rejected"}:
                        redirect = body.get("redirect_url") or ""
                        if redirect:
                            refreshed = browser.get(
                                self._url(redirect),
                                timeout=60,
                                allow_redirects=True,
                            )
                            current = refreshed.text
                except (requests.RequestException, ValueError):
                    pass
            time.sleep(0.15)
            if urls:
                match = re.search(r"/sessions/([0-9a-fA-F-]{36})/", urls[0])
                if match:
                    refreshed = browser.get(
                        self._url(f"/sessions/{match.group(1)}/workflow/"),
                        timeout=60,
                        allow_redirects=True,
                    )
                    current = refreshed.text
        return current

    def _upload_to_merge(self, browser: requests.Session) -> tuple[str, str]:
        start = self._get(browser, "/crm/duplicate-journeys/")
        connection_id = self._connection_id_from_start_page(start.text)
        files = {
            "population_file": ("records.csv", self._pair_csv_bytes(), "text/csv"),
        }
        posted = self._post_form(
            browser,
            "/crm/duplicate-journeys/",
            {
                "csrfmiddlewaretoken": self._extract_csrf(start.text),
                "form_token": self._extract_form_token(start.text),
                "connection_id": connection_id,
                "entity_family": "company",
                "source_mode": "uploaded_population",
            },
            files=files,
        )
        self.assertIn("map-record-id", posted.url, posted.text[:400])
        session_id = self._session_id_from_url(posted.url)
        mapped = self._post_form(
            browser,
            f"/sessions/{session_id}/crm-duplicates/map-record-id/",
            {
                "csrfmiddlewaretoken": self._extract_csrf(posted.text),
                "form_token": self._extract_form_token(posted.text),
                "source_column": "Id",
            },
        )
        self.assertTrue(
            any(part in mapped.url for part in ("progress", "review", "merge")),
            mapped.url,
        )
        if "Review duplicate groups" in mapped.text:
            review_html = mapped.text
        else:
            review_html = self._wait_for_review(browser, session_id)
        self.assertNotIn(PRODUCT_CONFLICT, review_html)
        # After analysis/review entry, primary product is frozen.
        self._assert_roles_lineage_and_active_primary(session_id)
        merge_html = self._submit_review_windows_until_merge(
            browser, session_id, review_html
        )
        self.assertNotIn(PRODUCT_CONFLICT, merge_html)
        # Review window may have materialised a review row with lineage.
        self._assert_roles_lineage_and_active_primary(session_id)
        return session_id, merge_html

    def _finalize_and_authorize(
        self,
        browser: requests.Session,
        session_id: str,
        merge_html: str,
        *,
        mode: str,
        double_submit: bool = False,
    ) -> str:
        self.assertIn("Review merge plan", merge_html)
        self.assertIn(APPROVE_MERGE_PLAN_LABEL, merge_html)
        # Repeated merge-summary GET before freeze (R5/R6).
        for _ in range(2):
            reloaded = self._get(
                browser, f"/sessions/{session_id}/crm-duplicates/merge/"
            )
            self.assertNotIn(PRODUCT_CONFLICT, reloaded.text)
            merge_html = reloaded.text
            self._assert_roles_lineage_and_active_primary(session_id)

        fin = self._post_form(
            browser,
            f"/sessions/{session_id}/crm-duplicates/merge/",
            {
                "csrfmiddlewaretoken": self._extract_csrf(merge_html),
                "form_token": self._extract_form_token(merge_html),
                "merge_action": "finalize",
            },
        )
        self.assertIn(fin.status_code, {200, 302}, fin.text[:800])
        self.assertNotIn(PRODUCT_CONFLICT, fin.text)
        merge2 = (
            fin
            if AUTHORIZE_MERGE_STEP_LABEL in fin.text
            else self._get(browser, f"/sessions/{session_id}/crm-duplicates/merge/")
        )
        self.assertIn(AUTHORIZE_MERGE_STEP_LABEL, merge2.text)
        self.assertIn(mode, merge2.text)
        self.assertNotIn(PRODUCT_CONFLICT, merge2.text)

        # After freeze: continuation role+source when present; primary still active.
        for _ in range(2):
            again = self._get(browser, f"/sessions/{session_id}/crm-duplicates/merge/")
            self.assertNotIn(PRODUCT_CONFLICT, again.text)
            self._assert_roles_lineage_and_active_primary(session_id)
            # Workflow-detail reload must not invent product conflict (R5/R6).
            wf = self._get(browser, f"/sessions/{session_id}/workflow/")
            self.assertNotIn(PRODUCT_CONFLICT, wf.text)
            self._assert_roles_lineage_and_active_primary(session_id)

        rows = self._django_workflows(session_id)
        roles = {r["role"] for r in rows}
        # Freeze materialises a continuation projection on merge GET path.
        self.assertIn("continuation", roles, rows)
        conts = [r for r in rows if r["role"] == "continuation"]
        self.assertTrue(conts)
        for cont in conts:
            self.assertIsNotNone(cont["source_workflow_id"])

        auth_payload = {
            "csrfmiddlewaretoken": self._extract_csrf(merge2.text),
            "form_token": self._extract_form_token(merge2.text),
            "merge_action": "authorize",
            "selected_mode": mode,
        }
        auth = self._post_form(
            browser,
            f"/sessions/{session_id}/crm-duplicates/merge/",
            auth_payload,
        )
        self.assertIn(auth.status_code, {200, 302}, auth.text[:800])
        self.assertNotIn(PRODUCT_CONFLICT, auth.text)
        if double_submit:
            again_auth = self._post_form(
                browser,
                f"/sessions/{session_id}/crm-duplicates/merge/",
                auth_payload,
            )
            self.assertIn(again_auth.status_code, {200, 302, 400, 403, 409})
            self.assertNotIn(PRODUCT_CONFLICT, again_auth.text)
        html = auth.text
        if "/workflow/" not in auth.url:
            html = self._get(browser, f"/sessions/{session_id}/workflow/").text
        self.assertNotIn(PRODUCT_CONFLICT, html)
        html = self._poll_mutation_status(browser, html)
        # Repeated workflow-detail reloads after authorize (R5/R6 terminal).
        for _ in range(2):
            final = self._get(browser, f"/sessions/{session_id}/workflow/")
            self.assertNotIn(PRODUCT_CONFLICT, final.text)
            # Active primary preserved; page still shows terminal continuation.
            self._assert_roles_lineage_and_active_primary(session_id)
            self._assert_terminal_accountability(final.text)
            html = final.text
        return html

    def test_r1_canonical_start_local_and_resolver(self):
        self.assertTrue(self.python_exe.is_absolute())
        self.assertTrue(self.python_exe.is_file())
        venv = self.repo / ".venv" / "Scripts" / "python.exe"
        if venv.is_file():
            self.assertEqual(self.python_exe, venv.resolve())
        # Ownership is listener PIDs only — each must still own a LISTENING socket
        # (port proof). Process probes may Access-deny under restricted shells.
        self.assertGreaterEqual(
            len(self.owned_listener_pids), 1, self.owned_listener_pids
        )
        for pid in self.owned_listener_pids:
            still_listening = self._pid_is_listening(pid)
            process_ok = self._pid_alive(pid)
            self.assertTrue(
                still_listening or process_ok,
                f"listener PID not listening and not alive: {pid} "
                f"listening={still_listening} process_ok={process_ok}",
            )
        self.assertTrue((self.repo / "scripts" / "start_local.ps1").is_file())
        self.assertTrue(
            (self.repo / "scripts" / "lib" / "run_service.ps1").is_file()
        )
        health = requests.get(f"{self.api_base}/health", timeout=5)
        self.assertEqual(health.status_code, 200)
        landing = requests.get(f"{self.django_base}/", timeout=5)
        self.assertIn(landing.status_code, {200, 302})
        # Restart proves stop frees ports, then start_local rebinds them.
        self._restart_stack_with_seed()
        self.assertGreaterEqual(len(self.owned_listener_pids), 1)
        for pid in self.owned_listener_pids:
            still_listening = self._pid_is_listening(pid)
            process_ok = self._pid_alive(pid)
            self.assertTrue(
                still_listening or process_ok,
                f"listener PID not listening after restart: {pid}",
            )
        busy_after_start = self._ports_busy()
        self.assertEqual(
            set(busy_after_start),
            {self.api_port, self.django_port},
            busy_after_start,
        )

    def test_full_path_dry_run_with_unrelated_v6_fixture(self):
        self._restart_stack_with_seed()
        self._assert_v6_fixture_retained()
        browser = self._browser()
        self._connect_fake(browser)
        before = self._org_snapshot()
        session_id, merge_html = self._upload_to_merge(browser)
        workflow_html = self._finalize_and_authorize(
            browser, session_id, merge_html, mode="dry_run", double_submit=True
        )
        after = self._org_snapshot()
        self.assertEqual(after["mutation_count"], before["mutation_count"])
        self.assertEqual(after["deleted_account_ids"], [])
        self.assertEqual(after["account_ids"], before["account_ids"])
        self.assertIn(
            "No live CRM changes were authorized for this run.",
            workflow_html,
        )
        self._assert_terminal_accountability(workflow_html)
        self._assert_v6_fixture_retained()

    def test_full_path_execute_exactly_one_loser(self):
        self._restart_stack_with_seed()
        self._assert_v6_fixture_retained()
        browser = self._browser()
        self._connect_fake(browser)
        before = self._org_snapshot()
        session_id, merge_html = self._upload_to_merge(browser)
        workflow_html = self._finalize_and_authorize(
            browser, session_id, merge_html, mode="execute", double_submit=True
        )
        after = self._org_snapshot()
        # Exact-once capability: one merge call → +1 mutation_count, one loser.
        self.assertEqual(
            after["mutation_count"],
            before["mutation_count"] + 1,
            f"before={before} after={after}",
        )
        self.assertEqual(len(after["deleted_account_ids"]), 1)
        remaining = set(after["account_ids"]) - set(after["deleted_account_ids"])
        self.assertEqual(len(remaining), PAIR_COUNT)
        self.assertNotIn(
            "No live CRM changes were authorized for this run.",
            workflow_html,
        )
        self._assert_terminal_accountability(workflow_html)
        self._assert_v6_fixture_retained()
