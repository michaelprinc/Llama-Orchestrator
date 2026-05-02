"""
Tests for CLI exit code standards.
"""

import pytest

from llama_orchestrator.cli_exit_codes import (
    EXIT_ERROR,
    EXIT_SUCCESS,
    ExitCode,
    exit_with_code,
    handle_cli_error,
)


class TestExitCode:
    """Tests for ExitCode enum."""
    
    def test_success_code_is_zero(self):
        """Test that SUCCESS is 0."""
        assert ExitCode.SUCCESS == 0
        assert ExitCode.SUCCESS.value == 0
    
    def test_general_error_is_one(self):
        """Test that GENERAL_ERROR is 1."""
        assert ExitCode.GENERAL_ERROR == 1
    
    def test_code_ranges(self):
        """Test that exit codes are in expected ranges."""
        # General errors: 1-9
        assert 1 <= ExitCode.GENERAL_ERROR <= 9
        assert 1 <= ExitCode.USAGE_ERROR <= 9
        
        # Config errors: 10-19
        assert 10 <= ExitCode.CONFIG_NOT_FOUND <= 19
        assert 10 <= ExitCode.CONFIG_INVALID <= 19
        assert 10 <= ExitCode.INSTANCE_NOT_FOUND <= 19
        
        # Instance errors: 20-29
        assert 20 <= ExitCode.INSTANCE_NOT_RUNNING <= 29
        assert 20 <= ExitCode.INSTANCE_ALREADY_RUNNING <= 29
        assert 20 <= ExitCode.INSTANCE_UNHEALTHY <= 29
        
        # Process errors: 30-39
        assert 30 <= ExitCode.PROCESS_START_FAILED <= 39
        assert 30 <= ExitCode.LOCK_ACQUIRE_FAILED <= 39
        
        # Network errors: 40-49
        assert 40 <= ExitCode.PORT_IN_USE <= 49
        assert 40 <= ExitCode.HEALTH_CHECK_FAILED <= 49
        
        # Binary errors: 50-59
        assert 50 <= ExitCode.BINARY_NOT_FOUND <= 59
        assert 50 <= ExitCode.MODEL_NOT_FOUND <= 59
        
        # Daemon errors: 60-69
        assert 60 <= ExitCode.DAEMON_NOT_RUNNING <= 69
        assert 60 <= ExitCode.DAEMON_START_FAILED <= 69


class TestExitCodeFromException:
    """Tests for from_exception method."""
    
    def test_file_not_found(self):
        """Test mapping FileNotFoundError."""
        code = ExitCode.from_exception(FileNotFoundError("test"))
        assert code == ExitCode.CONFIG_NOT_FOUND
    
    def test_permission_error(self):
        """Test mapping PermissionError."""
        code = ExitCode.from_exception(PermissionError("test"))
        assert code == ExitCode.PERMISSION_DENIED
    
    def test_timeout_error(self):
        """Test mapping TimeoutError."""
        code = ExitCode.from_exception(TimeoutError("test"))
        assert code == ExitCode.TIMEOUT
    
    def test_connection_refused(self):
        """Test mapping ConnectionRefusedError."""
        code = ExitCode.from_exception(ConnectionRefusedError("test"))
        assert code == ExitCode.CONNECTION_REFUSED
    
    def test_keyboard_interrupt(self):
        """Test mapping KeyboardInterrupt."""
        code = ExitCode.from_exception(KeyboardInterrupt())
        assert code == ExitCode.KEYBOARD_INTERRUPT
    
    def test_unknown_exception(self):
        """Test that unknown exceptions map to GENERAL_ERROR."""
        code = ExitCode.from_exception(ValueError("test"))
        assert code == ExitCode.GENERAL_ERROR


