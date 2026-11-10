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
    # Different names for the SAME thing in this institute's documents ("machine learning" = "ml"). Each key lists the other names it goes by. Used by search (a question
    # that says "ML" also finds "machine learning") and by the AI change analysis (a passage that says "ML" is about machine learning). Tenant data, not code.
    entity_aliases: dict[str, list[str]] = Field(default_factory=dict)
    # Build step 4 (tenant data, not code, so a second institute brings its own rules):
    # A make-up class may be requested for a session held within this many days (never a future one).
    makeup_window_days: int = Field(default=14, ge=1, le=90)
    # Which human team receives each escalation category, and how fast each priority should be looked at (hours).
    # Unknown categories fall back to `default_team`. These are internal targets for staff, never promised to students.
    escalation_teams: dict[str, str] = Field(default_factory=dict)
    default_team: str = "student-support"
    sla_hours: dict[str, int] = Field(default_factory=lambda: {"low": 72, "medium": 48, "high": 24, "urgent": 4})
