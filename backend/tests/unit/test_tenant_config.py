import pytest
from pydantic import ValidationError

from app.tenants.loader import TENANTS_DIR, load_tenant_config


def test_acme_config_loads():
    c = load_tenant_config("acme_store")
    assert (c.tenant_id, c.currency, c.return_window_days) == ("acme_store", "USD", 30)


@pytest.mark.parametrize("bad", ["../etc", "acme_store/../x", "Acme", "", "a b", "../../x"])
def test_tenant_id_cannot_escape_the_tenants_directory(bad):
    with pytest.raises(ValueError, match="Invalid tenant id"):
        load_tenant_config(bad)


def test_config_id_must_match_directory(tmp_path):
    (tmp_path / "shop_a").mkdir()
    (tmp_path / "shop_a" / "config.yaml").write_text("tenant_id: shop_b\nname: X\ncurrency: USD\nreturn_window_days: 5\n")
    with pytest.raises(ValueError, match="declares tenant_id"):
        load_tenant_config("shop_a", tenants_dir=tmp_path)


def test_unknown_and_invalid_fields_are_rejected(tmp_path):
    (tmp_path / "s").mkdir()
    f = tmp_path / "s" / "config.yaml"
    f.write_text("tenant_id: s\nname: X\ncurrency: USD\nreturn_window_days: 5\nsecret_backdoor: true\n")
    with pytest.raises(ValidationError):
        load_tenant_config("s", tenants_dir=tmp_path)
    f.write_text("tenant_id: s\nname: X\ncurrency: usd\nreturn_window_days: 0\n")
    with pytest.raises(ValidationError):
        load_tenant_config("s", tenants_dir=tmp_path)


def test_tenants_dir_points_at_the_repo_tenants_folder():
    assert (TENANTS_DIR / "acme_store" / "config.yaml").is_file()
