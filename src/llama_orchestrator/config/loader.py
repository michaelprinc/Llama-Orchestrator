"""
Configuration loader for llama-orchestrator.

Handles loading instance configs from JSON files and discovering
all configured instances.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import ValidationError

from llama_orchestrator.config.schema import InstanceConfig
from llama_orchestrator.naming import SERVER_BASE, executable_name, locate_executable

if TYPE_CHECKING:
    from collections.abc import Iterator


CURRENT_SCHEMA_VERSION = "2"


# Cache for configuration files: Path -> (mtime, size, InstanceConfig)
_CONFIG_CACHE: dict[Path, tuple[float, int, InstanceConfig]] = {}

# R16/D1: single lock guarding _CONFIG_CACHE and all config file reads/writes.
# The GUI, health monitor, and CLI share this module-level cache across threads;
# without the lock a concurrent save could tear a read mid-JSON or lose a cache
# invalidation. Reads hold the lock only for stat+cache-check+file-read (small,
# local JSON files); writes hold it for the full persist+evict critical section.
_CONFIG_LOCK = threading.Lock()


def _utc_now_iso() -> str:
    """Return a stable UTC timestamp for persisted config metadata."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _format_instance_no(value: int) -> str:
    """Format a monotonically increasing sequence number for directory readability."""
    return f"{value:08d}"


class ConfigLoadError(Exception):
    """Raised when configuration loading fails."""

    def __init__(self, path: Path, message: str, cause: Exception | None = None):
        self.path = path
        self.message = message
        self.cause = cause
        super().__init__(f"{path}: {message}")


def get_project_root() -> Path:
    """Get the llama-orchestrator project root directory."""
    # Walk up from this file to find the project root
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists() and parent.name == "llama-orchestrator":
            return parent
    # Fallback to current working directory
    return Path.cwd()


def get_instances_dir() -> Path:
    """Get the instances directory path."""
    return get_project_root() / "instances"


def get_bin_dir() -> Path:
    """Get the legacy bin directory path (deprecated, use bins/)."""
    return get_project_root() / "bin"


def get_bins_dir() -> Path:
    """Get the bins directory path (contains versioned binaries)."""
    bins_dir = get_project_root() / "bins"
    bins_dir.mkdir(exist_ok=True)
    return bins_dir


def get_models_dir() -> Path:
    """Get the persistent local models directory path."""
    models_dir = get_project_root() / "models"
    models_dir.mkdir(exist_ok=True)
    return models_dir


def get_llama_server_path(config: InstanceConfig | None = None) -> Path:
    """
    Get the path to llama-server executable.

    Resolution order:
    1. If config has binary.binary_id, lookup by UUID in registry
    2. If config has binary.version+variant, lookup by those
    3. Use default binary from registry
    4. Fall back to the legacy bin/ directory

    Args:
        config: Optional InstanceConfig for binary resolution

    Returns:
        Path to the llama-server executable (platform-native name)

    Raises:
        FileNotFoundError: If no valid binary found
    """
    from llama_orchestrator.binaries import get_binary_manager

    project_root = get_project_root()

    # Try new bins/ structure
    if config is not None and config.binary is not None:
        manager = get_binary_manager(project_root)
        server_path = manager.resolve_server_path(config.binary)
        if server_path is not None and server_path.exists():
            return server_path

    # Try default binary
    try:
        manager = get_binary_manager(project_root)
        default = manager.get_default()
        if default is not None:
            server_path = manager.registry.get_server_path(default.id)
            if server_path is not None and server_path.exists():
                return server_path
    except Exception:
        pass  # Fall through to legacy

    # Fall back to legacy bin/ (platform-native name first, then the
    # Windows-named file of a package created on another platform).
    legacy_path = locate_executable(get_bin_dir(), SERVER_BASE)
    if legacy_path is not None:
        return legacy_path

    raise FileNotFoundError(f"No {executable_name(SERVER_BASE)} found in bins/ or legacy bin/")


def get_state_dir() -> Path:
    """Get the state directory path."""
    state_dir = get_project_root() / "state"
    state_dir.mkdir(exist_ok=True)
    return state_dir


def get_logs_dir() -> Path:
    """Get the logs directory path."""
    logs_dir = get_project_root() / "logs"
    logs_dir.mkdir(exist_ok=True)
    return logs_dir


