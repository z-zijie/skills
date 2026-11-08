/**
 * app.js — workbench orchestration: load snapshot, render header/rail/toolbar/
 * board/topology/inspector, wire filters, sort, search, liveness, deep links,
 * keyboard, reduced motion, and expose the QA hooks.
 */

import { $, $$, h, clear, setText, rafBatch, clamp, icon, platformIcon } from "./dom.js";
import {
  fmtInt,
  fmtRuntime,
  fmtTokens,
  fmtTps,
  fmtUsd,
  fmtCount,
  fmtClock,
  fmtAgo,
  agentName,
} from "./format.js";
import {
  loadSnapshot,
  normalizeSnapshot,
  LivePoller,
  DemoSim,
  computeFleet,
  machineSummaries,
  agentSummaries,
  latestTps,
  latestTpsSum,
  latestSampleTime,
  countByStatus,
  filterSessions,
  sortSessions,
  sessionContextRatio,
} from "./data.js";
import { Spark, Donut } from "./charts.js";
import { Inspector } from "./inspector.js";
import { Topology } from "./topology.js";
import {
  Flip,
  NumberTween,
  enterStagger,
  withViewTransition,
  reducedMotion,
  onReducedMotionChange,
  setAmbientPaused,
} from "./motion.js";

/* ------------------------------------------------------------------ */
/* DOM refs                                                            */
/* ------------------------------------------------------------------ */

const el = {
  body: document.body,
  modeBadge: $("#mode-badge"),
  modeLabel: $("#mode-label"),
  fleetMetrics: $("#fleet-metrics"),
  rail: $("#rail"),
  railList: $("#rail-list"),
  railCount: $("#rail-count"),
  statusFilters: $("#status-filters"),
  agentFilters: $("#agent-filters"),
  search: $("#search"),
  sort: $("#sort"),
  boardView: $("#board-view"),
  board: $("#board"),
  empty: $("#empty"),
  emptyNote: $("#empty-note"),
  emptyReset: $("#empty-reset"),
  topologyView: $("#topology-view"),
  topology: $("#topology"),
  topoTip: $("#topo-tip"),
  viewToggle: $("#view-toggle"),
  inspector: $("#inspector"),
  scrim: $("#scrim"),
  tip: $("#tip"),
  statusMode: $("#status-mode"),
  statusRefresh: $("#status-refresh"),
  statusCount: $("#status-count"),
  statusUpdated: $("#status-updated"),
  liveSummary: $("#live-summary"),
  fleet: {
    machines: $('[data-fleet="machines"]'),
    agents: $('[data-fleet="agents"]'),
    sessions: $('[data-fleet="sessions"]'),
    requests: $('[data-fleet="requests"]'),
    tps: $('[data-fleet="tps"]'),
    delta: $('[data-fleet="tps-delta"]'),
  },
};

/* ------------------------------------------------------------------ */
/* State                                                               */
/* ------------------------------------------------------------------ */

const state = {
  snapshot: null,
  source: "demo",
  mode: "demo",
  view: "board",
  machineId: null,
  status: "all",
  agent: null,
  query: "",
  sort: "recent",
  selectedSessionId: null,
  hoveredCardId: null,
  visibleCount: null,
  lastUpdated: Date.now(),
  ready: false,
  paused: false,
};

let waveSpark = null;
let fleetTween = null;
let flip = null;
let inspector = null;
let topology = null;
let sim = null;
let poller = null;
let cards = new Map();
let railRefs = new Map();
let toolbarRefs = { status: new Map(), agents: new Map() };
let emptyNoteExtra = "";
let readyResolve = null;

window.__dashboardReady = new Promise((resolve) => {
  readyResolve = resolve;
});
window.__dashboardReadyState = "loading";

const STATUS_FILTERS = [
  ["all", "all"],
  ["running", "running"],
  ["waiting", "waiting"],
  ["done", "done"],
  ["failed", "failed"],
  ["idle", "idle"],
];

/* ------------------------------------------------------------------ */
/* Boot                                                                */
/* ------------------------------------------------------------------ */

boot();

async function boot() {
  bindStaticEvents();
  try {
    const { snapshot, source } = await loadSnapshot();
    init(snapshot, source);
  } catch (error) {
    showLoadError(error);
  }
  startClock();
}

function init(snapshot, source) {
  state.snapshot = snapshot;
  state.source = source;
  state.mode = snapshot.mode;
  state.lastUpdated = Date.now();
  state.paused = false;

  el.body.dataset.mode = snapshot.mode === "live" ? "live" : "demo";
  updateModeChrome();

  waveSpark = new Spark($(".wave-wrap"), {
    height: 24,
    width: 118,
    padY: 2,
    endRadius: 1.8,
    strokeWidth: 1.3,
  });
  waveSpark.el.classList.add("wave");

  fleetTween = new NumberTween(el.fleet.tps, (value) => fmtTps(value));
  flip = new Flip(el.board);

  inspector = new Inspector({
    root: el.inspector,
    scrim: el.scrim,
    getMachine: (id) => state.snapshot?.machines.find((machine) => machine.id === id),
    onClose: () => {
      state.selectedSessionId = null;
      updateAmbientPulses();
      writeHash();
    },
  });

  buildRail();
  buildToolbar();
  buildCards();
  updateModeChrome();
  applyFilters({ animate: false, updateTopology: false });
  refreshFleet({ animate: false });
  refreshRail();
  refreshToolbar();
  updateStatusbar();
  renderInspectorIfOpen();

  startLiveness();
  bindAdditionalEvents();

  // reveal the shell only once the first data frame is laid out (CLS = 0)
  el.body.dataset.ready = "true";
  playEntrance();
  applyHashFromLocation(true).then(() => {
    state.ready = true;
    el.body.dataset.ready = "true";
    window.__dashboardReadyState = "ready";
    window.__dashboardView = state.view;
    readyResolve?.(true);
    announceSummary();
  });
}

