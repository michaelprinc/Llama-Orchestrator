from pathlib import Path
from unittest.mock import patch

from llama_orchestrator.config.schema import (
    BinaryConfig,
    GpuConfig,
    InstanceConfig,
    ModelConfig,
    ServerConfig,
)
from llama_orchestrator.engine.command import build_command, validate_executable


def _config() -> InstanceConfig:
    return InstanceConfig(
        name="diffusion-test",
        binary=BinaryConfig(binary_id="00000000-0000-4000-8000-000000000001"),
        model=ModelConfig(path=Path("models/test.gguf"), context_size=100000),
        server=ServerConfig(port=8040),
        gpu=GpuConfig(backend="vulkan", device_id=0, layers=999),
        env={"LLAMA_ORCH_RUNTIME": "diffusion-gemma"},
    )


def test_build_command_uses_diffusion_adapter() -> None:
    with patch("llama_orchestrator.engine.command.get_llama_server_path", return_value=Path("C:/bin/llama-server.exe")):
        command = build_command(_config())
    assert command[1:3] == ["-m", "llama_orchestrator.diffusion_http_adapter"]
    assert command[command.index("--runner") + 1].endswith("llama-diffusion-gemma-visual-server.exe")
    assert command[command.index("--max-tokens") + 1] == "100000"


def test_build_command_appends_config_args() -> None:
    config = _config()
    config.args = ["--flash-attn", "on", "--reasoning", "on"]
    with patch("llama_orchestrator.engine.command.get_llama_server_path", return_value=Path("C:/bin/llama-server.exe")):
        command = build_command(config)
    assert command[-4:] == ["--flash-attn", "on", "--reasoning", "on"]


def test_validate_executable_checks_diffusion_runner(tmp_path: Path) -> None:
    runner = tmp_path / "llama-diffusion-gemma-visual-server.exe"
    runner.touch()
    with patch("llama_orchestrator.engine.command.get_llama_server_path", return_value=tmp_path / "llama-server.exe"):
        valid, message = validate_executable(_config())
    assert valid is True
    assert str(runner) in message


def test_is_llama_server_process_accepts_diffusion_patterns() -> None:
    from llama_orchestrator.engine.validator import is_llama_server_process
    # Standard
    assert is_llama_server_process("C:/bin/llama-server.exe --model ...") is True
    # Python adapter
    assert is_llama_server_process("python.exe -m llama_orchestrator.diffusion_http_adapter --runner ...") is True
    # Visual server runner
    assert is_llama_server_process("K:/bins/llama-diffusion-gemma-visual-server.exe models/diffusiongemma.gguf") is True
    # Unrelated
    assert is_llama_server_process("notepad.exe file.txt") is False


def test_resolve_latest_filters_out_experimental_binaries() -> None:
    from datetime import datetime, timezone
    from llama_orchestrator.binaries.manager import get_binary_manager
    from llama_orchestrator.binaries.schema import BinaryVersion
    
    manager = get_binary_manager()
    
    # Mock registry binaries
    standard_bin = BinaryVersion(
        id="00000000-0000-0000-0000-000000000001",
        version="b9590",
        variant="win-vulkan-x64",
        download_url="http://example.com/b9590.zip",
        sha256="abc",
        path=Path("bins/1"),
        size_bytes=1000,
        executables=[],
        installed_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
    )
    experimental_bin = BinaryVersion(
        id="00000000-0000-0000-0000-000000000002",
        version="diffusion-gemma-pr24423-49fc3723",
        variant="win-vulkan-x64",
        download_url="http://example.com/diff.zip",
        sha256="def",
        path=Path("bins/2"),
        size_bytes=1000,
        executables=[],
        installed_at=datetime(2026, 6, 13, tzinfo=timezone.utc),  # Installed later!
    )
    
    with patch.object(manager.registry, "list_all", return_value=[standard_bin, experimental_bin]):
        resolved = manager.resolve(BinaryConfig(version="latest", variant="win-vulkan-x64"))
        
    assert resolved is not None
    # Must resolve to the standard one, even though the experimental one is newer (installed later)
    assert resolved.id == standard_bin.id
