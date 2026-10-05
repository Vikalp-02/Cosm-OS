"""Detection and attribution: find revenue leaks in the warehouse and explain them."""

from __future__ import annotations

from pathlib import Path

import duckdb

from cosmos.engine.attribution import DRIVERS, compute_impact
from cosmos.engine.config import EngineConfig, Prior
from cosmos.engine.detect import detect
from cosmos.engine.findings import DailyPoint, Finding, RootCause
from cosmos.engine.panel import load_panel

__all__ = [
    "DRIVERS",
    "DailyPoint",
    "EngineConfig",
    "Finding",
    "Prior",
    "RootCause",
    "investigate",
    "list_tenants",
]


def list_tenants(database: Path) -> list[str]:
    conn = duckdb.connect(str(database), read_only=True)
    try:
        rows = conn.execute(
            "SELECT DISTINCT tenant_id FROM marts.dim_product ORDER BY tenant_id"
        ).fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows]


def investigate(
    database: Path, tenant_id: str, config: EngineConfig | None = None
) -> list[Finding]:
    """Read one tenant's history from the warehouse and return its revenue leaks, oldest first."""
    config = config or EngineConfig()
    conn = duckdb.connect(str(database), read_only=True)
    try:
        panel = load_panel(conn, tenant_id)
    finally:
        conn.close()
    return detect(panel, compute_impact(panel, config), config)