function showLoadError(error) {
  state.ready = false;
  el.body.dataset.ready = "error";
  window.__dashboardError = String(error?.message || error);
  el.board.replaceChildren();
  el.empty.hidden = false;
  el.emptyNote.textContent =
    "Could not load a snapshot. Serve this directory with the dashboard skill (or any static server).";
  el.emptyReset.hidden = true;
  el.statusMode.textContent = "no data";
  el.fleet.machines && console.error("[dashboard] snapshot load failed:", error);
  window.__dashboardReadyState = "ready";
  readyResolve?.(true);
}

/* ------------------------------------------------------------------ */
/* Liveness                                                            */
/* ------------------------------------------------------------------ */

function startLiveness() {
  stopLiveness();
  if (state.mode === "live") {
    poller = new LivePoller({
      intervalMs: Math.max(1, state.snapshot.refresh_seconds || 5) * 1000,
      onData: onLiveData,
      onError: onLiveError,
    });
    poller.start();
  } else if (state.mode === "demo") {
    sim = new DemoSim(state.snapshot, {
      seed: 2333,
      intervalMs: 1000,
      onTick: () => scheduleRefresh(),
    });
    sim.start();
  }
}

function stopLiveness() {
  sim?.stop();
  sim = null;
  poller?.stop();
  poller = null;
}

const scheduleRefresh = rafBatch(() => {
  state.lastUpdated = Date.now();
  refreshFleet({ animate: true });
  refreshRail();
  refreshCards();
  refreshToolbar();
  updateStatusbar();
  renderInspectorIfOpen();
  topology?.updateValues(state.snapshot);
  announceSummary();
});

function onLiveData(snapshot) {
  const previous = state.snapshot;
  state.snapshot = snapshot;
  state.lastUpdated = Date.now();
  el.body.dataset.stale = "false";
  const structureChanged =
    !sameSessionIds(previous, snapshot) ||
    (previous?.machines.length || 0) !== snapshot.machines.length;
  if (structureChanged) {
    buildRail();
    buildToolbar();
    buildCards();
  }
  applyFilters({ animate: true, updateTopology: true });
  refreshCards();
  refreshFleet({ animate: true });
  refreshRail();
  refreshToolbar();
  updateStatusbar();
  renderInspectorIfOpen();
  topology?.setSnapshot(snapshot);
  if (topology?.visible) topology.updateState(currentFilterState());
  announceSummary();
}

function sameSessionIds(a, b) {
  const left = (a?.sessions || []).map((session) => session.id).sort().join("|");
  const right = (b?.sessions || []).map((session) => session.id).sort().join("|");
  return left === right;
}

function onLiveError(error) {
  el.body.dataset.stale = error ? "true" : "false";
  if (error) {
    el.statusUpdated.textContent = "reconnecting";
    el.statusUpdated.title = String(error.message || error);
  }
}

/* ------------------------------------------------------------------ */
/* Mode chrome + statusbar                                             */
/* ------------------------------------------------------------------ */

function updateModeChrome() {
  const mode = state.mode;
  const label = mode === "live" ? "live" : mode === "snapshot" ? "snapshot" : "demo";
  setText(el.modeLabel, label);
  el.modeBadge.title =
    mode === "live"
      ? "live snapshot polled from the dashboard server"
      : mode === "snapshot"
        ? "fixed snapshot supplied by the dashboard server"
        : "demo data, simulated in this browser";
  el.body.dataset.mode = mode === "live" ? "live" : "demo";

  if (mode === "live") {
    setText(el.statusMode, "live snapshot");
    setText(el.statusRefresh, `polling ${state.snapshot?.refresh_seconds || 5}s`);
  } else if (mode === "snapshot") {
    setText(el.statusMode, "fixed snapshot");
    setText(el.statusRefresh, "no polling");
  } else {
    setText(el.statusMode, "demo data");
    setText(el.statusRefresh, "refresh simulated at 1s");
  }
}

function updateStatusbar() {
  const sessions = state.snapshot?.sessions || [];
  const active = sessions.filter((session) => session.status === "running").length;
  const visible = typeof state.visibleCount === "number" ? state.visibleCount : sessions.length;
  setText(
    el.statusCount,
    visible !== sessions.length
      ? `showing ${visible} of ${sessions.length} sessions`
      : `${active} running / ${sessions.length} sessions`
  );
  if (el.body.dataset.stale === "true") return;
  if (state.mode === "snapshot") {
    const generated = state.snapshot?.generated_at;
    el.statusUpdated.textContent = generated ? `as of ${fmtClock(generated)}` : "fixed snapshot";
    return;
  }
  const age = Math.max(0, Math.round((Date.now() - state.lastUpdated) / 1000));
  el.statusUpdated.textContent = age < 2 ? "updated now" : `updated ${age}s ago`;
}

function startClock() {
  window.setInterval(() => {
    updateStatusbar();
    announceSummary();
  }, 1000);
}

