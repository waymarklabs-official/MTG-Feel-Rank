"""Interface every mana model implements, for Stage 3.4's Monte Carlo
assembly-turn simulation.

The spec calls this out explicitly as "the piece most likely to be wrong":
a swappable interface so a better model can replace v1_naive without
touching anything that calls it (combos.py, run_analyze.py). Every
implementation is keyed by VERSION so deck_signals records which model
produced a given result -- comparing versions is a first-class operation,
not an afterthought.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class SimCard:
    oracle_id: str
    cmc: float
    is_land: bool
    is_ramp: bool
    is_fast_mana: bool


@dataclass
class AssemblyResult:
    median_turn: float | None       # None if the combo never assembled in any simulation
    p25_turn: float | None
    never_assembled_fraction: float  # share of simulations that hit the horizon unassembled


class ManaModel(Protocol):
    VERSION: str

    def simulate_assembly_turn(
        self,
        library: list[SimCard],
        target_oracle_ids: set[str],
        n_simulations: int,
    ) -> AssemblyResult:
        """library excludes the commander (command zone, not the library).
        target_oracle_ids are the combo pieces to track; each simulation
        reports the first turn all of them are simultaneously in hand and
        (per the model's own mana accounting) castable.
        """
        ...
