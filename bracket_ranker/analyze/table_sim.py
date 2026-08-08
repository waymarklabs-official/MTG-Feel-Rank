"""Table simulation: run the solo stress test independently for your deck
plus up to 3 opponent decks, then compare timing across all of them as a
crude "race" -- who reaches their fastest combo first, and how often your
deck's attempt would statistically survive the table's aggregate
interaction density.

Read this before trusting the numbers: this is explicitly NOT an
interactive Magic simulator. There is no stack, no targeting, no real
spell resolution, no blocking, no life totals from combat. Each deck's
game is simulated completely independently -- nobody's draws or plays
affect anybody else's -- and the results are paired up statistically after
the fact for comparison; pairing "game 7 of your deck" with "game 7 of
their deck" doesn't mean those two games happened at the same table, only
that averaging over many such pairings estimates the joint outcome
distribution correctly (a standard trick for combining independent
simulations, valid here specifically because the two simulations really
are independent of each other).

The "disruption" mechanic is a single aggregate probability derived from
the table's interaction_count signal (see feel_signals.py), applied as a
per-attempt coin flip. It says nothing about WHEN an opponent would
interact or WHICH piece they'd target -- only a rough "how likely is SOME
interaction to happen at all" estimate. Treat every number here as a
statistical estimate of table dynamics, not a play-by-play prediction.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from bracket_ranker.analyze.mana_model.base import SimCard
from bracket_ranker.analyze.stress_test import ComboTarget, simulate_one_game

# Crude linear model: each point of the table's average opponent
# interaction density trims a fixed chance off your combo attempt
# surviving to resolution, capped so it's never a certainty either way.
# Tunable -- there's no principled way to derive this constant, it's a
# documented guess at a reasonable slope, not a fitted parameter.
DISRUPTION_PER_INTERACTION_POINT = 0.03
MAX_DISRUPTION_CHANCE = 0.6


@dataclass
class TablePlayer:
    fingerprint: str
    commander_name: str
    library: list[SimCard]
    combo_targets: list[ComboTarget]
    interaction_count: int = 0  # unused for you (index 0); used for opponents' avg disruption


@dataclass
class PlayerSimResult:
    fingerprint: str
    commander_name: str
    best_turns: list[int | None] = field(default_factory=list)  # per game, this deck's fastest assembled combo turn


@dataclass
class TableSimReport:
    n_simulations: int
    players: list[PlayerSimResult]
    win_race_rate: dict[str, float]   # fingerprint -> fraction of paired games this deck was fastest
    your_raw_combo_rate: float        # fraction of your games where ANY combo assembled by the turn horizon
    your_disruption_chance: float     # per-attempt, derived from opponents' avg interaction density
    your_adjusted_combo_rate: float   # raw rate, discounted by a per-attempt disruption coin flip


def _best_turn_per_game(
    library: list[SimCard], combo_targets: list[ComboTarget], n_simulations: int, seed: int
) -> list[int | None]:
    rng = random.Random(seed)
    results = []
    for _ in range(n_simulations):
        _, _, _, _, combo_turns = simulate_one_game(library, combo_targets, rng)
        turns = [t for t in combo_turns.values() if t is not None]
        results.append(min(turns) if turns else None)
    return results


def run_table_simulation(players: list[TablePlayer], n_simulations: int = 1500) -> TableSimReport:
    if len(players) < 2:
        raise ValueError("need your deck plus at least one opponent")

    player_results = [
        PlayerSimResult(
            fingerprint=p.fingerprint,
            commander_name=p.commander_name,
            best_turns=_best_turn_per_game(p.library, p.combo_targets, n_simulations, seed=42 + i),
        )
        for i, p in enumerate(players)
    ]

    win_counts = {p.fingerprint: 0 for p in player_results}
    for game_idx in range(n_simulations):
        candidates = [
            (p.fingerprint, p.best_turns[game_idx])
            for p in player_results if p.best_turns[game_idx] is not None
        ]
        if candidates:
            winner_fp, _ = min(candidates, key=lambda c: c[1])
            win_counts[winner_fp] += 1
    win_race_rate = {fp: count / n_simulations for fp, count in win_counts.items()}

    your_turns = player_results[0].best_turns
    your_raw_rate = sum(1 for t in your_turns if t is not None) / n_simulations

    opponent_interaction = [p.interaction_count for p in players[1:]]
    avg_opponent_interaction = sum(opponent_interaction) / len(opponent_interaction) if opponent_interaction else 0
    disruption_chance = min(MAX_DISRUPTION_CHANCE, DISRUPTION_PER_INTERACTION_POINT * avg_opponent_interaction)

    rng = random.Random(999)
    survived = sum(1 for t in your_turns if t is not None and rng.random() >= disruption_chance)
    your_adjusted_rate = survived / n_simulations

    return TableSimReport(
        n_simulations=n_simulations,
        players=player_results,
        win_race_rate=win_race_rate,
        your_raw_combo_rate=your_raw_rate,
        your_disruption_chance=disruption_chance,
        your_adjusted_combo_rate=your_adjusted_rate,
    )