function refreshFleet({ animate = true } = {}) {
  if (!state.snapshot) return;
  const fleet = computeFleet(state.snapshot);
  const series = state.sim?.fleetSeries?.slice(-60) || fleet.series;
  const values = series.map((point) => point[1]);
  const average = values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0;
  const last = values.length ? values[values.length - 1] : fleet.tps;
  const delta = average > 0 ? ((last - average) / average) * 100 : 0;

  el.fleet.machines.replaceChildren(
    h("span", { text: String(fleet.machinesOnline) }),
    h("span", { class: "metric-sub", text: `/${fleet.machinesTotal}` })
  );
  setText(el.fleet.agents, String(fleet.agents));
  el.fleet.sessions.replaceChildren(
    h("span", { text: String(fleet.sessionsActive) }),
    h("span", { class: "metric-sub", text: `/${fleet.sessionsTotal}` })
  );
  setText(el.fleet.requests, fmtInt(fleet.requests));
  fleetTween?.set(fleet.tps, { animate: animate && !state.paused, duration: 420 });

  el.fleet.delta.hidden = values.length < 3 || average < 0.5;
  if (!el.fleet.delta.hidden) {
    const rounded = Math.abs(delta) < 0.4 ? 0 : delta;
    const sign = rounded > 0 ? "+" : rounded < 0 ? "−" : "";
    el.fleet.delta.textContent = rounded === 0 ? "flat" : `${sign}${Math.abs(rounded).toFixed(1)}%`;
    el.fleet.delta.classList.toggle("is-up", rounded > 0);
    el.fleet.delta.classList.toggle("is-down", rounded < 0);
    el.fleet.delta.classList.toggle("is-flat", rounded === 0);
  }

  if (waveSpark) {
    const points = series.slice();
    if (points.length) {
      points[points.length - 1] = [points[points.length - 1][0], fleet.tps];
    }
    waveSpark.setSeries(points, { status: "running" });
  }
}

/* ------------------------------------------------------------------ */
/* Machine rail                                                        */
/* ------------------------------------------------------------------ */

function railRow({ id, name, iconEl, sub, tps, count, offline = false, all = false }) {
  const row = h("button", {
    class: `rail-row${all ? " rail-row-all" : ""}${offline ? " is-offline" : ""}`,
    type: "button",
    "data-machine-id": id,
    "aria-pressed": "false",
    title: name,
  });
  const dot = h("span", { class: `dot status-${offline ? "idle" : "online"}` });
  const stateEl = h("span", { class: "rail-state", text: all ? "" : offline ? "" : "online" });
  const nameText = h("span", { class: "rail-name-text", text: name });
  const nameEl = h("span", { class: "rail-name" }, [dot, nameText, stateEl]);
  const subEl = h("span", { class: "rail-sub" });
  const meterFill = h("i", { style: { "--fill": "0" } });
  const meter = h("span", { class: "rail-meter" }, [meterFill]);
  const tpsEl = h("span", { class: "rail-tps", text: fmtTps(tps) });
  const tpsK = h("span", { class: "rail-tps-k", text: "t/s" });
  const right = h("span", { class: "rail-right" }, [tpsEl, tpsK]);
  const countEl = h("span", { class: "rail-n", text: String(count) });
  row.append(
    all ? h("span", { class: "rail-icon" }, [icon("layers", { size: 15 })]) : h("span", { class: "rail-icon" }, [iconEl]),
    h("span", { class: "rail-body" }, [nameEl, subEl, meter]),
    right,
    countEl
  );
  return { row, nameEl, stateEl, subEl, meterFill, tpsEl, tpsK, right, countEl, dot };
}

function buildRail() {
  railRefs = new Map();
  const rows = [];
  const sessions = state.snapshot.sessions;
  const machines = state.snapshot.machines;
  const fleet = computeFleet(state.snapshot);

  const all = railRow({
    id: "all",
    name: "All machines",
    iconEl: icon("layers", { size: 15 }),
    sub: "",
    tps: fleet.tps,
    count: sessions.length,
    all: true,
  });
  railRefs.set("all", all);
  rows.push(all.row);

  for (const summary of machineSummaries(state.snapshot)) {
    const machine = summary.machine;
    const refs = railRow({
      id: machine.id,
      name: machine.name,
      iconEl: platformIcon(machine.platform),
      sub: "",
      tps: summary.tps,
      count: summary.sessionCount,
      offline: !machine.online,
    });
    railRefs.set(machine.id, refs);
    rows.push(refs.row);
  }
  el.railList.replaceChildren(...rows);
  setText(el.railCount, fmtCount(machines.length, "machine"));
  refreshRail();
}

function refreshRail() {
  if (!state.snapshot) return;
  const summaries = machineSummaries(state.snapshot);
  const fleet = computeFleet(state.snapshot);
  const maxTps = Math.max(1, fleet.tps, ...summaries.map((summary) => summary.tps));

  const all = railRefs.get("all");
  if (all) {
    all.subEl.replaceChildren(
      h("span", { text: fmtCount(state.snapshot.machines.length, "machine") }),
      h("span", { class: "rail-sub-sep", text: "/", "aria-hidden": "true" }),
      h("span", { text: fmtCount(state.snapshot.sessions.length, "session") })
    );
    all.tpsEl.textContent = fmtTps(fleet.tps);
    all.tpsEl.style.display = "";
    all.tpsK.style.display = "";
    all.row.setAttribute("aria-pressed", state.machineId ? "false" : "true");
    all.meterFill.style.setProperty("--fill", String(clamp(fleet.tps / maxTps, 0, 1)));
    all.countEl.textContent = String(state.snapshot.sessions.length);
  }

  for (const summary of summaries) {
    const refs = railRefs.get(summary.machine.id);
    if (!refs) continue;
    const machine = summary.machine;
    refs.subEl.replaceChildren(
      h("span", { text: fmtCount(machine.agents.length, "agent") }),
      h("span", { class: "rail-sub-sep", text: "/", "aria-hidden": "true" }),
      h("span", { text: fmtCount(summary.sessionCount, "session") })
    );
    refs.row.classList.toggle("is-offline", !machine.online);
    refs.dot.className = `dot status-${machine.online ? "online" : "idle"}`;
    if (refs.stateEl) refs.stateEl.textContent = machine.online ? "online" : "";
    if (machine.online) {
      refs.tpsEl.textContent = fmtTps(summary.tps);
      refs.tpsEl.style.display = "";
      refs.tpsK.style.display = "";
    } else {
      refs.tpsEl.textContent = "offline";
      refs.tpsK.style.display = "none";
    }
    refs.row.setAttribute("aria-pressed", state.machineId === machine.id ? "true" : "false");
    refs.meterFill.style.setProperty("--fill", String(clamp(summary.tps / maxTps, 0, 1)));
    refs.countEl.textContent = String(summary.sessionCount);
    refs.row.setAttribute(
      "aria-label",
      `${machine.name}, ${machine.online ? "online" : "offline"}, ${machine.agents.length} agents, ${summary.sessionCount} sessions`
    );
  }
}

