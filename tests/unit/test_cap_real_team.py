"""S-006 golden: one real team, worked by hand.

Source: <URL>, viewed <YYYY-MM-DD>. Cap hits copied by eye.
spotrac.com/nba/detroit-pistons/cap/_/year/2026
Sum and tier worked on paper BEFORE running pytest.
"""
from decimal import Decimal

import pytest

from gmf.core.cap.ledger import ApronTier, Thresholds, cap_position
from gmf.core.types import Contract, Roster

SEASON = "2026-27"

# Copied from configs/cba/nba_2023.yaml. Swap for the S-007 loader later.
T_2026 = Thresholds(
    season=SEASON,
    salary_cap=164_961_000,
    minimum_team_salary=148_465_000,
    luxury_tax_line=200_428_000,
    first_apron=209_015_000,
    second_apron=221_686_000,
)

HITS = [  # (player_id, cap_hit) - integer dollars, one line per player
    ("cunningham-cade", 50_105_628),
    ("collins-john", 17_000_000),
    ("robinson-duncan", 15_992_957),
    ("joe-isaiah", 11_323_006),
    ("thompson-ausar", 11_117_925),
    ("huerter-kevin", 9_507_042),
    ("holland-ron-ii", 9_069_600),
    ("reed-paul", 5_602_689),
    ("okorie-ebuka", 4_481_280),
    ("jenkins-daniss", 4_000_000),
    ("green-javonte", 3_943_679),
    ("prince-taurean", 3_815_861),
    ("harris-gary", 3_815_861),
    ("smith-tolu-iii", 2_411_090),
    ("lanier-chaz", 2_150_917),
]
DEAD = [  # waived / stretched salary still on the books
]

HAND_TOTAL = Decimal(154_337_535)          # hand summed
HAND_TIER = ApronTier.UNDER_CAP   # your call before running


@pytest.mark.golden
def test_real_team_matches_hand_worked_figures():
    assert HITS, "fill in the roster from the cited source"
    contracts = [Contract(p, SEASON, h) for p, h in HITS]
    contracts += [Contract(p, SEASON, h, dead=True) for p, h in DEAD]
    pos = cap_position(Roster("DET", SEASON, tuple(contracts)), T_2026)

    assert pos.team_salary == HAND_TOTAL
    assert pos.cap_space == T_2026.salary_cap - HAND_TOTAL
    assert pos.tier is HAND_TIER
    assert pos.roster_count == len(HITS)
    assert not pos.is_exact  # no cap-hold or bonus detail yet