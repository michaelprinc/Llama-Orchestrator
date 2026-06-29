"""Tests for T1-5: TLS Verification & Safe Binary Downloads.

Tests cover:
- download_file TLS verification and insecure mode
- download_and_extract verify_tls parameter
- GitHubClient release metadata caching
- BinaryManager.install insecure parameter
- CLI --insecure flag
"""

import hashlib
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import httpx

from llama_orchestrator.binaries.downloader import (
    ChecksumError,
    DownloadError,
    DownloadProgress,
    InsecureModeWarning,
    TLSVerificationError,
    calculate_sha256,
    download_and_extract,
    download_file,
    download_file_safe,
    extract_archive,
    extract_tar_gz,
    extract_zip,
    find_executables,
    get_directory_size,
    verify_checksum,
)
from llama_orchestrator.binaries.github import (
    GitHubClient,
    GitHubError,
    RateLimitError,
    get_download_url,
    get_latest_version,
)
from llama_orchestrator.binaries.manager import BinaryManager
from llama_orchestrator.binaries.schema import SupportedVariant


# =============================================================================
# download_file Tests
# =============================================================================


class TestDownloadFile:
    """Tests for download_file with TLS verification."""

    def test_download_file_default_verifies_tls(self, tmp_path: Path) -> None:
        """download_file should verify TLS by default."""
        url = "https://example.com/test.zip"
        dest = tmp_path / "test.zip"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-length": "100"}
        mock_response.iter_bytes.return_value = [b"x" * 100]

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.stream.return_value.__enter__ = MagicMock(return_value=mock_response)
            mock_client.stream.return_value.__exit__ = MagicMock(return_value=False)

            download_file(url, dest)

            # Verify TLS was enabled
            call_kwargs = mock_client_class.call_args.kwargs
            assert call_kwargs.get("verify") is True

    def test_download_file_insecure_disables_tls(self, tmp_path: Path) -> None:
        """download_file with verify_tls=False should disable TLS."""
        url = "https://example.com/test.zip"
        dest = tmp_path / "test.zip"

        mock_response = MagicMock()
        mock_response.headers = {"content-length": "4"}
        mock_response.iter_bytes.return_value = [b"test"]

        with patch("httpx.Client") as mock_client_class:
            mock_client = mock_client_class.return_value
            mock_client.__enter__.return_value = mock_client
            mock_client.stream.return_value.__enter__.return_value = mock_response

            with pytest.warns(InsecureModeWarning):
                download_file(url, dest, verify_tls=False)

            assert mock_client_class.call_args.kwargs["verify"] is False
            assert dest.read_bytes() == b"test"

    def test_download_file_enforces_streaming_size_limit(self, tmp_path: Path) -> None:
        """The limit must also apply when Content-Length is absent or false."""
        url = "https://example.com/test.zip"
        dest = tmp_path / "test.zip"
        mock_response = MagicMock()
        mock_response.headers = {}
        mock_response.iter_bytes.return_value = [b"123", b"456"]

        with (
            patch("httpx.Client") as mock_client_class,
            patch("llama_orchestrator.binaries.downloader.MAX_DOWNLOAD_SIZE", 5),
        ):
            mock_client = mock_client_class.return_value
            mock_client.__enter__.return_value = mock_client
            mock_client.stream.return_value.__enter__.return_value = mock_response

            with pytest.raises(DownloadError, match="too large"):
                download_file(url, dest)

        assert not dest.exists()
        assert not list(tmp_path.glob("*.tmp"))

    def test_download_file_checksum_mismatch(self, tmp_path: Path) -> None:
        """download_file should raise ChecksumError on SHA256 mismatch."""
        url = "https://example.com/test.zip"
        dest = tmp_path / "test.zip"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-length": "100"}
        mock_response.iter_bytes.return_value = [b"x" * 100]

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.stream.return_value.__enter__ = MagicMock(return_value=mock_response)
            mock_client.stream.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(ChecksumError) as exc_info:
                download_file(
                    url,
                    dest,
                    expected_sha256="0" * 64,  # Wrong checksum
                )

            assert "checksum mismatch" in str(exc_info.value).lower()

    def test_download_file_http_error(self, tmp_path: Path) -> None:
        """download_file should raise DownloadError on HTTP error."""
        url = "https://example.com/test.zip"
        dest = tmp_path / "test.zip"

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.stream.return_value.__enter__ = MagicMock(
                return_value=mock_response
            )
            mock_client.stream.return_value.__exit__ = MagicMock(return_value=False)
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Not Found", request=MagicMock(), response=mock_response
            )

            with pytest.raises(DownloadError):
                download_file(url, dest)

    def test_download_file_request_error(self, tmp_path: Path) -> None:
        """download_file should raise DownloadError on network error."""
        url = "https://example.com/test.zip"
        dest = tmp_path / "test.zip"

        with patch("httpx.Client") as mock_client_class:
            mock_client_class.side_effect = httpx.RequestError("Connection failed")

            with pytest.raises(DownloadError):
                download_file(url, dest)


