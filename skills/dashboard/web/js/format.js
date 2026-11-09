/**
 * format.js — number, time and label formatting.
 * Rules from brief §4.10: numerals stay sans + tabular lining figures (CSS);
 * serif only for display words; mono only for session ids and timestamps.
 */

const intFormat = new Intl.NumberFormat("en-US");

const AGENT_NAMES = {
  "claude-code": "Claude Code",
  codex: "Codex",
  pi: "pi",
  dsh: "DSH",
  other: "Agent",
};

const AGENT_MARKS = {
  "claude-code": "CC",
  codex: "CX",
  pi: "π",
  dsh: "DSH",
  other: "•",
};

const STATUS_SET = new Set(["running", "waiting", "done", "failed", "idle"]);

export function agentName(id, fallback) {
  return fallback || AGENT_NAMES[id] || (id ? String(id) : "Agent");
}

export function agentMark(id) {
  return AGENT_MARKS[id] || "•";
}

export function normalizeStatus(status) {
  const value = String(status || "").toLowerCase();
  return STATUS_SET.has(value) ? value : "idle";
}

export function fmtInt(n) {
  const value = Number(n);
  return Number.isFinite(value) ? intFormat.format(Math.round(value)) : "—";
}

export function fmtTps(n) {
  const value = Number(n);
  if (!Number.isFinite(value) || value <= 0) return "0.0";
  return value.toFixed(1);
}

export function fmtTokens(n) {
  const value = Number(n);
  if (!Number.isFinite(value) || value <= 0) return "0";
  if (value >= 1_000_000) {
    const v = value / 1_000_000;
    return `${v >= 10 ? Math.round(v) : v.toFixed(1)}M`;
  }
  if (value >= 1_000) {
    const v = value / 1_000;
    return `${v >= 100 ? Math.round(v) : v.toFixed(1)}k`;
  }
  return String(Math.round(value));
}

export function fmtUsd(n) {
  const value = Number(n);
  if (!Number.isFinite(value)) return "—";
  if (value >= 100) return `$${Math.round(value)}`;
  if (value > 0 && value < 0.01) return "<$0.01";
  return `$${value.toFixed(2)}`;
}

export function fmtPercent(ratio, digits = 0) {
  const value = Number(ratio);
  if (!Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function fmtPct(n) {
  const value = Number(n);
  if (!Number.isFinite(value)) return "—";
  return `${Math.round(value)}%`;
}

export function fmtRuntime(ms) {
  const value = Number(ms);
  if (!Number.isFinite(value) || value <= 0) return "—";
  const totalSeconds = Math.floor(value / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  if (minutes > 0) return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
  return `${seconds}s`;
}

export function fmtDurationMs(ms) {
  const value = Number(ms);
  if (!Number.isFinite(value) || value < 0) return "—";
  if (value < 1000) return `${Math.round(value)}ms`;
  if (value < 60_000) {
    const v = value / 1000;
    return `${v >= 10 ? Math.round(v) : v.toFixed(1)}s`;
  }
  const minutes = Math.floor(value / 60_000);
  const seconds = Math.round((value % 60_000) / 1000);
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}

function parseDate(value) {
  if (value === null || value === undefined) return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  if (typeof value === "number") {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }
  const date = new Date(String(value));
  return Number.isNaN(date.getTime()) ? null : date;
}

const pad2 = (n) => String(n).padStart(2, "0");

/** Local wall-clock HH:MM:SS — timestamps are the only mono text besides ids. */
export function fmtClock(value) {
  const date = parseDate(value);
  if (!date) return "—";
  return `${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`;
}

export function fmtAgo(value, nowMs = Date.now()) {
  const date = parseDate(value);
  if (!date) return "—";
  const diff = Math.max(0, nowMs - date.getTime());
  const seconds = Math.round(diff / 1000);
  if (seconds < 2) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function fmtAgoShort(value, nowMs = Date.now()) {
  const date = parseDate(value);
  if (!date) return "—";
  const diff = Math.max(0, nowMs - date.getTime());
  const seconds = Math.round(diff / 1000);
  if (seconds < 2) return "now";
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

export function fmtRelativeSeconds(seconds) {
  const value = Math.round(seconds);
  if (value === 0) return "now";
  return `${Math.abs(value)}s ago`;
}

/** "1 agent", "3 agents" — keeps the rail and tooltips grammatical. */
export function fmtCount(n, singular, plural = `${singular}s`) {
  const value = Number(n) || 0;
  return `${value} ${value === 1 ? singular : plural}`;
}

/** "3 machines / 9 sessions" — slash separator, no middle dots (brief §4.10). */
export function joinCounts(parts) {
  return parts.filter(Boolean).join(" / ");
}

export function statusWord(status) {
  return normalizeStatus(status);
}

export function titleCaseAgent(value) {
  return agentName(value);
}
