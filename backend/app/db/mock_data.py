"""TEMPORARY in-memory mock store for build step 0.

Build step 1 replaces this with real Postgres tables + a seed script. Two tenants are
included on purpose: it lets us prove, from day one, that one tenant can never read
another tenant's rows -- even when order ids collide (both have an "A1001").

Tenant policy values (return window) will move to tenants/<tenant>/config.yaml in step 7.
"""

ORDERS: dict[str, dict[str, dict]] = {
    "acme_store": {
        "A1001": {
            "customer_id": "cust_alice", "status": "shipped", "carrier": "BlueDart",
            "tracking": "BD123456", "eta": "2026-10-27", "delivered_on": None,
            "items": ["Wireless Mouse"], "total": 1499.00, "currency": "USD",
        },
        "A1002": {
            "customer_id": "cust_alice", "status": "delivered", "carrier": "BlueDart",
            "tracking": "BD777001", "eta": None, "delivered_on": "2026-10-10",
            "items": ["USB-C Hub"], "total": 2999.00, "currency": "USD",
        },
        "A1003": {
            "customer_id": "cust_bob", "status": "delivered", "carrier": "DTDC",
            "tracking": "DT555002", "eta": None, "delivered_on": "2026-08-01",
            "items": ["Laptop Stand"], "total": 1899.00, "currency": "USD",
        },
    },
    "other_store": {
        # Same order id as acme's A1001 and the same customer id -- a deliberate collision.
        "A1001": {
            "customer_id": "cust_alice", "status": "processing", "carrier": None,
            "tracking": None, "eta": None, "delivered_on": None,
            "items": ["SECRET OTHER-TENANT ITEM"], "total": 1.00, "currency": "USD",
        },
        "Z9001": {
            "customer_id": "cust_zed", "status": "delivered", "carrier": "FedX",
            "tracking": "FX1", "eta": None, "delivered_on": "2026-10-20",
            "items": ["Other Tenant Gadget"], "total": 50.00, "currency": "USD",
        },
    },
}

RETURN_WINDOW_DAYS: dict[str, int] = {"acme_store": 30, "other_store": 7}
