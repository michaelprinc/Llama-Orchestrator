"""Hugging Face GGUF import helpers for the GUI."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

import httpx
from huggingface_hub import HfApi, hf_hub_url
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError
from keyring import get_password, set_password
from keyring.errors import KeyringError, PasswordDeleteError

from llama_orchestrator.config import get_models_dir, get_state_dir

if TYPE_CHECKING:
    from collections.abc import Callable

ExistingFileChoice = Literal["use_existing", "redownload", "cancel"]
DownloadAction = Literal["download", "use_existing", "cancel"]
LocalVariantStatus = Literal["not downloaded", "downloading", "downloaded"]

_HF_REPO_PATH_RE = re.compile(r"^[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*$")
_FILE_URL_SEGMENTS = {"blob", "resolve"}
_QUANTIZATION_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])(IQ[0-9]+(?:_[A-Z0-9]+)+)(?![A-Za-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])(Q[0-9]+(?:_[A-Z0-9]+)+)(?![A-Za-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])(Q[0-9]+_[0-9]+)(?![A-Za-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])(BF16|FP16|FP32|F16)(?![A-Za-z0-9])", re.IGNORECASE),
)
_MODEL_SIZE_RE = re.compile(r"(?<![A-Za-z0-9])(\d+(?:\.\d+)?)B(?![A-Za-z0-9])", re.IGNORECASE)
_SPLIT_GGUF_RE = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.IGNORECASE)
_DOWNLOAD_CHUNK_SIZE = 1024 * 1024
_GGUF_FILE_TYPE_LABELS = {
    0: "F32",
    1: "F16",
    2: "Q4_0",
    3: "Q4_1",
    6: "Q5_0",
    7: "Q5_1",
    8: "Q8_0",
    10: "Q2_K",
    11: "Q3_K_S",
    12: "Q3_K_M",
    13: "Q3_K_L",
    14: "Q4_K_S",
    15: "Q4_K_M",
    16: "Q5_K_S",
    17: "Q5_K_M",
    18: "Q6_K",
}

HF_TOKEN_SERVICE = "llama-orchestrator"
HF_TOKEN_USERNAME = "huggingface-read-token"


class HuggingFaceImportError(Exception):
    """User-facing import error."""


class DownloadCancelledError(HuggingFaceImportError):
    """Raised when the user cancels an in-flight download."""


@dataclass(frozen=True)
class HuggingFaceRepoRef:
    """Normalized Hugging Face repository reference."""

    repo_id: str
    filename: str | None = None


@dataclass(frozen=True)
class GGUFVariant:
    """One GGUF artifact exposed by a Hugging Face repo."""

    filename: str
    size_bytes: int | None
    quantization: str | None
    local_path: Path
    local_status: LocalVariantStatus
    note: str = ""

    @property
    def size_gb(self) -> float | None:
        if self.size_bytes is None:
            return None
        return self.size_bytes / (1024**3)


@dataclass(frozen=True)
class ImportSettings:
    """Persisted GUI preferences for Hugging Face imports."""

    local_models_directory: str


@dataclass(frozen=True)
class DownloadTargetPlan:
    """Resolved local-file action before a download starts."""

    action: DownloadAction
    final_path: Path
    temp_path: Path | None = None


@dataclass(frozen=True)
class DownloadProgress:
    """One UI progress update for a model download."""

    filename: str
    downloaded_bytes: int
    total_bytes: int | None


@dataclass(frozen=True)
class ImportedModelSelection:
    """Resolved local GGUF selection ready for Add Model autofill."""

    repo_id: str
    filename: str
    local_path: Path
    quantization: str | None
    size_bytes: int | None
    metadata_sidecar_path: Path | None = None
    validation_status: str | None = None
    validation_confidence: str | None = None
    validation_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelCardImportMetadata:
    """Lightweight claims extracted from a Hugging Face README/model card."""

    claimed_model: str | None = None
    claimed_context_length: int | None = None
    claimed_architecture: str | None = None
    claimed_chat_template: bool | None = None
    claimed_mtp_support: bool | None = None
    recommended_runtime: str | None = None


@dataclass(frozen=True)
class ImportValidationResult:
    """Import-time confidence and warnings for GGUF/model-card agreement."""

    status: Literal["ok", "warning", "error"]
    confidence: Literal["high", "medium", "low"]
    warnings: tuple[str, ...] = ()


def get_import_settings_path() -> Path:
    """Return the persisted Hugging Face import settings path."""

    return get_state_dir() / "huggingface_import.json"


def load_import_settings(default_models_dir: Path | None = None) -> ImportSettings:
    """Load persisted Hugging Face import settings."""

    fallback_dir = default_models_dir or get_models_dir()
    settings_path = get_import_settings_path()
    if not settings_path.exists():
        return ImportSettings(local_models_directory=str(fallback_dir))

    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ImportSettings(local_models_directory=str(fallback_dir))

    raw_directory = str(data.get("local_models_directory") or "").strip()
    return ImportSettings(
        local_models_directory=raw_directory or str(fallback_dir)
    )


def save_import_settings(settings: ImportSettings) -> Path:
    """Persist Hugging Face import settings."""

    settings_path = get_import_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {"local_models_directory": settings.local_models_directory},
            indent=2,
        ),
        encoding="utf-8",
    )
    return settings_path


class HuggingFaceTokenStore:
    """Store the Hugging Face read token in the system keyring when available."""

    def __init__(
        self,
        service_name: str = HF_TOKEN_SERVICE,
        username: str = HF_TOKEN_USERNAME,
    ) -> None:
        self.service_name = service_name
        self.username = username
        self._session_token: str | None = None

    def get_token(self) -> str | None:
        """Return a configured token from keyring or the current session."""

        try:
            stored = get_password(self.service_name, self.username)
        except Exception:
            stored = None
        return stored or self._session_token

    def is_configured(self) -> bool:
        """Return whether a token is currently available."""

        return bool(self.get_token())

    def save_token(self, token: str, validate: bool = True) -> None:
        """Validate and store a token securely when possible."""

        cleaned = token.strip()
        if not cleaned:
            raise HuggingFaceImportError("Hugging Face token is empty.")

        if validate:
            _validate_hf_token(cleaned)

        try:
            set_password(self.service_name, self.username, cleaned)
            self._session_token = None
        except KeyringError:
            self._session_token = cleaned

    def remove_token(self) -> None:
        """Remove any configured token."""

        self._session_token = None
        try:
            import keyring

            keyring.delete_password(self.service_name, self.username)
        except (KeyringError, PasswordDeleteError):
            return


def normalize_hf_model_reference(reference: str) -> HuggingFaceRepoRef:
    """Normalize a Hugging Face repo URL, file URL, or owner/repo ID."""

    raw_reference = reference.strip()
    if not raw_reference:
        raise HuggingFaceImportError("Model URL or ID is empty.")

    if raw_reference.startswith(("https://", "http://")):
        parsed = urlparse(raw_reference)
        if parsed.netloc.lower() not in {"huggingface.co", "www.huggingface.co"}:
            raise HuggingFaceImportError(
                "Only Hugging Face model URLs are supported."
            )
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if len(parts) < 2:
            raise HuggingFaceImportError("Invalid Hugging Face repository URL.")
        repo_id = f"{parts[0]}/{parts[1]}"
        if not _HF_REPO_PATH_RE.match(repo_id):
            raise HuggingFaceImportError("Invalid Hugging Face model ID.")
        filename = _extract_repo_filename(parts)
        return HuggingFaceRepoRef(repo_id=repo_id, filename=filename)

    if not _HF_REPO_PATH_RE.match(raw_reference):
        raise HuggingFaceImportError(
            "Use owner/repo or a https://huggingface.co/owner/repo URL."
        )
    return HuggingFaceRepoRef(repo_id=raw_reference)


def parse_gguf_quantization(filename: str) -> str | None:
    """Extract a likely quantization label from a GGUF filename."""

    name = Path(filename).name
    stem = name[:-5] if name.lower().endswith(".gguf") else name
    for pattern in _QUANTIZATION_PATTERNS:
        match = pattern.search(stem)
        if match:
            return match.group(1).upper()
    return None


def infer_model_size_tag(*values: str | None) -> str | None:
    """Infer a model-size tag like 8b from repo IDs or filenames."""

    for value in values:
        if not value:
            continue
        match = _MODEL_SIZE_RE.search(value)
        if match:
            return f"{match.group(1).lower()}b"
    return None


def split_gguf_note(filename: str) -> str:
    """Return a note when the GGUF looks like a split part."""

    match = _SPLIT_GGUF_RE.search(Path(filename).name)
    if not match:
        return ""
    return f"Split GGUF part {int(match.group(1))}/{int(match.group(2))}"


def list_gguf_variants(
    reference: str,
    local_models_dir: Path | None = None,
    token: str | None = None,
    api: HfApi | None = None,
) -> tuple[HuggingFaceRepoRef, list[GGUFVariant]]:
    """Load GGUF files from the target repository via Hugging Face Hub."""

    repo_ref = normalize_hf_model_reference(reference)
    models_dir = Path(local_models_dir) if local_models_dir is not None else get_models_dir()
    hub_api = api or HfApi(token=token)

    try:
        info = hub_api.model_info(repo_ref.repo_id, files_metadata=True, token=token)
    except RepositoryNotFoundError as exc:
        raise HuggingFaceImportError("Hugging Face repository does not exist.") from exc
    except GatedRepoError as exc:
        raise HuggingFaceImportError(
            "Repository is gated or private. Configure a valid Hugging Face read token."
        ) from exc
    except HfHubHTTPError as exc:
        raise _map_hf_http_error(exc) from exc
    except Exception as exc:
        raise HuggingFaceImportError(f"Failed to load Hugging Face variants: {exc}") from exc

    variants: list[GGUFVariant] = []
    for sibling in getattr(info, "siblings", []) or []:
        filename = getattr(sibling, "rfilename", None)
        if not filename or not filename.lower().endswith(".gguf"):
            continue
        local_path = resolve_local_variant_path(repo_ref.repo_id, filename, models_dir)
        variants.append(
            GGUFVariant(
                filename=filename,
                size_bytes=_extract_sibling_size(sibling),
                quantization=parse_gguf_quantization(filename),
                local_path=local_path,
                local_status="downloaded" if local_path.exists() else "not downloaded",
                note=split_gguf_note(filename),
            )
        )

    if not variants:
        raise HuggingFaceImportError("No GGUF files were found in this Hugging Face repository.")

    variants.sort(key=lambda variant: (0 if variant.filename == repo_ref.filename else 1, variant.filename.casefold()))
    return repo_ref, variants


def resolve_local_variant_path(repo_id: str, filename: str, local_models_dir: Path) -> Path:
    """Resolve the deterministic local path for a downloaded variant."""

    repo_slug = repo_id.replace("/", "__")
    repo_root = (Path(local_models_dir).expanduser().resolve() / repo_slug).resolve()
    relative_path = _normalize_repo_relative_path(filename)
    candidate = (repo_root / relative_path).resolve()
    try:
        candidate.relative_to(repo_root)
    except ValueError as exc:
        raise HuggingFaceImportError("Invalid GGUF filename returned by Hugging Face.") from exc
    return candidate


def plan_download_target(final_path: Path, existing_choice: ExistingFileChoice | None = None) -> DownloadTargetPlan:
    """Plan the local-file action before download or reuse."""

    if final_path.exists():
        if existing_choice == "use_existing":
            return DownloadTargetPlan(action="use_existing", final_path=final_path)
        if existing_choice == "redownload":
            return DownloadTargetPlan(
                action="download",
                final_path=final_path,
                temp_path=_build_temp_download_path(final_path),
            )
        if existing_choice == "cancel":
            return DownloadTargetPlan(action="cancel", final_path=final_path)
        raise HuggingFaceImportError("The target model file already exists.")

    return DownloadTargetPlan(
        action="download",
        final_path=final_path,
        temp_path=_build_temp_download_path(final_path),
    )


def ensure_disk_space(target_dir: Path, required_bytes: int) -> None:
    """Validate that the target disk has enough free space."""

    target_dir.mkdir(parents=True, exist_ok=True)
    free_bytes = shutil.disk_usage(target_dir).free
    if required_bytes > free_bytes:
        required_gb = required_bytes / (1024**3)
        free_gb = free_bytes / (1024**3)
        raise HuggingFaceImportError(
            f"Not enough free disk space. Need {required_gb:.2f} GB, have {free_gb:.2f} GB."
        )


def suggest_model_name(repo_id: str, filename: str, quantization: str | None) -> str:
    """Suggest the Add Model name from the repo and quantization."""

    repo_name = repo_id.rsplit("/", 1)[-1]
    base_name = re.sub(r"[-_]+", " ", repo_name).strip()
    if not base_name:
        base_name = Path(filename).stem
    return f"{base_name} {quantization}".strip()


def build_model_tags(repo_id: str, filename: str, quantization: str | None) -> list[str]:
    """Build default tags for an imported model."""

    owner, repo = repo_id.split("/", 1) if "/" in repo_id else (repo_id, "")
    repo_tag = f"hf_repo__{owner.lower()}__{repo.lower()}" if repo else "hf_repo"
    tags: list[str] = ["hf", repo_tag, "gguf"]
    if quantization:
        tags.append(quantization.lower())
    size_tag = infer_model_size_tag(repo_id, filename)
    if size_tag:
        tags.append(size_tag)

    deduped: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        key = tag.casefold()
        if key in seen:
            continue
        deduped.append(tag)
        seen.add(key)
    return deduped


def build_add_model_prefill(selection: ImportedModelSelection) -> tuple[str, str, list[str]]:
    """Build Add Model form values from a downloaded or reused GGUF."""

    return (
        suggest_model_name(selection.repo_id, selection.filename, selection.quantization),
        str(selection.local_path.resolve()),
        build_model_tags(selection.repo_id, selection.filename, selection.quantization),
    )


def get_import_metadata_sidecar_path(model_path: Path) -> Path:
    """Return the sidecar metadata path for a local GGUF artifact."""

    return model_path.with_name(f"{model_path.name}.metadata.json")


def extract_model_card_metadata(markdown: str) -> ModelCardImportMetadata:
    """Extract best-effort claims from a Hugging Face README/model card."""

    text = markdown.strip()
    if not text:
        return ModelCardImportMetadata()

    lower_text = text.lower()
    context_length = _extract_claimed_context_length(text)
    architecture = _extract_claimed_architecture(lower_text)
    claimed_model = _extract_model_card_title(text)
    if "no mtp" in lower_text or "without mtp" in lower_text:
        mtp_support: bool | None = False
    elif re.search(r"\b(mtp|nextn|next-n|speculative decoding)\b", lower_text):
        mtp_support = True
    else:
        mtp_support = None

    runtime = None
    for candidate in ("llama.cpp", "vllm", "transformers", "koboldcpp"):
        if candidate in lower_text:
            runtime = candidate
            break

    return ModelCardImportMetadata(
        claimed_model=claimed_model,
        claimed_context_length=context_length,
        claimed_architecture=architecture,
        claimed_chat_template=True if "chat template" in lower_text else None,
        claimed_mtp_support=mtp_support,
        recommended_runtime=runtime,
    )


def fetch_hf_model_card(
    repo_id: str,
    *,
    revision: str = "main",
    token: str | None = None,
) -> str | None:
    """Fetch the Hugging Face README/model card without persisting credentials."""

    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = hf_hub_url(repo_id=repo_id, filename="README.md", repo_type="model", revision=revision)
    try:
        response = httpx.get(url, headers=headers, follow_redirects=True, timeout=20)
    except httpx.RequestError:
        return None
    if response.status_code == 404:
        return None
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        return None
    return response.text


def build_import_validation(
    *,
    repo_id: str,
    filename: str,
    gguf_metadata: Any | None,
    model_card_metadata: ModelCardImportMetadata | None,
) -> ImportValidationResult:
    """Compare GGUF facts with filename, repo, and optional model-card claims."""

    warnings: list[str] = []
    if gguf_metadata is None:
        return ImportValidationResult(
            status="warning",
            confidence="low",
            warnings=("GGUF metadata could not be read from the local artifact.",),
        )

    architecture = getattr(gguf_metadata, "architecture", None)
    if not architecture:
        warnings.append("GGUF metadata is missing general.architecture.")
    if getattr(gguf_metadata, "context_length", None) is None:
        warnings.append("GGUF metadata is missing the architecture context length.")
    if not getattr(gguf_metadata, "chat_template", None):
        warnings.append("GGUF metadata does not include tokenizer.chat_template.")

    filename_quant = parse_gguf_quantization(filename)
    file_type = getattr(gguf_metadata, "file_type", None)
    file_type_label = _gguf_file_type_label(file_type)
    if filename_quant and file_type_label and not _quantization_labels_match(filename_quant, file_type_label):
        warnings.append(
            f"Filename quantization {filename_quant} does not match GGUF file_type {file_type_label}."
        )

    card = model_card_metadata or ModelCardImportMetadata()
    if (
        card.claimed_context_length
        and getattr(gguf_metadata, "context_length", None)
        and card.claimed_context_length != gguf_metadata.context_length
    ):
        warnings.append(
            "Model card context length "
            f"{card.claimed_context_length} does not match GGUF context length "
            f"{gguf_metadata.context_length}."
        )
    if (
        card.claimed_architecture
        and architecture
        and card.claimed_architecture.casefold() != architecture.casefold()
    ):
        warnings.append(
            f"Model card architecture {card.claimed_architecture} does not match GGUF architecture {architecture}."
        )
    nextn_layers = getattr(gguf_metadata, "nextn_predict_layers", None) or 0
    if card.claimed_mtp_support is True and nextn_layers <= 0:
        warnings.append("MTP/NextN is claimed in the model card but not present in GGUF metadata.")
    if card.claimed_mtp_support is False and nextn_layers > 0:
        warnings.append("GGUF metadata reports NextN layers although the model card says MTP is absent.")

    repo_and_file = f"{repo_id}/{filename}".casefold()
    if (
        architecture
        and architecture.casefold() not in repo_and_file
        and card.claimed_architecture is None
    ):
        warnings.append("GGUF architecture is not evident from the Hugging Face repo or filename.")

    status: Literal["ok", "warning", "error"] = "warning" if warnings else "ok"
    confidence: Literal["high", "medium", "low"] = "high" if not warnings else "medium"
    return ImportValidationResult(status=status, confidence=confidence, warnings=tuple(warnings))


def write_import_metadata_sidecar(
    selection: ImportedModelSelection,
    *,
    revision: str = "main",
    model_card_text: str | None = None,
    token: str | None = None,
) -> ImportedModelSelection:
    """Write additive HF/GGUF validation metadata next to a local GGUF file."""

    from llama_orchestrator.memory_fit import load_gguf_metadata

    gguf_metadata = load_gguf_metadata(selection.local_path)
    if model_card_text is None:
        model_card_text = fetch_hf_model_card(selection.repo_id, revision=revision, token=token)
    card_metadata = extract_model_card_metadata(model_card_text or "")
    validation = build_import_validation(
        repo_id=selection.repo_id,
        filename=selection.filename,
        gguf_metadata=gguf_metadata,
        model_card_metadata=card_metadata,
    )
    sidecar_path = get_import_metadata_sidecar_path(selection.local_path)
    payload = {
        "schema_version": "1",
        "source": {
            "provider": "huggingface",
            "repo_id": selection.repo_id,
            "filename": selection.filename,
            "revision": revision,
            "fetched_at": _utc_now_iso(),
        },
        "gguf_metadata": _serialize_gguf_import_metadata(gguf_metadata, selection.filename),
        "model_card_metadata": asdict(card_metadata),
        "validation": asdict(validation),
    }

    try:
        sidecar_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as exc:
        raise HuggingFaceImportError(f"Failed to write GGUF import metadata sidecar: {exc}") from exc

    return ImportedModelSelection(
        repo_id=selection.repo_id,
        filename=selection.filename,
        local_path=selection.local_path,
        quantization=selection.quantization,
        size_bytes=selection.size_bytes,
        metadata_sidecar_path=sidecar_path,
        validation_status=validation.status,
        validation_confidence=validation.confidence,
        validation_warnings=validation.warnings,
    )


def download_gguf_variant(
    repo_id: str,
    filename: str,
    local_models_dir: Path,
    *,
    token: str | None = None,
    size_bytes: int | None = None,
    existing_choice: ExistingFileChoice | None = None,
    progress_callback: Callable[[DownloadProgress], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> ImportedModelSelection:
    """Download one GGUF artifact into the persistent local models directory."""

    final_path = resolve_local_variant_path(repo_id, filename, local_models_dir)
    plan = plan_download_target(final_path, existing_choice=existing_choice)
    if plan.action == "cancel":
        raise DownloadCancelledError("Model download cancelled.")
    if plan.action == "use_existing":
        selection = ImportedModelSelection(
            repo_id=repo_id,
            filename=filename,
            local_path=final_path,
            quantization=parse_gguf_quantization(filename),
            size_bytes=final_path.stat().st_size,
        )
        return write_import_metadata_sidecar(selection, token=token)

    temp_path = plan.temp_path
    if temp_path is None:
        raise HuggingFaceImportError("Unable to allocate a temporary download path.")

    final_path.parent.mkdir(parents=True, exist_ok=True)
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = hf_hub_url(repo_id=repo_id, filename=filename, repo_type="model")
    downloaded = 0
    total_bytes = size_bytes

    try:
        with httpx.stream(
            "GET",
            url,
            headers=headers,
            follow_redirects=True,
            timeout=None,
        ) as response:
            response.raise_for_status()
            if total_bytes is None:
                total_header = response.headers.get("content-length")
                total_bytes = int(total_header) if total_header else None
            if total_bytes is None:
                raise HuggingFaceImportError(
                    "Unable to determine remote GGUF size for disk-space validation."
                )
            ensure_disk_space(final_path.parent, total_bytes)
            with temp_path.open("wb") as handle:
                for chunk in response.iter_bytes(chunk_size=_DOWNLOAD_CHUNK_SIZE):
                    if cancel_check and cancel_check():
                        raise DownloadCancelledError("Model download cancelled.")
                    if not chunk:
                        continue
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(
                            DownloadProgress(
                                filename=filename,
                                downloaded_bytes=downloaded,
                                total_bytes=total_bytes,
                            )
                        )
        os.replace(temp_path, final_path)
    except DownloadCancelledError:
        _cleanup_temp_file(temp_path)
        raise
    except httpx.HTTPStatusError as exc:
        _cleanup_temp_file(temp_path)
        raise _map_download_http_error(exc) from exc
    except httpx.RequestError as exc:
        _cleanup_temp_file(temp_path)
        raise HuggingFaceImportError(
            "Connection interrupted while downloading the GGUF model."
        ) from exc
    except OSError as exc:
        _cleanup_temp_file(temp_path)
        raise HuggingFaceImportError(
            f"Failed to write the GGUF model into the target directory: {exc}"
        ) from exc

    selection = ImportedModelSelection(
        repo_id=repo_id,
        filename=filename,
        local_path=final_path,
        quantization=parse_gguf_quantization(filename),
        size_bytes=total_bytes,
    )
    return write_import_metadata_sidecar(selection, token=token)


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _gguf_file_type_label(file_type: int | None) -> str | None:
    if file_type is None:
        return None
    return _GGUF_FILE_TYPE_LABELS.get(file_type)


def _quantization_labels_match(filename_quant: str, file_type_label: str) -> bool:
    normalized_filename = "F16" if filename_quant.upper() == "FP16" else filename_quant.upper()
    normalized_file_type = file_type_label.upper()
    return normalized_filename == normalized_file_type


def _serialize_gguf_import_metadata(gguf_metadata: Any | None, filename: str) -> dict[str, Any]:
    if gguf_metadata is None:
        return {}
    file_type = getattr(gguf_metadata, "file_type", None)
    return {
        "architecture": getattr(gguf_metadata, "architecture", None),
        "name": getattr(gguf_metadata, "general_name", None),
        "basename": getattr(gguf_metadata, "general_basename", None),
        "context_length": getattr(gguf_metadata, "context_length", None),
        "embedding_length": getattr(gguf_metadata, "embedding_length", None),
        "block_count": getattr(gguf_metadata, "block_count", None),
        "attention_head_count": getattr(gguf_metadata, "attention_head_count", None),
        "attention_head_count_kv": getattr(gguf_metadata, "attention_head_count_kv", None),
        "rope_freq_base": getattr(gguf_metadata, "rope_freq_base", None),
        "file_type": _gguf_file_type_label(file_type) or parse_gguf_quantization(filename),
        "file_type_value": file_type,
        "quantization_version": getattr(gguf_metadata, "quantization_version", None),
        "chat_template_present": bool(getattr(gguf_metadata, "chat_template", None)),
        "nextn_predict_layers": getattr(gguf_metadata, "nextn_predict_layers", None),
    }


def _extract_model_card_title(markdown: str) -> str | None:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            return title or None
    return None


def _extract_claimed_context_length(markdown: str) -> int | None:
    pattern = re.compile(
        r"(?:context|ctx|sequence(?:\s+length)?|context_length)[^\n]{0,80}?(\d+(?:\.\d+)?)\s*([kKmM])?",
        re.IGNORECASE,
    )
    for match in pattern.finditer(markdown):
        value = float(match.group(1))
        suffix = (match.group(2) or "").lower()
        if suffix == "m":
            value *= 1_000_000
        elif suffix == "k":
            value *= 1024
        parsed = int(value)
        if parsed >= 512:
            return parsed
    return None


def _extract_claimed_architecture(lower_text: str) -> str | None:
    for candidate in (
        "qwen3",
        "qwen2",
        "llama",
        "gemma",
        "deepseek2",
        "deepseek",
        "mistral",
        "mixtral",
        "phi4",
        "phi3",
    ):
        if re.search(rf"(?<![a-z0-9]){re.escape(candidate)}(?![a-z0-9])", lower_text):
            return candidate
    return None


def _extract_repo_filename(parts: list[str]) -> str | None:
    if len(parts) < 5:
        return None
    if parts[2] not in _FILE_URL_SEGMENTS:
        return None
    filename = "/".join(parts[4:]).strip()
    return filename or None


def _normalize_repo_relative_path(filename: str) -> Path:
    raw_path = PurePosixPath(filename)
    if raw_path.is_absolute() or not raw_path.parts:
        raise HuggingFaceImportError("Invalid GGUF filename returned by Hugging Face.")

    cleaned_parts: list[str] = []
    for part in raw_path.parts:
        if part in {"", "."}:
            continue
        if part == ".." or ":" in part:
            raise HuggingFaceImportError("Invalid GGUF filename returned by Hugging Face.")
        cleaned_parts.append(part)

    if not cleaned_parts:
        raise HuggingFaceImportError("Invalid GGUF filename returned by Hugging Face.")
    return Path(*cleaned_parts)


def _extract_sibling_size(sibling: object) -> int | None:
    size = getattr(sibling, "size", None)
    if isinstance(size, int):
        return size
    lfs = getattr(sibling, "lfs", None)
    if isinstance(lfs, dict):
        lfs_size = lfs.get("size")
        if isinstance(lfs_size, int):
            return lfs_size
    return None


def _build_temp_download_path(final_path: Path) -> Path:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=final_path.parent,
        prefix=f".{final_path.name}.",
        suffix=".part",
        delete=False,
    ) as handle:
        return Path(handle.name)


def _cleanup_temp_file(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


def _map_hf_http_error(exc: HfHubHTTPError) -> HuggingFaceImportError:
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    if status_code == 401:
        return HuggingFaceImportError("Hugging Face token is invalid.")
    if status_code == 403:
        return HuggingFaceImportError(
            "Repository is gated or private. Configure a valid Hugging Face read token."
        )
    if status_code == 404:
        return HuggingFaceImportError("Hugging Face repository does not exist.")
    return HuggingFaceImportError(f"Hugging Face request failed: {exc}")


def _map_download_http_error(exc: httpx.HTTPStatusError) -> HuggingFaceImportError:
    status_code = exc.response.status_code
    if status_code == 401:
        return HuggingFaceImportError("Hugging Face token is invalid.")
    if status_code == 403:
        return HuggingFaceImportError(
            "Repository is gated or private. Configure a valid Hugging Face read token."
        )
    if status_code == 404:
        return HuggingFaceImportError("The selected GGUF file was not found on Hugging Face.")
    return HuggingFaceImportError(f"Failed to download the GGUF model: HTTP {status_code}.")


def _validate_hf_token(token: str) -> None:
    try:
        HfApi(token=token).whoami(token=token)
    except HfHubHTTPError as exc:
        raise _map_hf_http_error(exc) from exc
    except Exception as exc:
        raise HuggingFaceImportError(f"Failed to validate Hugging Face token: {exc}") from exc
