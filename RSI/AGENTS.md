# AGENTS.md — RSI

Read `RSI/README.md`, `RSI/docs/README.md`, and `RSI/docs/permissions.md` before changing anything here.

- `RSI/evaluator/` is frozen: agents call its CLI, never edit it. Evaluator changes are human-only.
- Only `operators/*/workspace/` is writable by inner-loop agents; see `RSI/docs/permissions.md`.
- Keep the canonical vocabulary from `RSI/docs/vocabulary.md`; do not name a vendor tool or metric as the thing to do.
- A skill or playbook change must pass its own regression cases and `cd RSI && python3 tests/run_all.py`.
- Run `cd RSI && python3 tests/run_all.py` before claiming the RSI checks pass.
- Record a durable design decision in `RSI/docs/` or `docs/decisions/`; mechanical edits are exempt.
- The repository-wide contribution rules in `CONTRIBUTING.md` and `AGENTS.md` still apply.
