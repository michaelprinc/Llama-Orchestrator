"""Focused tests for platform-specific process shutdown helpers."""

from unittest.mock import MagicMock, patch

from llama_orchestrator.engine.process import _send_windows_ctrl_c


def test_windows_shutdown_targets_process_group_with_ctrl_break() -> None:
    kernel32 = MagicMock()
    kernel32.GenerateConsoleCtrlEvent.return_value = 1

    with (
        patch("sys.platform", "win32"),
        patch("ctypes.windll", create=True) as windll,
    ):
        windll.kernel32 = kernel32
        assert _send_windows_ctrl_c(1234) is True

    kernel32.GenerateConsoleCtrlEvent.assert_called_once_with(1, 1234)


def test_stop_detached_is_idempotent_when_process_already_exited() -> None:
    from llama_orchestrator.engine.detach import stop_detached

    result = {"method": "not_found", "duration": 0.0, "children_killed": 0}
    with patch("llama_orchestrator.engine.process.graceful_shutdown", return_value=result):
        assert stop_detached("test", 1234) is True
