"""Cap ledger tests. Fast, no network, no LLM, no config file reads.

Thresholds here are SYNTHETIC round numbers, not the real 2026-27 lines.
That is deliberate: unit tests assert arithmetic, and they must not start
failing next July when the league raises the cap. Real-config assertions
belong in tests/golden/.
"""

from decimal import Decimal

import pytest

from gmf.core.cap.ledger import (
    ApronTier,
    Boundary,
    PayrollAdjustments,
    Thresholds,
    UnsourcedConstantError,
    apron_salary,
    cap_position,
    team_salary,
)
from gmf.core.types import Contract, Roster

SEASON = "2026-27"

# floor 140M < cap 160M < tax 200M < apron1 210M < apron2 220M
T = Thresholds(
    season=SEASON,
    salary_cap=Decimal(160_000_000),
    minimum_team_salary=Decimal(140_000_000),
    luxury_tax_line=Decimal(200_000_000),
    first_apron=Decimal(210_000_000),
    second_apron=Decimal(220_000_000),
)

def roster(*amounts, team="TST", dead_amounts=()):
    contracts = [
        Contract(player_id=f"p{i}", season=SEASON, cap_hit=a)
        for i, a in enumerate(amounts)
    ]
    contracts += [
        Contract(player_id=f"d{i}", season=SEASON, cap_hit=a, dead=True)
        for i, a in enumerate(dead_amounts)
    ]
    return Roster(team_id=team, season=SEASON, contracts=tuple(contracts))


# --- the three required cases ----------------------------------------------


def test_team_under_the_cap():
    r = roster(50_000_000, 40_000_000, 30_000_000)  # 120M
    pos = cap_position(r, T)

    assert pos.team_salary == Decimal("120000000")
    assert pos.tier is ApronTier.UNDER_CAP
    assert pos.cap_space == Decimal("40000000")      # 160 - 120
    assert pos.cap_room == Decimal("40000000")
    assert pos.distance_to_tax == Decimal("80000000")
    assert pos.floor_shortfall == Decimal("20000000")  # 140 - 120, below floor
    assert pos.roster_count == 3


def test_team_between_tax_and_first_apron():
    r = roster(90_000_000, 80_000_000, 35_000_000)  # 205M
    pos = cap_position(r, T)

    assert pos.team_salary == Decimal("205000000")
    assert pos.tier is ApronTier.TAX_PAYER
    assert pos.cap_space == Decimal("-45000000")     # signed, not clamped
    assert pos.cap_room == Decimal("0")
    assert pos.distance_to_tax == Decimal("-5000000")
    assert pos.distance_to_first_apron == Decimal("5000000")
    assert pos.floor_shortfall == Decimal("0")


def test_team_over_the_second_apron():
    r = roster(100_000_000, 90_000_000, 45_000_000)  # 235M
    pos = cap_position(r, T)

    assert pos.tier is ApronTier.SECOND_APRON
    assert pos.distance_to_second_apron == Decimal("-15000000")
    assert pos.tier.rank > ApronTier.FIRST_APRON.rank


# --- the things that would bite later --------------------------------------


def test_dead_money_counts_against_the_cap_but_not_the_roster_count():
    r = roster(100_000_000, dead_amounts=(20_000_000,))
    pos = cap_position(r, T)
    assert pos.team_salary == Decimal("120000000")
    assert pos.roster_count == 1


def test_floats_are_rejected_at_the_boundary():
    with pytest.raises(TypeError, match="float"):
        Contract(player_id="x", season=SEASON, cap_hit=35_000_000.01)


def test_apron_salary_differs_from_team_salary_once_adjustments_exist():
    r = roster(205_000_000)
    adj = PayrollAdjustments(
        apron_unlikely_bonuses=6_000_000,
        apron_excluded_cap_holds=1_000_000,
    )
    assert team_salary(r) == Decimal("205000000")
    assert apron_salary(r, adj) == Decimal("210000000")  # 205 - 1 + 6

    pos = cap_position(r, T, adj)
    # Same roster, different measure, different tier.
    assert pos.tier is ApronTier.TAX_PAYER  # apron salary 210M is not ABOVE 210M


def test_approximation_is_advertised_not_hidden():
    pos = cap_position(roster(205_000_000), T)
    assert not pos.is_exact
    assert "apron_salary" in pos.approximations

    exact = cap_position(
        roster(205_000_000),
        T,
        PayrollAdjustments(apron_unlikely_bonuses=1, tax_adjustments=1),
    )
    assert exact.is_exact


def test_season_mismatch_is_loud():
    r = Roster(
        team_id="TST",
        season="2025-26",
        contracts=(Contract(player_id="p", season="2025-26", cap_hit=1),),
    )
    with pytest.raises(ValueError, match="season mismatch"):
        cap_position(r, T)


def test_unsourced_constants_refuse_to_compute():
    block = {
        "salary_cap": 154_647_000,
        "minimum_team_salary": None,
        "luxury_tax_line": None,
        "first_apron": None,
        "second_apron": None,
    }
    with pytest.raises(UnsourcedConstantError, match="unsourced"):
        Thresholds.from_config("2025-26", block)


def test_out_of_order_thresholds_are_rejected():
    with pytest.raises(ValueError, match="out of order"):
        Thresholds(
            season=SEASON,
            salary_cap=160_000_000,
            minimum_team_salary=140_000_000,
            luxury_tax_line=200_000_000,
            first_apron=220_000_000,   # swapped
            second_apron=210_000_000,
        )


def test_boundary_semantics_are_explicit():
    """Exactly ON the second apron. The answer depends on an unresolved
    CBA reading, so both behaviours are pinned until docs/design/ settles it."""
    r = roster(220_000_000)
    # Not ABOVE 220M, so not second-apron -- but comfortably above 210M,
    # which lands it in the first apron band. One line, two tiers apart.
    assert cap_position(r, T, boundary=Boundary.ABOVE).tier is ApronTier.FIRST_APRON
    assert (
        cap_position(r, T, boundary=Boundary.AT_OR_ABOVE).tier
        is ApronTier.SECOND_APRON
    )


def test_cap_position_is_pure():
    r = roster(205_000_000)
    before = tuple(c.cap_hit for c in r.contracts)
    cap_position(r, T)
    cap_position(r, T)
    assert tuple(c.cap_hit for c in r.contracts) == before
