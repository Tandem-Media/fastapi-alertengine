# fastapi_alertengine/actions/services.py
"""
Service action handlers for the remote-action system.

restart_container() executes a real docker restart via subprocess.
"""
import re
import subprocess
from typing import Optional

_MAX_NAME_LEN = 254
_VALID_NAME = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.\-/]*$')


async def restart_container(service: str) -> str:
    """
    Restart a named service/container via docker restart.

    Raises:
        ValueError      - invalid service name
        RuntimeError    - docker exited non-zero, timed out, or not found
    """
    # Validate service name
    if (
        not service
        or len(service) > _MAX_NAME_LEN
        or not _VALID_NAME.match(service)
    ):
        raise ValueError(f"Invalid service name: {service!r}")

    try:
        result = subprocess.run(
            ["docker", "restart", service],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"docker restart timed out for service {service!r}")
    except FileNotFoundError:
        raise RuntimeError("docker not found on PATH")

    if result.returncode != 0:
        raise RuntimeError(
            f"docker restart exited with code {result.returncode}: {result.stderr.strip()}"
        )

    container_id = result.stdout.strip()
    return f"Restarted {service} (container: {container_id})"
