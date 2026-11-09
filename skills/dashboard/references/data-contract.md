# Snapshot data contract

Version: `schema_version: 1`. `scripts/collect.py` produces this object, `scripts/dashboard.py` returns it from `/api/state`, `web/` consumes it. Field names and meanings below are frozen; implementations may add **optional** fields, but must not change the meaning of an existing one. The UI tolerates every optional field being absent.

```jsonc
{
  "schema_version": 1,
  "mode": "live" | "demo" | "snapshot",   // live = collected; demo = web/data/demo.json; snapshot = fixed file
  "generated_at": "2026-11-08T18:00:00.000Z",
  "refresh_seconds": 5,                    // optional: suggested poll interval
  "aggregate": { ... },                    // optional: fleet totals, see below
  "machines": [ Machine, ... ],
  "sessions": [ Session, ... ]             // flat; join via machine_id / agent
}
```

Optional top-level extras a producer may add: `warning` (string, e.g. "no live sessions found; showing demo data"), `error` (string, health/degraded state). Unknown keys are ignored.

## Machine

```jsonc
{
  "id": "mac-mini",
  "name": "mac-mini",
  "host": "Macmini.ts.net",               // display hostname / ssh alias
  "platform": "darwin",                  // darwin | linux | win32 | other
  "arch": "arm64",
  "online": true,
  "last_seen": "2026-11-08T17:59:58.000Z", // required when online=false (may be null if never seen)
  "agents": [ Agent, ... ],
  "error": "ssh: ..."                    // optional, only when online=false
}
```

An unreachable SSH host stays in `machines` with `online: false`, a `last_seen` from the collector's host cache, and its last-known agents with `status: "offline"`. It contributes no sessions.

## Agent

```jsonc
{
  "id": "claude-code",                   // claude-code | codex | pi | dsh | other
  "name": "Claude Code",
  "version": "2.1.273",                  // optional CLI version
  "status": "active",                    // active = has running sessions; idle; offline
  "sessions_active": 2,                  // sessions with status "running"
  "requests": 620,                       // sum over collected sessions
  "tokens": 1480000,                     // input+output+cache_read+cache_write
  "tps": 64.2,                           // sum of running sessions' displayed tps
  "model": "claude-sonnet-4-5"           // optional, newest session's model
}
```

## Session (one task card)

Required: `id`, `machine_id`, `agent`, `title`, `status`. The rest is optional but the collector tries to provide it; the UI renders a card when the required five exist.

```jsonc
{
  "id": "claude-code:mac-mini:8e0289db-2a7e-46b2-8115-0f88f1a89c89",
  "machine_id": "mac-mini",
  "agent": "claude-code",
  "agent_name": "Claude Code",
  "title": "Tighten auth redirect handling",   // agent-generated title or project dir; never message text
  "project": "atlas-web",
  "cwd": "~/code/atlas-web",                   // home shortened with ~
  "branch": "fix/auth-redirect",
  "model": "claude-sonnet-4-5",
  "status": "running",                         // running | waiting | done | failed | idle
  "started_at": "2026-11-08T17:20:00.000Z",
  "updated_at": "2026-11-08T17:59:58.000Z",
  "ended_at": null,
  "duration_ms": 492000,
  "requests": 138,                             // assistant/API requests
  "tokens": {"input": 322000, "output": 48122, "cache_read": 2104000, "cache_write": 180000},
  "context": {"used": 87400, "limit": 200000}, // used = sum(context_parts); limit null when unknown
  "subagents": {"count": 3, "active": 1},
  "tps": 38.4,                           // == last tps_series bucket (displayed value)
  "cost_usd": 2.41,
  "latency_ms": 2100,
  "tps_series": [[1760000000000, 38.1], ...],  // 90 × [epoch_ms, tps], 1s buckets, zero filled; last bucket == tps
  "context_parts": [{"key": "input", "label": "Input", "tokens": 12400}, ...], // per-request breakdown
  "subagent_tree": {"id": "root", "label": "main", "tokens": 48122, "status": "running", "children": []},
  "steps": [{"label": "read auth.ts", "kind": "tool", "start_ms": 0, "dur_ms": 1200}],
  "events": [{"at": "2026-11-08T17:59:41.000Z", "kind": "tool", "text": "Read auth.ts"}],
  "agent_version": "3"                         // optional extra used to fill Agent.version
}
```

Step/event `kind`: `think` | `tool` | `message` | `subagent`. `steps` is capped at 12, `events` at 4.

`context_parts` is the composition of the context after the **last request**: the request's prompt tokens plus the tokens it produced, one part per source. Collectors should provide it (`Input`, `Cache read`, `Cache write`, `Output` for real logs; demo data may use `System`/`Tools`/`Messages`/`Output`). The parts always sum to `context.used`. The UI prefers this field for a composition chart and falls back to a single used/limit capacity reading when it is absent — a composition must never be derived from cumulative `tokens`, which are session totals and can be orders of magnitude larger than the live context. `subagent_tree` is demo data only; `collect.py` omits it because it cannot measure it.

## Derivation rules

