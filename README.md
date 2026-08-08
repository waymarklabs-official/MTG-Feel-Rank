# Commander Bracket "Feel" Ranker

Ranks a corpus of Magic: The Gathering Commander decks by two things:

1. **Cost to complete** — dollars to buy what's missing from *your* collection (via a ManaBox export).
2. **Bracket "feel" score** — how the deck would actually play at the table on WotC's 1-5 Commander
   bracket scale, calibrated against real human judgments (self-declared Archidekt brackets, known
   Bracket-2 precons, known Bracket-5 cEDH tournament decks) rather than a naive card-counting heuristic.

Everything runs locally: SQLite for storage, no cloud services, no server beyond the optional local
web UI. The concrete motivating use case was *"find me a Prosper, Tome-Bound deck that'll genuinely
feel like a Bracket 4 at a convention table, for under $500"* — but the tool is general across any
commander.

## Table of contents

- [How it works](#how-it-works)
- [Setup](#setup)
- [First run](#first-run)
- [Using the web UI](#using-the-web-ui)
- [Using the CLI](#using-the-cli)
- [Tuning](#tuning)
- [Project structure](#project-structure)
- [Known limitations](#known-limitations)
- [Data sources](#data-sources)
- [Can this run on GitHub Pages?](#can-this-run-on-github-pages)

## How it works

Five independently-runnable stages, each writing inspectable artifacts to disk/SQLite:

```
ingest → resolve → analyze → calibrate → rank
```

- **Ingest** (Stage 1) — pulls decks from Archidekt (highest priority: real declared brackets, real
  prices, and card objects that already carry Scryfall's `oracle_id`), MTGJSON's precon database
  (ground-truth Bracket 2), EDHTop16 tournament results (ground-truth Bracket 5, cEDH by definition),
  and EDHREC's per-commander average decklists.
- **Resolve** (Stage 2) — every card, from every source, resolves to a Scryfall `oracle_id` — never
  a card name. Names are normalized/matched only as a fallback for the few sources that don't already
  hand us an oracle_id. Decks are fingerprinted (hash of their sorted oracle_id set) and deduped across
  sources.
- **Analyze** (Stage 3) — for each deck: cost to complete against your collection; a rules-based
  bracket *floor* (Game Changers, mass land denial, early-vs-late two-card infinite combos via a
  Monte Carlo mana simulation); combo detection against a local copy of the Commander Spellbook combo
  database; tutor/interaction/ramp/fast-mana density.
- **Calibrate** (Stage 4) — fits an interpretable ordinal model (five stacked logistic regressions,
  one per bracket threshold) predicting bracket from the Stage 3 signals, trained on real declared
  brackets. Reports accuracy, a confusion matrix, and two sanity gates (precons must score 1-2,
  EDHTop16 decks must score 5) on every run.
- **Rank** (Stage 5) — the queryable output: a CSV of the whole corpus, a CLI, and a web UI.

See the module docstrings for the full design rationale — most files explain *why* a given heuristic
or data-source choice was made, not just what the code does.

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
pip install -e .               # registers the `rank` command
```

Drop your ManaBox collection export at the project root as `ManaBox_Collection.csv` (this file is
gitignored — it's your personal collection, not part of the repo).

Dependencies, beyond the standard scientific Python stack (`pandas`, `numpy`, `scikit-learn`, `scipy`):

- `requests` — stdlib `urllib` makes streaming gzip downloads with retries/headers painful.
- `tqdm` — progress bars for long bulk downloads and Monte Carlo simulation loops.
- `flask` — the local web UI's backend. No async/ORM complexity; this is a single-user local tool.

## First run

Everything downloads and builds itself — no manual data collection needed. From the project root:

```bash
python -m bracket_ranker.scryfall      # Scryfall bulk card data (~35k cards)
python -m bracket_ranker.spellbook     # Commander Spellbook combo database (~100k variants)
python -m bracket_ranker.collection    # parse ManaBox_Collection.csv
python run_ingest.py                   # pull the deck corpus (Archidekt/MTGJSON/EDHTop16/EDHREC)
python -m bracket_ranker.resolve       # resolve to oracle_ids, fingerprint, dedupe
python run_analyze.py                  # cost, bracket floor, combos, assembly-turn simulation
python run_calibrate.py                # fit the model, print accuracy + sanity gates
python run_export.py                   # write data/reports/ranking.csv
```

This takes a while the first time (the Spellbook combo pull alone is ~100k records; the Archidekt
sweep fetches thousands of individual deck pages). Every step caches its raw responses to disk, so
re-running any step later is fast and safe — each stage just reads whatever the previous stage last
wrote.

Corpus size is controlled by constants in `bracket_ranker/config.py` (`ARCHIDEKT_TARGET_PER_BRACKET`,
`ARCHIDEKT_TARGET_GENERAL`, `EDHTOP16_TOURNAMENT_TARGET`, `EDHREC_MAX_COMMANDERS`) — the defaults pull
a few thousand decks across ~1,000 commanders in a reasonable amount of time. Raise them and re-run
`run_ingest.py` for a bigger corpus.

Once that's done, either use the CLI directly or launch the web UI:

```bash
python run_webapp.py
```

Then open `http://localhost:5050`.

## Using the web UI

The whole point of the web UI is that you never need to touch a terminal (or come back here) to run
another simulation.

**Explorer tab** — filter the corpus by commander, source, target bracket, max cost, minimum
ownership %, minimum confidence. Click any deck for the full breakdown: cost/ownership, the
rules-based bracket floor and why, detected combos and their assembly-turn simulation, and — for
calibrated decks — exactly which signals drove the score, with per-feature coefficient contributions.
Star or reject decks and leave yourself notes; that review state persists across sessions.

**Deep Pull tab** — the general corpus samples broadly across *all* commanders, so any one commander
usually only has a handful of decks. This pulls up to *N* real decks filtered specifically by
commander name (found via Archidekt's own search, not guessed), appends them to the corpus, and
automatically re-runs Stages 2-5 so the Explorer reflects the new decks by the time the job finishes.
This is the tool for "I'm seriously evaluating commander X" — run it, then go filter in Explorer.

**Pipeline tab** — every stage as a button (including refreshing your collection after a new ManaBox
export, and both optional cross-checks below), each running in the background with a live streaming
log. "Rebuild everything" reruns Stages 2-5 in order, for after a manual DB edit or partial run.

**Dashboard tab** — corpus size and per-source breakdown, the latest calibration run's accuracy and
sanity gates, feature importances, and two independent cross-checks: comparing our score against
Commander Spellbook's own `/estimate-bracket` endpoint on a sample of decks, and comparing our cost
estimate against Archidekt's own price field. Worth checking after any large corpus change — if a
sanity gate flips to FAIL, something's wrong before you trust the numbers.

**Simulate tab** — run the Monte Carlo simulator directly on specific decks, built for testing decks
you're actually building (which may not be in the sampled corpus at all). Import any deck by
Archidekt URL, select it as "Your deck," then either:
- **Stress-test it alone**: mulligan rate, color-screw rate (how often a card in hand is castable by
  raw mana value but not by actual color — the #1 blind spot of a naive mana model), first-castable
  -spell turn, a mana curve, and the full assembly-turn distribution for *every* detected combo (not
  just the top-ranked one).
- **Race it against up to three opponent decks**: each deck's game is simulated completely
  independently — there's no stack, no targeting, no blocking, this is explicitly not an interactive
  Magic simulator — and the results are compared statistically afterward, including a rough
  "disruption chance" derived from the table's aggregate interaction density. Read the full report
  before trusting a number here; it says exactly what is and isn't modeled.

Importing a deck only runs Stage 2 (resolve) automatically, not the full pipeline — enough to make it
immediately simulate-able without forcing a slow corpus-wide recalibration. Run "Rebuild everything"
from the Pipeline tab afterward if you also want the imported deck scored/ranked in Explorer.

### Typical workflow for one commander

1. **Deep Pull tab** → exact commander name (commas/hyphens matter — match the Scryfall oracle name)
   → target ~300 → submit, wait for the job to finish.
2. **Explorer tab** → filter by that commander, your target bracket, your budget → sort by cost.
3. Open each candidate's detail, sanity-check the combo/assembly-turn claims, open the source URL and
   actually read the decklist.
4. Star what's worth chasing, reject what's not, leave notes.

### Typical workflow for stress-testing a deck you're building

1. **Simulate tab** → paste the deck's Archidekt URL into the import form → it's auto-selected as
   "Your deck" once the import job finishes.
2. Click **Run Stress Test** for a solo report (mulligans, color screw, curve-out speed, every
   detected combo's assembly distribution).
3. Optionally search for up to three opponent decks (your own other imports, or anything already in
   the corpus) and click **Run Table Simulation** to see the race comparison.
4. Read the caveats printed with the report before treating any number as gospel — table simulation
   in particular is a statistical estimate of table dynamics, not a play-by-play prediction.

## Using the CLI

Most of what the web UI does is also a standalone script — the pipeline stages, ranking queries, and
commander deep-pull all predate the web layer and still work identically from a terminal. The
Simulate tab's three features (stress test, table simulation, single-deck import) are currently
web-UI-only — they're thin, synchronous wrappers over `bracket_ranker/analyze/stress_test.py`,
`table_sim.py`, and `ingest/archidekt.py:fetch_one_deck`, each independently callable from a Python
shell if you want to script against them directly, but there's no dedicated CLI entry point for them
yet:

```bash
# query the corpus
rank --commander "Prosper, Tome-Bound" --bracket 4 --max-cost 500
rank --explain <deck_fingerprint>          # full per-signal breakdown for one deck

# targeted deep-pull for one commander
python pull_commander.py "Prosper, Tome-Bound" --target 300

# individual stages (each also runnable as `python -m bracket_ranker.<module>`)
python run_ingest.py [--skip archidekt,edhtop16,...]
python -m bracket_ranker.resolve
python run_analyze.py
python run_calibrate.py
python run_export.py

# optional cross-checks
python -m bracket_ranker.analyze.price_crosscheck
python -m bracket_ranker.calibrate.spellbook_crosscheck
```

## Tuning

Everything below lives in `bracket_ranker/config.py` or `bracket_ranker/calibrate/fit.py`, documented
in place with the reasoning behind the default:

- `EARLY_COMBO_TURN_CUTOFF` — the turn threshold separating an "early" (floor-4-forcing) two-card
  infinite combo from a "late" one, per Bracket 3's rules.
- `GAME_CHANGERS_BRACKET_3_MAX` — how many Game Changers Bracket 3 tolerates before the floor jumps to 4.
- `MONTE_CARLO_SIMULATIONS` — simulations per combo assembly-turn check; trade run time for precision.
- `bracket_ranker/analyze/card_tags.py` — the regex heuristics for tutor/interaction/ramp/fast-mana/
  mass-land-denial classification. Deliberately crude (the spec's own instruction), documented per
  pattern, easy to extend.
- `bracket_ranker/analyze/mana_model/` — the Monte Carlo mana model is behind a swappable interface
  (`base.py`). `v1_naive.py` compares total mana value only; `v2_color_aware.py` (the current
  default, used by Stage 3 and the Simulate tab alike) actually checks colored castability via
  `mana_cost.py`'s greedy pip-matcher. Write a new module implementing the same interface to try a
  better one.
- `bracket_ranker/analyze/mana_cost.py` — the mana-cost parser and castability checker. Documents its
  own simplifications (X treated as 0, Phyrexian mana requires its color, etc.) inline.
- `bracket_ranker/analyze/stress_test.py` — `COLOR_SCREW_CHECK_TURNS` (how many early turns count
  toward the color-screw rate) and `TURN_HORIZON`.
- `bracket_ranker/analyze/table_sim.py` — `DISRUPTION_PER_INTERACTION_POINT` and
  `MAX_DISRUPTION_CHANCE`, the crude linear model behind the table simulation's "disruption chance."
  Explicitly a documented guess at a reasonable slope, not a fitted parameter.
- `bracket_ranker/calibrate/combos.py` — combo relevance-scoring weights (piece count, game-ending
  bonus, tutor-support bonus).

## Project structure

```
bracket_ranker/          the library
  config.py               all tunable constants, with sourcing/reasoning in comments
  db.py                    SQLite schema
  scryfall.py              bulk card data + oracle_id name index
  spellbook.py             Commander Spellbook combo database client
  collection.py            ManaBox CSV parser
  resolve.py               Stage 2: oracle_id resolution, fingerprinting, dedup
  ingest/                  Stage 1 source adapters (one file per source)
  analyze/                 Stage 3: cost, bracket floor, combos, mana model, feel signals
    mana_cost.py             mana-cost parsing + color-aware castability
    mana_model/              swappable mana models (v1_naive, v2_color_aware)
    stress_test.py           on-demand deep solo simulation (mulligans, color screw, curve, combos)
    table_sim.py             on-demand 4-player parallel-race simulation
    deck_library.py          shared "build a deck's simulation inputs" logic
  calibrate/               Stage 4: labels, model fitting, both cross-checks
  rank/                    Stage 5: query CLI, explanation, output CSV, limitations banner
webapp/                  the local web UI (Flask backend + vanilla JS/CSS frontend, no build step)
  app.py                    routes
  jobs.py                   background job runner
  actions.py                the operations jobs can run
  static/                   index.html / app.js / style.css
run_ingest.py, run_analyze.py, run_calibrate.py, run_export.py, pull_commander.py, rank.py
                          CLI entry points, one per stage
data/                     all generated (gitignored): cache/, db/, reports/
```

## Known limitations

Printed alongside every ranking result, and worth internalizing before trusting a score:

1. **No opponent is modeled.** Goldfishing can't account for a table holding up interaction.
2. **Non-combo decks are scored unfairly.** A stax/grindy value deck returns no combo signal and may
   rank below a mediocre combo pile. These are flagged `low_confidence` for this reason — don't read
   that flag as "worse deck," read it as "the model has less to go on."
3. **Combo false positives are common.** Spellbook reports every technically-present interaction, not
   just intended ones. Relevance scoring mitigates but doesn't eliminate this — eyeball the decklist.
4. **The mana model is crude.** Color requirements are ignored entirely; casting sequencing is
   simplified. See `mana_model_version` on each deck.
5. **The corpus is a biased sample, not a census.** It skews toward whatever Archidekt users happen to
   post and self-declare. Check the per-source breakdown before treating a ranking as exhaustive.
6. **Training labels are self-reported and skew low** (sandbagging). Measured and corrected — see the
   label-conflict rate on the Dashboard — but the correction is a floor-clamp, not ground truth.

## Data sources

- [Scryfall](https://scryfall.com) — card data, prices (estimates, not live quotes), the Game Changers list.
- [Archidekt](https://archidekt.com) — deck corpus, user-declared brackets, prices.
- [MTGJSON](https://mtgjson.com) — preconstructed deck lists.
- [EDHTop16](https://edhtop16.com) — cEDH tournament results.
- [EDHREC](https://edhrec.com) — per-commander average decklists.
- [Commander Spellbook](https://commanderspellbook.com) — the combo database.

All accessed via public, unauthenticated APIs at reasonable request rates with a descriptive
User-Agent. None of this data is redistributed here — the repo ships code, not the scraped corpus
(see `.gitignore`); running the pipeline rebuilds it from each source directly.

## Can this run on GitHub Pages?

No, and there isn't a version of this app for which the answer would be yes. GitHub Pages only serves
static files (HTML/CSS/JS) — it has no ability to run a Python process. This project's web UI is a
Flask *backend* fronting a SQLite database, background scraping jobs, and a scikit-learn model; none
of that has a static equivalent. The frontend alone, pointed at nothing, would just be a page full of
failed API calls.

If you want this reachable outside your own machine, that means actually hosting the Flask app
somewhere that runs Python (a small VM, Render/Fly.io/Railway, etc.) — a meaningfully different
project (auth, persistence across restarts, everyone's collection instead of just yours) rather than
a deployment-target tweak. Running it locally via `python run_webapp.py` is the intended shape of this
tool.
