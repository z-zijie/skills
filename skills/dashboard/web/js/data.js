/**
 * data.js — snapshot loading, normalization, fleet derivation and the
 * deterministic demo simulation.
 *
 * Data contract: plan/contract.md. The UI tolerates missing optional fields.
 * Demo mode (mode: "demo") is simulated locally at 1 Hz with a seeded PRNG so
 * screenshots are reproducible; live mode polls the server snapshot.
 */

import { normalizeStatus, agentName } from "./format.js";

export function hashString(value) {
  let hash = 2166136261;
  const text = String(value);
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

export function mulberry32(seed) {
  let a = seed >>> 0;
  return function random() {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const round1 = (value) => Math.round(value * 10) / 10;
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

/* ------------------------------------------------------------------ */
/* Loading                                                             */
/* ------------------------------------------------------------------ */

export function apiUrl() {
  const root = document.documentElement;
  return root.dataset.api || "./api/state";
}

export function demoUrl() {
  const root = document.documentElement;
  return root.dataset.demo || "./data/demo.json";
}

function fetchJson(url) {
  return fetch(url, {
    cache: "no-store",
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  }).then((response) => {
    if (!response.ok) throw new Error(`HTTP ${response.status} for ${url}`);
    return response.json();
  });
}

function isValidSnapshot(raw) {
  return Boolean(raw) && Array.isArray(raw.machines) && Array.isArray(raw.sessions);
}

/**
 * Load the snapshot. Order:
 *   1. ?demo=1 (or html[data-mode="static"]) -> bundled demo file, no API probe
 *   2. ./api/state (dashboard.py live/demo snapshots)
 *   3. ./data/demo.json fallback (plain static server)
 */
export async function loadSnapshot() {
  const params = new URLSearchParams(window.location.search);
  const forceDemo = params.get("demo") === "1" || document.documentElement.dataset.mode === "static";

  if (!forceDemo) {
    try {
      const raw = await fetchJson(apiUrl());
      if (isValidSnapshot(raw)) {
        return { snapshot: normalizeSnapshot(raw), source: "api" };
      }
    } catch {
      /* expected on a plain static server: fall through to the bundled demo */
    }
  }

  const raw = await fetchJson(demoUrl());
  if (!isValidSnapshot(raw)) throw new Error("demo snapshot is missing machines/sessions");
  return { snapshot: normalizeSnapshot(raw), source: "demo" };
}

export class LivePoller {
  constructor({ url = apiUrl(), intervalMs = 5000, onData, onError } = {}) {
    this.url = url;
    this.intervalMs = intervalMs;
    this.onData = onData;
    this.onError = onError;
    this.timer = null;
    this.running = false;
  }

  start() {
    if (this.running) return;
    this.running = true;
    this.schedule(200);
  }

  schedule(delay = this.intervalMs) {
    this.stopTimer();
    this.timer = window.setTimeout(() => this.poll(), delay);
  }

  stopTimer() {
    if (this.timer) {
      window.clearTimeout(this.timer);
      this.timer = null;
    }
  }

  async poll() {
    if (!this.running) return;
    try {
      const raw = await fetchJson(this.url);
      if (!this.running) return;
      if (!isValidSnapshot(raw)) throw new Error("invalid snapshot");
      this.onData?.(normalizeSnapshot(raw));
      this.onError?.(null);
    } catch (error) {
      this.onError?.(error);
    } finally {
      if (this.running) this.schedule();
    }
  }

  stop() {
    this.running = false;
    this.stopTimer();
  }
}

/* ------------------------------------------------------------------ */
/* Normalization                                                       */
/* ------------------------------------------------------------------ */

function toNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function normalizeSeries(series) {
  if (!Array.isArray(series)) return [];
  return series
    .map((point) => {
      if (Array.isArray(point)) return [toNumber(point[0], 0), Math.max(0, toNumber(point[1], 0))];
      const value = toNumber(point, 0);
      return [0, Math.max(0, value)];
    })
    .filter(([time, value]) => Number.isFinite(time) && Number.isFinite(value));
}

export function normalizeMachine(raw, index) {
  const id = raw.id || `machine-${index + 1}`;
  return {
    id,
    name: raw.name || id,
    host: raw.host || id,
    platform: raw.platform || "other",
    arch: raw.arch || "",
    online: raw.online !== false,
    last_seen: raw.last_seen || null,
    agents: Array.isArray(raw.agents)
      ? raw.agents.map((agent, agentIndex) => ({
          id: agent.id || `agent-${agentIndex + 1}`,
          name: agent.name || agentName(agent.id),
          version: agent.version || "",
          status: agent.status || "idle",
          sessions_active: toNumber(agent.sessions_active, 0),
          requests: toNumber(agent.requests, 0),
          tokens: toNumber(agent.tokens, 0),
          tps: Math.max(0, toNumber(agent.tps, 0)),
          model: agent.model || "",
        }))
      : [],
  };
}

export function normalizeSession(raw, index) {
  const id = raw.id || `session-${index + 1}`;
  const tokens = raw.tokens || {};
  const context = raw.context || {};
  const subagents = raw.subagents || {};
  return {
    id,
    machine_id: raw.machine_id || "",
    agent: raw.agent || "other",
    agent_name: raw.agent_name || agentName(raw.agent),
    title: raw.title || "Untitled session",
    project: raw.project || "",
    cwd: raw.cwd || "",
    branch: raw.branch || "",
    model: raw.model || "",
    status: normalizeStatus(raw.status),
    started_at: raw.started_at || null,
    updated_at: raw.updated_at || raw.started_at || null,
    ended_at: raw.ended_at || null,
    duration_ms: Math.max(0, toNumber(raw.duration_ms, 0)),
    requests: Math.max(0, toNumber(raw.requests, 0)),
    tokens: {
      input: Math.max(0, toNumber(tokens.input, 0)),
      output: Math.max(0, toNumber(tokens.output, 0)),
      cache_read: Math.max(0, toNumber(tokens.cache_read, 0)),
      cache_write: Math.max(0, toNumber(tokens.cache_write, 0)),
    },
    context: {
      used: Math.max(0, toNumber(context.used, 0)),
      limit: Math.max(0, toNumber(context.limit, 0)),
    },
    subagents: {
      count: Math.max(0, toNumber(subagents.count, 0)),
      active: Math.max(0, toNumber(subagents.active, 0)),
    },
    tps: Math.max(0, toNumber(raw.tps, 0)),
    cost_usd: raw.cost_usd === null || raw.cost_usd === undefined ? null : toNumber(raw.cost_usd, 0),
    latency_ms: raw.latency_ms === null || raw.latency_ms === undefined ? null : toNumber(raw.latency_ms, 0),
    tps_series: normalizeSeries(raw.tps_series),
    context_parts: Array.isArray(raw.context_parts)
      ? raw.context_parts.map((part, partIndex) => ({
          key: part.key || `part-${partIndex}`,
          label: part.label || part.key || `Part ${partIndex + 1}`,
          tokens: Math.max(0, toNumber(part.tokens, 0)),
        }))
      : [],
    subagent_tree: raw.subagent_tree || null,
    steps: Array.isArray(raw.steps)
      ? raw.steps.map((step) => ({
          label: step.label || "step",
          kind: step.kind || "tool",
          start_ms: Math.max(0, toNumber(step.start_ms, 0)),
          dur_ms: Math.max(0, toNumber(step.dur_ms, 0)),
        }))
      : [],
    events: Array.isArray(raw.events)
      ? raw.events.map((event) => ({
          at: event.at || null,
          kind: event.kind || "tool",
          text: event.text || "",
        }))
      : [],
  };
}

export function normalizeSnapshot(raw) {
  const mode = ["live", "demo", "snapshot"].includes(raw?.mode) ? raw.mode : "demo";
  return {
    schema_version: toNumber(raw?.schema_version, 1),
    mode,
    generated_at: raw?.generated_at || new Date().toISOString(),
    refresh_seconds: Math.max(1, toNumber(raw?.refresh_seconds, 5)),
    aggregate: raw?.aggregate ?? null,
    machines: Array.isArray(raw?.machines) ? raw.machines.map(normalizeMachine) : [],
    sessions: Array.isArray(raw?.sessions) ? raw.sessions.map(normalizeSession) : [],
  };
}

/* ------------------------------------------------------------------ */
/* Derived fleet data                                                  */
/* ------------------------------------------------------------------ */

/**
 * One clock: the TPS shown in numbers must equal the last point of the plotted
 * series (collectors define `tps` as a 60s mean, while the charts plot 1s samples).
 */
export function latestTps(session) {
  const series = Array.isArray(session?.tps_series) ? session.tps_series : [];
  if (series.length) {
    const last = series[series.length - 1];
    const value = Array.isArray(last) ? Number(last[1]) : Number(last);
    if (Number.isFinite(value)) return Math.max(0, value);
  }
  return Math.max(0, Number(session?.tps) || 0);
}

export function latestSampleTime(session) {
  const series = Array.isArray(session?.tps_series) ? session.tps_series : [];
  if (!series.length) return null;
  const last = series[series.length - 1];
  const stamp = Array.isArray(last) ? Number(last[0]) : NaN;
  return Number.isFinite(stamp) ? stamp : null;
}

export function latestTpsSum(sessions) {
  return round1(sessions.reduce((acc, session) => acc + latestTps(session), 0));
}

export function sessionContextRatio(session) {
  const used = Number(session.context?.used) || 0;
  const limit = Number(session.context?.limit) || 0;
  if (!limit) return 0;
  return clamp(used / limit, 0, 1);
}

export function contextLevel(session) {
  const ratio = sessionContextRatio(session);
  if (ratio >= 0.85) return "danger";
  if (ratio >= 0.7) return "warn";
  return "normal";
}

/** Sum of the running sessions' series, aligned on timestamps; shared 60s window. */
export function aggregateSeries(snapshot, window = 60) {
  const running = snapshot.sessions.filter((session) => session.status === "running");
  const series = running.filter((session) => session.tps_series.length);
  if (!series.length) return [];
  const sums = new Map();
  for (const session of series) {
    for (const [time, value] of session.tps_series) {
      sums.set(time, (sums.get(time) || 0) + value);
    }
  }
  return Array.from(sums.entries())
    .sort((a, b) => a[0] - b[0])
    .slice(-window)
    .map(([time, value]) => [time, round1(value)]);
}

export function machineSummaries(snapshot) {
  const byMachine = new Map();
  for (const session of snapshot.sessions) {
    if (!byMachine.has(session.machine_id)) byMachine.set(session.machine_id, []);
    byMachine.get(session.machine_id).push(session);
  }
  return snapshot.machines.map((machine) => {
    const sessions = byMachine.get(machine.id) || [];
    const active = sessions.filter((session) => session.status === "running");
    const tps = latestTpsSum(active);
    const agents = machine.agents.map((agent) => {
      const agentSessions = sessions.filter((session) => session.agent === agent.id);
      const agentActive = agentSessions.filter((session) => session.status === "running");
      return {
        ...agent,
        sessionCount: agentSessions.length,
        activeCount: agentActive.length,
        tps: round1(agentActive.reduce((acc, session) => acc + session.tps, 0)),
      };
    });
    return {
      machine,
      sessions,
      agents,
      sessionCount: sessions.length,
      activeCount: active.length,
      tps,
    };
  });
}

export function agentSummaries(snapshot) {
  const map = new Map();
  for (const session of snapshot.sessions) {
    if (!map.has(session.agent)) {
      map.set(session.agent, {
        id: session.agent,
        name: agentName(session.agent, session.agent_name),
        count: 0,
        activeCount: 0,
        tps: 0,
        requests: 0,
        machines: new Set(),
      });
    }
    const entry = map.get(session.agent);
    entry.count += 1;
    entry.requests += session.requests;
    entry.machines.add(session.machine_id);
    if (session.status === "running") {
      entry.activeCount += 1;
      entry.tps += latestTps(session);
    }
  }
  return Array.from(map.values())
    .map((entry) => ({ ...entry, tps: round1(entry.tps) }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}

export function computeFleet(snapshot, window = 60) {
  const sessions = snapshot.sessions;
  const online = snapshot.machines.filter((machine) => machine.online);
  const agents = snapshot.machines.reduce((acc, machine) => acc + machine.agents.length, 0);
  const active = sessions.filter((session) => session.status === "running");
  const tps = latestTpsSum(active);
  let series = aggregateSeries(snapshot, window);
  if (!series.length && Array.isArray(snapshot.aggregate?.tps_series)) {
    series = snapshot.aggregate.tps_series.slice(-window).map(([time, value]) => [time, value]);
  }
  const values = series.map(([, value]) => value);
  const average = values.length
    ? values.reduce((acc, value) => acc + value, 0) / values.length
    : 0;
  const last = values.length ? values[values.length - 1] : 0;
  const delta = average > 0 ? ((last - average) / average) * 100 : 0;
  return {
    machinesOnline: online.length,
    machinesTotal: snapshot.machines.length,
    agents,
    sessionsActive: active.length,
    sessionsTotal: sessions.length,
    requests: sessions.reduce((acc, session) => acc + session.requests, 0),
    tps,
    series,
    delta,
  };
}

export function countByStatus(sessions) {
  const counts = { all: sessions.length, running: 0, waiting: 0, done: 0, failed: 0, idle: 0 };
  for (const session of sessions) counts[session.status] = (counts[session.status] || 0) + 1;
  return counts;
}

export function filterSessions(sessions, { machineId = null, status = "all", agent = null, query = "" } = {}) {
  const needle = query.trim().toLowerCase();
  return sessions.filter((session) => {
    if (machineId && session.machine_id !== machineId) return false;
    if (status !== "all" && session.status !== status) return false;
    if (agent && session.agent !== agent) return false;
    if (!needle) return true;
    const haystack = [
      session.title,
      session.project,
      session.branch,
      session.agent_name,
      session.agent,
      session.machine_id,
      session.model,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return haystack.includes(needle);
  });
}

export function sortSessions(sessions, sort = "recent") {
  const list = sessions.slice();
  if (sort === "context") {
    const ratioKey = (session) =>
      Number(session.context?.limit) > 0 ? sessionContextRatio(session) : -1;
    list.sort(
      (a, b) =>
        ratioKey(b) - ratioKey(a) ||
        (Number(b.context?.used) || 0) - (Number(a.context?.used) || 0) ||
        timeValue(b.updated_at) - timeValue(a.updated_at)
    );
  } else if (sort === "tps") {
    // "—" cards (no live tokens) sort below every running card, matching the readout
    const rank = (session) => (session.status === "running" ? latestTps(session) : -1);
    list.sort((a, b) => rank(b) - rank(a) || timeValue(b.updated_at) - timeValue(a.updated_at));
  } else {
    list.sort((a, b) => timeValue(b.updated_at) - timeValue(a.updated_at));
  }
  return list;
}

function timeValue(value) {
  const date = new Date(value || 0);
  return Number.isNaN(date.getTime()) ? 0 : date.getTime();
}

/* ------------------------------------------------------------------ */
/* Demo simulation (deterministic, seeded)                             */
/* ------------------------------------------------------------------ */

export class DemoSim {
  constructor(snapshot, { seed = 2333, intervalMs = 1000, onTick } = {}) {
    this.snapshot = snapshot;
    this.seed = seed;
    this.intervalMs = intervalMs;
    this.onTick = onTick;
    this.timer = null;
    this.paused = false;
    this.rngs = new Map();
    this.baseTps = new Map();
    this.frames = 0;
    this.nowMs = Date.parse(snapshot.generated_at) || Date.now();
    for (const session of snapshot.sessions) {
      this.rngs.set(session.id, mulberry32(hashString(session.id) ^ seed));
      const values = session.tps_series.map(([, value]) => value);
      const mean = values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0;
      this.baseTps.set(session.id, Math.max(1, session.tps || mean));
    }
    this.fleetSeries = aggregateSeries(snapshot, 90);
    this.lastTs = this.fleetSeries.length
      ? this.fleetSeries[this.fleetSeries.length - 1][0]
      : this.nowMs;
  }

  start() {
    if (this.timer) return;
    this.timer = window.setInterval(() => {
      if (!this.paused) this.tick(1);
    }, this.intervalMs);
  }

  stop() {
    if (this.timer) {
      window.clearInterval(this.timer);
      this.timer = null;
    }
  }

  pause() {
    this.paused = true;
  }

  resume() {
    this.paused = false;
  }

  /** Advance N simulated seconds immediately (deterministic QA hook). */
  advance(seconds = 1) {
    const steps = Math.max(1, Math.min(600, Math.round(seconds)));
    for (let i = 0; i < steps; i += 1) this.tick(1, { silent: i < steps - 1 });
    return this.frames;
  }

  tick(_step = 1, { silent = false } = {}) {
    const snapshot = this.snapshot;
    this.frames += 1;
    this.lastTs += 1000;
    this.nowMs = this.lastTs;
    const nowIso = new Date(this.nowMs).toISOString();

    for (const session of snapshot.sessions) {
      const rng = this.rngs.get(session.id);
      if (!rng) continue;
      if (session.status === "running") {
        const base = this.baseTps.get(session.id) || 1;
        const drift = (base - session.tps) * 0.16;
        const noise = (rng() - 0.5) * base * 0.2;
        let next = clamp(session.tps + drift + noise, 0.2, base * 1.75);
        if (rng() < 0.008) next = Math.max(0.2, next * 0.5);
        session.tps = round1(next);
        session.duration_ms += 1000;
        session.requests += rng() < clamp(session.tps / 160, 0.02, 0.45) ? 1 : 0;
        session.updated_at = nowIso;

        const previousUsed = session.context.used;
        const limit = session.context.limit || Number.POSITIVE_INFINITY;
        const creep = Math.round(rng() * 90 + session.tps * 1.6);
        session.context.used = Math.min(limit, previousUsed + creep);
        const growth = session.context.used - previousUsed;
        session.tokens.output += Math.round(session.tps * (0.75 + rng() * 0.5));

        if (session.context_parts.length && growth > 0) {
          const factor = session.context.used / Math.max(1, previousUsed);
          session.context_parts = session.context_parts.map((part) => ({
            ...part,
            tokens: Math.round(part.tokens * factor),
          }));
        }

        session.tps_series.push([this.lastTs, session.tps]);
        if (session.tps_series.length > 90) session.tps_series.shift();
      } else if (session.status === "waiting") {
        if (rng() < 0.0018) {
          session.status = "running";
          session.updated_at = nowIso;
          this.baseTps.set(session.id, Math.max(4, this.baseTps.get(session.id) || 4));
        }
      }
    }

    // aggregate agents/machines from sessions; keep the three layers consistent
    for (const machine of snapshot.machines) {
      const machineSessions = snapshot.sessions.filter((session) => session.machine_id === machine.id);
      for (const agent of machine.agents) {
        const agentSessions = machineSessions.filter((session) => session.agent === agent.id);
        const running = agentSessions.filter((session) => session.status === "running");
        agent.tps = round1(running.reduce((acc, session) => acc + session.tps, 0));
        agent.sessions_active = running.length;
        agent.requests = agentSessions.reduce((acc, session) => acc + session.requests, 0);
        if (!machine.online) agent.status = "offline";
        else if (running.length) agent.status = "active";
        else if (agentSessions.length) agent.status = "idle";
      }
      if (machine.online) machine.last_seen = nowIso;
    }

    const fleetTps = round1(
      snapshot.sessions
        .filter((session) => session.status === "running")
        .reduce((acc, session) => acc + session.tps, 0)
    );
    this.fleetSeries.push([this.lastTs, fleetTps]);
    if (this.fleetSeries.length > 90) this.fleetSeries.shift();
    snapshot.generated_at = nowIso;

    if (!silent) this.onTick?.(this.snapshot);
  }
}
