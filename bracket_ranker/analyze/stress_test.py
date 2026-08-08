"""Solo stress test: a deeper simulation than Stage 3's per-combo assembly
check. One simulated game tracks the WHOLE hand every turn, not just a
single combo's target pieces, so a single pass can report mulligan rate,
color-screw events, first-nonland-spell turn, a mana curve, AND the
assembly-turn distribution for every detected combo in the deck (not just
the top-ranked one) -- instead of one simulation run per combo.

This is the on-demand "run this on the specific deck I'm evaluating"
feature, distinct from Stage 3's corpus-wide bracket-floor computation.

Shares its turn structure (opening hand + mulligan, one land drop, greedy
ramp-casting, color-aware castability via mana_cost.can_pay) with
mana_model/v2_color_aware.py BY DESIGN CHOICE, not by import -- kept
self-contained on purpose so each module's assumptions are readable in one
place, at the cost of ~30 duplicated lines. If v2's turn structure ever
changes, this module needs the same change made a second time here.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from bracket_ranker.analyze.mana_cost import ManaCost, can_pay, parse_mana_cost
from bracket_ranker.analyze.mana_model.base import SimCard

OPENING_HAND_SIZE = 7
MAX_MULLIGANS = 2
MIN_KEEPABLE_LANDS = 2
MAX_KEEPABLE_LANDS = 5
TURN_HORIZON = 15
# "Did your early game brick on colors" only means something in the early
# game -- by turn 10+ almost any deck has enough fixing that a screw event
# stops being informative, so the check window is capped here.
COLOR_SCREW_CHECK_TURNS = 6


@dataclass
class ComboTarget:
    variant_id: str
    oracle_ids: frozenset[str]
    piece_count: int
    is_game_ender: bool
    is_infinite: bool


@dataclass
class ComboAssemblyStats:
    variant_id: str
    piece_count: int
    is_game_ender: bool
    is_infinite: bool
    median_turn: float | None
    p25_turn: float | None
    p75_turn: float | None
    never_rate: float


@dataclass
class StressTestReport:
    n_simulations: int
    mulligan_rate: float           # fraction of games that took at least one mulligan
    avg_mulligans_taken: float
    first_spell_turn_median: float | None
    first_spell_turn_p75: float | None
    color_screw_game_rate: float   # fraction of games with >=1 color-screw event by turn 6
    mana_curve: list[float] = field(default_factory=list)  # index i -> avg total mana available on turn i+1
    combo_stats: list[ComboAssemblyStats] = field(default_factory=list)


def _combined_cost(cards: list[SimCard]) -> ManaCost:
    combined = ManaCost()
    for c in cards:
        piece = parse_mana_cost(c.mana_cost)
        combined.generic += piece.generic
        combined.colorless_pips += piece.colorless_pips
        combined.hybrid_choices.extend(piece.hybrid_choices)
    return combined


def _draw_opening_hand(
    library: list[SimCard], rng: random.Random
) -> tuple[list[SimCard], list[SimCard], int]:
    shuffled = library[:]
    rng.shuffle(shuffled)
    mulligans_taken = 0
    for _mull in range(MAX_MULLIGANS + 1):
        hand, rest = shuffled[:OPENING_HAND_SIZE], shuffled[OPENING_HAND_SIZE:]
        land_count = sum(1 for c in hand if c.is_land)
        if MIN_KEEPABLE_LANDS <= land_count <= MAX_KEEPABLE_LANDS or _mull == MAX_MULLIGANS:
            return hand, rest, mulligans_taken
        rng.shuffle(shuffled)
        mulligans_taken += 1
    return hand, rest, mulligans_taken  # pragma: no cover -- loop always returns above


def simulate_one_game(
    library: list[SimCard], combo_targets: list[ComboTarget], rng: random.Random
) -> tuple[int, int | None, bool, list[int], dict[str, int | None]]:
    hand, library_remaining, mulligans_taken = _draw_opening_hand(library, rng)
    battlefield_sources: list[frozenset[str]] = []
    mana_by_turn: list[int] = []
    first_spell_turn: int | None = None
    color_screwed = False
    combo_turns: dict[str, int | None] = {t.variant_id: None for t in combo_targets}

    for turn in range(1, TURN_HORIZON + 1):
        if turn > 1 and library_remaining:
            hand.append(library_remaining.pop(0))

        land_in_hand = next((c for c in hand if c.is_land), None)
        if land_in_hand:
            hand.remove(land_in_hand)
            battlefield_sources.append(frozenset(land_in_hand.produced_mana) or frozenset({"C"}))

        cast_more = True
        while cast_more:
            cast_more = False
            candidates = sorted((c for c in hand if c.is_ramp or c.is_fast_mana), key=lambda c: c.cmc)
            for piece in candidates:
                if can_pay(parse_mana_cost(piece.mana_cost), battlefield_sources):
                    hand.remove(piece)
                    battlefield_sources.append(frozenset(piece.produced_mana) or frozenset({"C"}))
                    cast_more = True
                    break

        mana_by_turn.append(len(battlefield_sources))

        if turn <= COLOR_SCREW_CHECK_TURNS and not color_screwed:
            for c in hand:
                if c.is_land:
                    continue
                cost = parse_mana_cost(c.mana_cost)
                total_needed = cost.generic + cost.colorless_pips + len(cost.hybrid_choices)
                # castable by raw mana value, but NOT by actual colors: a screw event
                if total_needed <= len(battlefield_sources) and not can_pay(cost, battlefield_sources):
                    color_screwed = True
                    break

        if first_spell_turn is None:
            castable_nonland = next(
                (c for c in hand if not c.is_land
                 and can_pay(parse_mana_cost(c.mana_cost), battlefield_sources)),
                None,
            )
            if castable_nonland is not None:
                first_spell_turn = turn

        hand_ids = {c.oracle_id for c in hand}
        for target in combo_targets:
            if combo_turns[target.variant_id] is not None:
                continue
            if target.oracle_ids <= hand_ids:
                pieces = [c for c in hand if c.oracle_id in target.oracle_ids]
                if can_pay(_combined_cost(pieces), battlefield_sources):
                    combo_turns[target.variant_id] = turn

    return mulligans_taken, first_spell_turn, color_screwed, mana_by_turn, combo_turns


def _percentile(values: list[int], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return float(s[int(len(s) * p)])


def run_stress_test(
    library: list[SimCard],
    combo_targets: list[ComboTarget],
    n_simulations: int = 2000,
    seed: int = 42,
) -> StressTestReport:
    rng = random.Random(seed)
    mulligan_counts = []
    first_spell_turns = []
    color_screw_games = 0
    mana_curves = []
    combo_turn_lists: dict[str, list[int]] = {t.variant_id: [] for t in combo_targets}

    for _ in range(n_simulations):
        mulligans, first_spell, screwed, mana_curve, combo_turns = simulate_one_game(
            library, combo_targets, rng
        )
        mulligan_counts.append(mulligans)
        if first_spell is not None:
            first_spell_turns.append(first_spell)
        if screwed:
            color_screw_games += 1
        mana_curves.append(mana_curve)
        for variant_id, turn in combo_turns.items():
            if turn is not None:
                combo_turn_lists[variant_id].append(turn)

    max_len = max((len(c) for c in mana_curves), default=0)
    avg_curve = [
        sum(c[i] for c in mana_curves if i < len(c)) / len(mana_curves)
        for i in range(max_len)
    ]

    combo_stats = []
    for target in combo_targets:
        turns = combo_turn_lists[target.variant_id]
        never_rate = 1 - (len(turns) / n_simulations)
        combo_stats.append(ComboAssemblyStats(
            variant_id=target.variant_id,
            piece_count=target.piece_count,
            is_game_ender=target.is_game_ender,
            is_infinite=target.is_infinite,
            median_turn=_percentile(turns, 0.5),
            p25_turn=_percentile(turns, 0.25),
            p75_turn=_percentile(turns, 0.75),
            never_rate=never_rate,
        ))
    combo_stats.sort(key=lambda s: (s.median_turn is None, s.median_turn or 0))

    return StressTestReport(
        n_simulations=n_simulations,
        mulligan_rate=sum(1 for m in mulligan_counts if m > 0) / n_simulations,
        avg_mulligans_taken=sum(mulligan_counts) / n_simulations,
        first_spell_turn_median=_percentile(first_spell_turns, 0.5),
        first_spell_turn_p75=_percentile(first_spell_turns, 0.75),
        color_screw_game_rate=color_screw_games / n_simulations,
        mana_curve=avg_curve,
        combo_stats=combo_stats,
    )
