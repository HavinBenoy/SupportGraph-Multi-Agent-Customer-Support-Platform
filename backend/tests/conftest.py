import pytest


@pytest.fixture
def alice_cfg():
    """Trusted scope for customer Alice at acme_store, with a frozen 'today' so tests are deterministic."""
    return {"configurable": {"tenant_id": "acme_store", "customer_id": "cust_alice", "today": "2026-10-24"}}


def scope_cfg(tenant: str, customer: str, today: str = "2026-10-24", **extra):
    return {"configurable": {"tenant_id": tenant, "customer_id": customer, "today": today}, **extra}
