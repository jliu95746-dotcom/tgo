"""Version-one TCP screen scope. No window handle or program identity leaves the client."""

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class ScreenRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: int = Field(strict=True, ge=0, le=8191)
    y: int = Field(strict=True, ge=0, le=8191)
    width: int = Field(strict=True, ge=1, le=8192)
    height: int = Field(strict=True, ge=1, le=8192)


class ScreenGrantStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_version: int = Field(strict=True, ge=1, le=1)
    active: StrictBool
    grant_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    width: int = Field(default=0, strict=True, ge=0, le=8192)
    height: int = Field(default=0, strict=True, ge=0, le=8192)
    dpi: int = Field(default=0, strict=True, ge=0, le=960)
    allow_click: StrictBool = False
    allow_text: StrictBool = False
    click_regions: list[ScreenRegion] = Field(default_factory=list, max_length=16)
    max_actions: int = Field(default=0, strict=True, ge=0, le=50)
    used_actions: int = Field(default=0, strict=True, ge=0, le=50)

    @model_validator(mode="after")
    def require_consistent_scope(self) -> "ScreenGrantStatus":
        if not self.active:
            if any(
                (
                    self.grant_id,
                    self.width,
                    self.height,
                    self.dpi,
                    self.allow_click,
                    self.allow_text,
                    self.click_regions,
                    self.max_actions,
                    self.used_actions,
                )
            ):
                raise ValueError("Inactive grants must not advertise stale scope")
            return self
        if (
            not self.grant_id
            or self.width < 1
            or self.height < 1
            or self.dpi < 1
            or self.width * self.height > 4 * 1024 * 1024
            or self.max_actions <= self.used_actions
            or self.allow_click != bool(self.click_regions)
        ):
            raise ValueError("Invalid local screen grant")
        for region in self.click_regions:
            if (
                region.x + region.width > self.width
                or region.y + region.height > self.height
            ):
                raise ValueError("Local click region exceeds the client area")
        return self


class ScreenPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_version: int = Field(strict=True, ge=1, le=1)
    revision: int = Field(strict=True, ge=1, le=2147483647)
    grant_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    max_actions: int = Field(strict=True, ge=1, le=50)
    allow_click: StrictBool
    allow_text: StrictBool
    click_regions: list[ScreenRegion] = Field(default_factory=list, max_length=16)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_explicit_regions(self) -> "ScreenPolicy":
        if self.allow_click != bool(self.click_regions):
            raise ValueError("Click permission and finite regions must agree")
        return self


class ScreenPolicyAck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_version: int = Field(strict=True, ge=1, le=1)
    revision: int = Field(strict=True, ge=1, le=2147483647)
    grant_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