# =============================================================================
# download_file_safe Tests
# =============================================================================


class TestDownloadFileSafe:
    """Tests for download_file_safe (recommended secure download)."""

    def test_download_file_safe_uses_tls(self, tmp_path: Path) -> None:
        """download_file_safe should always use TLS verification."""
        url = "https://example.com/test.zip"
        dest = tmp_path / "test.zip"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-length": "100"}
        mock_response.iter_bytes.return_value = [b"x" * 100]

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.stream.return_value.__enter__ = MagicMock(return_value=mock_response)
            mock_client.stream.return_value.__exit__ = MagicMock(return_value=False)

            download_file_safe(url, dest)

            call_kwargs = mock_client_class.call_args.kwargs
            assert call_kwargs.get("verify") is True


# =============================================================================
# download_and_extract Tests
# =============================================================================


class TestDownloadAndExtract:
    """Tests for download_and_extract with verify_tls parameter."""

    def test_download_and_extract_passes_verify_tls(self, tmp_path: Path) -> None:
        """download_and_extract should pass verify_tls to download_file."""
        url = "https://example.com/test.zip"
        dest_dir = tmp_path / "extracted"
        dest_dir.mkdir()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-length": "100"}
        mock_response.iter_bytes.return_value = [b"x" * 100]

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.stream.return_value.__enter__ = MagicMock(return_value=mock_response)
            mock_client.stream.return_value.__exit__ = MagicMock(return_value=False)

            # Create a dummy zip file for extraction
            with tempfile.TemporaryDirectory() as temp_dir:
                archive_path = Path(temp_dir) / "test.zip"
                import zipfile
                with zipfile.ZipFile(archive_path, "w") as zf:
                    zf.writestr("test.txt", "content")

                with patch("llama_orchestrator.binaries.downloader.download_file") as mock_dl:
                    mock_dl.return_value = archive_path
                    with patch("llama_orchestrator.binaries.downloader.calculate_sha256", return_value="abc" * 16):
                        with patch("llama_orchestrator.binaries.downloader.verify_checksum"):
                            with patch("llama_orchestrator.binaries.downloader.extract_archive"):
                                download_and_extract(url, dest_dir, verify_tls=False)

                                # Verify verify_tls was passed as False
                                call_kwargs = mock_dl.call_args.kwargs
                                assert call_kwargs.get("verify_tls") is False

    def test_download_and_extract_default_verifies_tls(self, tmp_path: Path) -> None:
        """download_and_extract should verify TLS by default."""
        url = "https://example.com/test.zip"
        dest_dir = tmp_path / "extracted"
        dest_dir.mkdir()

        with patch("llama_orchestrator.binaries.downloader.download_file") as mock_dl:
            with patch("llama_orchestrator.binaries.downloader.calculate_sha256", return_value="abc" * 16):
                with patch("llama_orchestrator.binaries.downloader.verify_checksum"):
                    with patch("llama_orchestrator.binaries.downloader.extract_archive"):
                        download_and_extract(url, dest_dir)

                        call_kwargs = mock_dl.call_args.kwargs
                        assert call_kwargs.get("verify_tls") is True


# =============================================================================
# GitHubClient Cache Tests
# =============================================================================


