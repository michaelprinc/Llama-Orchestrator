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


def test_validate_executable_checks_diffusion_runner(tmp_path: Path) -> None:
    runner = tmp_path / "llama-diffusion-gemma-visual-server.exe"
    runner.touch()
    with patch("llama_orchestrator.engine.command.get_llama_server_path", return_value=tmp_path / "llama-server.exe"):
        valid, message = validate_executable(_config())
    assert valid is True
    assert str(runner) in message
