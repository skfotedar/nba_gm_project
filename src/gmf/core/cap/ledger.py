"""Cap ledger: exact arithmetic on team payroll against CBA thresholds.

DETERMINISTIC PATH. Standard library only. This module must never import an
LLM client, an HTTP client, a database driver, or anything from ``gmf.agents``,
``gmf.graph``, or ``gmf.retrieval``. Enforced by
``tests/architecture/test_core_purity.py``.

Every function here is pure: same inputs, same outputs, no I/O, no clock, no
global state, no mutation of arguments.

THE CENTRAL FACT
    One roster produces three different payroll totals, each compared against
    a different threshold:

        team salary   -> salary cap        (salaries + cap holds + cap charges)
        tax salary    -> luxury tax line   (locked at end of regular season)
        apron salary  -> first/second apron (team salary, minus some cap holds,
                                             plus unlikely bonuses and similar)

    Collapsing these into one number is the most expensive available mistake
    in this layer. See configs/cba/nba_2023.yaml.

ON APPROXIMATION
    Until the ingest layer supplies cap holds and bonus detail, tax salary and
    apron salary cannot be computed exactly. Rather than silently returning a
    wrong number, every CapPosition carries ``approximations`` naming which of
    its measures are estimates. Callers -- especially the rules engine -- are
    expected to check it before asserting legality.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from gmf.core.types import ZERO, Contract, Money, Roster, to_money

__all__ = [
    "ApronTier",
    "Boundary",
    "CapPosition",
    "PayrollAdjustments",
    "Thresholds",
    "UnsourcedConstantError",
    "apron_salary",
    "cap_position",
    "classify_tier",
    "tax_salary",
    "team_salary",
]


class UnsourcedConstantError(ValueError):
    """A CBA constant needed for this calculation is null in config.

    Raised rather than defaulted. A missing threshold means the answer is
    unknown, and an unknown answer must not look like a computed one.
    """


class Boundary(StrEnum):
    """Whether a team sitting exactly ON a threshold is over it.

    OPEN ITEM. The CBA text decides this, not us. Until it is resolved and
    recorded in docs/design/, ``ABOVE`` is the working assumption and the
    golden test for the exact-boundary case is expected to fail.
    """

    ABOVE = "above"          # strictly greater than the line
    AT_OR_ABOVE = "at_or_above"


class ApronTier(StrEnum):
    """Which spending band a team occupies. Ordered least to most restricted."""

    UNDER_CAP = "under_cap"
    OVER_CAP = "over_cap"              # above cap, at or below tax line
    TAX_PAYER = "tax_payer"            # above tax, at or below first apron
    FIRST_APRON = "first_apron"        # above first apron, at or below second
    SECOND_APRON = "second_apron"      # above second apron

    @property
    def rank(self) -> int:
        return _TIER_ORDER.index(self)


_TIER_ORDER = [
    ApronTier.UNDER_CAP,
    ApronTier.OVER_CAP,
    ApronTier.TAX_PAYER,
    ApronTier.FIRST_APRON,
    ApronTier.SECOND_APRON,
]


@dataclass(frozen=True, slots=True)
class Thresholds:
    """League-wide dollar lines for one season.

    Built from configs/cba/nba_2023.yaml. Construct via ``from_config`` so
    that null values in the YAML fail loudly here instead of propagating as
    None into arithmetic.
    """

    season: str
    salary_cap: Money
    minimum_team_salary: Money
    luxury_tax_line: Money
    first_apron: Money
    second_apron: Money

    def __post_init__(self) -> None:
        for f in (
            "salary_cap",
            "minimum_team_salary",
            "luxury_tax_line",
            "first_apron",
            "second_apron",
        ):
            object.__setattr__(self, f, to_money(getattr(self, f)))

        # Structural invariant. If this ever fails, the config is wrong, not
        # the arithmetic -- and every downstream tier classification would be
        # nonsense.
        ordered = (
            self.minimum_team_salary
            < self.salary_cap
            < self.luxury_tax_line
            < self.first_apron
            < self.second_apron
        )
        if not ordered:
            raise ValueError(
                f"{self.season}: thresholds out of order -- expected "
                "floor < cap < tax < apron1 < apron2, got "
                f"{self.minimum_team_salary}, {self.salary_cap}, "
                f"{self.luxury_tax_line}, {self.first_apron}, {self.second_apron}"
            )

    @classmethod
    def from_config(cls, season: str, block: dict[str, Any]) -> Thresholds:
        """Build from a parsed ``seasons[<season>]`` block.

        Any required field that is null in the YAML raises rather than
        defaulting to zero. A season you have not sourced yet is a season you
        cannot compute against.
        """
        required = (
            "salary_cap",
            "minimum_team_salary",
            "luxury_tax_line",
            "first_apron",
            "second_apron",
        )
        missing = [k for k in required if block.get(k) is None]
        if missing:
            raise UnsourcedConstantError(
                f"{season}: unsourced constants {missing} -- populate them in "
                "configs/cba/nba_2023.yaml with a citation, or do not compute "
                "against this season"
            )
        return cls(season=season, **{k: block[k] for k in required})


@dataclass(frozen=True, slots=True)
class PayrollAdjustments:
    """Deltas a bare contract list cannot yet express.

    All default to zero, which makes tax and apron salary APPROXIMATE. That
    approximation is surfaced in CapPosition.approximations rather than
    hidden. Once ingest supplies cap holds and bonus detail, populate these
    and the approximation flags disappear on their own.
    """

    #: Unlikely bonuses, added back for apron purposes only.
    apron_unlikely_bonuses: Money = ZERO
    #: Cap holds counted in team salary but stripped out for apron purposes.
    apron_excluded_cap_holds: Money = ZERO
    #: Net end-of-season tax-salary adjustments.
    tax_adjustments: Money = ZERO

    def __post_init__(self) -> None:
        for f in (
            "apron_unlikely_bonuses",
            "apron_excluded_cap_holds",
            "tax_adjustments",
        ):
            object.__setattr__(self, f, to_money(getattr(self, f)))

    @property
    def is_empty(self) -> bool:
        return (
            self.apron_unlikely_bonuses == ZERO
            and self.apron_excluded_cap_holds == ZERO
            and self.tax_adjustments == ZERO
        )


@dataclass(frozen=True, slots=True)
class CapPosition:
    """Where one team sits against every threshold, for one season."""

    team_id: str
    season: str
    roster_count: int

    team_salary: Money
    tax_salary: Money
    apron_salary: Money

    thresholds: Thresholds
    tier: ApronTier
    boundary: Boundary

    #: Names of measures that are estimates, not exact. Empty means trustworthy.
    approximations: tuple[str, ...] = field(default_factory=tuple)

    # -- signed distances ----------------------------------------------------
    # Positive means room remaining below the line. Negative means over it.
    # Signed on purpose: clamping to zero throws away the information the
    # caller most often needs.

    @property
    def cap_space(self) -> Money:
        return to_money(self.thresholds.salary_cap - self.team_salary)

    @property
    def cap_room(self) -> Money:
        """Usable space. Zero for an over-the-cap team, never negative."""
        return max(ZERO, self.cap_space)

    @property
    def distance_to_tax(self) -> Money:
        return to_money(self.thresholds.luxury_tax_line - self.tax_salary)

    @property
    def distance_to_first_apron(self) -> Money:
        return to_money(self.thresholds.first_apron - self.apron_salary)

    @property
    def distance_to_second_apron(self) -> Money:
        return to_money(self.thresholds.second_apron - self.apron_salary)

    @property
    def floor_shortfall(self) -> Money:
        """Dollars below the minimum team salary. Zero if compliant."""
        return max(ZERO, to_money(self.thresholds.minimum_team_salary - self.team_salary))

    @property
    def is_exact(self) -> bool:
        return not self.approximations

    def __str__(self) -> str:
        flag = "" if self.is_exact else f"  [approx: {', '.join(self.approximations)}]"
        return (
            f"{self.team_id} {self.season}: team ${self.team_salary:,} "
            f"tier={self.tier.value}{flag}"
        )


# ---------------------------------------------------------------------------
# Payroll measures
# ---------------------------------------------------------------------------


def team_salary(roster: Roster) -> Money:
    """Salaries plus cap holds and other cap charges. Compared against the cap.

    Dead money counts. A waived player's stretched salary is as real to the
    cap as an active player's.
    """
    return _sum_hits(roster.contracts)


def tax_salary(roster: Roster, adjustments: PayrollAdjustments) -> Money:
    """Compared against the luxury tax line.

    APPROXIMATE while ``adjustments.tax_adjustments`` is zero and the roster
    carries no cap-hold detail.
    """
    return to_money(_sum_hits(roster.contracts) + adjustments.tax_adjustments)


def apron_salary(roster: Roster, adjustments: PayrollAdjustments) -> Money:
    """Compared against the first and second aprons.

    Team salary, less cap holds excluded for apron purposes, plus unlikely
    bonuses. APPROXIMATE while those adjustments are zero.
    """
    return to_money(
        _sum_hits(roster.contracts)
        - adjustments.apron_excluded_cap_holds
        + adjustments.apron_unlikely_bonuses
    )


def _sum_hits(contracts: Iterable[Contract]) -> Money:
    total: Decimal = ZERO
    for c in contracts:
        total += c.cap_hit
    return to_money(total)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_tier(
    *,
    team_sal: Money,
    tax_sal: Money,
    apron_sal: Money,
    thresholds: Thresholds,
    boundary: Boundary = Boundary.ABOVE,
) -> ApronTier:
    """Which spending band the team occupies.

    Each comparison uses the payroll measure that threshold is actually
    defined against -- team salary for the cap, tax salary for the tax line,
    apron salary for the aprons. Mixing them is the bug this signature exists
    to prevent.
    """
    over = _over(boundary)

    if over(apron_sal, thresholds.second_apron):
        return ApronTier.SECOND_APRON
    if over(apron_sal, thresholds.first_apron):
        return ApronTier.FIRST_APRON
    if over(tax_sal, thresholds.luxury_tax_line):
        return ApronTier.TAX_PAYER
    if over(team_sal, thresholds.salary_cap):
        return ApronTier.OVER_CAP
    return ApronTier.UNDER_CAP


def _over(boundary: Boundary) -> Callable[[Money, Money], bool]:
    """Return the comparison for 'is value over line' under this boundary rule."""
    if boundary is Boundary.ABOVE:
        return lambda value, line: value > line
    return lambda value, line: value >= line


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def cap_position(
    roster: Roster,
    thresholds: Thresholds,
    adjustments: PayrollAdjustments | None = None,
    boundary: Boundary = Boundary.ABOVE,
) -> CapPosition:
    """Full cap picture for one team-season. Pure.

    Raises if the roster's season disagrees with the thresholds' season --
    computing a 2026-27 roster against 2025-26 lines is a silent, plausible,
    completely wrong answer, so it is made loud instead.
    """
    if roster.season != thresholds.season:
        raise ValueError(
            f"season mismatch: roster is {roster.season}, thresholds are "
            f"{thresholds.season}"
        )

    adj = adjustments or PayrollAdjustments()

    team_sal = team_salary(roster)
    tax_sal = tax_salary(roster, adj)
    apron_sal = apron_salary(roster, adj)

    approximations: list[str] = []
    if adj.tax_adjustments == ZERO:
        approximations.append("tax_salary")
    if adj.apron_excluded_cap_holds == ZERO and adj.apron_unlikely_bonuses == ZERO:
        approximations.append("apron_salary")

    return CapPosition(
        team_id=roster.team_id,
        season=roster.season,
        roster_count=roster.roster_count,
        team_salary=team_sal,
        tax_salary=tax_sal,
        apron_salary=apron_sal,
        thresholds=thresholds,
        tier=classify_tier(
            team_sal=team_sal,
            tax_sal=tax_sal,
            apron_sal=apron_sal,
            thresholds=thresholds,
            boundary=boundary,
        ),
        boundary=boundary,
        approximations=tuple(approximations),
    )
