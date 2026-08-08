"""Parse Scryfall mana_cost strings and check castability against a pool of
untapped mana sources -- the piece that makes the v2 mana model actually
color-aware instead of just comparing totals.

Scryfall's mana_cost format ("{2}{U}{U}", "{W/U}", "{X}{R}{R}"...) is
well-documented and stable; this parser handles the common symbol types.
Deliberately crude on a few edge cases, each documented at the point it's
simplified, per the project's "crude is fine, document it" rule:
  - X is treated as 0 (X spells are rarely cast for X=0 in practice, but
    modeling "how much mana do you have left over to spend on X" is a much
    bigger simulation than this project's scope -- this makes X-spells look
    castable earlier than they'd really be worth casting).
  - Phyrexian mana ({W/P}) is modeled as requiring its color, ignoring the
    "or 2 life" alternative -- pessimistic for decks that lean on paying
    life instead of casting on-color.
  - Snow mana ({S}) is treated as 1 generic, ignoring the snow-permanent
    requirement.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_SYMBOL_RE = re.compile(r"\{([^}]+)\}")
_COLORS = {"W", "U", "B", "R", "G"}


@dataclass
class ManaCost:
    generic: int = 0
    colorless_pips: int = 0             # {C}: needs a source that produces colorless specifically
    hybrid_choices: list[list[str]] = field(default_factory=list)  # each: colors that satisfy this one pip


def parse_mana_cost(mana_cost: str | None) -> ManaCost:
    cost = ManaCost()
    if not mana_cost:
        return cost
    for symbol in _SYMBOL_RE.findall(mana_cost):
        symbol = symbol.upper()
        if symbol.isdigit():
            cost.generic += int(symbol)
        elif symbol == "X":
            pass  # documented simplification: X treated as 0
        elif symbol == "C":
            cost.colorless_pips += 1
        elif symbol == "S":
            cost.generic += 1
        elif symbol in _COLORS:
            cost.hybrid_choices.append([symbol])
        elif "/" in symbol:
            parts = [p for p in symbol.split("/") if p in _COLORS]
            if parts:
                cost.hybrid_choices.append(parts)  # {W/U} -> either; {2/W} -> just W (generic part folded in below)
            if any(p.isdigit() for p in symbol.split("/")):
                cost.generic += 0  # the "pay 2 generic instead" alternative isn't modeled; treat as needing the color
        # unrecognized symbols (rare/funny-set-only) are silently ignored -- crude, not a guess
    return cost


def can_pay(cost: ManaCost, available_colors: list[frozenset[str]]) -> bool:
    """available_colors: one entry per untapped mana source, each the set of
    colors (possibly {'C'}) it can produce. Greedy bipartite matching: the
    scarcest pip requirement gets first pick of compatible sources. This is
    a heuristic, not an exhaustive constraint solver -- it can theoretically
    pick wrong on an adversarial ordering, but matches how a real player
    reasons about their mana and is correct on the vast majority of hands.
    """
    remaining = list(available_colors)
    pip_requirements = list(cost.hybrid_choices) + [["C"]] * cost.colorless_pips

    # Scarcest requirement (fewest compatible sources) matched first.
    pip_requirements.sort(key=lambda choices: sum(
        1 for src in remaining if src & set(choices)
    ))
    for choices in pip_requirements:
        match_idx = next(
            (i for i, src in enumerate(remaining) if src & set(choices)), None
        )
        if match_idx is None:
            return False
        remaining.pop(match_idx)

    return len(remaining) >= cost.generic
