"""Safe mutation of an instance's immutable llama-server package pin."""

from __future__ import annotations

from uuid import UUID

from llama_orchestrator.binaries.manager import BinaryManager, BinaryManagerError
from llama_orchestrator.binaries.schema import BinaryVersion
from llama_orchestrator.config import BinaryConfig, get_instance_config, get_project_root, save_config
from llama_orchestrator.engine import InstanceStatus, list_instances, restart_instance


class BinarySwitchError(BinaryManagerError):
    """The requested instance/package switch is not safe to apply."""


def resolve_binary_selector(manager: BinaryManager, selector: str) -> BinaryVersion:
    """Resolve an exact or unambiguous UUID prefix to a registered package."""
    matches = [binary for binary in manager.list_installed() if str(binary.id).startswith(selector)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise BinarySwitchError(f"No registered binary matches '{selector}'")
    raise BinarySwitchError(f"Binary selector '{selector}' is ambiguous; provide a longer UUID")


def switch_instance_binary(
    instance_selector: str,
    binary_selector: str,
    *,
    restart: bool = False,
) -> tuple[BinaryVersion, bool]:
    """Persist a package pin and optionally restart the affected instance.

    All non-binary settings remain untouched.  A running server keeps its
    original executable until a caller explicitly requests the restart.
    """
    config = get_instance_config(instance_selector)
    manager = BinaryManager(get_project_root())
    binary = resolve_binary_selector(manager, binary_selector)
    server_path = manager.registry.get_server_path(binary.id)
    if server_path is None or not server_path.is_file():
        raise BinarySwitchError(f"Registered binary {binary.id} has no llama-server.exe")

    state = list_instances().get(config.name)
    is_running = state is not None and state.status == InstanceStatus.RUNNING
    if is_running and not restart:
        raise BinarySwitchError(
            f"Instance '{config.name}' is running; rerun with restart=True to apply this static change"
        )

    config.binary = BinaryConfig(
        binary_id=binary.id,
        version=binary.version,
        variant=binary.variant,
    )
    save_config(config)
    if is_running:
        restart_instance(config.name)
    return binary, is_running
