"""Read-only tools. They run freely: they cannot change anything.

Docstrings below are shown to the LLM as the tool description, so they are written for it.
"""

from datetime import date

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.db.mock_data import ORDERS, RETURN_WINDOW_DAYS
from app.guardrails.validators import normalize_order_id
from app.tools.scope import Scope, require_scope

# One message for "doesn't exist", "other customer's", and "other tenant's".
# Different messages would let an attacker probe which order ids exist elsewhere.
_NOT_FOUND = {"error": "Order not found for this account."}


def _find_order(order_id: str, scope: Scope) -> tuple[str, dict] | dict:
    """Return (canonical_id, order) if it exists AND belongs to this tenant+customer, else an error dict."""
    canonical = normalize_order_id(order_id)
    if canonical is None:
        return {"error": "Invalid order id. Order ids look like A1001."}
    order = ORDERS.get(scope.tenant_id, {}).get(canonical)  # tenant scoping happens here
    if order is None or order["customer_id"] != scope.customer_id:  # customer scoping
        return _NOT_FOUND
    return canonical, order


@tool
def get_order_status(order_id: str, config: RunnableConfig) -> dict:
    """Look up the status of one of the current customer's orders by order id (e.g. A1001).
    Returns status, carrier, tracking number, estimated delivery, items and total."""
    scope = require_scope(config)
    found = _find_order(order_id, scope)
    if isinstance(found, dict):
        return found
    canonical, o = found
    return {
        "order_id": canonical, "status": o["status"], "carrier": o["carrier"],
        "tracking": o["tracking"], "eta": o["eta"], "items": o["items"],
        "total": o["total"], "currency": o["currency"],
    }


@tool
def check_return_eligibility(order_id: str, config: RunnableConfig) -> dict:
    """Check whether one of the current customer's orders can still be returned, by order id.
    This only reports eligibility; it does not start a return or issue a refund."""
    scope = require_scope(config)
    found = _find_order(order_id, scope)
    if isinstance(found, dict):
        return found
    canonical, o = found

    window = RETURN_WINDOW_DAYS[scope.tenant_id]
    if o["status"] != "delivered" or not o["delivered_on"]:
        return {"order_id": canonical, "eligible": False,
                "reason": f"Order is '{o['status']}'; returns open once it has been delivered."}

    days_since = (scope.today - date.fromisoformat(o["delivered_on"])).days
    if days_since > window:
        return {"order_id": canonical, "eligible": False,
                "reason": f"Delivered {days_since} days ago; the return window is {window} days."}
    return {"order_id": canonical, "eligible": True,
            "reason": f"Eligible: delivered {days_since} days ago, {window - days_since} days left in the {window}-day window."}