/* ------------------------------------------------------------------ */
/* Toolbar                                                             */
/* ------------------------------------------------------------------ */

function buildToolbar() {
  toolbarRefs = { status: new Map(), agents: new Map() };
  const statusButtons = STATUS_FILTERS.map(([key, label]) => {
    const count = h("span", { class: "chip-n", text: "0" });
    const button = h(
      "button",
      {
        class: "chip",
        type: "button",
        "data-status-filter": key,
        "aria-pressed": String(state.status === key),
      },
      [
        key === "all" ? null : h("span", { class: `dot status-${key}` }),
        h("span", { text: label }),
        count,
      ]
    );
    button.addEventListener("click", () => {
      state.status = key;
      applyFilters({ animate: true });
      writeHash();
    });
    toolbarRefs.status.set(key, { button, count });
    return button;
  });
  el.statusFilters.replaceChildren(...statusButtons);
  refreshToolbar();
}

function syncAgentChips() {
  if (!state.snapshot) return;
  const scoped = state.snapshot.sessions.filter(
    (session) => !state.machineId || session.machine_id === state.machineId
  );
  const counts = new Map();
  for (const session of scoped) counts.set(session.agent, (counts.get(session.agent) || 0) + 1);
  if (state.agent && !counts.has(state.agent)) state.agent = null;
  const agents = agentSummaries(state.snapshot).filter((entry) => counts.has(entry.id));
  const keys = agents.map((entry) => entry.id).join("|");
  const current = [...toolbarRefs.agents.keys()].join("|");
  if (keys !== current) {
    // rebuild from scratch: the previous map survives machine-filter round trips
    toolbarRefs.agents.clear();
    const buttons = agents.map((entry) => {
      const count = h("span", { class: "chip-n", text: String(counts.get(entry.id) || 0) });
      const button = h(
        "button",
        {
          class: "chip chip-agent",
          type: "button",
          "data-agent": entry.id,
          "data-agent-filter": entry.id,
          "aria-pressed": String(state.agent === entry.id),
        },
        [h("span", { class: "agent-mark" }), h("span", { text: agentName(entry.id) }), count]
      );
      button.addEventListener("click", () => {
        state.agent = state.agent === entry.id ? null : entry.id;
        applyFilters({ animate: true });
        writeHash();
      });
      toolbarRefs.agents.set(entry.id, { button, count });
      return button;
    });
    el.agentFilters.replaceChildren(...buttons);
  }
  for (const [id, refs] of toolbarRefs.agents) {
    refs.count.textContent = String(counts.get(id) || 0);
    refs.button.setAttribute("aria-pressed", String(state.agent === id));
  }
}

function refreshToolbar() {
  if (!state.snapshot) return;
  const scoped = state.snapshot.sessions.filter(
    (session) => !state.machineId || session.machine_id === state.machineId
  );
  const scopedByAgent = state.agent
    ? scoped.filter((session) => session.agent === state.agent)
    : scoped;
  const statusCounts = countByStatus(scopedByAgent);
  for (const [key, refs] of toolbarRefs.status) {
    refs.count.textContent = String(statusCounts[key] || 0);
    refs.button.setAttribute("aria-pressed", String(state.status === key));
  }
  syncAgentChips();
}

/* ------------------------------------------------------------------ */
/* Board                                                               */
/* ------------------------------------------------------------------ */