def get_instance_catalog_db_path() -> Path:
    """Return the local SQLite catalog used for instance numbering and sync."""
    return get_state_dir() / "instance_catalog.sqlite"


@contextmanager
def _get_catalog_connection() -> Iterator[sqlite3.Connection]:
    """Yield an initialized catalog database connection."""
    db_path = get_instance_catalog_db_path()
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS instances (
                instance_uid TEXT PRIMARY KEY,
                instance_no TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                dir_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                sort_order INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS instance_no_sequence (
                next_val INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        row = conn.execute("SELECT COUNT(*) FROM instance_no_sequence").fetchone()
        if row is not None and int(row[0]) == 0:
            conn.execute("INSERT INTO instance_no_sequence (next_val) VALUES (1)")
        conn.commit()
        yield conn
    finally:
        conn.close()


def _allocate_instance_no() -> str:
    """Allocate the next local instance number from the SQLite sequence."""
    with _get_catalog_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT next_val FROM instance_no_sequence").fetchone()
        next_val = int(row[0]) if row is not None else 1
        existing_numbers = [
            int(row[0])
            for row in conn.execute(
                """
                SELECT instance_no FROM instances
                WHERE instance_no GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'
                """
            ).fetchall()
        ]
        instances_dir = get_instances_dir()
        if instances_dir.exists():
            for instance_dir in instances_dir.iterdir():
                if not instance_dir.is_dir():
                    continue
                if match := re.match(r"^(\d{8})(?:_|$)", instance_dir.name):
                    existing_numbers.append(int(match.group(1)))
                config_path = instance_dir / "config.json"
                if not config_path.exists():
                    continue
                try:
                    raw_instance_no = json.loads(config_path.read_text(encoding="utf-8")).get(
                        "instance_no"
                    )
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(raw_instance_no, str) and re.fullmatch(r"\d{8}", raw_instance_no):
                    existing_numbers.append(int(raw_instance_no))

        allocated = max(next_val, max(existing_numbers, default=0) + 1)
        conn.execute("UPDATE instance_no_sequence SET next_val = ?", (allocated + 1,))
        conn.commit()
    return _format_instance_no(allocated)


def _write_config_file(path: Path, data: dict[str, Any]) -> None:
    """Persist config data with consistent formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def _persist_config_file(config: InstanceConfig, path: Path) -> None:
    """Persist a config file under the module lock (R16/D1).

    Holds the lock across write + cache eviction + catalog sync so concurrent
    saves can never tear each other's files, lose an invalidation, or interleave
    catalog mutations.
    """
    with _CONFIG_LOCK:
        _write_config_file(path, config.model_dump(mode="json"))
        _CONFIG_CACHE.pop(path, None)
        if _is_managed_instance_config_path(path):
            _sync_instance_catalog(config, path)


def _validate_instance_data(path: Path, data: dict[str, Any]) -> InstanceConfig:
    """Validate raw JSON data into an InstanceConfig with a friendly error."""
    try:
        return InstanceConfig.model_validate(data)
    except ValidationError as e:
        # Format validation errors nicely
        errors = []
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"])
            msg = err["msg"]
            errors.append(f"  - {loc}: {msg}")
        error_str = "\n".join(errors)
        raise ConfigLoadError(
            path, f"Validation failed:\n{error_str}", e
        ) from e


def _is_managed_instance_config_path(path: Path) -> bool:
    """Return whether a path belongs to the managed instances tree."""
    if path.name != "config.json":
        return False
    try:
        path.resolve().relative_to(get_instances_dir().resolve())
    except ValueError:
        return False
    return True


def _sync_instance_catalog(config: InstanceConfig, path: Path) -> None:
    """Mirror persisted instance identity metadata into the local catalog."""
    if config.instance_no is None:
        raise ConfigLoadError(path, "instance_no must be assigned before catalog sync")
    with _get_catalog_connection() as conn:
        conn.execute(
            """
            INSERT INTO instances (
                instance_uid, instance_no, name, display_name, dir_path,
                created_at, updated_at, schema_version, sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instance_uid) DO UPDATE SET
                instance_no = excluded.instance_no,
                name = excluded.name,
                display_name = excluded.display_name,
                dir_path = excluded.dir_path,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                schema_version = excluded.schema_version,
                sort_order = excluded.sort_order
            """,
            (
                config.instance_uid,
                config.instance_no,
                config.name,
                config.display_name or config.name,
                str(path.parent),
                config.created_at,
                config.updated_at,
                config.schema_version,
                config.sort_order,
            ),
        )
        conn.commit()


def _catalog_uid_by_instance_no() -> dict[str, str]:
    """Return catalog identity ownership keyed by instance number."""
    with _get_catalog_connection() as conn:
        return {
            str(row[0]): str(row[1])
            for row in conn.execute("SELECT instance_no, instance_uid FROM instances").fetchall()
        }


def _repair_duplicate_instance_numbers() -> None:
    """Renumber unsynced duplicate instance configs before catalog sync."""
    by_number: dict[str, list[tuple[Path, InstanceConfig]]] = {}
    for config_path in _iter_config_paths():
        try:
            config = load_config(config_path)
        except ConfigLoadError:
            continue
        instance_no = config.instance_no
        if isinstance(instance_no, str) and re.fullmatch(r"\d{8}", instance_no):
            by_number.setdefault(instance_no, []).append((config_path, config))

    duplicate_groups = {
        instance_no: records
        for instance_no, records in by_number.items()
        if len(records) > 1
    }
    if not duplicate_groups:
        return

    catalog_owners = _catalog_uid_by_instance_no()
    for instance_no, records in sorted(duplicate_groups.items()):
        catalog_uid = catalog_owners.get(instance_no)
        sorted_records = sorted(
            records,
            key=lambda item: (
                item[1].instance_uid != catalog_uid if catalog_uid else False,
                str(item[0]),
            ),
        )
        for config_path, config in sorted_records[1:]:
            config.instance_no = _allocate_instance_no()
            config.updated_at = _utc_now_iso()
            save_config(config, config_path)


def _prepare_loaded_config(
    config: InstanceConfig,
    raw_data: dict[str, Any],
    path: Path,
) -> None:
    """Fill in identity metadata gaps for legacy configs (in memory only).

    R16/D1: this helper never touches disk; callers that need the backfill
    persisted (and catalog-synced) do so explicitly via _persist_config_file.
    """
    now = _utc_now_iso()
    managed_path = _is_managed_instance_config_path(path)

    if managed_path and "schema_version" not in raw_data:
        config.schema_version = CURRENT_SCHEMA_VERSION
    if managed_path and "instance_uid" not in raw_data:
        config.instance_uid = str(uuid4())
    if managed_path and ("instance_no" not in raw_data or not config.instance_no):
        config.instance_no = _allocate_instance_no()
    if managed_path and ("display_name" not in raw_data or not config.display_name):
        config.display_name = raw_data.get("name") or path.parent.name
    if managed_path and "created_at" not in raw_data:
        config.created_at = now
    if managed_path and "updated_at" not in raw_data:
        config.updated_at = now

    config.set_source_path(path)


def _iter_config_paths() -> Iterator[Path]:
    """Yield all config.json files under the instances directory."""
    instances_dir = get_instances_dir()
    if not instances_dir.exists():
        return
    for instance_dir in sorted(instances_dir.iterdir()):
        if not instance_dir.is_dir():
            continue
        config_path = instance_dir / "config.json"
        if config_path.exists():
            yield config_path


def resolve_instance_selector(token: str) -> Path:
    """Resolve a selector token to the canonical config path.

    Resolution precedence is instance_uid, instance_no, immutable alias `name`,
    then unique display_name.
    """
    _normalize_legacy_configs()
    _repair_duplicate_instance_numbers()
    uid_matches: list[Path] = []
    no_matches: list[Path] = []
    name_matches: list[Path] = []
    display_matches: list[Path] = []

    for config_path in _iter_config_paths():
        config = load_config(config_path)
        if config.instance_uid == token:
            uid_matches.append(config_path)
        if config.instance_no == token:
            no_matches.append(config_path)
        if config.name == token:
            name_matches.append(config_path)
        if config.display_name == token:
            display_matches.append(config_path)

    for matches, label in (
        (uid_matches, "instance_uid"),
        (no_matches, "instance_no"),
        (name_matches, "name"),
        (display_matches, "display_name"),
    ):
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ConfigLoadError(get_instances_dir(), f"Ambiguous selector '{token}' matched multiple {label} values")

    raise ConfigLoadError(get_instances_dir(), f"Instance '{token}' not found")


def load_config(path: Path) -> InstanceConfig:
    """
    Load an instance configuration from a JSON file (pure read).

    R16/D1: this function never mutates disk state — no backfill persistence,
    no catalog sync, no instance-number allocation. Identity fields missing
    from legacy files are completed in memory only; call :func:`repair_config`
    from write paths to persist them.

    Args:
        path: Path to the config.json file

    Returns:
        Validated InstanceConfig model

    Raises:
        ConfigLoadError: If file cannot be read or validation fails
    """
    path = Path(path).resolve()

    if not path.exists():
        with _CONFIG_LOCK:
            _CONFIG_CACHE.pop(path, None)
        raise ConfigLoadError(path, "Configuration file not found")

    if not path.is_file():
        with _CONFIG_LOCK:
            _CONFIG_CACHE.pop(path, None)
        raise ConfigLoadError(path, "Path is not a file")

    # Hold the lock across stat + cache check + file read so the (mtime,
    # content) pair stays consistent even while a writer re-creates the file.
    with _CONFIG_LOCK:
        try:
            stat = path.stat()
            mtime = stat.st_mtime
            size = stat.st_size
        except OSError as e:
            raise ConfigLoadError(path, f"Cannot get file stats: {e}", e) from e

        cached_val = _CONFIG_CACHE.get(path)
        if cached_val is not None:
            cached_mtime, cached_size, cached_config = cached_val
            if cached_mtime == mtime and cached_size == size:
                return cached_config

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigLoadError(path, f"Invalid JSON: {e}", e) from e
        except OSError as e:
            raise ConfigLoadError(path, f"Cannot read file: {e}", e) from e

    config = _validate_instance_data(path, data)
    config.set_source_path(path)

    with _CONFIG_LOCK:
        _CONFIG_CACHE[path] = (mtime, size, config)

    return config


def repair_config(path: Path) -> InstanceConfig:
    """Explicitly backfill legacy identity metadata and persist it (write path).

    R16/D1: the old backfill-on-read side effect of :func:`load_config` moved
    into this function. It completes schema_version, instance_uid,
    instance_no, display_name, created_at/updated_at for legacy configs and
    persists the result (file write + cache eviction + catalog sync) in one
    locked critical section. Call it from write paths only — discovery entry
    points use it to normalize legacy trees explicitly.

    Args:
        path: Path to the config.json file

    Returns:
        The repaired (and persisted) InstanceConfig model

    Raises:
        ConfigLoadError: If file cannot be read or validation fails
    """
    path = Path(path).resolve()

    if not path.exists():
        raise ConfigLoadError(path, "Configuration file not found")

    if not path.is_file():
        raise ConfigLoadError(path, "Path is not a file")

    with _CONFIG_LOCK:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigLoadError(path, f"Invalid JSON: {e}", e) from e
        except OSError as e:
            raise ConfigLoadError(path, f"Cannot read file: {e}", e) from e

    config = _validate_instance_data(path, data)
    _prepare_loaded_config(config, data, path)
    # Explicit write operation: always persist (write + evict + catalog sync).
    _persist_config_file(config, path)
    return config


def load_config_from_dict(data: dict[str, Any], name: str = "inline") -> InstanceConfig:
    """
    Load an instance configuration from a dictionary.

    Args:
        data: Configuration dictionary
        name: Name for error messages

    Returns:
        Validated InstanceConfig model

    Raises:
        ConfigLoadError: If validation fails
    """
    try:
        return InstanceConfig.model_validate(data)
    except ValidationError as e:
        errors = []
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"])
            msg = err["msg"]
            errors.append(f"  - {loc}: {msg}")
        error_str = "\n".join(errors)
        raise ConfigLoadError(
            Path(name), f"Validation failed:\n{error_str}", e
        ) from e


_LEGACY_IDENTITY_KEYS = (
    "schema_version",
    "instance_uid",
    "instance_no",
    "display_name",
    "created_at",
    "updated_at",
)


def _is_legacy_on_disk(config_path: Path) -> bool:
    """Return True when the on-disk file lacks any managed identity field."""
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False  # load_config will surface the real error
    if not isinstance(raw, dict):
        return False
    return any(not raw.get(key) for key in _LEGACY_IDENTITY_KEYS)


def _normalize_legacy_configs() -> None:
    """Explicitly repair legacy configs on disk before discovery (R16/D1).

    The old backfill-on-read side effect of load_config moved here: discovery
    entry points normalize legacy files through explicit repair_config() calls,
    so identity fields are persisted + catalog-synced once per file instead of
    as a hidden write during every read. Fully normalized trees take only the
    cheap raw-key check (reads never mutate disk).
    """
    for config_path in _iter_config_paths():
        if _is_legacy_on_disk(config_path):
            repair_config(config_path)


def discover_instances() -> Iterator[tuple[str, Path]]:
    """
    Discover all configured instances.

    Normalizes legacy configs (explicit repair) before yielding; the per-file
    loads themselves are pure reads.

    Yields:
        Tuples of (instance_name, config_path) for each found instance
    """
    _normalize_legacy_configs()
    _repair_duplicate_instance_numbers()
    for config_path in _iter_config_paths():
        config = load_config(config_path)
        yield config.name, config_path


def load_all_instances() -> dict[str, InstanceConfig]:
    """
    Load all configured instances.

    Returns:
        Dictionary mapping instance names to their configs

    Raises:
        ConfigLoadError: If any instance config is invalid
    """
    _normalize_legacy_configs()
    _repair_duplicate_instance_numbers()
    instances: dict[str, InstanceConfig] = {}

    seen_uids: dict[str, Path] = {}
    seen_numbers: dict[str, Path] = {}
    for config_path in _iter_config_paths():
        config = load_config(config_path)
        name = config.name
        if name in instances:
            raise ConfigLoadError(config_path, f"Duplicate instance name '{name}'")
        previous_uid = seen_uids.get(config.instance_uid)
        if previous_uid is not None:
            raise ConfigLoadError(config_path, f"Duplicate instance_uid '{config.instance_uid}' also found in {previous_uid}")
        if config.instance_no is None:
            raise ConfigLoadError(config_path, "instance_no was not assigned during load")
        previous_no = seen_numbers.get(config.instance_no)
        if previous_no is not None:
            raise ConfigLoadError(config_path, f"Duplicate instance_no '{config.instance_no}' also found in {previous_no}")
        seen_uids[config.instance_uid] = config_path
        seen_numbers[config.instance_no] = config_path
        instances[name] = config

    return instances


def get_instance_config(name: str) -> InstanceConfig:
    """
    Get configuration for a specific instance by name.

    Args:
        name: Instance name

    Returns:
        Instance configuration

    Raises:
        ConfigLoadError: If instance not found or config invalid
    """
    try:
        config_path = resolve_instance_selector(name)
    except ConfigLoadError as exc:
        raise ConfigLoadError(
            exc.path,
            f"Instance '{name}' not found. Use 'llama-orch init {name}' to create it.",
            exc,
        ) from exc

    return load_config(config_path)


def save_config(config: InstanceConfig, path: Path | None = None) -> Path:
    """
    Save an instance configuration to a JSON file.

    Args:
        config: Instance configuration to save
        path: Optional custom path (defaults to instances/<name>/config.json)

    Returns:
        Path where config was saved
    """
    if path is None:
        if config.source_path is not None:
            path = config.source_path
        else:
            instances_dir = get_instances_dir()
            if config.instance_no is None:
                config.instance_no = _allocate_instance_no()
            path = instances_dir / config.instance_dir_name / "config.json"

    path = Path(path).resolve()

    managed_path = _is_managed_instance_config_path(path)
    if managed_path and config.instance_no is None:
        config.instance_no = _allocate_instance_no()

    if not config.display_name:
        config.display_name = config.name
    if not config.created_at:
        config.created_at = _utc_now_iso()
    config.schema_version = CURRENT_SCHEMA_VERSION
    config.updated_at = _utc_now_iso()

    # R16/D1: serialized persist + cache eviction + catalog sync (one lock)
    _persist_config_file(config, path)
    config.set_source_path(path)

    return path
