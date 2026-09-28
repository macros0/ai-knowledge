"""Bounded diagnostics API DTOs; no raw log text or filesystem paths."""
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.diagnostics.schema import CaptureScope


class SessionStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: CaptureScope = "system"
    minutes: int = Field(default=15, ge=5, le=60)
    doc_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{16,32}$")

    @model_validator(mode="after")
    def validate_scope(self):
        if (self.scope == "document") != (self.doc_id is not None):
            raise ValueError("Document scope requires exactly one document")
        return self


class SessionOut(BaseModel):
    id: str
    status: Literal["starting", "active", "stopped"]
    scope: CaptureScope
    doc_id: str | None = None
    created_at: datetime
    expires_at: datetime
    stopped_at: datetime | None = None
    stop_reason: str | None = None
    bytes_written: int = 0
    audit_pending: bool = False


class BundleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: UUID | None = None
    from_utc: datetime | None = None
    to_utc: datetime | None = None
    request_id: UUID | None = None
    operation_id: UUID | None = None
    doc_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{16,32}$")

    @model_validator(mode="after")
    def validate_period(self):
        if self.session_id is not None:
            if self.from_utc is not None or self.to_utc is not None:
                raise ValueError("Select session or period")
            return self
        now = datetime.now(timezone.utc)
        self.to_utc = self.to_utc or now
        self.from_utc = self.from_utc or self.to_utc - timedelta(hours=1)
        for value in (self.from_utc, self.to_utc):
            if value.tzinfo is None or value.utcoffset().total_seconds() != 0:
                raise ValueError("Diagnostic interval must be UTC")
        if not timedelta(0) < self.to_utc - self.from_utc <= timedelta(days=7):
            raise ValueError("Diagnostic interval must be between zero and seven days")
        return self


class EventQuery(BundleRequest):
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)


class BundleOut(BaseModel):
    id: str
    status: str
    created_at: datetime
    finished_at: datetime | None = None
    expires_at: datetime | None = None
    size_bytes: int = 0
    sha256: str | None = None
    error_code: str | None = None
    counts: dict = Field(default_factory=dict)


class BrowserJoin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{22}$")


class BrowserLeave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    participation_id: str


class ClientEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    participation_id: str
    event_id: str
    event_code: Literal["browser_error"]
    error_code: Literal["browser_error"] = "browser_error"
    request_id: str | None = None
    route_template: str = "/unknown"
    build_id: str = "unknown"
    frames: list[dict] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_coordinates(self):
        from app.services.diagnostics.sanitize import sanitize_event
        raw = {"schema_version": 1, "boot_id": "00000000-0000-4000-8000-000000000000",
               "timestamp_utc": "2026-09-27T00:00:00+00:00", "component": "browser",
               "origin": "client_reported", "level": "ERROR", **self.model_dump(exclude_none=True, exclude={"participation_id"})}
        if sanitize_event(raw) is None:
            raise ValueError("Invalid browser coordinates")
        return self
