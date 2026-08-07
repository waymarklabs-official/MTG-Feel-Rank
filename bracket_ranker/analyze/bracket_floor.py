"""Stage 3.2: rules-based bracket floor.

A FLOOR, not a verdict -- the calibrated feel_score (Stage 4) is the actual
prediction. This just encodes the parts of the bracket rules that are
close to mechanical, verified live against WotC's Commander Brackets Beta
announcements (see config.py for sources/dates):

  - Game Changers (Scryfall's own `game_changer` field, so this list is
    never hand-maintained): 0 -> no floor from this rule; 1-3 -> floor 3
    (Bracket 3 allows up to GAME_CHANGERS_BRACKET_3_MAX); 4+ -> floor 4.
  - Mass land denial: forbidden in Brackets 1-3, so its presence forces
    floor 4 outright.
  - Two-card *infinite* combos: the easiest rule to get wrong, per the
    spec. Brackets 1-2 disallow them outright; Bracket 3 allows them
    UNLESS they assemble early (WotC's own Bracket-3 floor is "at least six
    turns", reused here as the early/late cutoff -- see
    EARLY_COMBO_TURN_CUTOFF in config.py). Combo *presence* alone must
    never force the floor to 4 -- only a fast two-card infinite does. This
    is why bracket_floor() takes the Stage 3.3/3.4 results as input rather
    than re-deriving them: the Monte Carlo assembly turn is what tells
    early from late apart.
"""
from __future__ import annotations

from dataclasses import dataclass

from bracket_ranker.config import EARLY_COMBO_TURN_CUTOFF, GAME_CHANGERS_BRACKET_3_MAX


@dataclass
class BracketFloorInputs:
    game_changer_count: int
    has_mass_land_denial: bool
    has_early_two_card_infinite: bool  # a 2-piece infinite combo with median assembly turn <= cutoff


@dataclass
class BracketFloorResult:
    floor: int
    reasons: list[str]


def compute_bracket_floor(inputs: BracketFloorInputs) -> BracketFloorResult:
    reasons = []
    floor = 1

    if inputs.game_changer_count == 0:
        pass
    elif inputs.game_changer_count <= GAME_CHANGERS_BRACKET_3_MAX:
        floor = max(floor, 3)
        reasons.append(f"{inputs.game_changer_count} Game Changer(s) -> floor 3")
    else:
        floor = max(floor, 4)
        reasons.append(f"{inputs.game_changer_count} Game Changers (>{GAME_CHANGERS_BRACKET_3_MAX}) -> floor 4")

    if inputs.has_mass_land_denial:
        floor = max(floor, 4)
        reasons.append("mass land denial detected -> floor 4")

    if inputs.has_early_two_card_infinite:
        floor = max(floor, 4)
        reasons.append(
            f"two-card infinite combo assembles by turn {EARLY_COMBO_TURN_CUTOFF} or earlier -> floor 4"
        )

    if not reasons:
        reasons.append("no floor-raising signals detected")

    return BracketFloorResult(floor=floor, reasons=reasons)
