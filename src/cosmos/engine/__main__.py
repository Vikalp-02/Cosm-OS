"""Command line entry point: `python -m cosmos.engine`."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from cosmos.engine import Finding, RootCause, investigate, list_tenants
from cosmos.warehouse import WAREHOUSE_FILE

FINDINGS_FILE = "findings.json"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cosmos.engine",
        description="Find revenue leaks in a built warehouse and explain them.",
    )
    parser.add_argument("--data", type=Path, default=Path("data"), help="data directory")
    parser.add_argument("--tenant", help="tenant to investigate; needed if there are several")
    args = parser.parse_args(argv)

    database = args.data / WAREHOUSE_FILE
    if not database.is_file():
        print(f"error: no warehouse at {database}; build it first", file=sys.stderr)
        return 1
    tenants = list_tenants(database)
    tenant = args.tenant or (tenants[0] if len(tenants) == 1 else None)
    if tenant is None or tenant not in tenants:
        print(f"error: choose a tenant with --tenant from {tenants}", file=sys.stderr)
        return 1

    findings = investigate(database, tenant)
    target = args.data / FINDINGS_FILE
    partial = target.with_suffix(".json.partial")
    partial.write_text(
        json.dumps([finding.to_json() for finding in findings], indent=2), encoding="utf-8"
    )
    partial.replace(target)

    for finding in findings:
        print(describe(finding))
    print(f"\n{len(findings)} findings for {tenant}. Written to {target.resolve()}")
    return 0


def describe(finding: Finding) -> str:
    where = finding.platform
    if finding.category:
        where += f", {finding.category}"
    where += ", all cities" if finding.cities is None else f", {', '.join(finding.cities)}"
    when = f"{finding.start_date} to {finding.end_date}"
    head = f"{finding.id}  {finding.cause.value:<21} {when}  {where}"

    if finding.cause is RootCause.DATA_GAP:
        cost = f"about {finding.unreported_units:,.0f} units of sales never reported"
    elif finding.cause is RootCause.UNEXPLAINED_DROP:
        cost = f"Rs {finding.unexplained_gmv or 0:,.0f} below what the drivers predict"
    else:
        cost = f"Rs {finding.cause_loss_gmv:,.0f} lost"
        if finding.cause_loss_gmv_sd:
            cost += f" (+/- {finding.cause_loss_gmv_sd:,.0f})"
        cost += f" across {len(finding.sku_ids)} products"
    return f"{head}\n         {cost}"


if __name__ == "__main__":
    sys.exit(main())
