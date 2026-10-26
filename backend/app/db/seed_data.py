"""Mock store data as pure Python (no DB, no dates). Dates are expressed as day offsets from an `as_of`
date so the demo never goes stale and tests can pin the date.

Two tenants on purpose:
  acme_store  - the real demo tenant (config from tenants/acme_store/config.yaml)
  other_store - a TEST FIXTURE that deliberately reuses acme's order id "A1001" and customer id
                "cust_alice", to prove one tenant can never read another's rows. Seeded only on request.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.tenants.loader import load_tenant_config
from app.tenants.schema import TenantConfig


@dataclass(frozen=True)
class SeedItem:
    sku: str
    name: str
    quantity: int
    unit_price: Decimal


@dataclass(frozen=True)
class SeedOrder:
    id: str
    customer_id: str
    status: str
    items: tuple[SeedItem, ...]
    placed_days_ago: int
    carrier: str | None = None
    tracking: str | None = None
    eta_in_days: int | None = None          # relative to as_of; negative = ETA already passed
    delivered_days_ago: int | None = None


@dataclass(frozen=True)
class SeedTenant:
    config: TenantConfig
    customers: tuple[tuple[str, str, str], ...]  # (id, name, email)
    orders: tuple[SeedOrder, ...]


def _i(sku: str, name: str, price: str, qty: int = 1) -> SeedItem:
    return SeedItem(sku, name, qty, Decimal(price))


def acme_store() -> SeedTenant:
    return SeedTenant(
        config=load_tenant_config("acme_store"),
        customers=(
            ("cust_alice", "Alice Nguyen", "alice@example.com"),
            ("cust_bob", "Bob Martin", "bob@example.com"),
            ("cust_carol", "Carol Diaz", "carol@example.com"),
            ("cust_dave", "Dave Okafor", "dave@example.com"),
            ("cust_erin", "Erin Walsh", "erin@example.com"),
        ),
        orders=(
            SeedOrder("A1001", "cust_alice", "shipped", (_i("MS-100", "Wireless Mouse", "29.99"),), 3,
                      carrier="UPS", tracking="1Z999AA10123456784", eta_in_days=3),
            SeedOrder("A1002", "cust_alice", "delivered", (_i("HB-220", "USB-C Hub", "49.99"),), 18,
                      carrier="UPS", tracking="1Z999AA10123456001", delivered_days_ago=14),
            SeedOrder("A1003", "cust_bob", "delivered", (_i("LS-310", "Laptop Stand", "39.99"),), 90,
                      carrier="FedEx", tracking="789012345001", delivered_days_ago=84),
            SeedOrder("A1004", "cust_alice", "processing",
                      (_i("PC-401", "Phone Case", "12.50", 2), _i("SP-402", "Screen Protector", "8.99")), 1),
            # Late: the ETA already passed (for "where is my order?!" tickets).
            SeedOrder("A1005", "cust_bob", "shipped", (_i("KB-510", "Mechanical Keyboard", "89.00"),), 9,
                      carrier="FedEx", tracking="789012345005", eta_in_days=-2),
            # Return-window boundary: exactly 30 days after delivery (eligible) and 31 days (not).
            SeedOrder("A1006", "cust_carol", "delivered", (_i("BS-610", "Bluetooth Speaker", "89.00"),), 35,
                      carrier="UPS", tracking="1Z999AA10123456006", delivered_days_ago=30),
            SeedOrder("A1007", "cust_carol", "delivered", (_i("DL-710", "Desk Lamp", "34.50"),), 36,
                      carrier="UPS", tracking="1Z999AA10123456007", delivered_days_ago=31),
            SeedOrder("A1008", "cust_dave", "cancelled", (_i("KB-811", "Compact Keyboard", "59.99"),), 6),
            SeedOrder("A1009", "cust_dave", "returned", (_i("WC-912", "HD Webcam", "74.00"),), 26,
                      carrier="DHL", tracking="JD0146000050", delivered_days_ago=20),
            # High-value order (for refund-above-approval-limit tickets).
            SeedOrder("A1010", "cust_erin", "delivered", (_i("LT-999", "Ultrabook Laptop", "1299.00"),), 8,
                      carrier="FedEx", tracking="789012345010", delivered_days_ago=5),
            SeedOrder("A1011", "cust_erin", "shipped",
                      (_i("CB-120", "USB-C Cable 2m", "9.99", 3), _i("CH-121", "65W Charger", "27.00")), 2,
                      carrier="UPS", tracking="1Z999AA10123456011", eta_in_days=2),
            SeedOrder("A1012", "cust_bob", "processing", (_i("MP-130", "Large Mouse Pad", "14.99"),), 0),
        ),
    )


def other_store() -> SeedTenant:
    return SeedTenant(
        config=TenantConfig(tenant_id="other_store", name="Other Store (test fixture)", currency="USD",
                            return_window_days=7),
        customers=(
            ("cust_alice", "Alicia Other", "alicia@other.example"),  # same customer id as acme's Alice
            ("cust_zed", "Zed Other", "zed@other.example"),
        ),
        orders=(
            SeedOrder("A1001", "cust_alice", "processing", (_i("SECRET-1", "SECRET OTHER-TENANT ITEM", "1.00"),), 1),
            SeedOrder("Z9001", "cust_zed", "delivered", (_i("GD-1", "Other Tenant Gadget", "50.00"),), 8,
                      carrier="DHL", tracking="JD0146009001", delivered_days_ago=4),
        ),
    )


def build_seed(include_isolation_fixture: bool = False) -> list[SeedTenant]:
    tenants = [acme_store()]
    if include_isolation_fixture:
        tenants.append(other_store())
    return tenants
