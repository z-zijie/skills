# Benchmark splits

- `dev.txt` — the operators the outer loop can see and tune against.
- `holdout.txt` — the held-out split. Invisible to agents, readable only by the replay judge.

The primary system metric is measured **on holdout**: does the speedup gained per experiment rise
with each scaffold version? Watching dev instead measures how well the scaffold overfits.

Watch the gap between the two. If it keeps widening, pause merging and find the cause before
accepting another change.
