from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from gmf.core.types import (
    ZERO,
    Contract,
    ContractOption,
    FloatMoneyError,
    Roster,
    season_key,
    to_money,
    total_cap_hits,
)

SEASON = "2026-27"


# --- to_money --------------------------------------------------------------

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("35000000"), Decimal("35000000")),
        (35_000_000, Decimal("35000000")),
        ("35000000", Decimal("35000000")),
        (" 1200 ", Decimal("1200")),
    ],
)
def test_to_money_accepts_exact_types(value: object, expected: Decimal) -> None:
    result = to_money(value)
    assert result == expected
    assert isinstance(result, Decimal)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("100.5", Decimal("101")), ("100.4", Decimal("100")), ("99.50", Decimal("100"))],
)
def test_to_money_rounds_to_whole_dollars_half_up(value: str, expected: Decimal) -> None:
    assert to_money(value) == expected


@pytest.mark.parametrize("bad", [0.1, 35_000_000.0, float("nan"), float("inf")])
def test_to_money_rejects_floats(bad: float) -> None:
    with pytest.raises(FloatMoneyError):
        to_money(bad)


def test_float_money_error_is_a_type_error() -> None:
    # keeps existing ``except TypeError`` handlers working
    assert issubclass(FloatMoneyError, TypeError)


@pytest.mark.parametrize("bad", [True, False, None, [1], object()])
def test_to_money_rejects_wrong_types(bad: object) -> None:
    with pytest.raises(TypeError):
        to_money(bad)


@pytest.mark.parametrize("bad", ["abc", "", "NaN", "Infinity", Decimal("NaN")])
def test_to_money_rejects_bad_values(bad: object) -> None:
    with pytest.raises(ValueError):
        to_money(bad)


# --- Contract --------------------------------------------------------------

def test_contract_converts_cap_hit_to_decimal() -> None:
    c = Contract("p1", SEASON, "12000000")  # type: ignore[arg-type]
    assert isinstance(c.cap_hit, Decimal)
    assert c.cap_hit == Decimal("12000000")


def test_contract_defaults() -> None:
    c = Contract("p1", SEASON, Decimal("1000000"))
    assert c.guaranteed is True
    assert c.option is ContractOption.NONE
    assert c.dead is False
    assert c.incoming_trade_exception is False


def test_contract_rejects_float_cap_hit() -> None:
    with pytest.raises(FloatMoneyError):
        Contract("p1", SEASON, 12_000_000.0)  # type: ignore[arg-type]


def test_contract_rejects_negative_cap_hit() -> None:
    with pytest.raises(ValueError):
        Contract("p1", SEASON, Decimal("-1"))


def test_contract_is_immutable() -> None:
    c = Contract("p1", SEASON, Decimal("1"))
    with pytest.raises(FrozenInstanceError):
        c.cap_hit = Decimal("2")  # type: ignore[misc]


# --- Roster ----------------------------------------------------------------

def _roster() -> Roster:
    return Roster(
        "PHX",
        SEASON,
        (
            Contract("a", SEASON, Decimal("40000000")),
            Contract("b", SEASON, Decimal("25000000"), option=ContractOption.PLAYER),
            Contract("c", SEASON, Decimal("5000000"), dead=True),
        ),
    )


def test_roster_splits_active_and_dead() -> None:
    r = _roster()
    assert [c.player_id for c in r.active] == ["a", "b"]
    assert [c.player_id for c in r.dead_money] == ["c"]
    assert r.roster_count == 2  # dead money takes no roster spot


def test_roster_rejects_wrong_season_contract() -> None:
    with pytest.raises(ValueError, match="wrong season"):
        Roster("PHX", SEASON, (Contract("a", "2025-26", Decimal("1")),))


def test_roster_converts_list_to_tuple() -> None:
    r = Roster("PHX", SEASON, [Contract("a", SEASON, Decimal("1"))])  # type: ignore[arg-type]
    assert isinstance(r.contracts, tuple)


def test_empty_roster() -> None:
    r = Roster("PHX", SEASON)
    assert r.contracts == ()
    assert r.roster_count == 0


# --- helpers ---------------------------------------------------------------

def test_total_cap_hits_is_exact_and_includes_dead_money() -> None:
    assert total_cap_hits(_roster().contracts) == Decimal("70000000")


def test_total_cap_hits_empty_is_zero() -> None:
    result = total_cap_hits([])
    assert result == ZERO
    assert isinstance(result, Decimal)


@pytest.mark.parametrize(
    ("year", "expected"),
    [(2025, "2025-26"), (1999, "1999-00"), (2099, "2099-00")],
)
def test_season_key(year: int, expected: str) -> None:
    assert season_key(year) == expected


@pytest.mark.parametrize("year", [1945, 2101])
def test_season_key_rejects_implausible_years(year: int) -> None:
    with pytest.raises(ValueError):
        season_key(year)
