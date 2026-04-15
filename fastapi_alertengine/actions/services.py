# fastapi_alertengine/actions/services.py
"""
Infrastructure service handlers for fastapi-alertengine remote actions.

Docker backend:
    Executes ``docker restart <container>`` in a subprocess so that no
    external Python packages are required.  The call is run in a thread
    executor to remain non-blocking inside an async handler.

Safety contract
---------------
* Service names are validated against ``_SAFE_NAME_RE`` before any
  subprocess call.  The pattern accepts the characters that are legal in
  Docker container names and Kubernetes resource names
  (alphanumerics, ``-``, ``_``, ``.``, ``/``).
* The command is always passed as a *list* — never via ``shell=True`` —
  so the validated name cannot be further interpreted by a shell.
* A hard timeout of ``_DOCKER_TIMEOUT`` seconds prevents the handler
  from hanging indefinitely when Docker is unresponsive.

Observability
-------------
The returned string is written verbatim into the audit-log ``detail``
field by the router, so every restart attempt produces a structured,
searchable audit record.
"""

import asyncio
import re
import subprocess

# Accepts Docker container names and Kubernetes-style <namespace>/<name>
# paths.  The leading character must be alphanumeric so that the name
# cannot start with a dash (which could be misread as a flag).
_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,253}$")

# Hard wall-clock timeout for the blocking Docker call.
_DOCKER_TIMEOUT: int = 30  # seconds


def _validate_service_name(service: str) -> None:
    """Raise ``ValueError`` if *service* contains unsafe characters."""
    if not _SAFE_NAME_RE.match(service):
        raise ValueError(
            f"Invalid service name {service!r}: must match "
            r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,253}$"
        )


def _docker_restart_sync(service: str) -> str:
    """
    Blocking helper — call ``docker restart <service>`` and return a
    human-readable outcome string, raising ``RuntimeError`` on failure.

    This function is intentionally *not* async so that it can be handed
    to ``run_in_executor`` without a secondary event-loop.
    """
    try:
        result = subprocess.run(
            ["docker", "restart", service],
            capture_output=True,
            text=True,
            timeout=_DOCKER_TIMEOUT,
            check=False,  # we inspect returncode ourselves below
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"docker restart timed out after {_DOCKER_TIMEOUT}s "
            f"for container {service!r}"
        ) from exc
    except FileNotFoundError as exc:
        raise RuntimeError(
            "docker binary not found on PATH; ensure Docker is installed "
            "and accessible to this process"
        ) from exc

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(
            f"docker restart exited with code {result.returncode} "
            f"for container {service!r}: {stderr}"
        )

    container_id = result.stdout.strip()
    return f"Restarted {service} (container id: {container_id})"


async def restart_container(service: str) -> str:
    """
    Restart the named Docker container.

    Parameters
    ----------
    service:
        The Docker container name (or Kubernetes-style ``namespace/name``).

    Returns
    -------
    str
        Human-readable outcome message written into the audit trail.

    Raises
    ------
    ValueError
        When *service* contains characters that are not permitted in a
        container or resource name.
    RuntimeError
        When the ``docker`` binary is not found, the command times out,
        or Docker reports a non-zero exit code.
    """
    _validate_service_name(service)

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _docker_restart_sync, service)
