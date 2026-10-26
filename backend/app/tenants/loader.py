import re
from pathlib import Path

import yaml

from app.tenants.schema import TenantConfig

TENANTS_DIR = Path(__file__).resolve().parents[3] / "tenants"
_TENANT_ID = re.compile(r"[a-z][a-z0-9_]*")


def load_tenant_config(tenant_id: str, tenants_dir: Path = TENANTS_DIR) -> TenantConfig:
    # tenant_id becomes a directory name: validate its shape first so "../x" can never escape tenants/.
    if not _TENANT_ID.fullmatch(tenant_id):
        raise ValueError(f"Invalid tenant id: {tenant_id!r}")
    path = tenants_dir / tenant_id / "config.yaml"
    cfg = TenantConfig.model_validate(yaml.safe_load(path.read_text()))
    if cfg.tenant_id != tenant_id:
        raise ValueError(f"{path} declares tenant_id={cfg.tenant_id!r}, expected {tenant_id!r}")
    return cfg
