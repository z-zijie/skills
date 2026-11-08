# Decision: adopt the agent-collaboration contribution discipline

## Problem

External contributors' agents can open PRs that mix unrelated changes, carry no Issue context, and present no scope or evidence. Each such PR costs a review round to discover the intent and ask for a split; the owner's review bandwidth is the bottleneck.

## Decision

Adopt the discipline described in [`docs/agent-contribution-discipline.md`](../agent-contribution-discipline.md):

- External contributions start with an accepted Issue; a PR without one is closed.
- `CONTRIBUTING.md` states accepted and rejected changes, evidence requirements, the AI participation declaration, protected paths, and close rules.
- The repo has `AGENTS.md` (root) plus `RSI/AGENTS.md` and `skills/AGENTS.md`; `CLAUDE.md` is a symlink.
- Every PR fills `.github/PULL_REQUEST_TEMPLATE.md` (Motivation / Scope / Changes / Testing / AI Usage / Checklist).
- `PR policy` runs from the default branch, reads only API data, and checks sections, Issue references (external authors), the AI declaration, Scope, and protected paths.
- `RSI checks / checks passed` is an aggregate required check; RSI paths run the full scaffold checks, other PRs succeed quickly.
- `CODEOWNERS` requires owner review for policy, docs, skills, and the frozen RSI surfaces.
- `docs/review-checklist.md` and `docs/decisions/` provide the review entry point and durable decision records.

Labels and branch protection are repository settings; [`docs/maintainer-setup.md`](../maintainer-setup.md) records the manual steps.

## Alternatives considered

- **Keep the current open-PR flow and rely on review.** Rejected: the review cost is the problem the change exists to remove.
- **Stop accepting external PRs entirely.** Rejected for now: an Issue-first path keeps useful contributions possible; switching to closed external PRs remains a one-line `CONTRIBUTING.md` change if the load stays high.
- **Copy deepseek-harness's weighted approval, Project lifecycle and i18n gates.** Rejected: they need multiple maintainers and a larger doc surface; for a single-maintainer repo the maintenance cost exceeds the benefit.

## Consequences

- External agents must open an Issue first and fill a structured PR description.
- The owner's own PRs still fill the template, but do not need an Issue reference and are not blocked by the protected-path check.
- Two required checks appear on every PR once branch protection is enabled; both must be kept green.
- The policy is machine-enforced, so it must be changed through a PR (from the default branch) rather than by editing the workflow of the PR being reviewed.
- The `skills/` layer still has no automatic content check; review and `skills/AGENTS.md` cover it for now.
