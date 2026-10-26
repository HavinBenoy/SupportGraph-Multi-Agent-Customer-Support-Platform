"""Read-only tools. They run freely: they cannot change anything.

Docstrings below are shown to the LLM as the tool description, so they are written for it.
All data access goes through app.db.repo, which requires a trusted Scope (tenant + customer).
"""

from datetime import date

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.db import repo
from app.db.models import Order
from app.db.session import session_scope
from app.guardrails.validators import normalize_order_id
from app.tools.scope import require_scope

# One message for "doesn't exist", "other customer's", and "other tenant's".
# Different messages would let an attacker probe which order ids exist elsewhere.
_NOT_FOUND = {"error": "Order not found for this account."}


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _item_names(o: Order) -> list[str]:
    return [i.name if i.quantity == 1 else f"{i.name} x{i.quantity}" for i in o.items]


@tool
def get_order_status(order_id: str, config: RunnableConfig) -> dict:
    """Look up the status of one of the current customer's orders by order id (e.g. A1001).
    Returns status, carrier, tracking number, estimated delivery, items and total."""
    scope = require_scope(config)
    canonical = normalize_order_id(order_id)
    if canonical is None:
        return {"error": "Invalid order id. Order ids look like A1001."}
    with session_scope() as db:
        o = repo.get_customer_order(db, scope, canonical)  # tenant + customer scoping happens in the query
        if o is None:
            return _NOT_FOUND
        return {
            "order_id": o.id, "status": o.status, "carrier": o.carrier, "tracking": o.tracking,
            "placed_on": _iso(o.placed_on), "eta": _iso(o.eta), "items": _item_names(o),
            "total": f"{o.total:.2f}", "currency": o.currency,
        }


@tool
def list_my_orders(config: RunnableConfig) -> dict:
    """List the current customer's most recent orders (up to 5) with status and total.
    Use this when the customer has not given an order id."""
    scope = require_scope(config)
    with session_scope() as db:
        orders = repo.list_customer_orders(db, scope, limit=5)
        return {"orders": [
            {"order_id": o.id, "status": o.status, "placed_on": _iso(o.placed_on),
             "items": _item_names(o), "total": f"{o.total:.2f}", "currency": o.currency}
            for o in orders
        ]}


@tool
def check_return_eligibility(order_id: str, config: RunnableConfig) -> dict:
    """Check whether one of the current customer's orders can still be returned, by order id.
    This only reports eligibility; it does not start a return or issue a refund."""
    scope = require_scope(config)
    canonical = normalize_order_id(order_id)
    if canonical is None:
        return {"error": "Invalid order id. Order ids look like A1001."}
    with session_scope() as db:
        o = repo.get_customer_order(db, scope, canonical)
        tenant = repo.get_tenant(db, scope)
        if o is None or tenant is None:  # unknown tenant fails closed with the same message
            return _NOT_FOUND

        if o.status == "returned":
            return {"order_id": o.id, "eligible": False, "reason": "This order has already been returned."}
        if o.status == "cancelled":
            return {"order_id": o.id, "eligible": False, "reason": "This order was cancelled, so there is nothing to return."}
        if o.status != "delivered" or o.delivered_on is None:
            return {"order_id": o.id, "eligible": False,
                    "reason": f"Order is '{o.status}'; returns open once it has been delivered."}

        window = tenant.return_window_days  # the policy number comes from tenant data, never from the LLM
        days_since = (scope.today - o.delivered_on).days
        if days_since > window:
            return {"order_id": o.id, "eligible": False,
                    "reason": f"Delivered {days_since} days ago; the return window is {window} days."}
        return {"order_id": o.id, "eligible": True,
                "reason": f"Eligible: delivered {days_since} days ago, {window - days_since} days left in the {window}-day window."}
