"""Score the engine's findings against the incidents planted in a synthetic dataset."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import median

import duckdb

from cosmos.engine import Finding, RootCause

# A finding is the same event as a planted incident only if it covers at least
# this share of the incident's days.
MIN_WINDOW_OVERLAP = 0.5


@dataclass(frozen=True, slots=True)
class Planted:
    incident_id: str
    tenant_id: str
    cause: str
    platform: str
    city: str | None
    sku_ids: frozenset[str]
    start_date: date
    end_date: date
    expected_gmv_lost: float
    reported_units_dropped: int

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1


@dataclass(frozen=True, slots=True)
class Match:
    planted: Planted
    finding: Finding | None

    @property
    def found(self) -> bool:
        return self.finding is not None

    @property
    def diagnosed(self) -> bool:
        return self.finding is not None and self.finding.cause.value == self.planted.cause

    @property
    def truth(self) -> float:
        """What the incident cost: rupees lost, or for a data gap the units that went missing."""
        if self.planted.cause == RootCause.DATA_GAP.value:
            return float(self.planted.reported_units_dropped)
        return self.planted.expected_gmv_lost

    @property
    def estimate(self) -> float | None:
        if self.finding is None or not self.diagnosed:
            return None
        if self.finding.cause is RootCause.DATA_GAP:
            return self.finding.unreported_units
        return self.finding.cause_loss_gmv

    @property
    def estimate_sd(self) -> float | None:
        """The engine's own standard error on its estimate, where it gives one."""
        if self.finding is None or self.estimate is None:
            return None
        return self.finding.cause_loss_gmv_sd or None

    @property
    def error(self) -> float | None:
        """Relative error of the estimate. None unless the cause was diagnosed correctly."""
        if self.estimate is None or self.truth == 0:
            return None
        return (self.estimate - self.truth) / self.truth


@dataclass(frozen=True, slots=True)
class Scorecard:
    matches: tuple[Match, ...]
    false_alarms: tuple[Finding, ...]

    @property
    def planted(self) -> int:
        return len(self.matches)

    @property
    def found(self) -> int:
        return sum(match.found for match in self.matches)

    @property
    def diagnosed(self) -> int:
        return sum(match.diagnosed for match in self.matches)

    @property
    def findings(self) -> int:
        return self.found + len(self.false_alarms)

    def missed_below(self, floor: float) -> int:
        """Misses that cost less than `floor` rupees, which the engine leaves out on purpose."""
        return sum(
            not match.found
            and match.planted.cause != RootCause.DATA_GAP.value
            and match.truth < floor
            for match in self.matches
        )

    @property
    def errors(self) -> tuple[float, ...]:
        return tuple(match.error for match in self.matches if match.error is not None)

    @property
    def median_abs_error(self) -> float | None:
        return median(abs(error) for error in self.errors) if self.errors else None


def read_planted(data_dir: Path) -> list[Planted]:
    truth = (data_dir / "truth" / "planted_incident.parquet").as_posix()
    conn = duckdb.connect()
    try:
        rows = conn.execute(
            "SELECT incident_id, tenant_id, cause, platform, city, sku_ids, start_date, end_date,"
            f" expected_gmv_lost, reported_units_dropped FROM read_parquet('{truth}')"
            " ORDER BY incident_id"
        ).fetchall()
    finally:
        conn.close()
    return [
        Planted(
            incident_id=row[0],
            tenant_id=row[1],
            cause=row[2],
            platform=row[3],
            city=row[4],
            sku_ids=frozenset(row[5]),
            start_date=row[6],
            end_date=row[7],
            expected_gmv_lost=float(row[8]),
            reported_units_dropped=int(row[9]),
        )
        for row in rows
    ]


def score(planted: Sequence[Planted], findings: Sequence[Finding]) -> Scorecard:
    """Pair each planted incident with the finding that best covers it, each finding used once."""
    unused = list(findings)
    matches: list[Match] = []
    for incident in planted:
        candidates = [finding for finding in unused if _same_event(incident, finding)]
        best = max(candidates, key=lambda finding: _shared_days(incident, finding), default=None)
        if best is not None:
            unused.remove(best)
        matches.append(Match(incident, best))
    return Scorecard(tuple(matches), tuple(unused))


def _shared_days(incident: Planted, finding: Finding) -> int:
    first = max(incident.start_date, finding.start_date)
    last = min(incident.end_date, finding.end_date)
    return max(0, (last - first).days + 1)


def _same_event(incident: Planted, finding: Finding) -> bool:
    if incident.platform != finding.platform:
        return False
    if _shared_days(incident, finding) < MIN_WINDOW_OVERLAP * incident.days:
        return False
    is_gap = incident.cause == RootCause.DATA_GAP.value
    if is_gap or finding.cause is RootCause.DATA_GAP:
        return is_gap and finding.cause is RootCause.DATA_GAP
    same_place = incident.city is None or finding.cities is None or incident.city in finding.cities
    return same_place and not incident.sku_ids.isdisjoint(finding.sku_ids)
