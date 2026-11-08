/**
 * topology.js — layered machine → agent → session node-link view.
 *
 * - persistent node identity: filtering transforms existing nodes, never rebuilds
 * - bezier links with particles whose speed is proportional to TPS
 * - hover highlights the whole path and dims the rest (never to transparent)
 * - wheel zoom (0.6–2.4), drag pan, reset; re-layout animates 320ms
 * - ambient particles pause on document.hidden and reduced motion
 */

import { h, svg, clamp } from "./dom.js";
import { fmtTps, fmtPct, fmtInt, fmtAgoShort, agentMark, agentName } from "./format.js";
import { STATUS_COLORS } from "./charts.js";
import { sessionContextRatio, latestTps, latestTpsSum } from "./data.js";
import { reducedMotion } from "./motion.js";

const MACHINE_W = 136;
const MACHINE_H = 46;
const AGENT_R = 13;
const LAYOUT_DURATION = 320;

const easeState = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

function truncate(text, maxChars) {
  const value = String(text || "");
  return value.length > maxChars ? `${value.slice(0, Math.max(1, maxChars - 1))}…` : value;
}

export class Topology {
  constructor({ svgEl, tipEl, container, onSelectSession, onSelectMachine, onSelectAgent }) {
    this.svg = svgEl;
    this.tip = tipEl;
    this.container = container;
    this.onSelectSession = onSelectSession;
    this.onSelectMachine = onSelectMachine;
    this.onSelectAgent = onSelectAgent;

    this.viewport = svg("g", { class: "topo-viewport" });
    this.linkLayer = svg("g", { class: "topo-links" });
    this.particleLayer = svg("g", { class: "topo-particles" });
    this.nodeLayer = svg("g", { class: "topo-nodes" });
    this.viewport.append(this.linkLayer, this.particleLayer, this.nodeLayer);
    this.chromeLayer = svg("g", { class: "topo-chrome" });
    this.svg.append(this.viewport, this.chromeLayer);

    this.nodes = new Map(); // key -> node
    this.links = [];
    this.linkByKey = new Map();
    this.particles = [];
    this.snapshot = null;
    this.state = { machineId: null, status: "all", agent: null, query: "", sort: "recent" };
    this.visible = false;
    this.transform = { x: 0, y: 0, k: 1 };
    this.raf = 0;
    this.lastFrame = 0;
    this.layoutAnimation = 0;
    this.transformAnimation = 0;
    this.hovered = null;
    this.hotSessions = new Set();
    this.panMoved = false;
    this.tipHideTimer = 0;
    this.width = 0;
    this.height = 0;

    this.bindPanZoom();
    this.onVisibility = () => {
      if (document.hidden) this.pause();
      else if (this.visible) this.start();
    };
    document.addEventListener("visibilitychange", this.onVisibility);
    this.resizeObserver = new ResizeObserver(() => {
      if (this.visible) this.relayout({ animate: false });
    });
    this.resizeObserver.observe(this.svg);
  }

  /* ---------------------------------------------------------------- */
  /* Structure                                                        */
  /* ---------------------------------------------------------------- */

  setSnapshot(snapshot) {
    this.snapshot = snapshot;
    this.hotSessions = new Set(
      snapshot.sessions
        .filter((session) => session.status === "running")
        .sort((a, b) => latestTps(b) - latestTps(a))
        .slice(0, 8)
        .map((session) => session.id)
    );
    this.syncNodes();
    if (this.visible) this.relayout({ animate: true });
  }

  setState(state) {
    this.state = { ...this.state, ...state };
    if (this.visible) this.relayout({ animate: true });
  }

  /** Data-only refresh for liveness: keeps layout and node identity intact. */
  updateValues(snapshot) {
    if (!snapshot) return;
    this.snapshot = snapshot;
    this.hotSessions = new Set(
      snapshot.sessions
        .filter((session) => session.status === "running")
        .sort((a, b) => latestTps(b) - latestTps(a))
        .slice(0, 8)
        .map((session) => session.id)
    );
    const machinesById = new Map(snapshot.machines.map((machine) => [machine.id, machine]));
    const agentsByKey = new Map();
    for (const machine of snapshot.machines) {
      for (const agent of machine.agents) agentsByKey.set(`${machine.id}::${agent.id}`, { machine, agent });
    }
    const sessionsById = new Map(snapshot.sessions.map((session) => [session.id, session]));
    for (const node of this.nodes.values()) {
      if (node.kind === "machine") {
        const machine = machinesById.get(node.machineId);
        if (machine) this.updateMachineNode(node, machine);
      } else if (node.kind === "agent") {
        const entry = agentsByKey.get(`${node.machineId}::${node.agentId}`);
        if (entry) this.updateAgentNode(node, entry.machine, entry.agent);
      } else {
        const session = sessionsById.get(node.sessionId);
        if (session) this.updateSessionNode(node, session);
      }
    }
    for (const link of this.links) {
      if (link.sessionId) {
        link.geo.tps = sessionsById.get(link.sessionId)?.tps || 0;
      } else {
        const sessions = this.snapshot.sessions.filter(
          (session) =>
            session.machine_id === link.machineId &&
            session.agent === link.agentId &&
            session.status === "running"
        );
        link.geo.tps = latestTpsSum(sessions);
      }
    }
  }

  nodeKey(kind, ...ids) {
    return `${kind}:${ids.join("::")}`;
  }

