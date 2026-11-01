import pytest
from pydantic import ValidationError

from app.tenants.loader import TENANTS_DIR, load_tenant_config


def test_sample_tenant_config_loads_and_is_marked_as_sample():
    c = load_tenant_config("qspiders_sample")
    assert (c.tenant_id, c.timezone, c.attendance_threshold_pct) == ("qspiders_sample", "Asia/Kolkata", 75)
    assert c.is_sample is True  # until the institute supplies approved material, the demo says so


@pytest.mark.parametrize("bad", ["../etc", "qspiders_sample/../x", "Sample", "", "a b", "../../x"])
def test_tenant_id_cannot_escape_the_tenants_directory(bad):
    with pytest.raises(ValueError, match="Invalid tenant id"):
        load_tenant_config(bad)


def test_config_id_must_match_directory(tmp_path):
    (tmp_path / "inst_a").mkdir()
    (tmp_path / "inst_a" / "config.yaml").write_text("tenant_id: inst_b\nname: X\ntimezone: Asia/Kolkata\nattendance_threshold_pct: 75\n")
    with pytest.raises(ValueError, match="declares tenant_id"):
        load_tenant_config("inst_a", tenants_dir=tmp_path)


def test_unknown_and_invalid_fields_are_rejected(tmp_path):
    (tmp_path / "s").mkdir()
    f = tmp_path / "s" / "config.yaml"
    f.write_text("tenant_id: s\nname: X\ntimezone: Asia/Kolkata\nattendance_threshold_pct: 75\nsecret_backdoor: true\n")
    with pytest.raises(ValidationError):
        load_tenant_config("s", tenants_dir=tmp_path)
    f.write_text("tenant_id: s\nname: X\ntimezone: Asia/Kolkata\nattendance_threshold_pct: 0\n")
    with pytest.raises(ValidationError):
        load_tenant_config("s", tenants_dir=tmp_path)


def test_tenants_dir_points_at_the_repo_tenants_folder():
    assert (TENANTS_DIR / "qspiders_sample" / "config.yaml").is_file()
