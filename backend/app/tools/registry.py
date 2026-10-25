"""Tool registry. Step 0: one global list. Step 7: per-tenant, driven by tenants/<id>/config.yaml."""

from langchain_core.tools import BaseTool

from app.tools.read_tools import check_return_eligibility, get_order_status

READ_TOOLS: list[BaseTool] = [get_order_status, check_return_eligibility]


def get_tools(tenant_id: str | None = None) -> list[BaseTool]:  # noqa: ARG001 (tenant filtering comes in step 7)
    return list(READ_TOOLS)
