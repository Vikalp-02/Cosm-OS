"""A leak as plain text, for a language model to read.

Every figure is written out the way it should appear to a reader. The model is
told to copy figures exactly, and what it writes is then checked against this
text, so anything worth saying about a leak has to be said here first.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from cosmos.app.narrative import (
    CAUSE_LABELS,
    DRIVER_LABELS,
    indian_grouping,
    narrate,
    platform_label,
    readings,
    shown,
)

# An unexplained figure within this many standard deviations of ordinary variation is chance.
NOISE_BAND = 2.0


def rupees(value: float) -> str:
    return f"₹{indian_grouping(abs(value))}"


def long_day(day: date) -> str:
    return f"{day.day} {day:%b %Y}"


def leak_facts(reference: str, payload: dict[str, Any]) -> str:
    story = narrate(payload)
    start = date.fromisoformat(payload["start_date"])
    end = date.fromisoformat(payload["end_date"])
    days = (end - start).days + 1
    place = "all cities" if payload["cities"] is None else ", ".join(payload["cities"])
    where = [platform_label(payload["platform"]), payload["category"], place]

    lines = [
        f"[{reference}] {CAUSE_LABELS.get(payload['cause'], payload['cause'])}",
        f"Where: {', '.join(part for part in where if part)}",
        f"When: {long_day(start)} to {long_day(end)} ({days} {'day' if days == 1 else 'days'})",
        f"Products affected: {len(payload['sku_ids'])}",
        f"In short: {story.headline}",
        f"What happened: {story.what_happened}",
        f"Why: {story.why}",
        f"Cost: {_cost(payload)}",
    ]
    if payload["actual_gmv"] is not None:
        lines.append(
            f"Expected sales: {rupees(payload['expected_gmv'])}."
            f" Actual sales: {rupees(payload['actual_gmv'])}."
        )
    if payload["unexplained_gmv"] is not None and payload["cause_loss_gmv"] is not None:
        lines.append(f"Left unexplained: {_unexplained(payload)}")

    evidence = [
        f"{item.label}: "
        + (
            f"{shown(item.before, item.unit)} before, {shown(item.during, item.unit)} during"
            if item.before is not None
            else shown(item.during, item.unit)
        )
        for item in readings(payload["evidence"])
    ]
    if evidence:
        lines.append("Readings: " + "; ".join(evidence) + ".")

    drivers = [
        f"{DRIVER_LABELS.get(name, name)} {'cost' if lost >= 0 else 'added'} {rupees(lost)}"
        for name, lost in sorted(payload["loss_gmv"].items(), key=lambda item: -item[1])
    ]
    if drivers:
        lines.append("By driver: " + "; ".join(drivers) + ".")
    return "\n".join(lines)


def _cost(payload: dict[str, Any]) -> str:
    if payload["cause"] == "data_gap":
        return (
            f"no sales were lost. About {payload['unreported_units']:,.0f} units of sales,"
            f" roughly {rupees(payload['unreported_gmv'])}, were never reported;"
            " they were most likely sold."
        )
    lost = payload["cause_loss_gmv"]
    if lost is None:
        return (
            f"sales came in {rupees(payload['unexplained_gmv'] or 0.0)} below what the"
            " measured drivers predict. No single cause accounts for it."
        )
    spread = payload["cause_loss_gmv_sd"]
    if spread > 0:
        return (
            f"{rupees(lost)} of sales lost to this cause. This is an estimate and could be"
            f" off by about {rupees(spread)} either way."
        )
    return f"{rupees(lost)} of sales lost to this cause, worked out directly with no estimate."


def _unexplained(payload: dict[str, Any]) -> str:
    amount = payload["unexplained_gmv"]
    within_noise = abs(payload["unexplained_units"]) <= NOISE_BAND * payload["noise_units"]
    direction = "less" if amount >= 0 else "more"
    verdict = (
        "which is within normal day-to-day variation"
        if within_noise
        else "which is more than chance would explain"
    )
    return f"{rupees(amount)} {direction} was sold than the drivers predict, {verdict}."
