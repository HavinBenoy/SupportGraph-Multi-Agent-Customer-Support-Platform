"""realign the schema from the retail mock store to a training institute (QSpiders / PySpiders)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-31 10:00:00.000000

What this does
- Drops the retail demo tables (customers, orders, order_items). They only ever held seeded demo rows.
- Deletes ONLY the two known retail demo tenants ('acme_store', 'other_store') and their ingested demo documents.
  Any other tenant row is left alone.
- tenants: replaces currency/return_window_days with timezone/attendance_threshold_pct/is_sample.
- documents/chunks: the allowed audience changes from 'customer' to 'public' (CHECK replaced, existing rows migrated).
- Creates students, courses, batches, enrollments, class_sessions, attendance_records, assessments,
  assessment_results. Every foreign key includes tenant_id.

Downgrade restores the OLD STRUCTURE only. The dropped demo rows are not recoverable (re-seed with the old code).
Run `make db-seed` after upgrading to load the institute sample tenant, then `make ingest` for the FAQ documents.
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

RETAIL_DEMO_TENANTS = ("acme_store", "other_store")


def _tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f(f"fk_{table}_tenant_id_tenants"))


def upgrade() -> None:
    bind = op.get_bind()
    demo = sa.bindparam("demo", expanding=True)
    for table in ("chunks", "documents"):
        bind.execute(sa.text(f"DELETE FROM {table} WHERE tenant_id IN :demo").bindparams(demo), {"demo": RETAIL_DEMO_TENANTS})

    op.drop_table("order_items")
    op.drop_table("orders")
    op.drop_table("customers")
    bind.execute(sa.text("DELETE FROM tenants WHERE id IN :demo").bindparams(demo), {"demo": RETAIL_DEMO_TENANTS})

    # tenants: drop dependent check with the column, add the new settings (defaults only to fill any leftover rows).
    op.drop_column("tenants", "return_window_days")
    op.drop_column("tenants", "currency")
    op.add_column("tenants", sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Kolkata"))
    op.add_column("tenants", sa.Column("attendance_threshold_pct", sa.Integer(), nullable=False, server_default="75"))
    op.add_column("tenants", sa.Column("is_sample", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.alter_column("tenants", "timezone", server_default=None)
    op.alter_column("tenants", "attendance_threshold_pct", server_default=None)
    op.create_check_constraint(op.f("ck_tenants_attendance_threshold_valid"), "tenants",
                               "attendance_threshold_pct BETWEEN 1 AND 100")

    # audience: 'customer' -> 'public'
    for table in ("documents", "chunks"):
        op.drop_constraint(op.f(f"ck_{table}_audience_customer_only"), table, type_="check")
        op.execute(f"UPDATE {table} SET audience = 'public'")
        op.create_check_constraint(op.f(f"ck_{table}_audience_public_only"), table, "audience = 'public'")

    op.create_table(
        "students",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        _tenant_fk("students"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name=op.f("pk_students")),
    )
    op.create_table(
        "courses",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("brand", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        _tenant_fk("courses"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name=op.f("pk_courses")),
    )
    op.create_table(
        "batches",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("course_id", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("centre", sa.String(length=100), nullable=True),
        sa.Column("timing", sa.String(length=100), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.CheckConstraint("mode IN ('online','offline')", name=op.f("ck_batches_mode_valid")),
        sa.CheckConstraint("(mode = 'offline' AND centre IS NOT NULL) OR (mode = 'online' AND centre IS NULL)",
                           name=op.f("ck_batches_centre_matches_mode")),
        sa.ForeignKeyConstraint(["tenant_id", "course_id"], ["courses.tenant_id", "courses.id"],
                                name=op.f("fk_batches_tenant_id_courses")),
        sa.PrimaryKeyConstraint("tenant_id", "id", name=op.f("pk_batches")),
    )
    op.create_table(
        "enrollments",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("student_id", sa.String(length=64), nullable=False),
        sa.Column("batch_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("enrolled_on", sa.Date(), nullable=False),
        sa.CheckConstraint("status IN ('active','completed','on_hold','withdrawn')", name=op.f("ck_enrollments_status_valid")),
        sa.ForeignKeyConstraint(["tenant_id", "student_id"], ["students.tenant_id", "students.id"],
                                name=op.f("fk_enrollments_tenant_id_students")),
        sa.ForeignKeyConstraint(["tenant_id", "batch_id"], ["batches.tenant_id", "batches.id"],
                                name=op.f("fk_enrollments_tenant_id_batches")),
        sa.PrimaryKeyConstraint("tenant_id", "id", name=op.f("pk_enrollments")),
    )
    op.create_index("ix_enrollments_tenant_student", "enrollments", ["tenant_id", "student_id"])
    op.create_table(
        "class_sessions",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("batch_id", sa.String(length=32), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("topic", sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "batch_id"], ["batches.tenant_id", "batches.id"],
                                name=op.f("fk_class_sessions_tenant_id_batches")),
        sa.PrimaryKeyConstraint("tenant_id", "batch_id", "session_date", name=op.f("pk_class_sessions")),
    )
    op.create_table(
        "attendance_records",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("enrollment_id", sa.String(length=32), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("present", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "enrollment_id"], ["enrollments.tenant_id", "enrollments.id"],
                                name=op.f("fk_attendance_records_tenant_id_enrollments")),
        sa.PrimaryKeyConstraint("tenant_id", "enrollment_id", "session_date", name=op.f("pk_attendance_records")),
    )
    op.create_index("ix_attendance_tenant_enrollment", "attendance_records", ["tenant_id", "enrollment_id"])
    op.create_table(
        "assessments",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("batch_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("max_score", sa.Integer(), nullable=False),
        sa.CheckConstraint("kind IN ('quiz','assignment','project','mock_interview')", name=op.f("ck_assessments_kind_valid")),
        sa.CheckConstraint("max_score > 0", name=op.f("ck_assessments_max_score_positive")),
        sa.ForeignKeyConstraint(["tenant_id", "batch_id"], ["batches.tenant_id", "batches.id"],
                                name=op.f("fk_assessments_tenant_id_batches")),
        sa.PrimaryKeyConstraint("tenant_id", "id", name=op.f("pk_assessments")),
    )
    op.create_table(
        "assessment_results",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("assessment_id", sa.String(length=32), nullable=False),
        sa.Column("enrollment_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.CheckConstraint("status IN ('submitted','graded','missing')", name=op.f("ck_assessment_results_status_valid")),
        sa.CheckConstraint("(status = 'graded' AND score IS NOT NULL) OR (status <> 'graded' AND score IS NULL)",
                           name=op.f("ck_assessment_results_score_matches_status")),
        sa.CheckConstraint("score IS NULL OR score >= 0", name=op.f("ck_assessment_results_score_non_negative")),
        sa.ForeignKeyConstraint(["tenant_id", "assessment_id"], ["assessments.tenant_id", "assessments.id"],
                                name=op.f("fk_assessment_results_tenant_id_assessments")),
        sa.ForeignKeyConstraint(["tenant_id", "enrollment_id"], ["enrollments.tenant_id", "enrollments.id"],
                                name=op.f("fk_assessment_results_tenant_id_enrollments")),
        sa.PrimaryKeyConstraint("tenant_id", "assessment_id", "enrollment_id", name=op.f("pk_assessment_results")),
    )


def downgrade() -> None:
    for table in ("assessment_results", "assessments", "attendance_records", "class_sessions", "enrollments",
                  "batches", "courses", "students"):
        op.drop_table(table)

    for table in ("documents", "chunks"):
        op.drop_constraint(op.f(f"ck_{table}_audience_public_only"), table, type_="check")
        op.execute(f"UPDATE {table} SET audience = 'customer'")
        op.create_check_constraint(op.f(f"ck_{table}_audience_customer_only"), table, "audience = 'customer'")

    op.drop_constraint(op.f("ck_tenants_attendance_threshold_valid"), "tenants", type_="check")
    for col in ("is_sample", "attendance_threshold_pct", "timezone"):
        op.drop_column("tenants", col)
    op.add_column("tenants", sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"))
    op.add_column("tenants", sa.Column("return_window_days", sa.Integer(), nullable=False, server_default="30"))
    op.alter_column("tenants", "currency", server_default=None)
    op.alter_column("tenants", "return_window_days", server_default=None)
    op.create_check_constraint(op.f("ck_tenants_return_window_positive"), "tenants", "return_window_days > 0")

    op.create_table(
        "customers",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_customers_tenant_id_tenants")),
        sa.PrimaryKeyConstraint("tenant_id", "id", name=op.f("pk_customers")),
    )
    op.create_table(
        "orders",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("customer_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("carrier", sa.String(length=64), nullable=True),
        sa.Column("tracking", sa.String(length=64), nullable=True),
        sa.Column("placed_on", sa.Date(), nullable=False),
        sa.Column("eta", sa.Date(), nullable=True),
        sa.Column("delivered_on", sa.Date(), nullable=True),
        sa.Column("total", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.CheckConstraint("status IN ('processing','shipped','delivered','cancelled','returned')", name=op.f("ck_orders_status_valid")),
        sa.CheckConstraint("(status IN ('delivered','returned') AND delivered_on IS NOT NULL) OR (status NOT IN ('delivered','returned') AND delivered_on IS NULL)", name=op.f("ck_orders_delivered_on_matches_status")),
        sa.CheckConstraint("total >= 0", name=op.f("ck_orders_total_non_negative")),
        sa.ForeignKeyConstraint(["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"], name=op.f("fk_orders_tenant_id_customers")),
        sa.PrimaryKeyConstraint("tenant_id", "id", name=op.f("pk_orders")),
    )
    op.create_index("ix_orders_tenant_customer", "orders", ["tenant_id", "customer_id"])
    op.create_table(
        "order_items",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.String(length=32), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(10, 2), nullable=False),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_order_items_quantity_positive")),
        sa.CheckConstraint("unit_price >= 0", name=op.f("ck_order_items_unit_price_non_negative")),
        sa.ForeignKeyConstraint(["tenant_id", "order_id"], ["orders.tenant_id", "orders.id"], name=op.f("fk_order_items_tenant_id_orders")),
        sa.PrimaryKeyConstraint("tenant_id", "order_id", "line_no", name=op.f("pk_order_items")),
    )
