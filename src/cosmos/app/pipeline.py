"""Run the engine for a tenant and store what it finds."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
from sqlalchemy.orm import Session

from cosmos.app.store import SyncResult, sync_leaks
from cosmos.engine import EngineConfig, Finding, RootCause, investigate
from cosmos.warehouse import WAREHOUSE_FILE


def refresh_leaks(
    data_dir: Path, tenant_id: str, db: Session, now: datetime, config: EngineConfig | None = None
) -> SyncResult:
    database = data_dir / WAREHOUSE_FILE
    findings = investigate(database, tenant_id, config)
    names = _product_names(database, tenant_id)
    return sync_leaks(db, tenant_id, [to_payload(finding, names) for finding in findings], now)


def to_payload(finding: Finding, names: dict[str, tuple[str, str]]) -> dict[str, Any]:
    """Everything the API needs to show a leak, so that it never has to query the warehouse."""
    has_cause = finding.cause not in (RootCause.DATA_GAP, RootCause.UNEXPLAINED_DROP)
    return {
        **finding.to_json(),
        "cause_loss_gmv": finding.cause_loss_gmv if has_cause else None,
        "cause_loss_gmv_sd": finding.cause_loss_gmv_sd,
        "products": [
            {
                "sku_id": sku,
                "name": names.get(sku, (sku, ""))[0],
                "brand": names.get(sku, ("", ""))[1],
            }
            for sku in finding.sku_ids
        ],
    }


def _product_names(database: Path, tenant_id: str) -> dict[str, tuple[str, str]]:
    conn = duckdb.connect(str(database), read_only=True)
    try:
        rows = conn.execute(
            "SELECT sku_id, product_name, brand FROM marts.dim_product WHERE tenant_id = ?",
            [tenant_id],
        ).fetchall()
    finally:
        conn.close()
    return {sku: (name, brand) for sku, name, brand in rows}
