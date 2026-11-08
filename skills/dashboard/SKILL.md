---
name: dashboard
description: Start a local workbench that shows every coding-agent session across machines — Claude Code, Codex, and pi — as session cards with runtime, context usage, subagents, requests and TPS, plus a machine→agent→session topology and a session inspector. Use when the user asks for a dashboard, a global or fleet view, or a live task board across machines ("show all my agents and sessions", "what is running on my machines", "全局工作台"), or asks how one specific session is doing.
---

# dashboard

A read-only local workbench over the session logs these agents already write. Machines → agents → sessions, with live-feeling cards, charts, and topology. No daemons, no Python dependencies, no external requests.

Run the commands from this skill directory (paths below are relative to it).

## Launch

```bash
python3 scripts/dashboard.py                               # this machine, refresh every 5s
python3 scripts/dashboard.py --hosts work-machine,server2  # add remote machines over SSH
python3 scripts/dashboard.py --demo                        # demo data, no collection
python3 scripts/dashboard.py --snapshot state.json         # freeze a snapshot file
python3 scripts/dashboard.py --port 8791 --no-open         # another port, do not open a browser
```

Serves `http://127.0.0.1:8787/` and opens it unless `--no-open` is passed. Completion criterion: `/api/health` returns `{"ok": true, ...}` and either the cards render (live) or demo cards do; Ctrl-C stops it.

To check by hand:

```bash
python3 scripts/dashboard.py --demo --no-open &
curl -s http://127.0.0.1:8787/api/health
curl -s http://127.0.0.1:8787/api/state | head -c 200
```

## Data modes

| Mode | Flag | Source |
|---|---|---|
| live (default) | — | `collect.py` scans the local stores on start and every `--refresh` seconds |
| demo | `--demo` | `web/data/demo.json` |
| snapshot | `--snapshot FILE` | any contract JSON file |

Live mode falls back to demo data with a `warning` field when no sessions are found or collection fails; the page never 500s and static files keep serving.

## What is collected

- Claude Code: `~/.claude/projects/**/*.jsonl`
- Codex: `~/.codex/sessions/**/rollout-*.jsonl` (titles from `~/.codex/session_index.jsonl`)
- pi: `~/.pi/agent/sessions/**/*.jsonl`

Remote hosts need SSH access and `python3` only: `--hosts` pipes the collector over `ssh -o BatchMode=yes <host> "python3 - --remote"`. Unreachable hosts stay in the view as offline machines with a `last_seen` from `~/.cache/dashboard/hosts.json`.

Titles come from the agent's own session title (`ai-title`, Codex `thread_name`) or the project directory name. Message bodies never enter the snapshot; step and event labels are tool names plus a file basename. `--no-titles` anonymises titles and drops `project`, `cwd`, and `branch`. Statuses are heuristics from log timestamps.

## Options

`--demo` · `--snapshot FILE` · `--hosts a,b` · `--port N` (default 8787, loopback only) · `--refresh N` · `--no-open` · `--no-titles` · `--quiet`.

## Verify

```bash
python3 scripts/selftest.py          # fixtures parse, contract schema, demo data, static refs, HTTP smoke
python3 -m py_compile scripts/*.py   # Python 3.9 syntax
```

Completion criterion: `selftest.py` exits 0. It needs no network, SSH, or browser; pass `--skip-assets` only while the web UI does not exist yet.

## Read next

- [`references/data-contract.md`](references/data-contract.md) — snapshot schema, derivation rules, collector limits, privacy.
- [`references/design-language.md`](references/design-language.md) — palette, type, motion, chart and layout rules for `web/`.
