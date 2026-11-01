"""Tool registry. Step 0: one global list. Step 7: per-tenant, driven by tenants/<id>/config.yaml."""

from langchain_core.tools import BaseTool

from app.tools.read_tools import get_my_attendance, get_my_batches, get_my_schedule, list_my_assessments

READ_TOOLS: list[BaseTool] = [get_my_batches, get_my_schedule, get_my_attendance, list_my_assessments]


def get_tools(tenant_id: str | None = None) -> list[BaseTool]:  # noqa: ARG001 (tenant filtering comes in step 7)
    return list(READ_TOOLS)
