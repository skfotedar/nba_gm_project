"""Core domain types.

This module is on the deterministic path. It imports nothing outside the
standard library, and it must never import an LLM client, an HTTP client,
or anything from ``gmf.agents`` / ``gmf.graph`` / ``gmf.retrieval``.
See ``tests/architecture/test_core_purity.py``.

Money is ``Decimal`` throughout. NBA salaries are exact integer dollar
amounts and cap arithmetic is compared against hard thresholds, so binary
floating point is not acceptable here.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum

__all__ = [
    "ZERO",
    "Contract",
    "ContractOption",
    "FloatMoneyError",
    "Money",
    "Roster",
    "season_key",
    "to_money",
    "total_cap_hits",
]

# Plain alias (not a PEP 695 ``type`` statement) so ``Money(...)`` and
# ``isinstance(x, Money)`` keep working for callers.
Money = Decimal

#: Cap arithmetic is done in whole dollars.
_DOLLAR = Decimal("1")

ZERO: Money = Decimal("0")


class FloatMoneyError(TypeError):
    """Raised when a float reaches a money boundary.

    Subclasses ``TypeError`` so existing ``except TypeError`` handlers still
    catch it.
    """


def to_money(value: object) -> Money:
    """Coerce to whole-dollar ``Decimal``.

    Floats are rejected rather than silently converted: a float reaching
    this function means an upstream layer lost exactness, and we want that
    to fail loudly at the boundary instead of drifting by a few cents.
    """
    # bool is a subclass of int; without this, True would become $1.
    if isinstance(value, bool):
        raise TypeError("refusing to build Money from bool")
    if isinstance(value, float):
        raise FloatMoneyError(
            f"refusing to build Money from float {value!r}; pass str, int, or Decimal"
        )
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, int):
        dec = Decimal(value)
    elif isinstance(value, str):
        try:
            dec = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"not a valid money string: {value!r}") from exc
    else:
        raise TypeError(f"cannot convert {type(value).__name__} to Money")
    if not dec.is_finite():
        raise ValueError(f"Money must be finite, got {dec}")
    return dec.quantize(_DOLLAR, rounding=ROUND_HALF_UP)


class ContractOption(StrEnum):
    """Option structure attached to a contract year."""

    NONE = "none"
    PLAYER = "player"
    TEAM = "team"
    EARLY_TERMINATION = "eto"


@dataclass(frozen=True, slots=True)
class Contract:
    """A single player's cap hit for a single season.

    ``cap_hit`` is what counts against team salary, which is not always the
    same as cash paid. Dead money (waived players, stretched salary) is
    modelled as a Contract with ``dead=True`` and no active roster slot.
    """

    player_id: str
    season: str
    cap_hit: Money
    guaranteed: bool = True
    option: ContractOption = ContractOption.NONE
    dead: bool = False
    incoming_trade_exception: bool = False

    def __post_init__(self) -> None:
        # frozen=True blocks normal assignment, so use object.__setattr__
        object.__setattr__(self, "cap_hit", to_money(self.cap_hit))
        if self.cap_hit < ZERO:
            raise ValueError(f"negative cap hit for {self.player_id}: {self.cap_hit}")


@dataclass(frozen=True, slots=True)
class Roster:
    """A team's full set of cap obligations for one season."""

    team_id: str
    season: str
    contracts: tuple[Contract, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "contracts", tuple(self.contracts))
        bad = [c.player_id for c in self.contracts if c.season != self.season]
        if bad:
            raise ValueError(
                f"contracts for wrong season on {self.team_id} {self.season}: {bad}"
            )

    @property
    def active(self) -> tuple[Contract, ...]:
        return tuple(c for c in self.contracts if not c.dead)

    @property
    def dead_money(self) -> tuple[Contract, ...]:
        return tuple(c for c in self.contracts if c.dead)

    @property
    def roster_count(self) -> int:
        """Players occupying a roster spot. Dead money does not."""
        return len(self.active)


def season_key(start_year: int) -> str:
    """``2025`` -> ``'2025-26'``. One canonical season string, everywhere."""
    if not 1946 <= start_year <= 2100:
        raise ValueError(f"implausible season start year: {start_year}")
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def total_cap_hits(contracts: Iterable[Contract]) -> Money:
    """Sum cap hits exactly. Empty iterable sums to zero, not None."""
    total = ZERO
    for c in contracts:
        total += c.cap_hit
    return to_money(total)
