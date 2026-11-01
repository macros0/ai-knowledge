"""Public selected recipes; credentials/endpoints are supplied through runtime settings."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PINS = {
    "postgres": ("postgres-17-single-v1", "postgres:17@sha256:67f41722b7a8cbdb868a44a4995c846eddfdc2973bccb291ce937dce88ad5675", "PostgreSQL"),
    "qdrant": ("qdrant-1.19-single-v1", "qdrant/qdrant:v1.19.0@sha256:057ee3a8da769fe7310dd3537b4dc7583bf87a95ce8ac43c0af5a46bc580d1fc", "Apache-2.0"),
    "artifacts": ("rustfs-1.0.4-single-v1", "rustfs/rustfs:1.0.4@sha256:e287e99762a4a3e7b706526e01f2ff755f67db165e9be9f6b907adc6fb6d6ee1", "Apache-2.0"),
}


class StorageComponent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    provider: Literal["managed", "external"]
    recipe: str = Field(min_length=1, max_length=128)
    image: str | None = Field(default=None, max_length=255)
    license: str = Field(min_length=1, max_length=128)
    install_scope: Literal["namespace", "external"]
    disk_layout: str = Field(min_length=1, max_length=1024)
    failure_model: str = Field(min_length=1, max_length=1024)
    backup_restore_recipe: str = Field(min_length=1, max_length=128)
    maintenance_owner: str = Field(min_length=1, max_length=128)


class StorageSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1]
    installation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    namespace: str = Field(min_length=1, max_length=63, pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
    profile: Literal["basic"]
    artifact_backend: Literal["s3", "rwx"]
    postgres: StorageComponent
    qdrant: StorageComponent
    artifacts: StorageComponent

    @model_validator(mode="after")
    def selected_recipes(self):
        for name, (recipe, image, license_name) in PINS.items():
            component = getattr(self, name)
            if component.provider == "managed":
                if ((component.recipe, component.license, component.install_scope) != (recipe, license_name, "namespace")
                        or not component.image or component.image.rsplit("@", 1)[-1] != image.rsplit("@", 1)[-1]):
                    raise ValueError("Managed component differs from the selected recipe")
                if name == "artifacts" and self.artifact_backend != "s3":
                    raise ValueError("Managed RWX requires a separately approved platform recipe")
            elif component.image is not None or component.install_scope != "external":
                raise ValueError("External components must not claim managed images/install scope")
        return self

    def validate_runtime(self, settings):
        if (settings.deployment_namespace != self.namespace or settings.resilience_class != self.profile
                or settings.artifact_backend != self.artifact_backend
                or any(getattr(settings, field) != getattr(self, name).provider
                    for name, field in (("postgres", "postgres_provider"), ("qdrant", "qdrant_provider"),
                                        ("artifacts", "artifact_provider")))):
            raise ValueError("Storage selection differs from runtime ownership/namespace")
