"""
Download and extraction utilities for llama.cpp binaries.

Handles downloading archives from GitHub and extracting them
to the bins/ directory structure.
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
import warnings
import zipfile
from pathlib import Path
from typing import Callable, Optional

import httpx

logger = logging.getLogger(__name__)

# Download settings
DEFAULT_TIMEOUT = 300.0  # 5 minutes for large files
CHUNK_SIZE = 8192  # 8KB chunks for progress reporting
VERIFY_TLS = True  # Verify TLS certificates by default
MAX_DOWNLOAD_SIZE = 5 * 1024 * 1024 * 1024  # 5GB max download size
INSECURE_MODE = False  # Global insecure mode — must be explicitly enabled


class InsecureModeWarning(RuntimeWarning):
    """Emitted when TLS verification is disabled."""

    def __init__(self, url: str):
        super().__init__(
            f"SECURITY WARNING: TLS certificate verification is DISABLED for {url}. "
            f"This exposes the download to man-in-the-middle attacks. "
            f"Use only for diagnostic purposes with explicit --insecure flag."
        )


class DownloadError(Exception):
    """Error during download or extraction."""

    def __init__(self, message: str, cause: Optional[Exception] = None):
        self.message = message
        self.cause = cause
        super().__init__(message)


class ChecksumError(DownloadError):
    """SHA256 checksum verification failed."""

    def __init__(self, expected: str, actual: str):
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"SHA256 checksum mismatch: expected {expected}, got {actual}"
        )


class TLSVerificationError(DownloadError):
    """TLS certificate verification failed."""

    def __init__(self, url: str, cause: Exception):
        self.url = url
        self.cause = cause
        super().__init__(
            f"TLS certificate verification failed for {url}: {cause}",
            cause=cause
        )


# Type for progress callback: (downloaded_bytes, total_bytes) -> None
ProgressCallback = Callable[[int, Optional[int]], None]


def download_file(
    url: str,
    dest_path: Path,
    timeout: float = DEFAULT_TIMEOUT,
    progress_callback: Optional[ProgressCallback] = None,
    verify_tls: bool = VERIFY_TLS,
    expected_sha256: Optional[str] = None,
) -> Path:
    """
    Download a file from URL to destination path with TLS verification.

    Downloads to a temporary file first, then atomically moves to destination
    to prevent partial/corrupted files.

    Args:
        url: URL to download from
        dest_path: Destination file path
        timeout: Request timeout in seconds
        progress_callback: Optional callback for progress updates
        verify_tls: Whether to verify TLS certificates (default: True)
        expected_sha256: Optional SHA256 to verify after download

    Returns:
        Path to downloaded file

    Raises:
        DownloadError: If download fails
        TLSVerificationError: If TLS verification fails
        ChecksumError: If checksum verification fails
    """
    # Disabling verification is allowed only through an explicit call-site
    # choice (for example CLI --insecure), but must remain visible.
    if not verify_tls:
        logger.critical(
            "SECURITY WARNING: TLS certificate verification is DISABLED for %s. "
            "This exposes the download to man-in-the-middle attacks. "
            "Use only for diagnostic purposes with explicit --insecure flag.",
            url,
        )
        warnings.warn(InsecureModeWarning(url), stacklevel=2)

    logger.info(
        "Downloading %s to %s (TLS verification: %s)",
        url,
        dest_path,
        "enabled" if verify_tls else "disabled",
    )

    # Ensure parent directory exists
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path: Path | None = None
    try:
        # Use httpx client with TLS verification
        client_kwargs = {"timeout": timeout, "follow_redirects": True}

        if verify_tls:
            client_kwargs["verify"] = True
        else:
            client_kwargs["verify"] = False
            logger.warning(f"TLS verification disabled for {url}")

        with httpx.Client(**client_kwargs) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()

                # Get total size if available
                total_size = response.headers.get("content-length")
                total_bytes = int(total_size) if total_size else None

                # Check max download size
                if total_bytes and total_bytes > MAX_DOWNLOAD_SIZE:
                    raise DownloadError(
                        f"Download too large: {total_bytes} bytes "
                        f"(max: {MAX_DOWNLOAD_SIZE} bytes)"
                    )

                # Download to temporary file first (atomic write)
                with tempfile.NamedTemporaryFile(
                    dir=str(dest_path.parent),
                    suffix=".tmp",
                    delete=False,
                ) as tmp_file:
                    tmp_path = Path(tmp_file.name)
                    downloaded = 0

                    for chunk in response.iter_bytes(chunk_size=CHUNK_SIZE):
                        downloaded += len(chunk)
                        if downloaded > MAX_DOWNLOAD_SIZE:
                            raise DownloadError(
                                f"Download too large: exceeded {MAX_DOWNLOAD_SIZE} bytes"
                            )
                        tmp_file.write(chunk)

                        if progress_callback:
                            progress_callback(downloaded, total_bytes)

                # Verify size if we got content-length
                if total_bytes and downloaded != total_bytes:
                    tmp_path.unlink(missing_ok=True)
                    raise DownloadError(
                        f"Download size mismatch: expected {total_bytes}, "
                        f"got {downloaded}"
                    )

                # Verify checksum if expected
                if expected_sha256:
                    actual_sha256 = calculate_sha256(tmp_path)
                    if actual_sha256 != expected_sha256.lower().strip():
                        tmp_path.unlink(missing_ok=True)
                        raise ChecksumError(expected_sha256, actual_sha256)
                    logger.info(
                        "SHA256 checksum verified for %s: %s",
                        dest_path.name,
                        actual_sha256[:16] + "...",
                    )

                # Atomically move to destination
                tmp_path.replace(dest_path)
                tmp_path = None

                logger.info(
                    "Downloaded %d bytes to %s (TLS verification: %s, source: %s)",
                    downloaded,
                    dest_path,
                    "enabled" if verify_tls else "disabled",
                    url,
                )
                return dest_path

    except httpx.HTTPStatusError as e:
        # A response returned from ``client.stream()`` has deliberately not
        # been read yet.  Accessing ``response.text`` here raises
        # httpx.ResponseNotRead and masks the useful HTTP error (for example a
        # GitHub 404).  Status metadata is available without consuming the
        # streamed body.
        raise DownloadError(
            f"HTTP error {e.response.status_code} ({e.response.reason_phrase}) for {url}",
            cause=e,
        ) from e
    except httpx.RequestError as e:
        raise DownloadError(f"Request failed: {e}", cause=e) from e
    except OSError as e:
        raise DownloadError(f"File write error: {e}", cause=e) from e
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove temporary download %s", tmp_path)


def download_file_safe(
    url: str,
    dest_path: Path,
    expected_sha256: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
    progress_callback: Optional[ProgressCallback] = None,
) -> Path:
    """
    Download a file with full safety checks (TLS + checksum + atomic write).

    This is the recommended function for downloading binaries.

    Args:
        url: URL to download from
        dest_path: Destination file path
        expected_sha256: Required SHA256 checksum for verification
        timeout: Request timeout in seconds
        progress_callback: Optional callback for progress updates

    Returns:
        Path to downloaded file

    Raises:
        DownloadError: If download fails
        TLSVerificationError: If TLS verification fails
        ChecksumError: If checksum verification fails
    """
    if expected_sha256 is None:
        logger.warning(
            f"Download without checksum verification: {url}. "
            f"Use download_file_safe with expected_sha256 for security."
        )

    return download_file(
        url=url,
        dest_path=dest_path,
        timeout=timeout,
        progress_callback=progress_callback,
        verify_tls=True,
        expected_sha256=expected_sha256,
    )


def calculate_sha256(file_path: Path) -> str:
    """
    Calculate SHA256 checksum of a file.

    Args:
        file_path: Path to file

    Returns:
        Lowercase hex SHA256 hash
    """
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            sha256.update(chunk)
    return sha256.hexdigest().lower()


def verify_checksum(file_path: Path, expected_sha256: str) -> bool:
    """
    Verify SHA256 checksum of a file.

    Args:
        file_path: Path to file
        expected_sha256: Expected SHA256 hash (hex)

    Returns:
        True if checksum matches

    Raises:
        ChecksumError: If checksum doesn't match
    """
    actual = calculate_sha256(file_path)
    expected = expected_sha256.lower().strip()

    if actual != expected:
        raise ChecksumError(expected, actual)

    logger.info(f"Checksum verified for {file_path}")
    return True


def extract_zip(archive_path: Path, dest_dir: Path) -> Path:
    """
    Extract a ZIP archive to destination directory.

    Args:
        archive_path: Path to ZIP file
        dest_dir: Destination directory

    Returns:
        Path to extracted directory

    Raises:
        DownloadError: If extraction fails
    """
    logger.info(f"Extracting {archive_path} to {dest_dir}")

    dest_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            # Check for zip bomb (very basic check)
            total_size = sum(info.file_size for info in zf.infolist())
            if total_size > 10 * 1024 * 1024 * 1024:  # 10GB limit
                raise DownloadError("Archive too large (potential zip bomb)")

            destination = dest_dir.resolve()
            for info in zf.infolist():
                member_path = (destination / info.filename).resolve()
                if member_path != destination and destination not in member_path.parents:
                    raise DownloadError(f"Unsafe path in archive: {info.filename}")

            zf.extractall(dest_dir)

            # Count extracted files
            file_count = len(zf.namelist())
            logger.info(f"Extracted {file_count} files to {dest_dir}")

        return dest_dir

    except zipfile.BadZipFile as e:
        raise DownloadError(f"Invalid ZIP file: {e}", cause=e) from e
    except OSError as e:
        raise DownloadError(f"Extraction failed: {e}", cause=e) from e


def extract_tar_gz(archive_path: Path, dest_dir: Path) -> Path:
    """
    Extract a tar.gz archive to destination directory.

    Args:
        archive_path: Path to tar.gz file
        dest_dir: Destination directory

    Returns:
        Path to extracted directory

    Raises:
        DownloadError: If extraction fails
    """
    import tarfile

    logger.info(f"Extracting {archive_path} to {dest_dir}")

    dest_dir.mkdir(parents=True, exist_ok=True)

    try:
        with tarfile.open(archive_path, "r:gz") as tf:
            # Security: filter for safe extraction (Python 3.12+)
            # For older Python, we manually check paths
            members = tf.getmembers()

            for member in members:
                # Prevent path traversal
                if member.name.startswith("/") or ".." in member.name:
                    raise DownloadError(f"Unsafe path in archive: {member.name}")

            tf.extractall(dest_dir, filter="data" if hasattr(tarfile, "data_filter") else None)

            logger.info(f"Extracted {len(members)} files to {dest_dir}")

        return dest_dir

    except tarfile.TarError as e:
        raise DownloadError(f"Invalid tar.gz file: {e}", cause=e) from e
    except OSError as e:
        raise DownloadError(f"Extraction failed: {e}", cause=e) from e


def extract_archive(archive_path: Path, dest_dir: Path) -> Path:
    """
    Extract an archive (ZIP or tar.gz) based on extension.

    Args:
        archive_path: Path to archive file
        dest_dir: Destination directory

    Returns:
        Path to extracted directory
    """
    suffix = archive_path.suffix.lower()

    if suffix == ".zip":
        return extract_zip(archive_path, dest_dir)
    elif suffix == ".gz" and archive_path.name.endswith(".tar.gz"):
        return extract_tar_gz(archive_path, dest_dir)
    else:
        raise DownloadError(f"Unsupported archive format: {suffix}")


def get_directory_size(path: Path) -> int:
    """Get total size of all files in a directory."""
    total = 0
    for file in path.rglob("*"):
        if file.is_file():
            total += file.stat().st_size
    return total


def find_executables(path: Path) -> list[str]:
    """Find all executable files in a directory."""
    executables = []
    for file in path.rglob("*"):
        if file.is_file() and file.suffix.lower() in (".exe", ".dll"):
            executables.append(file.name)
    return sorted(set(executables))


def download_and_extract(
    url: str,
    dest_dir: Path,
    expected_sha256: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
    cleanup: bool = True,
    verify_tls: bool = True,
) -> tuple[Path, str]:
    """
    Download and extract an archive in one operation with safety checks.

    Downloads to a temporary file first, verifies checksum, then extracts.
    Uses atomic write to prevent partial/corrupted files.

    Args:
        url: URL to download from
        dest_dir: Directory to extract to
        expected_sha256: Optional SHA256 to verify
        progress_callback: Optional progress callback
        cleanup: Whether to delete archive after extraction
        verify_tls: Whether to verify TLS certificates (default: True)

    Returns:
        Tuple of (extracted_dir, actual_sha256)

    Raises:
        DownloadError: If download or extraction fails
        ChecksumError: If checksum verification fails
    """
    # Create temp directory for download
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Determine archive filename from URL
        archive_name = url.split("/")[-1]
        archive_path = temp_path / archive_name

        # Download with TLS verification and checksum check
        download_file(
            url,
            archive_path,
            progress_callback=progress_callback,
            verify_tls=verify_tls,
            expected_sha256=expected_sha256,
        )

        # Calculate checksum
        actual_sha256 = calculate_sha256(archive_path)

        # Verify if expected checksum provided (already done in download_file,
        # but double-check here for safety)
        if expected_sha256:
            verify_checksum(archive_path, expected_sha256)

        # Extract
        extract_archive(archive_path, dest_dir)

        # Archive is automatically cleaned up when temp_dir is deleted

    return dest_dir, actual_sha256


class DownloadProgress:
    """Helper class for tracking download progress with Rich."""

    def __init__(self):
        self.downloaded = 0
        self.total: Optional[int] = None

    def update(self, downloaded: int, total: Optional[int]) -> None:
        """Update progress."""
        self.downloaded = downloaded
        self.total = total

    @property
    def percent(self) -> Optional[float]:
        """Get progress percentage."""
        if self.total is None or self.total == 0:
            return None
        return (self.downloaded / self.total) * 100

    def format_size(self, size: int) -> str:
        """Format bytes as human-readable string."""
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    def __str__(self) -> str:
        """Format progress as string."""
        downloaded_str = self.format_size(self.downloaded)
        if self.total:
            total_str = self.format_size(self.total)
            percent = self.percent or 0
            return f"{downloaded_str} / {total_str} ({percent:.1f}%)"
        return downloaded_str
