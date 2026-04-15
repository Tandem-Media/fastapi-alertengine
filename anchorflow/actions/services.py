# anchorflow/actions/services.py
"""
Infrastructure service handlers for AnchorFlow remote actions.

Phase 1 — simulated mode:
    All handlers return a descriptive string without touching real
    infrastructure.  This is intentional: ship the wiring safely first,
    then swap in the real implementation once it is tested in staging.

Future phases (add behind a feature flag or subclass):
    - Docker: ``docker.DockerClient().containers.get(service).restart()``
    - Kubernetes: patch the ``Deployment`` via the k8s Python client
    - SSH: paramiko exec of ``systemctl restart <service>``
"""


async def restart_container(service: str) -> str:
    """
    Restart the named service / container.

    Parameters
    ----------
    service:
        The service or container name to restart.

    Returns
    -------
    str
        Human-readable outcome message (logged in the audit trail).

    Notes
    -----
    Currently runs in *simulated* mode: no real infrastructure is touched.
    Replace the body of the ``# --- real implementation ---`` block to wire
    in Docker, Kubernetes, or another backend.
    """
    # --- real implementation goes here when ready ---
    # Example (Docker):
    #   import docker
    #   client = docker.from_env()
    #   client.containers.get(service).restart()
    #
    # Example (Kubernetes):
    #   from kubernetes import client as k8s_client, config as k8s_config
    #   k8s_config.load_incluster_config()
    #   apps = k8s_client.AppsV1Api()
    #   apps.patch_namespaced_deployment(
    #       name=service, namespace="default",
    #       body={"spec": {"template": {"metadata": {"annotations": {"restartedAt": ...}}}}}
    #   )
    # ------------------------------------------------

    return f"[SIMULATED] Restarted {service}"
