from pydantic import BaseModel, ConfigDict, Field


class TenantConfig(BaseModel):
    """Per-tenant settings stored in tenants/<id>/config.yaml. Grows in build step 7
    (tone, tool allow-list, approval limits, taxonomy)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    return_window_days: int = Field(ge=1, le=365)
