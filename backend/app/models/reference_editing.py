from pydantic import BaseModel, ConfigDict, Field, StrictInt


class EditToken(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edit_token: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)


class EditSession(EditToken):
    lease_epoch: StrictInt = Field(ge=1)


class EditRenew(EditSession):
    activity_sequence: StrictInt = Field(ge=0, le=2**63 - 1)


class EditRevoke(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease_epoch: StrictInt = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)


class EditTarget(EditToken):
    resource_type: str
    resource_id: str
    kind: str | None = None
    org_id: StrictInt | None = Field(default=None, ge=1)
    lease_epoch: StrictInt | None = Field(default=None, ge=1)


class EditAcquireMany(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resources: list[EditTarget] = Field(min_length=1, max_length=2)
