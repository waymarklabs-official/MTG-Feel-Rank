"""v1 mana model: intentionally crude, per the spec's own instruction to
"start crude" and document assumptions rather than guess at a sophisticated
model that might be wrong in a less legible way.

Assumptions (all deliberate simplifications -- replace this module, not
these comments, when a better model is needed):
  - "On the play": no draw on turn 1.
  - One land drop per turn, always taken if a land is in hand. Color
    requirements are ignored entirely -- a land is a land. This is the
    single biggest source of optimism in this model: real decks miss land
    drops and get colorscrewed; this one never does.
  - A mana rock/dork in hand gets cast the instant it's affordable, ahead
    of anything else that turn. Each ramp piece taps for exactly 1 mana;
    each fast-mana piece taps for 2 (a stand-in for Sol Ring/Mox-scale
    effects -- crude, not card-specific).
  - Treasure tokens, rituals-as-one-shots, and anything else that isn't a
    permanent are NOT modeled as mana sources at all in v1 (documented
    limitation, not silently ignored: they simply don't help assemble a
    combo faster in this model, which undercounts true speed for
    treasure/ritual-heavy decks).
  - Combo pieces are "castable" the instant they're all simultaneously in
    hand and total available mana >= the sum of their mana values --
    real cast sequencing (tapping one piece to help cast another) is not
    modeled; this is optimistic for combos that literally work that way
    (e.g. a piece that taps for the mana to cast the other piece) and
    pessimistic for nothing in particular. Net effect: roughly a wash, but
    unverified -- worth revisiting if a v2 model is built.
  - Mulligan model: simple London-mulligan approximation. A 7-card hand
    with fewer than 2 or more than 5 lands mulligans; up to 2 mulligans;
    the final hand size is always 7 (cards are put on the bottom after
    a keep, which doesn't affect the "is this playable" resampling loop
    since we don't currently model the bottomed cards mattering later).
"""
from __future__ import annotations

import random

from bracket_ranker.analyze.mana_model.base import AssemblyResult, SimCard

VERSION = "v1_naive"

OPENING_HAND_SIZE = 7
MAX_MULLIGANS = 2
MIN_KEEPABLE_LANDS = 2
MAX_KEEPABLE_LANDS = 5
TURN_HORIZON = 15  # simulations that don't assemble the combo by this turn count as "never"


def _draw_opening_hand(library: list[SimCard], rng: random.Random) -> tuple[list[SimCard], list[SimCard]]:
    shuffled = library[:]
    rng.shuffle(shuffled)
    for _mulligan in range(MAX_MULLIGANS + 1):
        hand, rest = shuffled[:OPENING_HAND_SIZE], shuffled[OPENING_HAND_SIZE:]
        land_count = sum(1 for c in hand if c.is_land)
        if MIN_KEEPABLE_LANDS <= land_count <= MAX_KEEPABLE_LANDS or _mulligan == MAX_MULLIGANS:
            return hand, rest
        rng.shuffle(shuffled)  # mulligan: reshuffle everything and redraw
    return hand, rest  # pragma: no cover -- loop always returns above


def _simulate_one(library: list[SimCard], target_ids: set[str], rng: random.Random) -> int | None:
    hand, library_remaining = _draw_opening_hand(library, rng)
    battlefield_lands = 0
    battlefield_mana_sources = 0  # ramp/fast-mana permanents already in play

    for turn in range(1, TURN_HORIZON + 1):
        if turn > 1 and library_remaining:  # on the play: no turn-1 draw
            hand.append(library_remaining.pop(0))

        land_in_hand = next((c for c in hand if c.is_land), None)
        if land_in_hand:
            hand.remove(land_in_hand)
            battlefield_lands += 1

        available_mana = battlefield_lands + battlefield_mana_sources
        # Cast the cheapest ramp/fast-mana piece we can afford, greedily,
        # since accelerating is always the right play in this crude model.
        cast_more = True
        while cast_more:
            cast_more = False
            candidates = sorted(
                (c for c in hand if (c.is_ramp or c.is_fast_mana) and c.cmc <= available_mana),
                key=lambda c: c.cmc,
            )
            if candidates:
                piece = candidates[0]
                hand.remove(piece)
                available_mana -= piece.cmc
                battlefield_mana_sources += 2 if piece.is_fast_mana else 1
                available_mana = battlefield_lands + battlefield_mana_sources
                cast_more = True

        hand_ids = {c.oracle_id for c in hand}
        if target_ids <= hand_ids:
            needed_mana = sum(c.cmc for c in hand if c.oracle_id in target_ids)
            if available_mana >= needed_mana:
                return turn

    return None  # never assembled within the horizon


class NaiveManaModel:
    VERSION = VERSION

    def simulate_assembly_turn(
        self,
        library: list[SimCard],
        target_oracle_ids: set[str],
        n_simulations: int,
    ) -> AssemblyResult:
        if not target_oracle_ids or not target_oracle_ids.issubset({c.oracle_id for c in library}):
            return AssemblyResult(median_turn=None, p25_turn=None, never_assembled_fraction=1.0)

        rng = random.Random(42)  # fixed seed: reruns are reproducible, not just "close"
        turns: list[int] = []
        never = 0
        for _ in range(n_simulations):
            result = _simulate_one(library, target_oracle_ids, rng)
            if result is None:
                never += 1
            else:
                turns.append(result)

        if not turns:
            return AssemblyResult(median_turn=None, p25_turn=None, never_assembled_fraction=1.0)

        turns.sort()
        median_turn = turns[len(turns) // 2]
        p25_turn = turns[len(turns) // 4]
        return AssemblyResult(
            median_turn=float(median_turn),
            p25_turn=float(p25_turn),
            never_assembled_fraction=never / n_simulations,
        )
