"""Mock store schema (build step 1).

Tenant isolation is built into the keys: every table is keyed by (tenant_id, id) and every foreign key
INCLUDES tenant_id. So the database itself rejects an order that points at another tenant's customer;
isolation does not rely on every query remembering a WHERE clause.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKeyConstraint, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

ORDER_STATUSES = ("processing", "shipped", "delivered", "cancelled", "returned")


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (CheckConstraint("return_window_days > 0", name="return_window_positive"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(String(3))
    return_window_days: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320))


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"]),
        CheckConstraint("status IN ('processing','shipped','delivered','cancelled','returned')", name="status_valid"),
        # A delivery date exists exactly when the parcel was delivered (a returned order was delivered first).
        CheckConstraint(
            "(status IN ('delivered','returned') AND delivered_on IS NOT NULL)"
            " OR (status NOT IN ('delivered','returned') AND delivered_on IS NULL)",
            name="delivered_on_matches_status",
        ),
        CheckConstraint("total >= 0", name="total_non_negative"),
        Index("ix_orders_tenant_customer", "tenant_id", "customer_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    carrier: Mapped[str | None] = mapped_column(String(64))
    tracking: Mapped[str | None] = mapped_column(String(64))
    placed_on: Mapped[date] = mapped_column(Date)
    eta: Mapped[date | None] = mapped_column(Date)
    delivered_on: Mapped[date | None] = mapped_column(Date)
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2))  # money is never a float
    currency: Mapped[str] = mapped_column(String(3))

    items: Mapped[list["OrderItem"]] = relationship(order_by="OrderItem.line_no", viewonly=True)


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "order_id"], ["orders.tenant_id", "orders.id"]),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_non_negative"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