class SessionCard {
  constructor(session) {
    this.id = session.id;
    this.el = h("article", {
      class: "card",
      role: "button",
      tabindex: "0",
      "data-session-id": session.id,
      "data-machine-id": session.machine_id,
      "data-agent": session.agent,
      "data-status": session.status,
      "data-flip-id": session.id,
    });
    this.dot = h("span", { class: "dot" });
    this.agentEl = h("span", { class: "card-agent" });
    this.statusEl = h("span", { class: "card-status" });
    this.machineEl = h("span", { class: "card-machine" });
    const top = h("div", { class: "card-top" }, [this.dot, this.agentEl, this.statusEl, this.machineEl]);
    this.titleEl = h("h3", { class: "card-title" });
    this.sparkHost = h("div", { class: "spark-wrap" });
    this.tpsValue = h("b", { class: "card-tps-v" });
    this.tpsUnit = h("i", { class: "card-tps-u", text: "t/s" });
    this.tpsEl = h("span", { class: "card-tps", title: "current throughput" }, [
      this.tpsValue,
      this.tpsUnit,
    ]);
    this.donutHost = h("div", { class: "donut-host" });
    const viz = h("div", { class: "card-viz" }, [this.sparkHost, this.tpsEl, this.donutHost]);
    this.statValues = {
      runtime: h("span", { class: "stat-v" }),
      requests: h("span", { class: "stat-v" }),
      subagents: h("span", { class: "stat-v" }),
      cost: h("span", { class: "stat-v" }),
    };
    const stats = h("div", { class: "card-stats" }, [
      this.stat("runtime", "runtime"),
      this.stat("requests", "requests"),
      this.stat("subagents", "subagents"),
      this.stat("cost", "cost"),
    ]);
    this.el.append(top, this.titleEl, viz, stats);
    this.spark = new Spark(this.sparkHost, { height: 44, padY: 4 });
    this.donut = new Donut(this.donutHost);
    this.tpsTween = new NumberTween(this.tpsValue, (value) => fmtTps(value));
    this.el.addEventListener("click", () => openSession(this.id, this.el));
    this.el.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
        event.preventDefault();
        openSession(this.id, this.el);
      }
    });
    this.el.addEventListener("mouseenter", () => {
      if (this.spark?.el) this.spark.el.style.opacity = "1";
      state.hoveredCardId = this.id;
      updateAmbientPulses();
    });
    this.el.addEventListener("mouseleave", () => {
      if (state.hoveredCardId === this.id) state.hoveredCardId = null;
      updateAmbientPulses();
    });
    this.titleEl.addEventListener("mouseenter", (event) => this.showTitleTip(event));
    this.titleEl.addEventListener("mousemove", (event) => this.showTitleTip(event));
    this.titleEl.addEventListener("mouseleave", () => hideTip());
    this.update(session, { initial: true });
  }

  stat(key, label) {
    return h("div", { class: "stat" }, [this.statValues[key], h("span", { class: "stat-k", text: label })]);
  }

  update(session, { initial = false } = {}) {
    this.session = session;
    this.el.dataset.status = session.status;
    this.el.dataset.machineId = session.machine_id;
    this.el.dataset.agent = session.agent;

    this.dot.className = `dot status-${session.status}`;
    setText(this.agentEl, session.agent_name || agentName(session.agent));
    this.statusEl.className = `card-status status-word-${session.status}`;
    setText(this.statusEl, session.status);
    setText(this.machineEl, session.machine_id);

    setText(this.titleEl, session.title);
    setText(this.statValues.runtime, fmtRuntime(session.duration_ms));
    setText(this.statValues.requests, fmtInt(session.requests));
    setText(this.statValues.subagents, String(session.subagents.count || 0));
    setText(
      this.statValues.cost,
      session.cost_usd === null || session.cost_usd === undefined ? "—" : fmtUsd(session.cost_usd)
    );

    this.spark.setSeries(session.tps_series, { status: session.status });
    this.donut.set(session.context.used, session.context.limit);

    // one clock: running cards show the last bucket of the plotted series; every
    // other state shows a muted em dash (the chart keeps its history)
    const running = session.status === "running";
    const tps = latestTps(session);
    if (running) {
      this.tpsTween.set(tps, { animate: !initial, duration: 400 });
      this.tpsEl.dataset.mode = "live";
      this.tpsEl.title = "current throughput (last 1 s bucket)";
    } else {
      this.tpsTween.destroy();
      this.tpsTween.current = null;
      setText(this.tpsValue, "—");
      this.tpsEl.dataset.mode = "muted";
      const lastActive = latestSampleTime(session);
      this.tpsEl.title =
        tps > 0 && lastActive
          ? `not running — last activity ${fmtAgo(lastActive)}`
          : "no tokens in the last 60 s";
    }
    this.tpsUnit.style.display = running ? "" : "none";

    const limit = Number(session.context.limit) || 0;
    this.el.dataset.tps = running ? latestTps(session).toFixed(1) : "";
    this.el.dataset.contextPct = limit ? String(Math.round(sessionContextRatio(session) * 100)) : "unknown";
    this.el.dataset.requests = String(session.requests || 0);
    this.el.dataset.runtimeMs = String(session.duration_ms || 0);

    const ratio = sessionContextRatio(session);
    const contextLabel = session.context.limit
      ? `${Math.round(ratio * 100)} percent of the context window`
      : `${fmtTokens(session.context.used)} context tokens`;
    this.el.setAttribute(
      "aria-label",
      `${session.status}, ${session.agent_name || agentName(session.agent)} on ${session.machine_id}, ${session.title}. Runtime ${fmtRuntime(session.duration_ms)}, ${session.requests} requests, ${session.subagents.count} subagents, ${contextLabel}, ${
        running ? `${fmtTps(tps)} tokens per second current` : this.notRunningAria(session, tps)
      }. Press Enter to open details.`
    );
  }

  notRunningAria(session, tps) {
    const lastActive = latestSampleTime(session);
    if (tps > 0 && lastActive) return `not running — last activity ${fmtAgo(lastActive)}`;
    return "no tokens in the last 60 seconds";
  }

  showTitleTip(event) {
    if (this.titleEl.scrollWidth <= this.titleEl.clientWidth + 1) {
      hideTip();
      return;
    }
    el.tip.textContent = this.titleEl.textContent;
    window.clearTimeout(tipHideTimer);
    const width = el.tip.offsetWidth || 200;
    const height = el.tip.offsetHeight || 24;
    const left = clamp(event.clientX - width / 2, 10, window.innerWidth - width - 10);
    const top = event.clientY - height - 12;
    el.tip.style.left = `${left}px`;
    el.tip.style.top = `${Math.max(8, top)}px`;
    if (el.tip.hidden) {
      el.tip.hidden = false;
      requestAnimationFrame(() => {
        el.tip.dataset.open = "true";
      });
    } else {
      el.tip.dataset.open = "true";
    }
  }

  setHidden(hidden) {
    this.el.dataset.hidden = hidden ? "true" : "false";
  }

  remove() {
    this.el.remove();
  }
}

function buildCards() {
  const existing = new Map(cards);
  cards = new Map();
  const next = new Map();
  for (const session of state.snapshot.sessions) {
    const card = existing.get(session.id) || new SessionCard(session);
    card.update(session);
    next.set(session.id, card);
  }
  for (const [id, card] of existing) {
    if (!next.has(id)) card.remove();
  }
  cards = next;
}

function refreshCards() {
  if (!state.snapshot) return;
  if (cards.size !== state.snapshot.sessions.length) {
    buildCards();
    applyFilters({ animate: false });
  }
  for (const session of state.snapshot.sessions) {
    cards.get(session.id)?.update(session);
  }
  updateAmbientPulses();
  updateBoardOverflow();
}

