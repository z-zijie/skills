# AGENTS.md

This repository contains installable Agent Skills (`skills/`), the RSI self-improving kernel-optimization scaffold (`RSI/`), and the repository policy (`.github/`, `CONTRIBUTING.md`). Read `CONTRIBUTING.md` before changing anything. This file holds the standing orders an agent needs in every session; deeper rules live beside the code.

## Before you start

- External contributions work only on an accepted Issue. Changes authored by the repository owner (or the owner's automation account) may go without one, but still declare scope.
- One PR does one thing. Split unrelated fixes, refactors, formatting, dependency bumps, and renames.
- Declare the expected change scope in the PR: paths/directories, and what is explicitly out of scope.
- Do not expand scope after review starts. If a fix needs a wider change, stop and comment on the Issue.

## Repository layout

- `skills/<name>/SKILL.md` — installable agent skills. Follow `skills/writing-for-agents/SKILL.md` and `skills/AGENTS.md`.
- `RSI/` — self-improving kernel-optimization scaffold. Start at `RSI/README.md` and `RSI/docs/README.md`; follow `RSI/AGENTS.md`.
- `docs/` — design record (`agent-contribution-discipline.md`), review checklist, decision records.
- `.github/` — CI, templates, and repository policy. Protected: do not change it in a PR that is trying to pass it.

## Commands

- RSI checks: `cd RSI && python3 tests/run_all.py`
- Reference-backend demo: `cd RSI && python3 tools/demo.py --no-tests`
- Local Markdown checks: verify links and fenced blocks.
- PR checks: `gh pr checks` after pushing.

## Change discipline

- Keep the change face equal to the review face: only lines the stated behavior change needs.
- Mechanical or local edits need no design note; a durable design decision goes to `docs/decisions/`.
- State only behavior the diff guarantees and the tests cover.
- Comments state contracts, not reasoning transcripts.

## Evidence

- Run the smallest check that would fail for this regression; report only commands actually run.
- Behavior change: a test that fails before and passes after.
- Performance: before/after numbers with measurement conditions.
- Documentation: link and render check.
- CI owns the exhaustive matrix; do not run the full suite by default.

## Safety and protected paths

- Never modify `RSI/evaluator/` or `RSI/schemas/` from an agent PR; those changes are human-only.
- Never commit secrets or credentials.
- Never change `.github/`, `AGENTS.md`, `CONTRIBUTING.md`, or `CLAUDE.md` to make your own PR pass. Such a change needs a separate Issue and human approval.
- Do not merge your own PR; the repository owner merges.

## Pull requests

- Fill every section of `.github/PULL_REQUEST_TEMPLATE.md`; an incomplete description is a blocked PR.
- Declare AI usage honestly.
- Reply to every review thread with a fix or a reason; do not resolve threads you did not address.
