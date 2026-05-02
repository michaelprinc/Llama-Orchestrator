"""
Download and extraction utilities for llama.cpp binaries.

Handles downloading archives from GitHub and extracting them
to the bins/ directory structure.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, Optional

import httpx

logger = logging.getLogger(__name__)

# Download settings
DEFAULT_TIMEOUT = 300.0  # 5 minutes for large files
CHUNK_SIZE = 8192  # 8KB chunks for progress reporting


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


# Type for progress callback: (downloaded_bytes, total_bytes) -> None
ProgressCallback = Callable[[int, Optional[int]], None]


def download_file(
    url: str,
    dest_path: Path,
    timeout: float = DEFAULT_TIMEOUT,
    progress_callback: Optional[ProgressCallback] = None,
) -> Path:
    """
    Download a file from URL to destination path.
    
    Args:
        url: URL to download from
        dest_path: Destination file path
        timeout: Request timeout in seconds
        progress_callback: Optional callback for progress updates
        
    Returns:
        Path to downloaded file
        
    Raises:
        DownloadError: If download fails
    """
    logger.info(f"Downloading {url} to {dest_path}")
    
    # Ensure parent directory exists
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as response:
            response.raise_for_status()
            
            # Get total size if available
            total_size = response.headers.get("content-length")
            total_bytes = int(total_size) if total_size else None
            
            downloaded = 0
            with open(dest_path, "wb") as f:
                for chunk in response.iter_bytes(chunk_size=CHUNK_SIZE):
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    if progress_callback:
                        progress_callback(downloaded, total_bytes)
            
            logger.info(f"Downloaded {downloaded} bytes to {dest_path}")
            return dest_path
            
    except httpx.HTTPStatusError as e:
        raise DownloadError(
            f"HTTP error {e.response.status_code}: {e.response.text}",
            cause=e
        ) from e
    except httpx.RequestError as e:
        raise DownloadError(f"Request failed: {e}", cause=e) from e
    except OSError as e:
        raise DownloadError(f"File write error: {e}", cause=e) from e


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
) -> tuple[Path, str]:
    """
    Download and extract an archive in one operation.
    
    Args:
        url: URL to download from
        dest_dir: Directory to extract to
        expected_sha256: Optional SHA256 to verify
        progress_callback: Optional progress callback
        cleanup: Whether to delete archive after extraction
        
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
        
        # Download
        download_file(url, archive_path, progress_callback=progress_callback)
        
        # Calculate checksum
        actual_sha256 = calculate_sha256(archive_path)
        
        # Verify if expected checksum provided
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