let tipHideTimer = 0;
function hideTip() {
  el.tip.dataset.open = "false";
  window.clearTimeout(tipHideTimer);
  tipHideTimer = window.setTimeout(() => {
    if (el.tip.dataset.open !== "true") el.tip.hidden = true;
  }, 110);
}

/* ------------------------------------------------------------------ */
/* Filters, sort, empty state                                          */
/* ------------------------------------------------------------------ */

function currentFilterState() {
  return {
    machineId: state.machineId,
    status: state.status,
    agent: state.agent,
    query: state.query,
    sort: state.sort,
  };
}

function applyFilters({ animate = true, updateTopology = true } = {}) {
  if (!state.snapshot) return;
  // a machine switch can retire the selected agent; drop it before matching
  if (state.agent && state.machineId) {
    const present = state.snapshot.sessions.some(
      (session) => session.machine_id === state.machineId && session.agent === state.agent
    );
    if (!present) state.agent = null;
  }
  const matched = filterSessions(state.snapshot.sessions, currentFilterState());
  const sorted = sortSessions(matched, state.sort);
  const matchedIds = new Set(matched.map((session) => session.id));

  if (animate && flip) flip.capture("[data-flip-id]");
  for (const [id, card] of cards) card.setHidden(!matchedIds.has(id));
  el.board.replaceChildren(...sorted.map((session) => cards.get(session.id)?.el).filter(Boolean));
  for (const card of cards.values()) card.spark?.resize();
  centerLastRow();
  state.visibleCount = sorted.length;
  updateAmbientPulses();
  if (animate && flip) flip.play("[data-flip-id]");
  updateBoardOverflow();

  el.empty.hidden = sorted.length > 0;
  emptyNoteExtra = describeFilters();
  el.emptyNote.textContent = emptyNoteExtra;

  refreshToolbar();
  refreshRail();
  updateStatusbar();
  if (updateTopology && topology) topology.setState(currentFilterState());
}

/** Centre the trailing row so a 10-item board doesn't leave a ragged orphan pair. */
function centerLastRow() {
  const nodes = Array.from(el.board.querySelectorAll(".card")).filter(
    (card) => card.dataset.hidden !== "true"
  );
  for (const card of nodes) card.style.gridColumnStart = "";
  if (nodes.length < 2) return;
  const columns = getComputedStyle(el.board).gridTemplateColumns.split(" ").filter(Boolean).length;
  if (columns < 2 || nodes.length <= columns) return;
  const remainder = nodes.length % columns;
  if (!remainder) return;
  const offset = Math.floor((columns - remainder) / 2);
  if (offset > 0) nodes[nodes.length - remainder].style.gridColumnStart = String(offset + 1);
}

function updateBoardOverflow() {
  const view = el.boardView;
  if (!view) return;
  const overflow = view.scrollHeight - view.clientHeight > 10;
  const atBottom = view.scrollTop + view.clientHeight >= view.scrollHeight - 40;
  view.classList.toggle("has-more", overflow && !atBottom);
}

/** Ambient pulses are a scarce signal: top running cards + hovered/inspector only. */
function updateAmbientPulses() {
  const ranked = [];
  for (const card of cards.values()) {
    if (card.session?.status === "running") ranked.push({ card, tps: latestTps(card.session) });
  }
  ranked.sort((a, b) => b.tps - a.tps);
  const keep = new Set(ranked.slice(0, 10).map((entry) => entry.card.id));
  if (state.hoveredCardId) keep.add(state.hoveredCardId);
  if (state.selectedSessionId) keep.add(state.selectedSessionId);
  let index = 0;
  for (const card of cards.values()) {
    const ambient = keep.has(card.id);
    card.el.classList.toggle("is-ambient", ambient);
    if (ambient) {
      card.el.style.setProperty("--ambient-delay", `${(-(index % 10) * 0.41).toFixed(2)}s`);
      index += 1;
    }
  }
}

function describeFilters() {
  const bits = [];
  if (state.machineId) bits.push(state.machineId);
  if (state.status !== "all") bits.push(state.status);
  if (state.agent) bits.push(agentName(state.agent));
  if (state.query) bits.push(`“${state.query}”`);
  return bits.length ? `Filtered by ${bits.join(" / ")}` : "Adjust the filters or search.";
}

function resetFilters() {
  state.machineId = null;
  state.status = "all";
  state.agent = null;
  state.query = "";
  el.search.value = "";
  applyFilters({ animate: true });
  writeHash();
}

/* ------------------------------------------------------------------ */
/* Views                                                               */
/* ------------------------------------------------------------------ */

function ensureTopology() {
  if (topology) return topology;
  topology = new Topology({
    svgEl: el.topology,
    tipEl: el.topoTip,
    container: el.topologyView,
    onSelectSession: (id) => openSession(id, null),
    onSelectMachine: (machineId) => {
      state.machineId = state.machineId === machineId ? null : machineId;
      applyFilters({ animate: true });
      writeHash();
    },
    onSelectAgent: (machineId, agentId) => {
      state.machineId = state.machineId === machineId ? null : machineId;
      state.agent = state.agent === agentId ? null : agentId;
      applyFilters({ animate: true });
      writeHash();
    },
  });
  topology.setSnapshot(state.snapshot);
  topology.setState(currentFilterState());
  return topology;
}

function setView(view, { animate = true, updateHash = true } = {}) {
  if (view !== "board" && view !== "topology") return;
  if (state.view === view) return;
  state.view = view;
  const apply = () => {
    el.boardView.hidden = view !== "board";
    el.topologyView.hidden = view !== "topology";
    for (const button of $$("[data-view]", el.viewToggle)) {
      button.setAttribute("aria-pressed", String(button.dataset.view === view));
    }
    if (view === "topology") {
      ensureTopology();
      topology.setSnapshot(state.snapshot);
      topology.setState(currentFilterState());
      topology.show();
    } else if (topology) {
      topology.hide();
      void el.topologyView.offsetWidth;
    }
  };
  if (animate) withViewTransition(apply);
  else apply();
  if (updateHash) writeHash();
  window.__dashboardView = view;
}

