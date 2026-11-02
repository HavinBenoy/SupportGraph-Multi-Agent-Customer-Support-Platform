from pydantic import BaseModel, ConfigDict, Field


class TenantConfig(BaseModel):
    """Per-tenant settings stored in tenants/<id>/config.yaml. Grows in build step 7
    (tone, tool allow-list, escalation teams, taxonomy)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    timezone: str = Field(min_length=3, max_length=64)  # IANA name, e.g. Asia/Kolkata
    # The attendance figure the institute publishes in its student help docs. Tools compare against THIS number
    # (tenant data), never against a number the LLM supplies.
    attendance_threshold_pct: int = Field(ge=1, le=100)
    # True while the data is a labelled stand-in rather than the institute's approved material.
    is_sample: bool = True
    # The institute's own vocabulary, used to expand search queries (app/rag/rewrite.py): "marks" also searches "score".
    # Keys are lowercase single words. Tenant data, so a second tenant brings its own words with no code change.
    query_synonyms: dict[str, list[str]] = Field(default_factory=dict)
