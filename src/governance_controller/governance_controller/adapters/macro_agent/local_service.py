"""Start the local macro-agent service scaffold as a subprocess."""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import subprocess
import time
from types import TracebackType
from typing import Self

import httpx
import structlog

from governance_controller.config import settings

logger = structlog.get_logger(__name__)


class LocalMacroAgentService:
    """Context manager that starts the local macro-agent service.

    This is intended for dev and integration testing. It looks for the
    ``macro_agent_service`` package in the sibling ``src`` directory relative to
    this source tree and starts it with ``uv run``.
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        startup_timeout: float = 30.0,
    ) -> None:
        self.host = host or settings.macro_agent_local_host
        self.port = port or settings.macro_agent_local_port
        self.startup_timeout = startup_timeout
        self._proc: subprocess.Popen[str] | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _service_root(self) -> str:
        """Return the filesystem path to the macro_agent_service package root."""
        # Prefer an explicit env override for non-standard layouts/tests.
        env_root = os.environ.get("MACRO_AGENT_SERVICE_ROOT")
        if env_root:
            return env_root

        this_file = os.path.abspath(__file__)
        # Walk up from governance_controller/adapters/macro_agent/local_service.py
        # to the repo root: .../src/governance_controller/governance_controller/...
        gc_src = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(this_file)))
        )
        repo_root = os.path.dirname(gc_src)
        # First try the sibling source layout used in this repo.
        sibling = os.path.join(repo_root, "src", "macro_agent_service")
        if os.path.isdir(sibling):
            return sibling
        return os.path.join(repo_root, "macro_agent_service")

    @staticmethod
    def _wait_for_port(host: str, port: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.2)
                if sock.connect_ex((host, port)) == 0:
                    return True
            time.sleep(0.2)
        return False

    async def _health_check(self, timeout: float) -> bool:
        """Wait until the process we started responds on the expected port.

        Also verifies the responding PID matches our subprocess so a stale
        service squatting the port is not mistaken for the new one.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        async with httpx.AsyncClient(timeout=1.0) as client:
            while asyncio.get_event_loop().time() < deadline:
                try:
                    response = await client.get(f"{self.base_url}/docs")
                    if response.status_code == 200:
                        if self._port_owned_by_process():
                            return True
                        logger.warning(
                            "port_squatted_by_stale_process",
                            host=self.host,
                            port=self.port,
                            expected_pid=self._proc.pid if self._proc else None,
                        )
                        return False
                except httpx.RequestError:
                    pass
                await asyncio.sleep(0.2)
        return False

    def _port_owned_by_process(self) -> bool:
        """Return True if the bound port belongs to our subprocess."""
        proc = self._proc
        if proc is None or proc.pid is None:
            return False
        try:
            import psutil
        except ImportError:  # pragma: no cover
            # Without psutil we cannot verify ownership; fall back to trusting
            # the health check, which is the pre-fix behaviour.
            return True

        try:
            process = psutil.Process(proc.pid)
        except psutil.NoSuchProcess:
            return False

        # Walk the process tree in case the binding process is a child (uv
        # wrapper -> python child).
        candidates = [process] + list(process.children(recursive=True))
        for candidate in candidates:
            try:
                for conn in candidate.connections(kind="inet"):
                    if (
                        conn.status == psutil.CONN_LISTEN
                        and conn.laddr.port == self.port
                    ):
                        return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False

    async def __aenter__(self) -> Self:
        service_root = self._service_root()
        if not os.path.isdir(service_root):
            raise RuntimeError(f"Local macro-agent service not found at {service_root}")

        env = os.environ.copy()
        env["MACRO_AGENT_SERVICE_PORT"] = str(self.port)
        env["MACRO_AGENT_SERVICE_HOST"] = self.host

        logger.info(
            "starting_local_macro_agent_service",
            host=self.host,
            port=self.port,
            service_root=service_root,
        )

        # Use ``uv run`` inside the service directory so the service sees its
        # own virtual environment and dependencies. Start a new process group so
        # we can terminate the whole process tree (uv wrapper + python child).
        self._proc = subprocess.Popen(
            [
                "uv",
                "run",
                "python",
                "-c",
                "from macro_agent_service.main import main; main()",
            ],
            cwd=service_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )

        # Wait for the port to become reachable, then ensure it is owned by
        # the process we just started rather than a stale one.
        if not self._wait_for_port(self.host, self.port, self.startup_timeout):
            await self._terminate()
            raise RuntimeError(
                "Local macro-agent service did not bind to port "
                f"{self.host}:{self.port}"
            )

        if not await self._health_check(5.0):
            stdout = ""
            if self._proc and self._proc.stdout:
                try:
                    stdout = self._proc.stdout.read(8192)
                except Exception:  # pragma: no cover
                    stdout = ""
            await self._terminate()
            raise RuntimeError(
                "Local macro-agent service bound to port but health check failed; "
                f"stdout: {stdout}"
            )

        logger.info(
            "local_macro_agent_service_ready",
            base_url=self.base_url,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._terminate()

    async def _terminate(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return

        pgid = getattr(proc, "pid", None)
        if pgid is not None:
            with contextlib.suppress(ProcessLookupError, OSError):
                os.killpg(pgid, 15)  # SIGTERM the whole process group
        try:
            await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, proc.wait),
                timeout=5.0,
            )
        except TimeoutError:
            if pgid is not None:
                with contextlib.suppress(ProcessLookupError, OSError):
                    os.killpg(pgid, 9)  # SIGKILL the whole process group
            await asyncio.get_event_loop().run_in_executor(None, proc.wait)

        logger.info("local_macro_agent_service_stopped")
