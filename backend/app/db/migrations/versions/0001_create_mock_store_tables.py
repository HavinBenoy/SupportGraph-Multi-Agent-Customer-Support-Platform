"""create mock store tables

Revision ID: 0001
Revises: 
Create Date: 2026-10-25 16:01:49.823968
"""
from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('tenants',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('return_window_days', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('return_window_days > 0', name=op.f('ck_tenants_return_window_positive')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tenants'))
    )
    op.create_table('customers',
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_customers_tenant_id_tenants')),
    sa.PrimaryKeyConstraint('tenant_id', 'id', name=op.f('pk_customers'))
    )
    op.create_table('orders',
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('customer_id', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('carrier', sa.String(length=64), nullable=True),
    sa.Column('tracking', sa.String(length=64), nullable=True),
    sa.Column('placed_on', sa.Date(), nullable=False),
    sa.Column('eta', sa.Date(), nullable=True),
    sa.Column('delivered_on', sa.Date(), nullable=True),
    sa.Column('total', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.CheckConstraint("(status IN ('delivered','returned') AND delivered_on IS NOT NULL) OR (status NOT IN ('delivered','returned') AND delivered_on IS NULL)", name=op.f('ck_orders_delivered_on_matches_status')),
    sa.CheckConstraint("status IN ('processing','shipped','delivered','cancelled','returned')", name=op.f('ck_orders_status_valid')),
    sa.CheckConstraint('total >= 0', name=op.f('ck_orders_total_non_negative')),
    sa.ForeignKeyConstraint(['tenant_id', 'customer_id'], ['customers.tenant_id', 'customers.id'], name=op.f('fk_orders_tenant_id_customers')),
    sa.PrimaryKeyConstraint('tenant_id', 'id', name=op.f('pk_orders'))
    )
    op.create_index('ix_orders_tenant_customer', 'orders', ['tenant_id', 'customer_id'], unique=False)
    op.create_table('order_items',
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('order_id', sa.String(length=32), nullable=False),
    sa.Column('line_no', sa.Integer(), nullable=False),
    sa.Column('sku', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('unit_price', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.CheckConstraint('quantity > 0', name=op.f('ck_order_items_quantity_positive')),
    sa.CheckConstraint('unit_price >= 0', name=op.f('ck_order_items_unit_price_non_negative')),
    sa.ForeignKeyConstraint(['tenant_id', 'order_id'], ['orders.tenant_id', 'orders.id'], name=op.f('fk_order_items_tenant_id_orders')),
    sa.PrimaryKeyConstraint('tenant_id', 'order_id', 'line_no', name=op.f('pk_order_items'))
    )


def downgrade() -> None:
    op.drop_table('order_items')
    op.drop_index('ix_orders_tenant_customer', table_name='orders')
    op.drop_table('orders')
    op.drop_table('customers')
    op.drop_table('tenants')