class TestGitHubClientCache:
    """Tests for GitHubClient release metadata caching."""

    def test_cache_hit_returns_cached_data(self) -> None:
        """GitHubClient should return cached data on cache hit."""
        client = GitHubClient()
        client.clear_cache()

        # Pre-populate cache
        cached_data = {"tag_name": "b7572", "assets": []}
        client._cache_set("latest", cached_data)

        # Patch the client property to ensure HTTP is NOT called
        with patch.object(GitHubClient, "client", new_callable=lambda: property(
            fget=lambda self: MagicMock()
        )):
            result = client.get_latest_release()
            assert result == cached_data

    def test_cache_miss_fetches_from_api(self) -> None:
        """GitHubClient should fetch from API on cache miss."""
        client = GitHubClient()
        client.clear_cache()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"tag_name": "b7573", "assets": []}

        mock_http_client = MagicMock()
        mock_http_client.get.return_value = mock_response

        with patch.object(GitHubClient, "client", new_callable=lambda: property(
            fget=lambda self: mock_http_client
        )):
            result = client.get_latest_release()
            assert result["tag_name"] == "b7573"
            mock_http_client.get.assert_called_once()

    def test_cache_expires_after_ttl(self) -> None:
        """GitHubClient cache should expire after TTL."""
        import time

        client = GitHubClient()
        client.clear_cache()

        # Pre-populate cache with old timestamp
        old_timestamp = time.time() - 2000  # 2000 seconds ago (> 1800 TTL)
        client._release_cache["latest"] = (old_timestamp, {"tag_name": "old"})

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"tag_name": "new"}

        mock_http_client = MagicMock()
        mock_http_client.get.return_value = mock_response

        with patch.object(GitHubClient, "client", new_callable=lambda: property(
            fget=lambda self: mock_http_client
        )):
            result = client.get_latest_release()
            assert result["tag_name"] == "new"
            mock_http_client.get.assert_called_once()

    def test_clear_cache_removes_all_entries(self) -> None:
        """GitHubClient.clear_cache should remove all cached entries."""
        client = GitHubClient()
        client._cache_set("latest", {"tag_name": "b7572"})
        client._cache_set("tag:b7571", {"tag_name": "b7571"})

        client.clear_cache()
        assert len(client._release_cache) == 0

    def test_get_release_uses_cache(self) -> None:
        """GitHubClient.get_release should use per-tag cache."""
        client = GitHubClient()
        client.clear_cache()

        cached_data = {"tag_name": "b7572", "assets": []}
        client._cache_set("tag:b7572", cached_data)

        mock_http_client = MagicMock()

        with patch.object(GitHubClient, "client", new_callable=lambda: property(
            fget=lambda self: mock_http_client
        )):
            result = client.get_release("b7572")
            assert result == cached_data


# =============================================================================
# BinaryManager Insecure Parameter Tests
# =============================================================================


class TestBinaryManagerInsecure:
    """Tests for BinaryManager.install with insecure parameter."""

    def test_install_passes_insecure_to_download(self, tmp_path: Path) -> None:
        """BinaryManager.install should pass insecure to download_and_extract."""
        manager = BinaryManager(tmp_path)

        mock_version = MagicMock()
        mock_version.id = "test-uuid"
        mock_version.version = "b7572"
        mock_version.variant = "win-cpu-x64"

        with patch("llama_orchestrator.binaries.manager.download_and_extract") as mock_extract, \
             patch("llama_orchestrator.binaries.manager.logger") as mock_logger, \
             patch.object(manager.registry, "add"), \
             patch.object(manager.registry, "count", return_value=1):

            mock_extract.return_value = (tmp_path / "extracted", "abc" * 16)

            manager.install(
                version="b7572",
                variant="win-cpu-x64",
                source_url="https://example.com/test.zip",
                insecure=True,
            )

            # Verify insecure was passed through
            call_kwargs = mock_extract.call_args.kwargs
            assert call_kwargs.get("verify_tls") is False

    def test_install_default_verifies_tls(self, tmp_path: Path) -> None:
        """BinaryManager.install should verify TLS by default."""
        manager = BinaryManager(tmp_path)

        mock_version = MagicMock()
        mock_version.id = "test-uuid"
        mock_version.version = "b7572"
        mock_version.variant = "win-cpu-x64"

        with patch("llama_orchestrator.binaries.manager.download_and_extract") as mock_extract, \
             patch("llama_orchestrator.binaries.manager.logger") as mock_logger, \
             patch.object(manager.registry, "add"), \
             patch.object(manager.registry, "count", return_value=1):

            mock_extract.return_value = (tmp_path / "extracted", "abc" * 16)

            manager.install(
                version="b7572",
                variant="win-cpu-x64",
                source_url="https://example.com/test.zip",
                insecure=False,
            )

            call_kwargs = mock_extract.call_args.kwargs
            assert call_kwargs.get("verify_tls") is True

    def test_install_latest_resolves_version(self, tmp_path: Path) -> None:
        """BinaryManager.install should resolve 'latest' via GitHubClient."""
        manager = BinaryManager(tmp_path)

        with patch("llama_orchestrator.binaries.manager.download_and_extract") as mock_extract, \
             patch("llama_orchestrator.binaries.manager.logger") as mock_logger, \
             patch.object(manager.registry, "add"), \
             patch.object(manager.registry, "count", return_value=1), \
             patch("llama_orchestrator.binaries.manager.GitHubClient") as mock_github:

            mock_extract.return_value = (tmp_path / "extracted", "abc" * 16)

            # Mock the context manager for GitHubClient
            mock_client = MagicMock()
            mock_client.resolve_latest_version.return_value = "b7573"
            mock_client.get_release_info.return_value = None  # Return None to avoid Pydantic issues
            mock_github.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_github.return_value.__exit__ = MagicMock(return_value=False)

            manager.install(
                version="latest",
                variant="win-cpu-x64",
            )

            # Verify GitHubClient was called to resolve latest
            mock_client.resolve_latest_version.assert_called_once()