/* ------------------------------------------------------------------ */
/* Inspector                                                           */
/* ------------------------------------------------------------------ */

function openSession(id, sourceEl = null) {
  const session = state.snapshot?.sessions.find((entry) => entry.id === id);
  if (!session || !inspector) return;
  state.selectedSessionId = id;
  updateAmbientPulses();
  inspector.open(session, { sourceEl: sourceEl || cards.get(id)?.el || null });
  writeHash();
  hideTip();
}

function renderInspectorIfOpen() {
  if (!inspector?.isOpen || !state.selectedSessionId) return;
  const session = state.snapshot?.sessions.find((entry) => entry.id === state.selectedSessionId);
  if (session) inspector.patch(session);
}

/* ------------------------------------------------------------------ */
/* Deep links                                                          */
/* ------------------------------------------------------------------ */

function parseHash() {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const params = new URLSearchParams(raw);
  return {
    view: params.get("view"),
    session: params.get("session"),
    machine: params.get("machine"),
    status: params.get("status"),
    agent: params.get("agent"),
    q: params.get("q") || params.get("query"),
    sort: params.get("sort"),
  };
}

async function applyHashFromLocation(initial = false) {
  const hash = parseHash();
  let changed = false;
  if (hash.machine !== null) {
    state.machineId = hash.machine && hash.machine !== "all" ? hash.machine : null;
    changed = true;
  }
  if (hash.status) {
    state.status = STATUS_FILTERS.some(([key]) => key === hash.status) ? hash.status : "all";
    changed = true;
  }
  if (hash.agent !== null) {
    state.agent = hash.agent && hash.agent !== "all" ? hash.agent : null;
    changed = true;
  }
  if (hash.q !== null) {
    state.query = hash.q;
    el.search.value = hash.q;
    changed = true;
  }
  if (hash.sort) {
    state.sort = ["recent", "context", "tps"].includes(hash.sort) ? hash.sort : "recent";
    for (const button of $$("[data-sort]", el.sort)) {
      button.setAttribute("aria-pressed", String(button.dataset.sort === state.sort));
    }
    changed = true;
  }
  if (changed) applyFilters({ animate: !initial });
  if (hash.view === "topology") {
    setView("topology", { animate: !initial, updateHash: false });
  } else if (hash.view === "board") {
    setView("board", { animate: !initial, updateHash: false });
  }
  if (hash.session) {
    await new Promise((resolve) => requestAnimationFrame(() => resolve()));
    openSession(hash.session, null);
  }
}

function writeHash() {
  const params = new URLSearchParams();
  if (state.view === "topology") params.set("view", "topology");
  if (state.machineId) params.set("machine", state.machineId);
  if (state.status !== "all") params.set("status", state.status);
  if (state.agent) params.set("agent", state.agent);
  if (state.query) params.set("q", state.query);
  if (state.sort !== "recent") params.set("sort", state.sort);
  if (state.selectedSessionId) params.set("session", state.selectedSessionId);
  const hash = params.toString();
  if (window.location.hash === (hash ? `#${hash}` : "")) return;
  try {
    history.replaceState(null, "", hash ? `#${hash}` : window.location.pathname + window.location.search);
  } catch {
    /* ignore */
  }
}

/* ------------------------------------------------------------------ */
/* Entrance animation                                                  */
/* ------------------------------------------------------------------ */

function playEntrance() {
  if (reducedMotion()) return;
  const brand = $(".brand");
  const metrics = $$(".metric", el.fleetMetrics);
  const toggle = $(".topbar-end");
  enterStagger([brand, ...metrics, toggle], { delay: 0, step: 22, duration: 300, distance: 6 });
  enterStagger($$(".rail-row", el.railList), { delay: 70, step: 28, duration: 300, distance: 8 });
  enterStagger([$(".toolbar")], { delay: 110, step: 0, duration: 260, distance: 6 });
  const cardEls = Array.from(el.board.querySelectorAll(".card"));
  enterStagger(cardEls, { delay: 140, step: 32, duration: 320, distance: 8 });
  cardEls.forEach((card, index) => {
    const controller = cards.get(card.dataset.sessionId);
    controller?.spark?.playEntrance(140 + index * 32 + 160);
  });
  waveSpark?.playEntrance(120);
  el.body.dataset.ready = "true";
}

/* ------------------------------------------------------------------ */
/* Events                                                              */
/* ------------------------------------------------------------------ */

