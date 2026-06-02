"""Explicit instance migration helpers for identity-aware directory layout."""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from llama_orchestrator.benchmark import sync_benchmark_instance_identity
from llama_orchestrator.config.loader import get_instances_dir, load_all_instances, save_config
from llama_orchestrator.config.schema import ModelMetadata
from llama_orchestrator.engine.state import sync_state_instance_identity
from llama_orchestrator.model_metadata import build_model_metadata


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


@dataclass(frozen=True)
class ModelMetadataMigrationRecord:
    """One model-metadata refresh decision for a profile."""

    name: str
    display_name: str
    instance_uid: str
    instance_no: str
    config_path: Path
    backup_path: Path | None
    changed: bool
    reason: str


@dataclass(frozen=True)
class ModelMetadataMigrationSummary:
    """Structured result for metadata refresh preview and apply flows."""

    applied: bool
    total: int
    changed: int
    skipped: int
    records: tuple[ModelMetadataMigrationRecord, ...]


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


def migrate_model_metadata(
    *,
    apply: bool = False,
    include_sha256: bool = False,
    fetch_hf_license: bool = False,
    hf_token: str | None = None,
) -> ModelMetadataMigrationSummary:
    """Preview or apply additive model-metadata refresh on existing profiles."""

    configs = load_all_instances()
    records: list[ModelMetadataMigrationRecord] = []
    timestamp = time.strftime("%Y%m%d-%H%M%S")

    for _name, config in sorted(configs.items(), key=lambda item: (item[1].instance_no or "", item[0])):
        if config.source_path is None:
            raise RuntimeError(f"Missing source_path for instance '{config.name}'")
        if config.instance_no is None:
            raise RuntimeError(f"Missing instance_no for instance '{config.name}'")

        generated = build_model_metadata(
            config,
            include_sha256=include_sha256,
            fetch_hf_license=fetch_hf_license,
            hf_token=hf_token,
        )
        generated_payload = generated.model_dump(mode="json")
        current_payload = config.model_metadata.model_dump(mode="json") if config.model_metadata else None

        changed = generated_payload != current_payload
        reason = "refreshed" if changed else "already_up_to_date"
        backup_path: Path | None = None

        if apply and changed:
            backup_path = config.source_path.with_name(f"config.json.bak-model-metadata-{timestamp}")
            shutil.copy2(config.source_path, backup_path)
            config.model_metadata = generated
            save_config(config, config.source_path)

        records.append(
            ModelMetadataMigrationRecord(
                name=config.name,
                display_name=config.display_name or config.name,
                instance_uid=config.instance_uid,
                instance_no=config.instance_no,
                config_path=config.source_path,
                backup_path=backup_path,
                changed=changed,
                reason=reason,
            )
        )

    changed_count = sum(1 for record in records if record.changed)
    return ModelMetadataMigrationSummary(
        applied=apply,
        total=len(records),
        changed=changed_count,
        skipped=len(records) - changed_count,
        records=tuple(records),
    )


def export_model_metadata(output_path: Path) -> Path:
    """Export model metadata for transfer between installations."""

    payload: dict[str, object] = {
        "schema": "llama-orchestrator-model-metadata-v1",
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "instances": [],
    }

    instances: list[dict[str, object]] = []
    for _name, config in sorted(load_all_instances().items(), key=lambda item: (item[1].instance_no or "", item[0])):
        if config.model_metadata is None:
            continue
        instances.append(
            {
                "instance_uid": config.instance_uid,
                "instance_no": config.instance_no,
                "name": config.name,
                "display_name": config.display_name,
                "model_metadata": config.model_metadata.model_dump(mode="json"),
            }
        )
    payload["instances"] = instances

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path


def import_model_metadata(input_path: Path, *, apply: bool = False) -> ModelMetadataMigrationSummary:
    """Preview or apply metadata import while preserving user-owned metadata."""

    try:
        data = json.loads(input_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"Unable to read metadata import file: {input_path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON metadata import file: {input_path}") from exc

    imported_instances = data.get("instances") if isinstance(data, dict) else None
    if not isinstance(imported_instances, list):
        raise RuntimeError("Metadata import file is missing the 'instances' array")

    by_uid: dict[str, dict[str, object]] = {}
    by_name: dict[str, dict[str, object]] = {}
    for row in imported_instances:
        if not isinstance(row, dict):
            continue
        instance_uid = row.get("instance_uid")
        if isinstance(instance_uid, str) and instance_uid.strip():
            by_uid[instance_uid] = row
        instance_name = row.get("name")
        if isinstance(instance_name, str) and instance_name.strip():
            by_name[instance_name] = row

    configs = load_all_instances()
    records: list[ModelMetadataMigrationRecord] = []
    timestamp = time.strftime("%Y%m%d-%H%M%S")

    for _name, config in sorted(configs.items(), key=lambda item: (item[1].instance_no or "", item[0])):
        if config.source_path is None:
            raise RuntimeError(f"Missing source_path for instance '{config.name}'")
        if config.instance_no is None:
            raise RuntimeError(f"Missing instance_no for instance '{config.name}'")

        source_row = by_uid.get(config.instance_uid) or by_name.get(config.name)
        if source_row is None:
            records.append(
                ModelMetadataMigrationRecord(
                    name=config.name,
                    display_name=config.display_name or config.name,
                    instance_uid=config.instance_uid,
                    instance_no=config.instance_no,
                    config_path=config.source_path,
                    backup_path=None,
                    changed=False,
                    reason="not_present_in_import",
                )
            )
            continue

        imported_payload = source_row.get("model_metadata")
        if not isinstance(imported_payload, dict):
            records.append(
                ModelMetadataMigrationRecord(
                    name=config.name,
                    display_name=config.display_name or config.name,
                    instance_uid=config.instance_uid,
                    instance_no=config.instance_no,
                    config_path=config.source_path,
                    backup_path=None,
                    changed=False,
                    reason="invalid_import_payload",
                )
            )
            continue

        existing_user_metadata = (
            config.model_metadata.user_metadata.model_dump(mode="json")
            if config.model_metadata
            else None
        )

        merged_payload = dict(imported_payload)
        if existing_user_metadata is not None:
            merged_payload["user_metadata"] = existing_user_metadata

        generated = ModelMetadata.model_validate(merged_payload)

        generated_payload = generated.model_dump(mode="json")
        current_payload = config.model_metadata.model_dump(mode="json") if config.model_metadata else None
        changed = generated_payload != current_payload
        reason = "imported" if changed else "already_up_to_date"
        backup_path: Path | None = None

        if apply and changed:
            backup_path = config.source_path.with_name(f"config.json.bak-model-metadata-import-{timestamp}")
            shutil.copy2(config.source_path, backup_path)
            config.model_metadata = generated
            save_config(config, config.source_path)

        records.append(
            ModelMetadataMigrationRecord(
                name=config.name,
                display_name=config.display_name or config.name,
                instance_uid=config.instance_uid,
                instance_no=config.instance_no,
                config_path=config.source_path,
                backup_path=backup_path,
                changed=changed,
                reason=reason,
            )
        )

    changed_count = sum(1 for record in records if record.changed)
    return ModelMetadataMigrationSummary(
        applied=apply,
        total=len(records),
        changed=changed_count,
        skipped=len(records) - changed_count,
        records=tuple(records),
    )