class TestExitCodeProperties:
    """Tests for ExitCode properties."""
    
    def test_is_success(self):
        """Test is_success property."""
        assert ExitCode.SUCCESS.is_success is True
        assert ExitCode.GENERAL_ERROR.is_success is False
        assert ExitCode.CONFIG_NOT_FOUND.is_success is False
    
    def test_is_error(self):
        """Test is_error property."""
        assert ExitCode.SUCCESS.is_error is False
        assert ExitCode.GENERAL_ERROR.is_error is True
        assert ExitCode.CONFIG_NOT_FOUND.is_error is True
    
    def test_description(self):
        """Test description property."""
        assert "successfully" in ExitCode.SUCCESS.description.lower()
        assert ExitCode.CONFIG_NOT_FOUND.description != ""
        assert ExitCode.INSTANCE_NOT_RUNNING.description != ""
    
    def test_category_success(self):
        """Test category for success."""
        assert ExitCode.SUCCESS.category == "success"
    
    def test_category_general(self):
        """Test category for general errors."""
        assert ExitCode.GENERAL_ERROR.category == "general"
        assert ExitCode.USAGE_ERROR.category == "general"
    
    def test_category_config(self):
        """Test category for config errors."""
        assert ExitCode.CONFIG_NOT_FOUND.category == "config"
        assert ExitCode.INSTANCE_NOT_FOUND.category == "config"
    
    def test_category_instance(self):
        """Test category for instance errors."""
        assert ExitCode.INSTANCE_NOT_RUNNING.category == "instance"
        assert ExitCode.INSTANCE_UNHEALTHY.category == "instance"
    
    def test_category_process(self):
        """Test category for process errors."""
        assert ExitCode.PROCESS_START_FAILED.category == "process"
        assert ExitCode.LOCK_ACQUIRE_FAILED.category == "process"
    
    def test_category_network(self):
        """Test category for network errors."""
        assert ExitCode.PORT_IN_USE.category == "network"
        assert ExitCode.HEALTH_CHECK_FAILED.category == "network"
    
    def test_category_binary(self):
        """Test category for binary errors."""
        assert ExitCode.BINARY_NOT_FOUND.category == "binary"
        assert ExitCode.MODEL_NOT_FOUND.category == "binary"
    
    def test_category_daemon(self):
        """Test category for daemon errors."""
        assert ExitCode.DAEMON_NOT_RUNNING.category == "daemon"
        assert ExitCode.DAEMON_START_FAILED.category == "daemon"


class TestExitWithCode:
    """Tests for exit_with_code function."""
    
    def test_exits_with_correct_code(self):
        """Test that exit_with_code raises SystemExit."""
        with pytest.raises(SystemExit) as exc_info:
            exit_with_code(ExitCode.SUCCESS)
        
        assert exc_info.value.code == 0
    
    def test_exits_with_error_code(self):
        """Test exit with error code."""
        with pytest.raises(SystemExit) as exc_info:
            exit_with_code(ExitCode.CONFIG_NOT_FOUND)
        
        assert exc_info.value.code == 10


class TestHandleCliError:
    """Tests for handle_cli_error function."""
    
    def test_returns_correct_code(self):
        """Test that correct exit code is returned."""
        code = handle_cli_error(FileNotFoundError("test"))
        assert code == ExitCode.CONFIG_NOT_FOUND
    
    def test_handles_unknown_exception(self):
        """Test handling of unknown exceptions."""
        code = handle_cli_error(RuntimeError("test"))
        assert code == ExitCode.GENERAL_ERROR


class TestConvenienceAliases:
    """Tests for convenience aliases."""
    
    def test_exit_success(self):
        """Test EXIT_SUCCESS alias."""
        assert EXIT_SUCCESS == ExitCode.SUCCESS
        assert EXIT_SUCCESS == 0
    
    def test_exit_error(self):
        """Test EXIT_ERROR alias."""
        assert EXIT_ERROR == ExitCode.GENERAL_ERROR
        assert EXIT_ERROR == 1


class TestExitCodeIntegration:
    """Integration tests for exit codes."""
    
    def test_all_codes_have_descriptions(self):
        """Test that all exit codes have descriptions."""
        for code in ExitCode:
            assert code.description, f"{code.name} has no description"
    
    def test_all_codes_have_categories(self):
        """Test that all exit codes have valid categories."""
        valid_categories = {
            "success", "general", "config", "instance", 
            "process", "network", "binary", "daemon", "unknown"
        }
        for code in ExitCode:
            assert code.category in valid_categories, f"{code.name} has invalid category"
    
    def test_no_duplicate_values(self):
        """Test that all exit code values are unique."""
        values = [code.value for code in ExitCode]
        assert len(values) == len(set(values)), "Duplicate exit code values found"
    
    def test_success_is_only_zero(self):
        """Test that only SUCCESS has value 0."""
        zero_codes = [code for code in ExitCode if code.value == 0]
        assert len(zero_codes) == 1
        assert zero_codes[0] == ExitCode.SUCCESS