- `tps`: the displayed output rate — **exactly `tps_series[-1][1]`**, the most recent 1 s bucket of observed output tokens (one number, one clock). `tps_series` holds the last 90 one-second output-token buckets, zero filled, ending at the last second that produced output (falling back to the last log record when the log has no output timestamps). A session with no generation in that window reports `0.0`, which the UI renders as `—`. The UI must print this value next to the sparkline; peaks and means belong to the series (inspector chart), never to `tps`.

  Caveat (documented limitation): `tps` is the most recent *output-producing* second, not a smoothed rate. A completed assistant message may attribute its whole output to one bucket, so a live value can spike while a session between generation bursts can read `0.0`. For sessions that are not actively generating, the UI may render `—` instead of the stored last-observed value; the sparkline still shows the last-observed window. Whatever it renders, the UI must never show a TPS number that disagrees with the plotted series — the number and the series' right edge are the same value.
- `context.used`: tokens in context after the last request, always `sum(context_parts)`. Per agent it is the last request's prompt plus its output: Claude Code `input_tokens + cache_read_input_tokens + cache_creation_input_tokens + output_tokens`; pi `input + cacheRead + cacheWrite + output`; Codex `last_token_usage.total_tokens` (the last turn's `input + output`, already inclusive) or the delta between two cumulative `total_token_usage` rows when the log has no per-turn row. With no recoverable per-request usage, `context_parts` is omitted and `used` is 0.
- `context.limit`: measured window where the log states it (Codex `model_context_window`), otherwise a model-family estimate (`MODEL_WINDOWS` in `collect.py`), otherwise `null`. Claude Code falls back to 200000. A family estimate contradicted by the observed context (`used > limit`) is reported as `null`: a request cannot exceed its window, so the estimate was wrong and a >100% gauge would be a lie.
- `status` heuristic: explicit error end → `failed`; explicit end → `done`; activity < 120s → `running`; < 30min → `waiting`; no activity > 6h → `done`; otherwise `idle`.
- `requests`: unique assistant message ids (Claude Code), assistant message count (Codex, pi).
- `tokens`: Claude `input_tokens` / `output_tokens` / `cache_read_input_tokens` / `cache_creation_input_tokens`; Codex cumulative `input_tokens - cached_input_tokens` / `output_tokens` / `cached_input_tokens` / 0; pi `input` / `output` / `cacheRead` / `cacheWrite`.
- `subagents`: distinct Claude Code sidechain `agentId`s, or the number of `Agent`/`Task` tool calls when the log records no sidechains; `active` counts sidechains seen in the last 120s. Codex and pi report 0.
- `duration_ms`: `ended_at` (explicit end) or `updated_at` minus `started_at`.
- Times are ISO-8601 UTC strings. `tps_series` uses epoch milliseconds.

## `aggregate` (optional, matches what the UI header needs)

```jsonc
{
  "machines_online": 2,
  "agents": 6,
  "sessions_active": 4,      // sessions with status "running"
  "sessions_total": 10,
  "requests_total": 1080,
  "tps": 190.0,              // sum of running sessions' displayed tps
  "tps_series": [[1760000000000, 188.8], ...]  // 90 × [epoch_ms, tps], sum of running sessions by timestamp
}
```

A UI may compute any of these itself; the collector provides them so the header has one source of truth.

## What the collector reads

| Agent | Path | Signals |
|---|---|---|
| Claude Code | `~/.claude/projects/**/*.jsonl` | `ai-title`, `message.usage` (deduped by `message.id`), `isSidechain`/`agentId`, `cwd`, `gitBranch`, `version`, `cost-state.totalCostUSD`, `tool_use` names |
| Codex | `~/.codex/sessions/**/rollout-*.jsonl` plus `~/.codex/session_index.jsonl` | `session_meta`, `turn_context.model`, `token_count.info`, `task_started`/`task_complete`/`turn_aborted`, `response_item` |
| pi | `~/.pi/agent/sessions/**/*.jsonl` | `session`, `model_change`, `message.usage`/`cost`/`durationMs`, `toolCall` |

Limits: the newest `--max-sessions` (default 20) files per agent by mtime, the first 128 KB and last 512 KB of each file, and whole lines only — records over 2 MB and lines cut by the read window are dropped rather than parsed. DSH session logs are zstd-compressed, so a stdlib-only collector cannot read them; they are out of scope.

`--hosts a,b` runs the same file on each host with `ssh -o BatchMode=yes host "python3 - --remote"` (source piped on stdin, compressed) and merges the returned snapshot. Remote ids are rewritten to the ssh alias so they stay stable. Host metadata (`platform`, `arch`, `last_seen`, last-known agents) is cached in `~/.cache/dashboard/hosts.json` (override with `DASHBOARD_CACHE_DIR`) so an unreachable host still renders as offline with a last_seen.

## Privacy

The collector never puts message text in the snapshot. Titles come from the agent's own title (`ai-title`, Codex `thread_name`) or the project directory name, falling back to `<Agent> session`; step/event labels are tool names plus a file basename. `--no-titles` drops titles to `<Agent> session` and omits `project`, `cwd`, and `branch`. Remote collection transfers only this metadata.

## Demo data requirement

`web/data/demo.json` must be a valid snapshot (`mode: "demo"`) with ≥3 machines (≥1 offline), ≥5 sessions covering running/waiting/done/failed, and every session carrying `tps_series`, `context`, `tokens`, `requests`, `duration_ms`, `subagents`; ≥3 need `subagent_tree`, ≥3 need `steps` or `context_parts`. Every session's `tps` must equal its last `tps_series` bucket, and `aggregate.tps` must equal the sum of running sessions' `tps`. Numbers and curves must look plausible (peaks and valleys, not straight lines). `scripts/selftest.py` enforces these rules.
