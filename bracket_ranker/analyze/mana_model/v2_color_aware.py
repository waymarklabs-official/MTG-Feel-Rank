"""v2 mana model: color-aware castability, on the same turn structure v1
uses (draw / land drop / greedy ramp-casting / check combo).

The one thing this changes from v1_naive: "can I cast this" now means "can
I actually pay this spell's colored mana cost from what my specific mana
sources can produce" (bracket_ranker.analyze.mana_cost.can_pay), not just
"do I have enough total mana value". This directly closes the single
biggest gap the spec's known-limitations list calls out about v1 -- real
decks brick on colors far more often than they brick on raw mana count.

Every simplification v1_naive's docstring documents (no turn-1 draw, one
land drop, greedy ramp-casting order, no real cast sequencing, the same
crude mulligan rule, no treasures/rituals-as-one-shots) still applies here
unless noted otherwise -- this model only changes what "castable" means.

One color-specific wrinkle worth calling out: Scryfall's produced_mana for
identity-dependent fixers (Arcane Signet, Chromatic Lantern: "any color in
your commander's color identity") lists all five colors regardless of the
deck it's in -- verified live. Left as-is, that would make those cards
look like universal 5-color fixers even in a mono-color deck. Callers are
expected to intersect each SimCard's produced_mana against the deck's own
color identity before handing it to this model (see run_analyze.py); this
module doesn't do that itself since it has no notion of "the deck",only
the library it's handed.
"""
from __future__ import annotations

import random

from bracket_ranker.analyze.mana_cost import ManaCost, can_pay, parse_mana_cost
from bracket_ranker.analyze.mana_model.base import AssemblyResult, SimCard

VERSION = "v2_color_aware"

OPENING_HAND_SIZE = 7
MAX_MULLIGANS = 2
MIN_KEEPABLE_LANDS = 2
MAX_KEEPABLE_LANDS = 5
TURN_HORIZON = 15


def _draw_opening_hand(library: list[SimCard], rng: random.Random) -> tuple[list[SimCard], list[SimCard]]:
    shuffled = library[:]
    rng.shuffle(shuffled)
    for _mulligan in range(MAX_MULLIGANS + 1):
        hand, rest = shuffled[:OPENING_HAND_SIZE], shuffled[OPENING_HAND_SIZE:]
        land_count = sum(1 for c in hand if c.is_land)
        if MIN_KEEPABLE_LANDS <= land_count <= MAX_KEEPABLE_LANDS or _mulligan == MAX_MULLIGANS:
            return hand, rest
        rng.shuffle(shuffled)
    return hand, rest  # pragma: no cover -- loop always returns above


def _combined_cost(cards: list[SimCard]) -> ManaCost:
    combined = ManaCost()
    for c in cards:
        piece = parse_mana_cost(c.mana_cost)
        combined.generic += piece.generic
        combined.colorless_pips += piece.colorless_pips
        combined.hybrid_choices.extend(piece.hybrid_choices)
    return combined


def _simulate_one(library: list[SimCard], target_ids: set[str], rng: random.Random) -> int | None:
    hand, library_remaining = _draw_opening_hand(library, rng)
    battlefield_sources: list[frozenset[str]] = []  # every mana-producing permanent in play, by producible colors

    for turn in range(1, TURN_HORIZON + 1):
        if turn > 1 and library_remaining:
            hand.append(library_remaining.pop(0))

        land_in_hand = next((c for c in hand if c.is_land), None)
        if land_in_hand:
            hand.remove(land_in_hand)
            battlefield_sources.append(frozenset(land_in_hand.produced_mana) or frozenset({"C"}))

        # Greedily cast affordable ramp/fast-mana, same priority order as
        # v1 -- but "affordable" is now a real color-aware castability
        # check instead of a mana-value comparison.
        cast_more = True
        while cast_more:
            cast_more = False
            candidates = sorted(
                (c for c in hand if c.is_ramp or c.is_fast_mana),
                key=lambda c: c.cmc,
            )
            for piece in candidates:
                if can_pay(parse_mana_cost(piece.mana_cost), battlefield_sources):
                    hand.remove(piece)
                    # A ramp/fast-mana piece with no produced_mana data of
                    # its own (rare -- a ritual or oddly-worded effect
                    # Scryfall doesn't tag) still contributes *some*
                    # capacity rather than none, modeled as colorless-only:
                    # optimistic about how much mana it adds, pessimistic
                    # about which colors, which roughly cancel out for
                    # castability purposes.
                    battlefield_sources.append(frozenset(piece.produced_mana) or frozenset({"C"}))
                    cast_more = True
                    break

        hand_ids = {c.oracle_id for c in hand}
        if target_ids <= hand_ids:
            target_cards = [c for c in hand if c.oracle_id in target_ids]
            if can_pay(_combined_cost(target_cards), battlefield_sources):
                return turn

    return None  # never assembled within the horizon


class ColorAwareManaModel:
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
