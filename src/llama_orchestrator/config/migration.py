"""Explicit instance migration helpers for identity-aware directory layout."""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from llama_orchestrator.benchmark import sync_benchmark_instance_identity
from llama_orchestrator.config.loader import get_instances_dir, load_all_instances, save_config
from llama_orchestrator.engine.state import sync_state_instance_identity


@dataclass(frozen=True)
class InstanceMigrationRecord:
    """One planned or applied instance migration step."""

    name: str
    display_name: str
    instance_uid: str
    instance_no: str
    current_config_path: Path
    target_config_path: Path
    backup_path: Path | None
    changed: bool


@dataclass(frozen=True)
class InstanceMigrationSummary:
    """Structured result for preview and apply flows."""

    applied: bool
    total: int
    changed: int
    skipped: int
    records: tuple[InstanceMigrationRecord, ...]


def migrate_instances(*, apply: bool = False) -> InstanceMigrationSummary:
    """Preview or apply the explicit migration to immutable instance directories."""
    configs = load_all_instances()
    records: list[InstanceMigrationRecord] = []
    identity_rows: list[dict[str, str]] = []
    timestamp = time.strftime("%Y%m%d-%H%M%S")

    for name, config in sorted(configs.items(), key=lambda item: (item[1].instance_no or "", item[0])):
        if config.source_path is None:
            raise RuntimeError(f"Missing source_path for instance '{name}'")
        if config.instance_no is None:
            raise RuntimeError(f"Missing instance_no for instance '{name}'")

        current_config_path = config.source_path
        target_config_path = get_instances_dir() / config.instance_dir_name / "config.json"
        changed = current_config_path != target_config_path
        backup_path: Path | None = None

        if apply and changed:
            backup_path = current_config_path.with_name(f"config.json.bak-{timestamp}")
            shutil.copy2(current_config_path, backup_path)
            if target_config_path.parent.exists() and target_config_path.parent != current_config_path.parent:
                raise RuntimeError(
                    f"Target instance directory already exists for '{name}': {target_config_path.parent}"
                )
            current_config_path.parent.rename(target_config_path.parent)
            backup_path = target_config_path.parent / backup_path.name
            config.set_source_path(target_config_path)
            save_config(config, target_config_path)

        identity_rows.append(
            {
                "name": config.name,
                "instance_uid": config.instance_uid,
                "instance_no": config.instance_no,
                "display_name": config.display_name or config.name,
            }
        )
        records.append(
            InstanceMigrationRecord(
                name=config.name,
                display_name=config.display_name or config.name,
                instance_uid=config.instance_uid,
                instance_no=config.instance_no,
                current_config_path=current_config_path,
                target_config_path=target_config_path,
                backup_path=backup_path,
                changed=changed,
            )
        )

    if apply:
        sync_state_instance_identity(identity_rows)
        sync_benchmark_instance_identity(identity_rows)

    changed_count = sum(1 for record in records if record.changed)
    return InstanceMigrationSummary(
        applied=apply,
        total=len(records),
        changed=changed_count,
        skipped=len(records) - changed_count,
        records=tuple(records),
    )