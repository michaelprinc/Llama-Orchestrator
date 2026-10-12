"""
GitHub API client for llama.cpp releases.

Handles fetching release information from ggml-org/llama.cpp.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import httpx

from llama_orchestrator.binaries.schema import (
    GITHUB_API_RELEASES_URL,
    GitHubReleaseInfo,
    SupportedVariant,
    build_download_url,
)

logger = logging.getLogger(__name__)

# Default timeout for API requests
DEFAULT_TIMEOUT = 30.0

# User-Agent header (GitHub recommends setting this)
USER_AGENT = "llama-orchestrator/0.1.0"

# Release metadata cache TTL (30 minutes)
RELEASE_CACHE_TTL = 1800.0


class GitHubError(Exception):
    """Error interacting with GitHub API."""

    def __init__(self, message: str, status_code: int | None = None):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class RateLimitError(GitHubError):
    """GitHub API rate limit exceeded."""

    def __init__(self, reset_time: datetime | None = None):
        self.reset_time = reset_time
        message = "GitHub API rate limit exceeded"
        if reset_time:
            message += f" (resets at {reset_time.isoformat()})"
        super().__init__(message, status_code=403)


class GitHubClient:
    """
    Client for GitHub releases API.

    Fetches release information for llama.cpp from ggml-org/llama.cpp.
    """

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        token: str | None = None,
    ):
        """
        Initialize GitHub client.

        Args:
            timeout: Request timeout in seconds
            token: Optional GitHub personal access token for higher rate limits
        """
        self.timeout = timeout
        self.token = token
        self._client: httpx.Client | None = None
        # Release metadata cache: {cache_key: (timestamp, data)}
        self._release_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def clear_cache(self) -> None:
        """Clear the release metadata cache."""
        self._release_cache.clear()

    def _cache_get(self, key: str) -> dict[str, Any] | None:
        """Get a cached release entry if it hasn't expired."""
        entry = self._release_cache.get(key)
        if entry is None:
            return None
        timestamp, data = entry
        if time.time() - timestamp > RELEASE_CACHE_TTL:
            del self._release_cache[key]
            return None
        return data

    def _cache_set(self, key: str, data: dict[str, Any]) -> None:
        """Store a release entry in the cache."""
        self._release_cache[key] = (time.time(), data)

    def _get_headers(self) -> dict[str, str]:
        """Get request headers."""
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github.v3+json",
        }
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        return headers

    @property
    def client(self) -> httpx.Client:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout,
                headers=self._get_headers(),
            )
        return self._client

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _check_status(self, response: httpx.Response) -> None:
        """Raise appropriate errors for non-success API responses."""
        if response.status_code == 403:
            # Check for rate limiting
            remaining = response.headers.get("X-RateLimit-Remaining")
            if remaining == "0":
                reset_timestamp = response.headers.get("X-RateLimit-Reset")
                reset_time = None
                if reset_timestamp:
                    reset_time = datetime.fromtimestamp(int(reset_timestamp))
                raise RateLimitError(reset_time)
            raise GitHubError("Access forbidden", status_code=403)

        if response.status_code == 404:
            raise GitHubError("Resource not found", status_code=404)

        if response.status_code >= 400:
            raise GitHubError(
                f"GitHub API error: {response.text}",
                status_code=response.status_code
            )

    def _handle_response(self, response: httpx.Response) -> dict[str, Any]:
        """Handle object API responses and raise appropriate errors."""
        self._check_status(response)
        data = response.json()
        if not isinstance(data, dict):
            raise GitHubError(f"Expected a JSON object from the API, got {type(data).__name__}")
        return data

    def _handle_list_response(self, response: httpx.Response) -> list[dict[str, Any]]:
        """Handle array API responses and raise appropriate errors."""
        self._check_status(response)
        data = response.json()
        if not isinstance(data, list):
            raise GitHubError(f"Expected a JSON array from the API, got {type(data).__name__}")
        return [item for item in data if isinstance(item, dict)]

    def get_latest_release(self) -> dict[str, Any]:
        """
        Get the latest release information.

        Returns:
            Release data including tag_name, assets, etc.
        """
        cache_key = "latest"
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug("Release cache HIT (latest)")
            return cached

        url = f"{GITHUB_API_RELEASES_URL}/latest"
        logger.debug("Fetching latest release from %s", url)

        response = self.client.get(url)
        data = self._handle_response(response)
        self._cache_set(cache_key, data)
        return data

    def get_release(self, tag: str) -> dict[str, Any]:
        """
        Get release information by tag.

        Args:
            tag: Release tag (e.g., 'b7572')

        Returns:
            Release data including tag_name, assets, etc.
        """
        cache_key = f"tag:{tag}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug("Release cache HIT (%s)", tag)
            return cached

        url = f"{GITHUB_API_RELEASES_URL}/tags/{tag}"
        logger.debug("Fetching release %s from %s", tag, url)

        response = self.client.get(url)
        data = self._handle_response(response)
        self._cache_set(cache_key, data)
        return data

    def list_releases(self, per_page: int = 30, page: int = 1) -> list[dict[str, Any]]:
        """
        List releases with pagination.

        Args:
            per_page: Number of releases per page (max 100)
            page: Page number (1-indexed)

        Returns:
            List of release data
        """
        url = GITHUB_API_RELEASES_URL
        params = {"per_page": min(per_page, 100), "page": page}
        logger.debug(f"Listing releases from {url} (page {page})")

        response = self.client.get(url, params=params)
        return self._handle_list_response(response)

    @staticmethod
    def _asset_url_from_release(
        release: dict[str, Any], variant: str
    ) -> str | None:
        """Return the exact archive URL for ``variant`` in one release."""
        tag = release["tag_name"]
        extension = ".zip" if variant.startswith("win-") else ".tar.gz"
        expected_name = f"llama-{tag}-bin-{variant}{extension}"
        return next(
            (
                asset["browser_download_url"]
                for asset in release.get("assets", [])
                if asset.get("name") == expected_name
            ),
            None,
        )

    def get_latest_binary_release(
        self, variant: str | None = None
    ) -> dict[str, Any]:
        """Return the newest release that includes a downloadable binary.

        GitHub's ``/releases/latest`` endpoint can point to a semantic release
        without prebuilt archives.  The Orchestrator installs the rolling
        llama.cpp binary builds, so it must instead select the first published
        release whose assets contain a binary, optionally for one backend.
        """
        cache_key = f"latest-binary:{variant or 'any'}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        for release in self.list_releases(per_page=100):
            # llama.cpp's rolling binary builds may be marked as prereleases.
            # They are still the current published backend packages; only
            # drafts are unavailable to download.
            if release.get("draft"):
                continue
            if variant is not None:
                if self._asset_url_from_release(release, variant) is None:
                    continue
            elif not any("-bin-" in asset.get("name", "") for asset in release.get("assets", [])):
                continue

            self._cache_set(cache_key, release)
            return release

        qualifier = f" for backend '{variant}'" if variant else ""
        raise GitHubError(f"No downloadable llama.cpp binary release found{qualifier}")

    def resolve_latest_version(self, variant: str | None = None) -> str:
        """
        Get the newest version tag that has a downloadable binary archive.

        Returns:
            Version tag string (e.g., 'b7572')
        """
        release = self.get_latest_binary_release(variant)
        return str(release["tag_name"])

    def get_release_info(self, tag: str) -> GitHubReleaseInfo:
        """
        Get structured release info for a tag.

        Args:
            tag: Release tag (e.g., 'b7572')

        Returns:
            GitHubReleaseInfo model
        """
        release = self.get_latest_binary_release() if tag == "latest" else self.get_release(tag)

        published_at = None
        if release.get("published_at"):
            published_at = datetime.fromisoformat(
                release["published_at"].replace("Z", "+00:00")
            )

        return GitHubReleaseInfo(
            tag_name=release["tag_name"],
            published_at=published_at,
            commit_sha=release.get("target_commitish"),
            html_url=release.get("html_url"),
        )

    def get_asset_url(
        self,
        tag: str,
        variant: SupportedVariant,
    ) -> str | None:
        """
        Get the download URL for a specific asset.

        Args:
            tag: Release tag (e.g., 'b7572')
            variant: Platform variant (e.g., 'win-vulkan-x64')

        Returns:
            Direct download URL or None if asset not found
        """
        if tag == "latest":
            release = self.get_latest_binary_release(variant)
        else:
            release = self.get_release(tag)

        return self._asset_url_from_release(release, variant)

    def check_release_exists(self, tag: str) -> bool:
        """
        Check if a release exists.

        Args:
            tag: Release tag to check

        Returns:
            True if release exists, False otherwise
        """
        try:
            self.get_release(tag)
            return True
        except GitHubError as e:
            if e.status_code == 404:
                return False
            raise

    def get_available_variants(self, tag: str) -> list[str]:
        """
        Get list of available variants for a release.

        Args:
            tag: Release tag (e.g., 'b7572')

        Returns:
            List of variant names that have assets
        """
        release = self.get_latest_binary_release() if tag == "latest" else self.get_release(tag)

        variants = []
        for asset in release.get("assets", []):
            name = asset["name"]
            # Parse variant from filename: llama-b7572-bin-{variant}.zip
            if name.startswith("llama-") and "-bin-" in name:
                # Extract variant part
                parts = name.split("-bin-")
                if len(parts) == 2:
                    variant = parts[1].replace(".zip", "").replace(".tar.gz", "")
                    variants.append(variant)

        return variants


# Convenience function for one-off operations
def get_latest_version() -> str:
    """Get the latest llama.cpp release version."""
    with GitHubClient() as client:
        return client.resolve_latest_version()


def get_download_url(version: str, variant: SupportedVariant) -> str:
    """
    Get the download URL for a specific version and variant.

    Args:
        version: Release version (e.g., 'b7572' or 'latest')
        variant: Platform variant

    Returns:
        Download URL
    """
    with GitHubClient() as client:
        url = client.get_asset_url(version, variant)
        if url is not None:
            return url
        if version == "latest":
            raise GitHubError(f"No latest download available for backend '{variant}'")
        return build_download_url(version, variant)