# =============================================================================
# CLI --insecure Flag Tests
# =============================================================================


class TestCLIInsecureFlag:
    """Tests for CLI --insecure flag."""

    def test_binary_install_accepts_insecure_flag(self) -> None:
        """CLI binary install should accept --insecure flag."""
        from typer.testing import CliRunner
        from llama_orchestrator.cli import app

        runner = CliRunner()

        with patch("llama_orchestrator.cli._resolve_instance_token"), \
             patch("llama_orchestrator.binaries.BinaryManager") as mock_manager_class:

            mock_manager = MagicMock()
            mock_version = MagicMock()
            mock_version.id = "test-uuid"
            mock_version.version = "b7572"
            mock_version.variant = "win-cpu-x64"
            mock_manager.install.return_value = mock_version
            mock_manager_class.return_value = mock_manager

            result = runner.invoke(
                app,
                ["binary", "install", "b7572", "--variant", "win-cpu-x64", "--insecure"],
            )

            assert result.exit_code == 0
            mock_manager.install.assert_called_once()
            call_kwargs = mock_manager.install.call_args.kwargs
            assert call_kwargs.get("insecure") is True

    def test_binary_install_default_no_insecure(self) -> None:
        """CLI binary install should default to secure (no --insecure)."""
        from typer.testing import CliRunner
        from llama_orchestrator.cli import app

        runner = CliRunner()

        with patch("llama_orchestrator.cli._resolve_instance_token"), \
             patch("llama_orchestrator.binaries.BinaryManager") as mock_manager_class:

            mock_manager = MagicMock()
            mock_version = MagicMock()
            mock_version.id = "test-uuid"
            mock_version.version = "b7572"
            mock_version.variant = "win-cpu-x64"
            mock_manager.install.return_value = mock_version
            mock_manager_class.return_value = mock_manager

            result = runner.invoke(
                app,
                ["binary", "install", "b7572", "--variant", "win-cpu-x64"],
            )

            assert result.exit_code == 0
            call_kwargs = mock_manager.install.call_args.kwargs
            assert call_kwargs.get("insecure") is False

    def test_binary_install_passes_latest_as_version_string(self) -> None:
        """The manager resolves 'latest'; passing None breaks URL construction."""
        from typer.testing import CliRunner
        from llama_orchestrator.cli import app

        mock_version = MagicMock()
        mock_version.id = "test-uuid"
        mock_version.version = "b9999"
        mock_version.variant = "win-cpu-x64"
        mock_version.path = Path("test")

        with patch("llama_orchestrator.binaries.BinaryManager") as manager_class:
            manager_class.return_value.install.return_value = mock_version
            result = CliRunner().invoke(
                app,
                ["binary", "install", "latest", "--variant", "win-cpu-x64"],
            )

        assert result.exit_code == 0
        assert manager_class.return_value.install.call_args.kwargs["version"] == "latest"


# =============================================================================
# Helper Function Tests
# =============================================================================