  syncNodes() {
    if (!this.snapshot) return;
    const wanted = new Set();

    const ensure = (key, factory, updater, args) => {
      wanted.add(key);
      let node = this.nodes.get(key);
      if (!node) {
        node = factory.call(this, ...args);
        node.key = key;
        this.nodes.set(key, node);
        this.nodeLayer.append(node.el);
      }
      updater.call(this, node, ...args);
      return node;
    };

    for (const machine of this.snapshot.machines) {
      ensure(
        this.nodeKey("machine", machine.id),
        this.createMachineNode,
        this.updateMachineNode,
        [machine]
      );
      for (const agent of machine.agents) {
        ensure(
          this.nodeKey("agent", machine.id, agent.id),
          this.createAgentNode,
          this.updateAgentNode,
          [machine, agent]
        );
      }
    }
    for (const session of this.snapshot.sessions) {
      ensure(
        this.nodeKey("session", session.id),
        this.createSessionNode,
        this.updateSessionNode,
        [session]
      );
    }

    for (const [key, node] of this.nodes) {
      if (!wanted.has(key)) {
        node.el.remove();
        this.nodes.delete(key);
      }
    }
  }

  createMachineNode(machine) {
    const el = svg("g", {
      class: "topo-node node-machine",
      "data-node-kind": "machine",
      "data-node-id": machine.id,
      tabindex: "0",
      role: "button",
    });
    const bg = svg("rect", {
      class: "node-machine-bg",
      width: MACHINE_W,
      height: MACHINE_H,
      rx: 9,
      x: -MACHINE_W / 2,
      y: -MACHINE_H / 2,
    });
    const ring = svg("rect", {
      class: "focus-ring",
      width: MACHINE_W + 6,
      height: MACHINE_H + 6,
      rx: 12,
      x: -MACHINE_W / 2 - 3,
      y: -MACHINE_H / 2 - 3,
    });
    const dot = svg("circle", {
      class: "node-status",
      cx: -MACHINE_W / 2 + 12,
      cy: -MACHINE_H / 2 + 14,
      r: 2.6,
    });
    const name = svg("text", { class: "node-name", x: -MACHINE_W / 2 + 20, y: -MACHINE_H / 2 + 18 });
    const meta = svg("text", { class: "node-meta", x: -MACHINE_W / 2 + 12, y: MACHINE_H / 2 - 10 });
    const count = svg("text", {
      class: "node-count",
      x: MACHINE_W / 2 - 12,
      y: MACHINE_H / 2 - 10,
      "text-anchor": "end",
    });
    el.append(bg, ring, dot, name, meta, count);
    const node = { el, kind: "machine", machineId: machine.id, bg, dot, name, meta, count, x: 0, y: 0 };
    el.addEventListener("click", (event) => {
      event.stopPropagation();
      if (this.panMoved) return;
      this.onSelectMachine?.(machine.id);
    });
    el.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        this.onSelectMachine?.(machine.id);
      }
    });
    this.bindNodeHover(node);
    return node;
  }

  updateMachineNode(node, machine) {
    node.machine = machine;
    const offline = !machine.online;
    node.el.classList.toggle("is-offline", offline);
    node.dot.setAttribute("fill", offline ? STATUS_COLORS.idle : STATUS_COLORS.running);
    node.name.textContent = truncate(machine.name, 17);
    node.meta.textContent = offline
      ? "offline"
      : `online / ${[machine.platform, machine.arch].filter(Boolean).join(" / ")}`.replace(/ \/ $/, "");
    const sessions = this.snapshot.sessions.filter((session) => session.machine_id === machine.id);
    node.sessionCount = sessions.length;
    node.count.textContent = `${sessions.length} sess`;
    node.el.setAttribute(
      "aria-label",
      `${machine.name}, ${offline ? "offline" : "online"}, ${machine.agents.length} agents, ${sessions.length} sessions`
    );
  }

  createAgentNode(machine, agent) {
    const el = svg("g", {
      class: "topo-node node-agent",
      "data-node-kind": "agent",
      "data-node-id": `${machine.id}::${agent.id}`,
      tabindex: "0",
      role: "button",
    });
    const bg = svg("circle", { class: "agent-bg", r: AGENT_R });
    const ring = svg("circle", { class: "focus-ring", r: AGENT_R + 4 });
    const mark = svg("text", { class: "agent-mark", "text-anchor": "middle", y: 3.2 });
    const name = svg("text", { class: "agent-name", x: 20, y: 3.6 });
    el.append(bg, ring, mark, name);
    const node = {
      el,
      kind: "agent",
      machineId: machine.id,
      agentId: agent.id,
      bg,
      mark,
      name,
      x: 0,
      y: 0,
    };
    el.addEventListener("click", (event) => {
      event.stopPropagation();
      if (this.panMoved) return;
      this.onSelectAgent?.(machine.id, agent.id);
    });
    el.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        this.onSelectAgent?.(machine.id, agent.id);
      }
    });
    this.bindNodeHover(node);
    return node;
  }

  updateAgentNode(node, machine, agent) {
    node.machine = machine;
    node.agent = agent;
    node.mark.textContent = agentMark(agent.id);
    node.name.textContent = truncate(agent.name || agentName(agent.id), 14);
    node.el.classList.toggle("is-offline", !machine.online || agent.status === "offline");
    node.el.setAttribute(
      "aria-label",
      `${agent.name} on ${machine.name}, ${agent.status}, ${agent.sessions_active} active sessions`
    );
  }

  createSessionNode(session) {
    const el = svg("g", {
      class: "topo-node node-session",
      "data-node-kind": "session",
      "data-node-id": session.id,
      tabindex: "0",
      role: "button",
    });
    const hit = svg("rect", { class: "node-hit", x: -12, y: -12, width: 260, height: 24, rx: 6 });
    const dot = svg("circle", { class: "session-dot", r: 5 });
    const pulse = svg("circle", { class: "session-pulse", r: 8 });
    const ring = svg("circle", { class: "focus-ring", r: 10 });
    const label = svg("text", { class: "session-label", x: 12, y: 3.4 });
    el.append(hit, dot, pulse, ring, label);
    const node = { el, kind: "session", sessionId: session.id, dot, pulse, ring, label, x: 0, y: 0 };
    el.addEventListener("click", (event) => {
      event.stopPropagation();
      if (this.panMoved) return;
      this.onSelectSession?.(session.id);
    });
    el.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        this.onSelectSession?.(session.id);
      }
    });
    this.bindNodeHover(node);
    return node;
  }

  updateSessionNode(node, session) {
    node.session = session;
    node.machineId = session.machine_id;
    node.agentId = session.agent;
    const radius = this.sessionRadius(session);
    node.dot.setAttribute("fill", STATUS_COLORS[session.status] || STATUS_COLORS.idle);
    node.dot.setAttribute("r", radius.toFixed(2));
    node.ring.setAttribute("r", (radius + 4).toFixed(2));
    node.pulse.style.display =
      session.status === "running" && this.hotSessions.has(session.id) ? "" : "none";
    node.label.textContent = truncate(session.title, this.labelChars || 34);
    node.el.setAttribute(
      "aria-label",
      `${session.title}, ${session.status}, agent ${session.agent_name}, machine ${session.machine_id}`
    );
  }

  sessionRadius(session) {
    const used = Math.max(0, Number(session.context?.used) || 0);
    const reference = Math.max(1, ...this.snapshot.sessions.map((entry) => entry.context?.used || 0));
    return 4.2 + Math.sqrt(used / reference) * 5.4;
  }

  /* ---------------------------------------------------------------- */
  /* Layout                                                           */
  /* ---------------------------------------------------------------- */

  matches(session) {
    const state = this.state;
    if (state.machineId && session.machine_id !== state.machineId) return false;
    if (state.status !== "all" && session.status !== state.status) return false;
    if (state.agent && session.agent !== state.agent) return false;
    if (state.query) {
      const needle = state.query.toLowerCase();
      const haystack = `${session.title} ${session.project} ${session.agent_name} ${session.machine_id}`.toLowerCase();
      if (!haystack.includes(needle)) return false;
    }
    return true;
  }

  visibleSets() {
    const snapshot = this.snapshot;
    const machines = snapshot.machines.filter(
      (machine) => !this.state.machineId || machine.id === this.state.machineId
    );
    const sessions = snapshot.sessions.filter((session) => this.matches(session));
    const sessionIds = new Set(sessions.map((session) => session.id));

    // agents stay in the graph even with zero matching sessions: structure is stable
    const agents = new Map();
    for (const machine of machines) {
      for (const agent of machine.agents) {
        const list = sessions.filter(
          (session) => session.machine_id === machine.id && session.agent === agent.id
        );
        agents.set(this.nodeKey("agent", machine.id, agent.id), { machine, agent, sessions: list });
      }
    }
    return { sessions, sessionIds, agents, machines };
  }

  relayout({ animate = false } = {}) {
    if (!this.snapshot) return;
    const rect = this.svg.getBoundingClientRect();
    const measuredWidth = Math.round(rect.width || 0);
    const measuredHeight = Math.round(rect.height || 0);
    if (measuredWidth < 240 || measuredHeight < 220) {
      this.layoutRetry = (this.layoutRetry || 0) + 1;
      if (this.layoutRetry < 8) {
        if (this.pendingLayout) cancelAnimationFrame(this.pendingLayout);
        this.pendingLayout = requestAnimationFrame(() => this.relayout({ animate: false }));
        return;
      }
    } else {
      this.layoutRetry = 0;
    }
    const width = Math.max(360, measuredWidth || window.innerWidth - 300);
    const height = Math.max(280, measuredHeight || window.innerHeight - 220);
    this.width = width;
    this.height = height;
    this.svg.setAttribute("viewBox", `0 0 ${width} ${height}`);

    const { sessions, sessionIds, agents, machines } = this.visibleSets();
    const machineX = clamp(width * 0.105, 88, 150);
    const agentX = width * 0.4;
    const sessionX = width * (sessions.length > 18 ? 0.78 : 0.7);

    const top = 70;
    const bottom = height - 70;
    const available = Math.max(120, bottom - top);

    // sessions group by agent; each cluster is centred on its agent and clusters
    // never overlap. For many sessions the gaps contract until everything fits.
    const agentsByMachine = new Map();
    for (const [key, entry] of agents) {
      if (!agentsByMachine.has(entry.machine.id)) agentsByMachine.set(entry.machine.id, []);
      agentsByMachine.get(entry.machine.id).push({ key, ...entry });
    }

    const sorted = sortForTopology(sessions, this.state.sort);
    const sessionsByAgent = new Map();
    for (const session of sorted) {
      const key = this.nodeKey("agent", session.machine_id, session.agent);
      if (!sessionsByAgent.has(key)) sessionsByAgent.set(key, []);
      sessionsByAgent.get(key).push(session);
    }
    const machineAgents = machines.map((machine) =>
      (agentsByMachine.get(machine.id) || []).map((entry) => ({
        ...entry,
        sessions: sessionsByAgent.get(entry.key) || [],
      }))
    );

    const layoutGroup = (list, gapS, gapA) => {
      const spans = list.map((entry) => Math.max(0, entry.sessions.length - 1) * gapS);
      const rel = [];
      let cursor = 0;
      list.forEach((entry, index) => {
        if (index > 0) cursor += spans[index - 1] / 2 + spans[index] / 2 + gapA;
        rel.push(cursor);
      });
      let min = 0;
      let max = 0;
      if (list.length) {
        min = Infinity;
        max = -Infinity;
        rel.forEach((value, index) => {
          min = Math.min(min, value - spans[index] / 2);
          max = Math.max(max, value + spans[index] / 2);
        });
      }
      return { spans, rel, min, max, extent: Math.max(56, max - min) };
    };

    let gapS = 24;
    let gapA = 40;
    let groups = machineAgents.map((list) => layoutGroup(list, gapS, gapA));
    let totalExtent = groups.reduce((acc, group) => acc + group.extent + 28, 0);
    let gaps = machines.length > 1 ? Math.max(54, (available - totalExtent) / (machines.length - 1)) : 0;
    for (let attempt = 0; attempt < 6 && totalExtent + gaps * (machines.length - 1) > available; attempt += 1) {
      const factor = Math.max(0.45, available / (totalExtent + gaps * (machines.length - 1)));
      gapS = Math.max(9, gapS * factor);
      gapA = Math.max(26, gapA * factor);
      groups = machineAgents.map((list) => layoutGroup(list, gapS, gapA));
      totalExtent = groups.reduce((acc, group) => acc + group.extent + 28, 0);
      gaps = machines.length > 1 ? Math.max(54, (available - totalExtent) / (machines.length - 1)) : 0;
    }

    const stackHeight = totalExtent + gaps * Math.max(0, machines.length - 1);
    let cursor = top + Math.max(0, (available - stackHeight) / 2);
    const agentY = new Map();
    const sessionY = new Map();
    const machineCenter = new Map();
    machines.forEach((machine, index) => {
      const group = groups[index];
      const list = machineAgents[index];
      const bandHeight = Math.max(56, group.extent + 28);
      const center = cursor + bandHeight / 2;
      cursor += bandHeight + gaps;
      const relCenter = list.length ? (group.min + group.max) / 2 : 0;
      let agentSum = 0;
      list.forEach((entry, agentIndex) => {
        const y = center + group.rel[agentIndex] - relCenter;
        agentY.set(entry.key, y);
        agentSum += y;
        const gap = gapS;
        entry.sessions.forEach((session, sessionIndex) => {
          sessionY.set(
            session.id,
            clamp(y + (sessionIndex - (entry.sessions.length - 1) / 2) * gap, 34, height - 34)
          );
        });
      });
      machineCenter.set(machine.id, list.length ? agentSum / list.length : center);
    });

    const machineY = new Map(machineCenter);

    const dense = sessions.length > 18;
    this.svg.classList.toggle("is-dense", dense);
    this.labelChars = dense
      ? Math.round(clamp((width - sessionX - 28) / 6.1, 8, 14))
      : Math.round(clamp((width - sessionX - 28) / 5.35, 14, 64));
    for (const node of this.nodes.values()) {
      if (node.kind === "session" && node.session) {
        node.label.textContent = truncate(node.session.title, this.labelChars);
      }
    }
    this.updateChrome(width, height, machineX, agentX, sessionX, machines.length, agents.size, sessions.length);

    for (const [key, entry] of agents) {
      const node = this.nodes.get(key);
      if (node) node.el.classList.toggle("is-quiet", entry.sessions.length === 0);
    }

    const next = new Map();
    const machinePos = new Map();
    for (const machine of machines) {
      const pos = { x: machineX, y: machineCenter.get(machine.id) ?? height / 2 };
      machinePos.set(machine.id, pos);
      const key = this.nodeKey("machine", machine.id);
      if (this.nodes.has(key)) next.set(key, pos);
    }
    const agentPos = new Map();
    for (const [key] of agents) {
      const pos = { x: agentX, y: agentY.get(key) ?? height / 2 };
      agentPos.set(key, pos);
      if (this.nodes.has(key)) next.set(key, pos);
    }
    const sessionPos = new Map();
    for (const session of sessions) {
      const pos = { x: sessionX, y: sessionY.get(session.id) ?? height / 2 };
      sessionPos.set(session.id, pos);
      const key = this.nodeKey("session", session.id);
      if (this.nodes.has(key)) next.set(key, pos);
    }

    const geometry = this.buildGeometry(agentPos, machinePos, sessionPos);
    const previousPositions = this.positions || new Map();
    const previousGeometry = new Map(this.links.map((link) => [link.key, { ...link.geo }]));
    this.positions = next;

    this.applyVisibility(machines, agents, sessionIds);
    this.syncLinks(geometry);
    if (animate && !reducedMotion() && previousPositions.size) {
      this.animateLayout(previousPositions, next, previousGeometry, geometry);
    } else {
      for (const [key, pos] of next) {
        const node = this.nodes.get(key);
        if (!node) continue;
        node.x = pos.x;
        node.y = pos.y;
        node.el.setAttribute("transform", `translate(${pos.x.toFixed(2)} ${pos.y.toFixed(2)})`);
      }
      this.applyGeometry(geometry);
    }
    this.buildParticles();
  }

  updateChrome(width, height, machineX, agentX, sessionX, machineCount, agentCount, sessionCount) {
    const guide = (x) =>
      svg("line", {
        class: "topo-guide",
        x1: x.toFixed(1),
        x2: x.toFixed(1),
        y1: 44,
        y2: (height - 44).toFixed(1),
      });
    const label = (x, text) =>
      svg("text", {
        class: "topo-layer-label",
        x: x.toFixed(1),
        y: 26,
        "text-anchor": "middle",
        text,
      });
    this.chromeLayer.replaceChildren(
      guide(machineX),
      guide(agentX),
      guide(sessionX),
      label(machineX, `${machineCount} ${machineCount === 1 ? "machine" : "machines"}`),
      label(agentX, `${agentCount} ${agentCount === 1 ? "agent" : "agents"}`),
      label(sessionX, `${sessionCount} ${sessionCount === 1 ? "session" : "sessions"}`)
    );
  }

  applyVisibility(machines, agents, sessionIds) {
    const machineIds = new Set(machines.map((machine) => machine.id));
    for (const node of this.nodes.values()) {
      let visible = true;
      if (node.kind === "machine") visible = machineIds.has(node.machineId);
      else if (node.kind === "agent") visible = agents.has(node.key);
      else visible = sessionIds.has(node.sessionId);
      node.visible = visible;
      node.el.style.opacity = visible ? "" : "0";
      node.el.style.pointerEvents = visible ? "" : "none";
      if (!visible) {
        node.el.setAttribute("tabindex", "-1");
      } else {
        node.el.setAttribute("tabindex", "0");
      }
    }
  }

  buildGeometry(agentPos, machinePos, sessionPos) {
    const geometry = [];
    for (const [key, node] of this.nodes) {
      if (node.kind !== "agent" || !node.visible) continue;
      const machine = machinePos.get(node.machineId);
      const agent = agentPos.get(key);
      if (!machine || !agent) continue;
      const x1 = machine.x + MACHINE_W / 2;
      const x2 = agent.x - AGENT_R;
      geometry.push({
        key: `link:${node.machineId}::${node.agentId}`,
        machineId: node.machineId,
        agentId: node.agentId,
        sessionId: null,
        kind: "agent",
        tps: latestTpsSum(
          this.snapshot.sessions.filter(
            (session) =>
              session.machine_id === node.machineId &&
              session.agent === node.agentId &&
              session.status === "running"
          )
        ),
        x1,
        y1: machine.y,
        c1x: x1 + (x2 - x1) * 0.45,
        c1y: machine.y,
        c2x: x1 + (x2 - x1) * 0.55,
        c2y: agent.y,
        x2,
        y2: agent.y,
      });
    }
    for (const [key, node] of this.nodes) {
      if (node.kind !== "session" || !node.visible) continue;
      const session = node.session;
      if (!session) continue;
      const agent = agentPos.get(this.nodeKey("agent", session.machine_id, session.agent));
      const pos = sessionPos.get(session.id);
      if (!agent || !pos) continue;
      const radius = this.sessionRadius(session);
      const x1 = agent.x + AGENT_R;
      const x2 = pos.x - radius - 1.5;
      geometry.push({
        key: `link:${session.machine_id}::${session.agent}::${session.id}`,
        machineId: session.machine_id,
        agentId: session.agent,
        sessionId: session.id,
        kind: "session",
        tps: latestTps(session),
        x1,
        y1: agent.y,
        c1x: x1 + (x2 - x1) * 0.45,
        c1y: agent.y,
        c2x: x1 + (x2 - x1) * 0.55,
        c2y: pos.y,
        x2,
        y2: pos.y,
      });
    }
    return geometry;
  }

  linkPath(geo) {
    return `M${geo.x1.toFixed(1)} ${geo.y1.toFixed(1)}C${geo.c1x.toFixed(1)} ${geo.c1y.toFixed(1)} ${geo.c2x.toFixed(1)} ${geo.c2y.toFixed(1)} ${geo.x2.toFixed(1)} ${geo.y2.toFixed(1)}`;
  }

  syncLinks(geometry) {
    const wanted = new Map(geometry.map((geo) => [geo.key, geo]));
    for (const link of this.links) {
      if (!wanted.has(link.key)) {
        link.el.remove();
        this.linkByKey.delete(link.key);
      }
    }
    const nextLinks = [];
    for (const geo of geometry) {
      let link = this.linkByKey.get(geo.key);
      if (!link) {
        const el = svg("path", { class: "topo-link" });
        this.linkLayer.append(el);
        link = { key: geo.key, el, geo };
        this.linkByKey.set(geo.key, link);
      }
      link.geo = geo;
      link.machineId = geo.machineId;
      link.agentId = geo.agentId;
      link.sessionId = geo.sessionId;
      nextLinks.push(link);
    }
    this.links = nextLinks;
  }

  applyGeometry(geometry) {
    for (const geo of geometry) {
      const link = this.linkByKey.get(geo.key);
      if (link) link.el.setAttribute("d", this.linkPath(geo));
    }
    this.measureLinks();
    this.applyHoverClasses();
  }

  measureLinks() {
    for (const link of this.links) {
      let length = 0;
      try {
        length = link.el.getTotalLength();
      } catch {
        length = 0;
      }
      link.length = length;
    }
  }

  animateLayout(previousPositions, next, previousGeometry, geometry) {
    if (this.layoutAnimation) cancelAnimationFrame(this.layoutAnimation);
    const start = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - start) / LAYOUT_DURATION);
      const eased = easeState(t);
      for (const [key, pos] of next) {
        const node = this.nodes.get(key);
        if (!node) continue;
        const before = previousPositions.get(key) || pos;
        node.x = before.x + (pos.x - before.x) * eased;
        node.y = before.y + (pos.y - before.y) * eased;
        node.el.setAttribute("transform", `translate(${node.x.toFixed(2)} ${node.y.toFixed(2)})`);
      }
      for (const geo of geometry) {
        const link = this.linkByKey.get(geo.key);
        if (!link) continue;
        const before = previousGeometry.get(geo.key);
        if (!before) {
          link.el.setAttribute("d", this.linkPath(geo));
          continue;
        }
        const mixed = { ...geo };
        for (const field of ["x1", "y1", "c1x", "c1y", "c2x", "c2y", "x2", "y2"]) {
          mixed[field] = before[field] + (geo[field] - before[field]) * eased;
        }
        link.el.setAttribute("d", this.linkPath(mixed));
      }
      if (t < 1) {
        this.layoutAnimation = requestAnimationFrame(step);
      } else {
        this.layoutAnimation = 0;
        this.applyGeometry(geometry);
      }
    };
    this.layoutAnimation = requestAnimationFrame(step);
  }

  /* ---------------------------------------------------------------- */
  /* Particles                                                        */
  /* ---------------------------------------------------------------- */

  buildParticles() {
    this.particleLayer.replaceChildren();
    this.particles = [];
    if (reducedMotion()) return;
    for (const link of this.links) {
      if (!link.geo || (link.geo.tps || 0) < 0.5) continue;
      const count = link.kind === "agent" ? 2 : 2;
      for (let index = 0; index < count; index += 1) {
        const el = svg("circle", {
          class: `topo-particle${index === 1 ? " is-soft" : ""}`,
          r: link.kind === "agent" ? 1.5 : 1.9,
        });
        this.particleLayer.append(el);
        this.particles.push({
          el,
          link,
          phase: (index * 0.5 + ((link.key.length * 7) % 10) / 10) % 1,
        });
      }
    }
  }

  updateParticles(time) {
    for (const particle of this.particles) {
      const link = particle.link;
      if (!link || !link.length) continue;
      const pxPerSecond = clamp(12 + (link.geo.tps || 0) * 1.9, 10, 90);
      const progress = (particle.phase + (time * pxPerSecond) / link.length) % 1;
      try {
        const point = link.el.getPointAtLength(progress * link.length);
        particle.el.setAttribute(
          "transform",
          `translate(${point.x.toFixed(2)} ${point.y.toFixed(2)})`
        );
      } catch {
        /* path not ready */
      }
    }
  }

  /* ---------------------------------------------------------------- */
  /* Hover, tooltip, focus                                            */
  /* ---------------------------------------------------------------- */

  bindNodeHover(node) {
    const el = node.el;
    el.addEventListener("pointerenter", (event) => {
      this.setHover(node);
      this.showTip(node, event.clientX, event.clientY);
    });
    el.addEventListener("pointermove", (event) => {
      if (this.hovered === node) this.showTip(node, event.clientX, event.clientY);
    });
    el.addEventListener("pointerleave", () => {
      this.setHover(null);
      this.hideTip();
    });
    el.addEventListener("focus", () => {
      this.setHover(node);
      const rect = this.svg.getBoundingClientRect();
      const k = this.transform.k;
      this.showTip(
        node,
        rect.left + node.x * k + this.transform.x,
        rect.top + node.y * k + this.transform.y - 10
      );
    });
    el.addEventListener("blur", () => {
      if (this.hovered === node) {
        this.setHover(null);
        this.hideTip();
      }
    });
  }

  setHover(node) {
    this.hovered = node;
    this.applyHoverClasses();
  }

  chainFor(node) {
    const nodes = new Set();
    const links = new Set();
    if (!node) return { nodes, links };
    if (node.kind === "machine") {
      for (const other of this.nodes.values()) {
        if (other.kind === "machine" && other.machineId === node.machineId) nodes.add(other.el);
        if (other.kind === "agent" && other.machineId === node.machineId) nodes.add(other.el);
        if (other.kind === "session" && other.machineId === node.machineId) nodes.add(other.el);
      }
      for (const link of this.links) {
        if (link.machineId === node.machineId) links.add(link.key);
      }
    } else if (node.kind === "agent") {
      const machineKey = this.nodeKey("machine", node.machineId);
      if (this.nodes.has(machineKey)) nodes.add(this.nodes.get(machineKey).el);
      for (const other of this.nodes.values()) {
        if (
          other.kind === "agent" &&
          other.machineId === node.machineId &&
          other.agentId === node.agentId
        )
          nodes.add(other.el);
        if (
          other.kind === "session" &&
          other.session &&
          other.session.machine_id === node.machineId &&
          other.session.agent === node.agentId
        )
          nodes.add(other.el);
      }
      for (const link of this.links) {
        if (link.machineId === node.machineId && link.agentId === node.agentId) links.add(link.key);
      }
    } else {
      if (node.el) nodes.add(node.el);
      const agentKey = this.nodeKey("agent", node.session.machine_id, node.session.agent);
      const machineKey = this.nodeKey("machine", node.session.machine_id);
      if (this.nodes.has(agentKey)) nodes.add(this.nodes.get(agentKey).el);
      if (this.nodes.has(machineKey)) nodes.add(this.nodes.get(machineKey).el);
      for (const link of this.links) {
        if (link.sessionId === node.sessionId) links.add(link.key);
        else if (
          !link.sessionId &&
          link.machineId === node.session.machine_id &&
          link.agentId === node.session.agent
        )
          links.add(link.key);
      }
    }
    return { nodes, links };
  }

  applyHoverClasses() {
    if (!this.hovered) {
      for (const node of this.nodes.values()) node.el.classList.remove("is-dim", "is-hover");
      for (const link of this.links) link.el.classList.remove("is-hl", "is-dim");
      return;
    }
    const { nodes, links } = this.chainFor(this.hovered);
    for (const node of this.nodes.values()) {
      const inChain = nodes.has(node.el);
      node.el.classList.toggle("is-dim", !inChain && node.el.style.opacity !== "0");
      node.el.classList.toggle("is-hover", node.el === this.hovered.el);
    }
    for (const link of this.links) {
      const inChain = links.has(link.key);
      link.el.classList.toggle("is-hl", inChain);
      link.el.classList.toggle("is-dim", !inChain);
    }
  }

  showTip(node, clientX, clientY) {
    if (!this.tip) return;
    let title = "";
    let meta = [];
    if (node.kind === "machine") {
      const machine = node.machine;
      const sessions = this.snapshot.sessions.filter((session) => session.machine_id === machine.id);
      const active = sessions.filter((session) => session.status === "running");
      title = machine.name;
      meta = [
        machine.online ? "online" : "offline",
        `${machine.agents.length} ${machine.agents.length === 1 ? "agent" : "agents"}`,
        `${sessions.length} ${sessions.length === 1 ? "session" : "sessions"}`,
        machine.online
          ? `${fmtTps(latestTpsSum(active))} t/s`
          : `last seen ${fmtAgoShort(machine.last_seen)} ago`,
      ];
    } else if (node.kind === "agent") {
      const agent = node.agent;
      title = agent.name || agentName(agent.id);
      const agentTps = latestTpsSum(
        this.snapshot.sessions.filter(
          (session) =>
            session.machine_id === node.machineId &&
            session.agent === agent.id &&
            session.status === "running"
        )
      );
      meta = [
        agent.status,
        `${agent.sessions_active} active`,
        `${fmtTps(agentTps)} t/s`,
        `${fmtInt(agent.requests)} req`,
      ];
    } else {
      const session = node.session;
      title = session.title;
      const limit = session.context?.limit;
      meta = [
        session.status,
        session.status === "running" ? `${fmtTps(latestTps(session))} t/s` : "no tokens in the last 60 s",
        limit ? `context ${fmtPct(sessionContextRatio(session) * 100)}` : "context —",
        session.subagents.count ? `${session.subagents.count} subagents` : "no subagents",
      ];
    }
    this.tip.replaceChildren(
      h("div", { class: "topo-tip-title", text: title }),
      h("div", { class: "topo-tip-meta", text: meta.join(" / ") })
    );
    this.tip.hidden = false;
    const containerRect = this.container.getBoundingClientRect();
    const tipWidth = this.tip.offsetWidth || 170;
    const tipHeight = this.tip.offsetHeight || 46;
    let left = clientX - containerRect.left + 14;
    let top = clientY - containerRect.top + 14;
    if (left + tipWidth > containerRect.width - 10) left = clientX - containerRect.left - tipWidth - 12;
    if (top + tipHeight > containerRect.height - 10) top = clientY - containerRect.top - tipHeight - 12;
    this.tip.style.left = `${Math.max(8, left)}px`;
    this.tip.style.top = `${Math.max(8, top)}px`;
    window.clearTimeout(this.tipHideTimer);
    if (this.tip.hidden) {
      this.tip.hidden = false;
      requestAnimationFrame(() => {
        this.tip.dataset.open = "true";
      });
    } else {
      this.tip.dataset.open = "true";
    }
  }

  hideTip() {
    if (!this.tip) return;
    this.tip.dataset.open = "false";
    window.clearTimeout(this.tipHideTimer);
    this.tipHideTimer = window.setTimeout(() => {
      if (this.tip.dataset.open !== "true") this.tip.hidden = true;
    }, 110);
  }

  /** Runtime prefers-reduced-motion changes: rebuild ambient particles accordingly. */
  applyMotionPreference() {
    this.buildParticles();
    if (reducedMotion()) this.stop();
    else if (this.visible) this.start();
  }

  /* ---------------------------------------------------------------- */
  /* Pan / zoom                                                       */
  /* ---------------------------------------------------------------- */

  bindPanZoom() {
    const svgEl = this.svg;
    let dragging = false;
    let startX = 0;
    let startY = 0;
    let originX = 0;
    let originY = 0;

    // Drag panning uses window listeners rather than pointer capture: capturing
    // the pointer on the <svg> would retarget pointerup and break click events on
    // the node groups underneath.
    this.onPointerDown = (event) => {
      if (event.button !== 0) return;
      dragging = true;
      this.panMoved = false;
      startX = event.clientX;
      startY = event.clientY;
      originX = this.transform.x;
      originY = this.transform.y;
      svgEl.classList.add("is-panning");
      window.removeEventListener("pointermove", this.onPointerMove);
      window.addEventListener("pointermove", this.onPointerMove, { passive: true });
      window.addEventListener("pointerup", this.onPointerUp, { passive: true });
      window.addEventListener("pointercancel", this.onPointerUp, { passive: true });
    };
    this.onPointerMove = (event) => {
      if (!dragging) return;
      const dx = event.clientX - startX;
      const dy = event.clientY - startY;
      if (Math.abs(dx) + Math.abs(dy) > 4) this.panMoved = true;
      this.setTransform(originX + dx, originY + dy, this.transform.k, { animate: false });
      this.hideTip();
    };
    this.onPointerUp = () => {
      if (!dragging) return;
      dragging = false;
      svgEl.classList.remove("is-panning");
      window.removeEventListener("pointermove", this.onPointerMove);
      window.removeEventListener("pointerup", this.onPointerUp);
      window.removeEventListener("pointercancel", this.onPointerUp);
      window.setTimeout(() => {
        this.panMoved = false;
      }, 0);
    };
    this.onWheel = (event) => {
      event.preventDefault();
      const rect = svgEl.getBoundingClientRect();
      const px = event.clientX - rect.left;
      const py = event.clientY - rect.top;
      const factor = Math.exp(-event.deltaY * 0.0016);
      const nextK = clamp(this.transform.k * factor, 0.6, 2.4);
      if (nextK === this.transform.k) return;
      const ratio = nextK / this.transform.k;
      this.setTransform(px - (px - this.transform.x) * ratio, py - (py - this.transform.y) * ratio, nextK, {
        animate: false,
      });
      this.hideTip();
    };
    this.onDoubleClick = (event) => {
      if (event.target.closest(".topo-node")) return;
      this.reset();
    };

    svgEl.addEventListener("pointerdown", this.onPointerDown);
    svgEl.addEventListener("wheel", this.onWheel, { passive: false });
    svgEl.addEventListener("dblclick", this.onDoubleClick);
  }

  setTransform(x, y, k, { animate = false } = {}) {
    if (animate && !reducedMotion()) {
      this.animateTransform({ x, y, k }, 260);
      return;
    }
    this.transform = { x, y, k };
    this.applyTransform();
  }

  animateTransform(target, duration = 260) {
    const from = { ...this.transform };
    const start = performance.now();
    if (this.transformAnimation) cancelAnimationFrame(this.transformAnimation);
    const step = (now) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = easeState(t);
      this.transform = {
        x: from.x + (target.x - from.x) * eased,
        y: from.y + (target.y - from.y) * eased,
        k: from.k + (target.k - from.k) * eased,
      };
      this.applyTransform();
      this.transformAnimation = t < 1 ? requestAnimationFrame(step) : 0;
    };
    this.transformAnimation = requestAnimationFrame(step);
  }

  applyTransform() {
    const { x, y, k } = this.transform;
    this.viewport.setAttribute(
      "transform",
      `translate(${x.toFixed(2)} ${y.toFixed(2)}) scale(${k.toFixed(4)})`
    );
  }

  zoomBy(factor) {
    const width = this.svg.clientWidth || 900;
    const height = this.svg.clientHeight || 560;
    const nextK = clamp(this.transform.k * factor, 0.6, 2.4);
    const ratio = nextK / this.transform.k;
    const cx = width / 2;
    const cy = height / 2;
    this.setTransform(
      cx - (cx - this.transform.x) * ratio,
      cy - (cy - this.transform.y) * ratio,
      nextK,
      { animate: true }
    );
  }

  reset() {
    this.setTransform(0, 0, 1, { animate: true });
  }

  /* ---------------------------------------------------------------- */
  /* Frame loop / visibility                                          */
  /* ---------------------------------------------------------------- */

  show() {
    this.visible = true;
    this.layoutRetry = 0;
    this.relayout({ animate: false });
    requestAnimationFrame(() => {
      if (this.visible) this.relayout({ animate: false });
    });
    this.start();
  }

  hide() {
    this.visible = false;
    this.hideTip();
    this.setHover(null);
    this.stop();
  }

  start() {
    if (this.raf) return;
    this.lastFrame = performance.now();
    const frame = (now) => {
      this.raf = 0;
      if (!this.visible || document.hidden) return;
      this.updateParticles(now / 1000);
      this.raf = requestAnimationFrame(frame);
    };
    this.raf = requestAnimationFrame(frame);
  }

  pause() {
    this.stop();
  }

  stop() {
    if (this.raf) {
      cancelAnimationFrame(this.raf);
      this.raf = 0;
    }
    if (this.transformAnimation) {
      cancelAnimationFrame(this.transformAnimation);
      this.transformAnimation = 0;
    }
    if (this.layoutAnimation) {
      cancelAnimationFrame(this.layoutAnimation);
      this.layoutAnimation = 0;
    }
  }

  destroy() {
    this.stop();
    document.removeEventListener("visibilitychange", this.onVisibility);
    this.resizeObserver?.disconnect();
    this.svg.removeEventListener("pointerdown", this.onPointerDown);
    window.removeEventListener("pointermove", this.onPointerMove);
    window.removeEventListener("pointerup", this.onPointerUp);
    window.removeEventListener("pointercancel", this.onPointerUp);
    this.svg.removeEventListener("wheel", this.onWheel);
    this.svg.removeEventListener("dblclick", this.onDoubleClick);
  }
}

function sortForTopology(sessions, sort) {
  const list = sessions.slice();
  if (sort === "tps") list.sort((a, b) => b.tps - a.tps);
  else if (sort === "context") list.sort((a, b) => sessionContextRatio(b) - sessionContextRatio(a));
  else
    list.sort(
      (a, b) => new Date(b.updated_at || 0).getTime() - new Date(a.updated_at || 0).getTime()
    );
  return list;
}
