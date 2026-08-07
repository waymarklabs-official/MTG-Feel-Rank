"""The known-limitations banner the spec requires on every result: printed
by both the CSV export and the interactive `rank` CLI. Centralized so it
can't drift between the two.
"""

KNOWN_LIMITATIONS = """\
Known limitations of this ranking (read before trusting a score):
  0. All dollar figures (usd_to_complete and every price behind it) are
     ESTIMATES from Scryfall's daily bulk price snapshot, not live quotes.
     Scryfall itself warns these can be stale by up to 24h -- fine for
     ranking decks against each other, not for checking out a cart.
  1. No opponent is modeled. Goldfishing can't account for a table holding
     up interaction -- two decks with identical assembly curves play very
     differently depending on interaction density.
  2. Non-combo decks are scored unfairly. A stax/grindy value deck returns
     no combo and no assembly turn, and may rank below a mediocre combo
     pile. Zero-combo decks are flagged low_confidence for this reason.
  3. Combo false positives are common. Spellbook reports every technically
     present interaction, not just intended ones -- relevance scoring
     mitigates but does not eliminate this.
  4. The mana model is crude (see mana_model_version on each deck) --
     color requirements are ignored entirely and sequencing is simplified.
  5. The corpus is a biased sample, not a census. See the per-source
     breakdown printed alongside these results.
  6. Training labels are self-reported and skew low (sandbagging). See the
     measured label-conflict rate printed by Stage 4.
"""


def print_limitations() -> None:
    print(KNOWN_LIMITATIONS)
