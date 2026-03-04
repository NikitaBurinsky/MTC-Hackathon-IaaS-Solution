import re
from uuid import uuid4


class DockerProvider:
    def __init__(self) -> None:
        try:
            import docker  # pylint: disable=import-outside-toplevel
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Docker SDK is not installed") from exc

        self._docker_module = docker
        try:
            self.client = docker.from_env()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Docker daemon is unavailable. For containerized API, mount /var/run/docker.sock.",
            ) from exc

    def ping(self) -> None:
        self.client.ping()

    def _container_name(self, base_name: str) -> str:
        safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "-", base_name).strip("-")
        safe_name = safe_name or "instance"
        return f"{safe_name}-{uuid4().hex[:8]}"

    def create_instance(self, base_name: str, image_ref: str, cpu: int, ram_mb: int) -> str:
        self.client.images.pull(image_ref)
        container = self.client.containers.create(
            image=image_ref,
            name=self._container_name(base_name),
            command="sleep infinity",
            detach=True,
            mem_limit=f"{ram_mb}m",
            nano_cpus=cpu * 1_000_000_000,
        )
        return container.id

    def start_instance(self, container_id: str) -> None:
        self.client.containers.get(container_id).start()

    def stop_instance(self, container_id: str) -> None:
        self.client.containers.get(container_id).stop(timeout=10)

    def reboot_instance(self, container_id: str) -> None:
        self.client.containers.get(container_id).restart(timeout=10)

    def remove_instance(self, container_id: str) -> None:
        self.client.containers.get(container_id).remove(force=True)

    def get_instance_ip(self, container_id: str) -> str | None:
        container = self.client.containers.get(container_id)
        container.reload()
        networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
        for network_data in networks.values():
            ip_address = network_data.get("IPAddress")
            if ip_address:
                return ip_address
        return None

    def exec_script(self, container_id: str, script_body: str) -> tuple[int, str, str]:
        container = self.client.containers.get(container_id)
        result = container.exec_run(cmd=["/bin/sh", "-c", script_body], demux=True)
        stdout = (result.output[0] or b"").decode("utf-8", errors="replace") if result.output else ""
        stderr = (result.output[1] or b"").decode("utf-8", errors="replace") if result.output else ""
        return result.exit_code, stdout, stderr


_provider: DockerProvider | None = None


def get_docker_provider() -> DockerProvider:
    global _provider
    if _provider is None:
        _provider = DockerProvider()
    return _provider