class TestHelperFunctions:
    """Tests for download helper functions."""

    def test_calculate_sha256(self, tmp_path: Path) -> None:
        """calculate_sha256 should return correct hash."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        expected = hashlib.sha256(b"hello world").hexdigest()
        actual = calculate_sha256(test_file)
        assert actual == expected

    def test_verify_checksum_match(self, tmp_path: Path) -> None:
        """verify_checksum should return True on match."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        expected = hashlib.sha256(b"hello world").hexdigest()
        result = verify_checksum(test_file, expected)
        assert result is True

    def test_verify_checksum_mismatch(self, tmp_path: Path) -> None:
        """verify_checksum should raise ChecksumError on mismatch."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        with pytest.raises(ChecksumError):
            verify_checksum(test_file, "0" * 64)

    def test_extract_zip(self, tmp_path: Path) -> None:
        """extract_zip should extract archive contents."""
        import zipfile

        archive = tmp_path / "test.zip"
        dest = tmp_path / "extracted"

        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("test.txt", "content")

        result = extract_zip(archive, dest)
        assert (result / "test.txt").exists()
        assert (result / "test.txt").read_text() == "content"

    def test_extract_zip_rejects_path_traversal(self, tmp_path: Path) -> None:
        """ZIP members must not escape the requested destination."""
        import zipfile

        archive = tmp_path / "unsafe.zip"
        dest = tmp_path / "extracted"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../escaped.txt", "unsafe")

        with pytest.raises(DownloadError, match="Unsafe path"):
            extract_zip(archive, dest)

        assert not (tmp_path / "escaped.txt").exists()

    def test_extract_tar_gz(self, tmp_path: Path) -> None:
        """extract_tar_gz should extract archive contents."""
        import tarfile
        from io import BytesIO

        archive = tmp_path / "test.tar.gz"
        dest = tmp_path / "extracted"

        with tarfile.open(archive, "w:gz") as tf:
            info = tarfile.TarInfo(name="test.txt")
            data = b"content"
            info.size = len(data)
            tf.addfile(info, fileobj=BytesIO(data))

        result = extract_tar_gz(archive, dest)
        assert (result / "test.txt").exists()

    def test_extract_archive_zip(self, tmp_path: Path) -> None:
        """extract_archive should delegate to extract_zip for .zip."""
        import zipfile

        archive = tmp_path / "test.zip"
        dest = tmp_path / "extracted"

        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("test.txt", "content")

        result = extract_archive(archive, dest)
        assert (result / "test.txt").exists()

    def test_extract_archive_unsupported_format(self, tmp_path: Path) -> None:
        """extract_archive should raise DownloadError for unsupported format."""
        archive = tmp_path / "test.7z"
        archive.touch()
        dest = tmp_path / "extracted"

        with pytest.raises(DownloadError) as exc_info:
            extract_archive(archive, dest)

        assert "Unsupported archive format" in str(exc_info.value)

    def test_get_directory_size(self, tmp_path: Path) -> None:
        """get_directory_size should return total file sizes."""
        (tmp_path / "file1.txt").write_text("hello")
        (tmp_path / "file2.txt").write_text("world!")

        size = get_directory_size(tmp_path)
        assert size == 5 + 6  # "hello" + "world!"

    def test_find_executables(self, tmp_path: Path) -> None:
        """find_executables should find .exe and .dll files."""
        (tmp_path / "test.exe").touch()
        (tmp_path / "test.dll").touch()
        (tmp_path / "test.txt").touch()

        executables = find_executables(tmp_path)
        assert "test.exe" in executables
        assert "test.dll" in executables
        assert "test.txt" not in executables

    def test_download_progress_format_size(self) -> None:
        """DownloadProgress.format_size should format bytes correctly."""
        progress = DownloadProgress()

        assert progress.format_size(500) == "500.0 B"
        assert progress.format_size(1024) == "1.0 KB"
        assert progress.format_size(1024 * 1024) == "1.0 MB"
        assert progress.format_size(1024 * 1024 * 1024) == "1.0 GB"

    def test_download_progress_percent(self) -> None:
        """DownloadProgress.percent should calculate percentage."""
        progress = DownloadProgress()
        progress.downloaded = 500
        progress.total = 1000

        assert progress.percent == 50.0

    def test_download_progress_no_total(self) -> None:
        """DownloadProgress.percent should return None when total is None."""
        progress = DownloadProgress()
        progress.downloaded = 500
        progress.total = None

        assert progress.percent is None


# =============================================================================
# GitHubClient Convenience Function Tests
# =============================================================================


class TestGitHubConvenienceFunctions:
    """Tests for GitHub convenience functions."""

    def test_get_latest_version(self) -> None:
        """get_latest_version should return latest tag."""
        with patch.object(GitHubClient, "resolve_latest_version", return_value="b7573"):
            result = get_latest_version()
            assert result == "b7573"

    def test_get_download_url(self) -> None:
        """get_download_url should return download URL."""
        with patch.object(GitHubClient, "resolve_latest_version", return_value="b7572"), \
             patch.object(GitHubClient, "get_asset_url", return_value="https://example.com/test.zip"):
            result = get_download_url("latest", "win-cpu-x64")
            assert result == "https://example.com/test.zip"