function bindStaticEvents() {
  el.viewToggle.addEventListener("click", (event) => {
    const button = event.target.closest("[data-view]");
    if (!button) return;
    setView(button.dataset.view);
  });

  el.railList.addEventListener("click", (event) => {
    const row = event.target.closest("[data-machine-id]");
    if (!row) return;
    const id = row.dataset.machineId;
    state.machineId = !id || id === "all" || state.machineId === id ? null : id;
    applyFilters({ animate: true });
    writeHash();
  });

  el.sort.addEventListener("click", (event) => {
    const button = event.target.closest("[data-sort]");
    if (!button) return;
    state.sort = button.dataset.sort;
    for (const entry of $$("[data-sort]", el.sort)) {
      entry.setAttribute("aria-pressed", String(entry === button));
    }
    applyFilters({ animate: true });
    writeHash();
  });

  let searchTimer = 0;
  el.search.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      state.query = el.search.value.trim();
      applyFilters({ animate: true });
      writeHash();
    }, 120);
  });

  el.emptyReset.addEventListener("click", () => {
    resetFilters();
  });

  el.boardView.addEventListener("scroll", updateBoardOverflow, { passive: true });

  document.addEventListener(
    "pointerdown",
    (event) => {
      const target = event.target.closest(".card, .chip");
      if (target) target.classList.add("is-pressing");
    },
    true
  );
  document.addEventListener(
    "pointerup",
    () => {
      const pressed = document.querySelectorAll(".is-pressing");
      pressed.forEach((target) => {
        target.classList.remove("is-pressing");
        target.classList.add("is-releasing");
        window.setTimeout(() => target.classList.remove("is-releasing"), 170);
      });
    },
    true
  );

  el.board.addEventListener("keydown", (event) => {
    const card = event.target.closest(".card");
    if (!card) return;
    if (event.key === "ArrowRight" || event.key === "ArrowDown" || event.key === "ArrowLeft" || event.key === "ArrowUp") {
      event.preventDefault();
      const visible = visibleCards();
      const index = visible.indexOf(card);
      const delta = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : -1;
      const next = visible[clamp(index + delta, 0, visible.length - 1)];
      next?.focus();
    } else if (event.key === "Home") {
      event.preventDefault();
      visibleCards()[0]?.focus();
    } else if (event.key === "End") {
      event.preventDefault();
      visibleCards().at(-1)?.focus();
    }
  });

  window.addEventListener("keydown", (event) => {
    if (event.defaultPrevented) return;
    const target = event.target;
    const typing = target instanceof HTMLElement && /input|textarea|select/i.test(target.tagName);
    if (typing) {
      if (event.key === "Escape") {
        el.search.blur();
      }
      return;
    }
    if (event.key === "/") {
      event.preventDefault();
      el.search.focus();
      return;
    }
    if (event.key === "Escape") {
      if (inspector?.isOpen) return;
      if (state.view === "topology" && (state.machineId || state.agent || state.status !== "all" || state.query)) {
        resetFilters();
        return;
      }
      if (state.view === "topology") {
        setView("board");
      }
    }
    if (event.key === "1") setView("board");
    if (event.key === "2") setView("topology");
  });

  window.addEventListener("hashchange", () => {
    applyHashFromLocation(false);
  });

  onReducedMotionChange(() => {
    topology?.applyMotionPreference?.();
    saveSparklines();
  });

  document.addEventListener("visibilitychange", () => {
    setAmbientPaused(document.hidden);
    if (document.hidden) {
      topology?.pause();
    } else if (topology?.visible) {
      topology.start();
    }
  });

  window.addEventListener("resize", () => {
    topoTip();
    waveSpark?.resize();
    for (const card of cards.values()) card.spark?.resize();
    inspector?.resize?.();
    centerLastRow();
    updateBoardOverflow();
    if (state.view === "topology") topology?.relayout({ animate: false });
  });
}

function saveSparklines() {
  // re-plot with the new motion preference (slide vs snap is decided per tick)
  for (const card of cards.values()) card.spark?.render();
  waveSpark?.render();
}

function bindAdditionalEvents() {
  // topology zoom controls
  $("#topo-zoom-in")?.addEventListener("click", () => topology?.zoomBy(1.25));
  $("#topo-zoom-out")?.addEventListener("click", () => topology?.zoomBy(0.8));
  $("#topo-reset")?.addEventListener("click", () => topology?.reset());
}

function topoTip() {
  // helper to keep the tooltip hidden when resizing
  if (el.topoTip) el.topoTip.dataset.open = "false";
}

function visibleCards() {
  return Array.from(el.board.querySelectorAll(".card")).filter(
    (card) => card.dataset.hidden !== "true" && !card.hidden
  );
}

let lastAnnounce = 0;
function announceSummary() {
  if (!state.snapshot || !el.liveSummary) return;
  const now = Date.now();
  if (now - lastAnnounce < 9000) return;
  lastAnnounce = now;
  const fleet = computeFleet(state.snapshot);
  el.liveSummary.textContent = `${fleet.sessionsActive} sessions running on ${fleet.machinesOnline} machines, ${fmtTps(fleet.tps)} tokens per second.`;
}

/* ------------------------------------------------------------------ */
/* QA hooks                                                            */
/* ------------------------------------------------------------------ */

window.__dashboardState = () => ({
  mode: state.mode,
  view: state.view,
  sessionId: state.selectedSessionId,
  filters: currentFilterState(),
  cards: cards.size,
  paused: state.paused,
});

window.__dashboardPause = () => {
  state.paused = true;
  sim?.pause();
  return true;
};

window.__dashboardResume = () => {
  state.paused = false;
  sim?.resume();
  return true;
};

window.__dashboardAdvance = (seconds = 1) => {
  if (!sim) return 0;
  const frames = sim.advance(seconds);
  scheduleRefresh();
  renderInspectorIfOpen();
  return frames;
};

window.__dashboardSnapshot = () => state.snapshot;
window.__dashboardMotion = () => ({
  wave: waveSpark
    ? { values: waveSpark.values.slice(-4), scaleMax: waveSpark.scaleMax, sliding: waveSpark.sliding }
    : null,
  cards: Array.from(cards.values())
    .filter((card) => card.session?.status === "running")
    .slice(0, 4)
    .map((card) => ({
      id: card.id,
      text: card.tpsValue.textContent,
      dataTps: card.el.dataset.tps,
      values: card.spark.values.slice(-4),
      sliding: card.spark.sliding,
    })),
  inspector: inspector?.isOpen
    ? {
        metric: inspector.tpsNumber?.textContent,
        chartValues: inspector.chart?.values.slice(-4),
      }
    : null,
});
window.__dashboardOpenSession = (id) => openSession(id);
window.__dashboardTopology = () => setView("topology");
window.__dashboardBoard = () => setView("board");
