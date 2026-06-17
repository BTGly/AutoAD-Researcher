"""Generic external asset plan and manifest contracts."""

from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Identifier = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
Sha256Hex = r"^[0-9a-f]{64}$"


class AssetSource(BaseModel):
    """Where an asset should be obtained from."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_type: Literal["url", "local_path", "registry", "manual"]
    uri: str = Field(min_length=1)
    description: str | None = None

    @model_validator(mode="after")
    def _validate_source(self):
        if self.source_type == "local_path":
            _validate_relative_path(self.uri)
        if self.source_type == "url" and not self.uri.startswith("https://"):
            raise ValueError("asset URL sources must use https")
        return self


class AssetValidation(BaseModel):
    """Offline validation requested for a prepared asset."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    validation_id: str = Field(pattern=Identifier)
    kind: Literal["sha256", "file_exists", "framework_load", "cli_inspect", "custom_probe"]
    parameters: dict[str, Any] = Field(default_factory=dict)
    required: bool = True
    network: Literal[False] = False


class AssetRequirement(BaseModel):
    """One required or optional external asset."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    asset_id: str = Field(pattern=Identifier)
    kind: Literal["model_weight", "checkpoint", "tokenizer", "index", "other"]
    source: AssetSource
    destination: str = Field(min_length=1)
    expected_sha256: str | None = Field(default=None, pattern=Sha256Hex)
    required: bool
    validation: list[AssetValidation] = Field(min_length=1)

    @field_validator("destination")
    @classmethod
    def _validate_destination(cls, value: str) -> str:
        _validate_relative_path(value)
        if PurePosixPath(value).parts[:2] != ("assets", "prepared"):
            raise ValueError("asset destination must be under assets/prepared")
        return value


class AssetPlan(BaseModel):
    """Plan for preparing external assets."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    plan_id: str = Field(pattern=Identifier)
    run_id: str = Field(pattern=Identifier)
    assets: list[AssetRequirement] = Field(min_length=1)
    network_during_prepare: bool
    network_during_execution: Literal[False]

    @model_validator(mode="after")
    def _validate_unique_ids(self):
        asset_ids = [asset.asset_id for asset in self.assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("duplicate asset_id")
        validation_ids = [
            validation.validation_id
            for asset in self.assets
            for validation in asset.validation
        ]
        if len(validation_ids) != len(set(validation_ids)):
            raise ValueError("duplicate asset validation_id")
        return self


class AssetManifestEntry(BaseModel):
    """Prepared asset evidence."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    asset_id: str = Field(pattern=Identifier)
    kind: str = Field(min_length=1)
    source: AssetSource
    path: str = Field(min_length=1)
    sha256: str | None = Field(default=None, pattern=Sha256Hex)
    required: bool
    status: Literal["prepared", "failed", "skipped"]
    failure_code: str | None = None
    failure_message: str | None = None

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return _validate_relative_path(value)


class AssetManifest(BaseModel):
    """Prepared asset manifest with stable hash."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    plan_id: str = Field(pattern=Identifier)
    run_id: str = Field(pattern=Identifier)
    assets: list[AssetManifestEntry]
    manifest_sha256: str = Field(pattern=Sha256Hex)

    @model_validator(mode="after")
    def _validate_manifest_entries(self):
        ids = [asset.asset_id for asset in self.assets]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate manifest asset_id")
        for asset in self.assets:
            if asset.status == "prepared" and asset.failure_code is not None:
                raise ValueError("prepared asset must not have failure_code")
            if asset.status == "failed" and not asset.failure_code:
                raise ValueError("failed asset requires failure_code")
        return self


def _validate_relative_path(value: str) -> str:
    if "\\" in value:
        raise ValueError(f"backslash forbidden in path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError(f"absolute path forbidden: {value!r}")
    if value in {"", "."}:
        raise ValueError("path must not be empty or '.'")
    if any(part == ".." for part in path.parts):
        raise ValueError(f"parent traversal forbidden: {value!r}")
    return value
