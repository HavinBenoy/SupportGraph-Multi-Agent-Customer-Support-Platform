import pytest

from app.guardrails.validators import normalize_order_id
from app.llm.gateway import get_chat_model
from app.config import Settings
from app.llm.fake import ScriptedChatModel
from app.tools.read_tools import check_return_eligibility, get_order_status
from app.tools.scope import ScopeError


def test_order_status_for_own_order(alice_cfg):
    out = get_order_status.invoke({"order_id": "A1001"}, alice_cfg)
    assert out["status"] == "shipped"
    assert out["carrier"] == "UPS"
    assert out["total"] == "29.99"  # money comes back as an exact string, not a float


def test_order_id_is_normalized(alice_cfg):
    assert get_order_status.invoke({"order_id": " a1001 "}, alice_cfg)["order_id"] == "A1001"


@pytest.mark.parametrize("bad", ["", "hello", "A12", "AA1001", "A1001; DROP TABLE orders"])
def test_malformed_order_id_rejected(alice_cfg, bad):
    assert "Invalid order id" in get_order_status.invoke({"order_id": bad}, alice_cfg)["error"]


def test_normalize_order_id():
    assert normalize_order_id("b2002") == "B2002"
    assert normalize_order_id("nope") is None


def test_return_eligible_inside_window(alice_cfg):
    out = check_return_eligibility.invoke({"order_id": "A1002"}, alice_cfg)  # delivered 14 days before 'today'
    assert out["eligible"] is True


def test_return_not_eligible_when_not_delivered(alice_cfg):
    out = check_return_eligibility.invoke({"order_id": "A1001"}, alice_cfg)  # still shipped
    assert out["eligible"] is False
    assert "delivered" in out["reason"]


def test_return_not_eligible_after_window():
    cfg = {"configurable": {"tenant_id": "acme_store", "customer_id": "cust_bob", "today": "2026-10-24"}}
    out = check_return_eligibility.invoke({"order_id": "A1003"}, cfg)  # delivered 84 days ago
    assert out["eligible"] is False
    assert "30 days" in out["reason"]


def test_window_boundary_is_inclusive(alice_cfg):
    # A1002 delivered 2026-10-10; on 2026-11-09 it is exactly 30 days -> still eligible; 31 -> not.
    on_day_30 = {"configurable": {**alice_cfg["configurable"], "today": "2026-11-09"}}
    on_day_31 = {"configurable": {**alice_cfg["configurable"], "today": "2026-11-10"}}
    assert check_return_eligibility.invoke({"order_id": "A1002"}, on_day_30)["eligible"] is True
    assert check_return_eligibility.invoke({"order_id": "A1002"}, on_day_31)["eligible"] is False


def test_missing_scope_fails_closed():
    with pytest.raises(ScopeError):
        get_order_status.invoke({"order_id": "A1001"}, {"configurable": {}})


def test_gateway_returns_fake_model_by_default_provider():
    model = get_chat_model("action", Settings(llm_provider="fake"))
    assert isinstance(model, ScriptedChatModel)


# ---- build step 1: tools now read from the database ----

def test_list_my_orders_returns_only_own_orders_newest_first(alice_cfg):
    from app.tools.read_tools import list_my_orders

    out = list_my_orders.invoke({}, alice_cfg)
    assert [o["order_id"] for o in out["orders"]] == ["A1004", "A1001", "A1002"]  # placed 1, 3, 18 days ago
    assert out["orders"][0]["items"] == ["Phone Case x2", "Screen Protector"]
    assert out["orders"][0]["total"] == "33.99"  # 2 x 12.50 + 8.99


def test_list_my_orders_for_customer_with_no_orders():
    from app.tools.read_tools import list_my_orders
    from tests.conftest import scope_cfg

    assert list_my_orders.invoke({}, scope_cfg("acme_store", "cust_nobody")) == {"orders": []}


def test_return_window_boundary_day_30_eligible_day_31_not():
    from tests.conftest import scope_cfg

    carol = scope_cfg("acme_store", "cust_carol")
    assert check_return_eligibility.invoke({"order_id": "A1006"}, carol)["eligible"] is True  # delivered 30 days ago
    assert check_return_eligibility.invoke({"order_id": "A1007"}, carol)["eligible"] is False  # 31 days ago


def test_return_for_returned_and_cancelled_orders():
    from tests.conftest import scope_cfg

    dave = scope_cfg("acme_store", "cust_dave")
    assert "already been returned" in check_return_eligibility.invoke({"order_id": "A1009"}, dave)["reason"]
    assert "cancelled" in check_return_eligibility.invoke({"order_id": "A1008"}, dave)["reason"]


def test_return_window_comes_from_the_tenants_own_row():
    from tests.conftest import scope_cfg

    # Z9001 was delivered 4 days ago at other_store, whose window is 7 days (acme's is 30).
    out = check_return_eligibility.invoke({"order_id": "Z9001"}, scope_cfg("other_store", "cust_zed"))
    assert out["eligible"] is True
    assert "7-day window" in out["reason"] and "3 days left" in out["reason"]


def test_unknown_tenant_fails_closed_with_the_same_message():
    from tests.conftest import scope_cfg

    out = check_return_eligibility.invoke({"order_id": "A1002"}, scope_cfg("ghost_store", "cust_alice"))
    assert out == {"error": "Order not found for this account."}
