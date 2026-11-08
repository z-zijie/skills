/**
 * charts.js — hand-rolled SVG charts. No dependencies, no per-frame path churn.
 *
 * Rules applied from brief §4.10 / research:
 * - sparkline: no axes, no gridlines, no labels, last-point dot, one shared window
 * - context ring is a capacity gauge against the model window; part-to-whole
 *   composition uses a stacked bar
 * - chart marks use the AA-safe chart palette (>=3:1 on paper)
 * - paths are rebuilt once per data tick (1 Hz), drawn in with stroke-dashoffset
 */

import { h, svg, clamp } from "./dom.js";
import {
  fmtTokens,
  fmtTps,
  fmtPct,
  fmtDurationMs,
  fmtRelativeSeconds,
  fmtClock,
  fmtAgo,
} from "./format.js";
import { drawIn, reducedMotion, EASE, fadeIn } from "./motion.js";

/* ---------- palette (single source: tokens.css) ---------- */

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export const C = {
  chart1: cssVar("--chart-1"),
  chart2: cssVar("--chart-2"),
  chart3: cssVar("--chart-3"),
  chart4: cssVar("--chart-4"),
  chart5: cssVar("--chart-5"),
  ink: cssVar("--ink"),
  ink2: cssVar("--ink-2"),
  ink3: cssVar("--ink-3"),
  inkFaint: cssVar("--ink-faint"),
  line: cssVar("--line"),
  lineStrong: cssVar("--line-strong"),
  grid: cssVar("--grid-line"),
  ramp1: cssVar("--ramp-1"),
  ramp2: cssVar("--ramp-2"),
  ramp3: cssVar("--ramp-3"),
  ramp4: cssVar("--ramp-4"),
  surface: cssVar("--surface"),
  accent: cssVar("--accent"),
  ok: cssVar("--ok"),
  warn: cssVar("--warn"),
  danger: cssVar("--danger"),
  info: cssVar("--info"),
};

export const STATUS_COLORS = {
  running: C.ok,
  waiting: C.warn,
  done: C.info,
  failed: C.danger,
  idle: C.inkFaint,
};

export const WINDOW_SECONDS = 60;
// gentler start than the entrance ease: the one-step slide must not read as a snap
const SLIDE_EASE = "cubic-bezier(.34,0,.2,1)";

let uid = 0;
const nextId = (prefix) => `${prefix}-${++uid}`;

/** Values + timestamps from a series, using the shared 60s window. */
export function windowSeries(series, window = WINDOW_SECONDS) {
  if (!Array.isArray(series) || series.length === 0) return { values: [], times: [] };
  const points = series
    .map((point) => (Array.isArray(point) ? [Number(point[0]), Number(point[1])] : [null, Number(point)]))
    .filter(([, value]) => Number.isFinite(value));
  const sliced = points.slice(-window);
  return { values: sliced.map(([, value]) => value), times: sliced.map(([time]) => time) };
}

/* ------------------------------------------------------------------ */
/* Sparkline                                                           */
/* ------------------------------------------------------------------ */

export class Spark {
  constructor(container, options = {}) {
    this.container = container;
    this.height = options.height ?? 44;
    this.strokeWidth = options.strokeWidth ?? 1.5;
    this.endRadius = options.endRadius ?? 2.2;
    this.padY = options.padY ?? 4;
    this.window = options.window ?? WINDOW_SECONDS;
    this.values = [];
    this.times = [];
    this.scaleMax = 0;
    this.slideTimer = 0;
    this.sliding = false;
    this.color = options.color || C.chart1;
    this.gradientId = nextId("spark-grad");
    this.clipId = nextId("spark-clip");

    const el = svg("svg", {
      class: "spark",
      height: this.height,
      width: 100,
      "aria-hidden": "true",
      focusable: "false",
      "data-status": options.status || "running",
    });

    const defs = svg("defs");
    this.gradient = svg("linearGradient", { id: this.gradientId, x1: 0, y1: 0, x2: 0, y2: 1 });
    this.stopTop = svg("stop", { offset: "0", "stop-color": this.color, "stop-opacity": 0.16 });
    this.stopBottom = svg("stop", { offset: "1", "stop-color": this.color, "stop-opacity": 0 });
    this.gradient.append(this.stopTop, this.stopBottom);
    this.clip = svg("clipPath", { id: this.clipId });
    this.clipRect = svg("rect", { x: 0, y: 0, width: 100, height: this.height });
    this.clip.append(this.clipRect);
    defs.append(this.gradient, this.clip);

    this.clipGroup = svg("g", { class: "spark-clip" });
    this.clipGroup.setAttribute("clip-path", `url(#${this.clipId})`);
    this.slideGroup = svg("g", { class: "spark-slide" });
    this.area = svg("path", { class: "spark-area", fill: `url(#${this.gradientId})`, d: "" });
    this.line = svg("path", { class: "spark-line", d: "" });
    this.slideGroup.append(this.area, this.line);
    this.clipGroup.append(this.slideGroup);

    this.endRing = svg("circle", { class: "spark-end-ring", r: this.endRadius + 2.2, cx: 0, cy: 0 });
    this.endDot = svg("circle", { class: "spark-end", r: this.endRadius, cx: 0, cy: 0 });

    el.append(defs, this.clipGroup, this.endRing, this.endDot);
    container.append(el);
    this.el = el;
    this.el.classList.toggle("is-running", (options.status || "running") === "running");
    this.width = 100;
    this.resize();
  }

  resize() {
    const measured = Math.max(24, Math.round(this.container.clientWidth || 100));
    if (measured === this.width) return false;
    this.width = measured;
    this.el.setAttribute("width", String(measured));
    this.el.setAttribute("viewBox", `0 0 ${measured} ${this.height}`);
    this.clipRect.setAttribute("width", String(measured));
    if (this.values.length) this.render();
    else this.line.setAttribute("d", "");
    return true;
  }

  setSeries(series, { status, color } = {}) {
    const { values, times } = windowSeries(series, this.window);
    if (status) {
      this.el.setAttribute("data-status", status);
      this.el.classList.toggle("is-running", status === "running");
    }
    if (color) this.applyColor(color);
    this.el.classList.toggle("is-empty", values.length === 0);

    const previousLast = this.times.length ? this.times[this.times.length - 1] : 0;
    const nextLast = times.length ? times[times.length - 1] : 0;
    const appended =
      !reducedMotion() &&
      this.values.length === values.length &&
      values.length > 3 &&
      previousLast > 0 &&
      nextLast > previousLast &&
      nextLast - previousLast <= 2600;
    if (appended) this.slide(values, times);
    else {
      this.values = values;
      this.times = times;
      this.render();
    }
  }

  applyColor(color) {
    this.color = color;
    this.stopTop.setAttribute("stop-color", color);
    this.stopBottom.setAttribute("stop-color", color);
  }

  computeScale(values) {
    const target = Math.max(4, Math.max(...values, 0) * 1.12);
    if (!this.scaleMax) this.scaleMax = target;
    else this.scaleMax = Math.max(target, this.scaleMax * 0.995);
    return this.scaleMax;
  }

  plot(values, scaleMax, step) {
    const padY = this.padY;
    const innerH = Math.max(1, this.height - padY * 2);
    let line = "";
    for (let i = 0; i < values.length; i += 1) {
      const x = 1.5 + i * step;
      const y = this.height - padY - (values[i] / scaleMax) * innerH;
      line += `${i === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`;
    }
    return line;
  }

  render() {
    const width = this.width;
    const height = this.height;
    const values = this.values;
    const padY = this.padY;
    const padX = 1.5;
    if (!values.length) {
      const y = height - padY;
      this.line.setAttribute("d", `M1.5 ${y} L${Math.max(2, width - 1.5)} ${y}`);
      this.area.setAttribute("d", "");
      this.endDot.setAttribute("cx", String(width - 1.5));
      this.endDot.setAttribute("cy", String(y));
      this.endRing.setAttribute("cx", String(width - 1.5));
      this.endRing.setAttribute("cy", String(y));
      return;
    }
    const scaleMax = this.computeScale(values);
    this.el.dataset.scale = scaleMax.toFixed(2);
    const n = values.length;
    const step = (width - padX * 2) / Math.max(1, n - 1);
    const line = this.plot(values, scaleMax, step);
    const lastY = height - padY - (values[n - 1] / scaleMax) * Math.max(1, height - padY * 2);
    this.line.setAttribute("d", line);
    this.area.setAttribute("d", `${line}L${(padX + (n - 1) * step).toFixed(2)} ${height - padY}L${padX.toFixed(2)} ${height - padY}Z`);
    this.endDot.setAttribute("cx", (padX + (n - 1) * step).toFixed(2));
    this.endDot.setAttribute("cy", lastY.toFixed(2));
    this.endRing.setAttribute("cx", (padX + (n - 1) * step).toFixed(2));
    this.endRing.setAttribute("cy", lastY.toFixed(2));
    const live = this.el.classList.contains("is-running");
    this.endDot.style.opacity = live ? "1" : "0.45";
    this.endRing.style.opacity = live ? "1" : "0.3";
    this.slideGroup.removeAttribute("transform");
  }

  /** One-sample slide: old window + new sample shifts left; no per-frame path rebuild. */
  slide(nextValues, nextTimes) {
    const width = this.width;
    const padX = 1.5;
    const n = this.values.length;
    const step = (width - padX * 2) / Math.max(1, n - 1);
    const combined = this.values.concat(nextValues[nextValues.length - 1]);
    const scaleMax = this.computeScale(combined);
    this.el.dataset.scale = scaleMax.toFixed(2);
    const line = this.plot(combined, scaleMax, step);
    const lastX = padX + (combined.length - 1) * step;
    this.line.setAttribute("d", line);
    this.area.setAttribute(
      "d",
      `${line}L${lastX.toFixed(2)} ${this.height - this.padY}L${padX.toFixed(2)} ${this.height - this.padY}Z`
    );
    // hide the endpoint during the slide; it fades back in at its final spot
    this.endDot.style.opacity = "0";
    this.endRing.style.opacity = "0";

    this.sliding = true;
    this.slideAnim?.cancel();
    this.slideGroup.removeAttribute("transform");
    try {
      this.slideAnim = this.slideGroup.animate(
        [{ transform: "translate(0px, 0px)" }, { transform: `translate(${(-step).toFixed(2)}px, 0px)` }],
        { duration: 420, easing: SLIDE_EASE, fill: "forwards" }
      );
    } catch {
      this.slideAnim = null;
    }
    window.clearTimeout(this.slideTimer);
    this.slideTimer = window.setTimeout(() => {
      this.sliding = false;
      // swap to the committed window and clear the transform in the same task
      this.slideAnim?.cancel();
      this.slideAnim = null;
      this.values = nextValues;
      this.times = nextTimes;
      this.render();
    }, 425);
  }

  /** Entrance: line draws in, area and endpoint rise with it. */
  playEntrance(delay = 0) {
    if (reducedMotion()) return;
    drawIn(this.line, { delay });
    fadeIn(this.area, { duration: 460, delay: delay + 80 });
    const endAnimation = this.el.animate([{ opacity: 0 }, { opacity: 1 }], {
      duration: 260,
      delay: delay + 240,
      easing: EASE.out,
      fill: "backwards",
    });
    endAnimation.finished.catch(() => {});
  }
}

/* ------------------------------------------------------------------ */
/* Context capacity ring                                               */
/* ------------------------------------------------------------------ */

export class Donut {
  constructor(container) {
    this.el = h("div", { class: "donut", dataset: { level: "normal" }, "aria-hidden": "true" });
    this.size = 44;
    this.radius = 19;
    this.circumference = 2 * Math.PI * this.radius;
    const root = svg("svg", { viewBox: "0 0 44 44", "aria-hidden": "true", focusable: "false" });
    this.track = svg("circle", {
      class: "donut-track",
      cx: 22,
      cy: 22,
      r: this.radius,
    });
    this.arc = svg("circle", {
      class: "donut-arc",
      cx: 22,
      cy: 22,
      r: this.radius,
      "stroke-dasharray": `${this.circumference.toFixed(2)} ${this.circumference.toFixed(2)}`,
      "stroke-dashoffset": this.circumference.toFixed(2),
    });
    root.append(this.track, this.arc);
    this.label = h("span", { class: "donut-label", text: "—" });
    this.el.append(root, this.label);
    container.append(this.el);
  }

  set(used, limit) {
    const usedValue = Number(used) || 0;
    const limitValue = Number(limit) > 0 ? Number(limit) : 0;
    // capacity gauge vs the model window; unknown window degrades to used tokens
    if (!limitValue) {
      this.el.dataset.level = "unknown";
      this.arc.setAttribute("stroke-dasharray", "2.5 3.5");
      this.arc.setAttribute("stroke-dashoffset", "0");
      this.label.textContent = "n/a";
      this.el.setAttribute("title", `${fmtTokens(usedValue)} context tokens used, model window unknown`);
      return;
    }
    this.arc.setAttribute(
      "stroke-dasharray",
      `${this.circumference.toFixed(2)} ${this.circumference.toFixed(2)}`
    );
    const ratio = clamp(usedValue / limitValue, 0, 1);
    const level = ratio >= 0.85 ? "danger" : ratio >= 0.7 ? "warn" : "normal";
    const offset = this.circumference * (1 - Math.max(ratio, 0.015));
    this.el.dataset.level = level;
    this.arc.setAttribute("stroke-dashoffset", offset.toFixed(2));
    this.label.textContent = fmtPct(ratio * 100);
    this.el.setAttribute("title", `${fmtTokens(usedValue)} of ${fmtTokens(limitValue)} tokens (${fmtPct(ratio * 100)})`);
  }
}

/* ------------------------------------------------------------------ */
/* Inspector throughput area chart                                     */
/* ------------------------------------------------------------------ */

export class AreaChart {
  constructor(container, options = {}) {
    this.container = container;
    this.height = options.height ?? 112;
    this.window = options.window ?? WINDOW_SECONDS;
    this.values = [];
    this.times = [];
    this.scaleMax = 0;
    this.slideTimer = 0;
    this.hoverIndex = -1;
    this.endState = { running: true, lastActive: null };
    this.gradientId = nextId("area-grad");
    this.clipId = nextId("area-clip");

    this.wrap = h("div", { class: "throughput-wrap" });
    this.svg = svg("svg", {
      class: "throughput-svg",
      role: "img",
      "aria-label": "Session throughput over the last 60 seconds",
    });
    const defs = svg("defs");
    this.gradient = svg("linearGradient", { id: this.gradientId, x1: 0, y1: 0, x2: 0, y2: 1 });
    this.gradient.append(
      svg("stop", { offset: "0", "stop-color": C.chart1, "stop-opacity": 0.18 }),
      svg("stop", { offset: "1", "stop-color": C.chart1, "stop-opacity": 0.01 })
    );
    this.clip = svg("clipPath", { id: this.clipId });
    this.clipRect = svg("rect", { x: 0, y: 0, width: 0, height: this.height });
    this.clip.append(this.clipRect);
    defs.append(this.gradient, this.clip);

    this.gridGroup = svg("g");
    this.clipGroup = svg("g", { class: "tp-clip" });
    this.clipGroup.setAttribute("clip-path", `url(#${this.clipId})`);
    this.plotGroup = svg("g", { class: "tp-plot" });
    this.area = svg("path", { class: "tp-area", fill: `url(#${this.gradientId})` });
    this.line = svg("path", { class: "tp-line" });
    this.plotGroup.append(this.area, this.line);
    this.clipGroup.append(this.plotGroup);
    this.crosshair = svg("line", { class: "tp-cross" });
    this.dot = svg("circle", { class: "tp-dot", r: 2.6 });
    this.hit = svg("rect", { class: "tp-hit", fill: "transparent", x: 0, y: 0, width: 0, height: this.height });

    this.svg.append(defs, this.gridGroup, this.clipGroup, this.hit, this.crosshair, this.dot);
    this.tip = h("div", { class: "tp-tip" });
    this.tipValue = h("span", { text: "0.0" });
    this.tipUnit = h("span", { class: "tp-tip-k", text: "t/s" });
    this.tipWhen = h("span", { class: "tp-tip-k", text: "" });
    this.tip.append(this.tipValue, this.tipUnit, this.tipWhen);
    this.wrap.append(this.svg, this.tip);
    this.axis = h("div", { class: "tp-axis" }, [
      h("span", { text: "60s ago" }),
      h("span", { text: "now" }),
    ]);
    container.append(this.wrap, this.axis);

    this.onPointerMove = this.handlePointerMove.bind(this);
    this.onPointerLeave = this.handlePointerLeave.bind(this);
    this.onKeyDown = this.handleKeyDown.bind(this);
    this.onFocus = () => {
      if (this.hoverIndex < 0) this.setHover(this.values.length - 1);
    };
    this.onBlur = () => this.clearHover();

    this.svg.addEventListener("pointermove", this.onPointerMove, { passive: true });
    this.svg.addEventListener("pointerleave", this.onPointerLeave, { passive: true });
    this.svg.addEventListener("keydown", this.onKeyDown);
    this.svg.addEventListener("focus", this.onFocus);
    this.svg.addEventListener("blur", this.onBlur);
    this.svg.setAttribute("tabindex", "0");
    this.width = 0;
    this.resize();
  }

  setEndpointState({ running = true, lastActive = null } = {}) {
    this.endState = { running, lastActive };
    if (this.hoverIndex < 0) this.svg.setAttribute("aria-label", this.describe());
  }

  describe() {
    if (this.endState.running) return "Session throughput over the last 60 seconds";
    return this.endState.lastActive
      ? `Session throughput; last active ${fmtAgo(this.endState.lastActive)}`
      : "Session throughput; no recent samples";
  }

  setSeries(series) {
    const { values, times } = windowSeries(series, this.window);
    const previousLast = this.times.length ? this.times[this.times.length - 1] : 0;
    const nextLast = times.length ? times[times.length - 1] : 0;
    const appended =
      !reducedMotion() &&
      this.values.length === values.length &&
      values.length > 3 &&
      previousLast > 0 &&
      nextLast > previousLast &&
      nextLast - previousLast <= 2600;
    if (appended) this.slide(values, times);
    else {
      this.values = values;
      this.times = times;
      this.render();
    }
  }

  computeScale(values) {
    const target = Math.max(6, Math.max(...values, 0) * 1.15);
    if (!this.scaleMax) this.scaleMax = target;
    else this.scaleMax = Math.max(target, this.scaleMax * 0.995);
    return this.scaleMax;
  }

  plotPath(values, step, max) {
    const padX = 2;
    const padTop = 10;
    const padBottom = 3;
    const innerH = Math.max(1, this.height - padTop - padBottom);
    let line = "";
    for (let i = 0; i < values.length; i += 1) {
      const x = padX + i * step;
      const y = this.height - padBottom - (values[i] / max) * innerH;
      line += `${i === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`;
    }
    return line;
  }

  slide(nextValues, nextTimes) {
    const width = this.width;
    const padX = 2;
    const padBottom = 3;
    const n = this.values.length;
    const step = (width - padX * 2) / Math.max(1, n - 1);
    const combined = this.values.concat(nextValues[nextValues.length - 1]);
    const max = this.computeScale(combined);
    this.svg.dataset.scale = max.toFixed(2);
    const line = this.plotPath(combined, step, max);
    const lastX = padX + (combined.length - 1) * step;
    this.line.setAttribute("d", line);
    this.area.setAttribute(
      "d",
      `${line}L${lastX.toFixed(2)} ${this.height - padBottom}L${padX.toFixed(2)} ${this.height - padBottom}Z`
    );
    this.clearHover();
    this.slideAnim?.cancel();
    this.plotGroup.removeAttribute("transform");
    try {
      this.slideAnim = this.plotGroup.animate(
        [{ transform: "translate(0px, 0px)" }, { transform: `translate(${(-step).toFixed(2)}px, 0px)` }],
        { duration: 400, easing: SLIDE_EASE, fill: "forwards" }
      );
    } catch {
      this.slideAnim = null;
    }
    window.clearTimeout(this.slideTimer);
    this.slideTimer = window.setTimeout(() => {
      this.slideAnim?.cancel();
      this.slideAnim = null;
      this.values = nextValues;
      this.times = nextTimes;
      this.render();
    }, 405);
  }

  resize() {
    const width = Math.max(80, Math.round(this.wrap.clientWidth || 300));
    const height = this.height;
    if (width === this.width && this.svg.getAttribute("height") === String(height)) return false;
    this.width = width;
    this.svg.setAttribute("width", String(width));
    this.svg.setAttribute("height", String(height));
    this.svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    this.hit.setAttribute("width", String(width));
    this.hit.setAttribute("height", String(height));
    this.clipRect.setAttribute("width", String(width));
    this.clipRect.setAttribute("height", String(height));
    this.render();
    return true;
  }

  geometry() {
    const padX = 2;
    const padTop = 10;
    const padBottom = 3;
    const width = this.width;
    const height = this.height;
    const values = this.values;
    const n = values.length;
    const max = this.scaleMax || Math.max(6, Math.max(...values, 0) * 1.15);
    const innerW = Math.max(1, width - padX * 2);
    const innerH = Math.max(1, height - padTop - padBottom);
    const x = (i) => padX + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW);
    const y = (v) => height - padBottom - (v / max) * innerH;
    return { padX, padTop, padBottom, width, height, values, n, max, innerW, innerH, x, y };
  }

  render() {
    if (this.values.length) this.scaleMax = this.computeScale(this.values);
    this.svg.dataset.scale = this.scaleMax.toFixed(2);
    this.slideAnim?.cancel();
    this.slideAnim = null;
    this.plotGroup.removeAttribute("transform");
    const g = this.geometry();
    const { values, n, x, y, width, height } = g;
    this.gridGroup.replaceChildren();
    for (const ratio of [0.5, 1]) {
      const gy = Math.round((height - g.padBottom - g.innerH * ratio) * 2) / 2 + 0.5;
      this.gridGroup.append(
        svg("line", { class: "tp-grid", x1: 0, y1: gy, x2: width, y2: gy })
      );
    }
    if (!n) {
      this.line.setAttribute("d", "");
      this.area.setAttribute("d", "");
      return;
    }
    let line = "";
    for (let i = 0; i < n; i += 1) {
      line += `${i === 0 ? "M" : "L"}${x(i).toFixed(2)} ${y(values[i]).toFixed(2)}`;
    }
    this.line.setAttribute("d", line);
    this.area.setAttribute("d", `${line}L${x(n - 1).toFixed(2)} ${height - g.padBottom}L${x(0).toFixed(2)} ${height - g.padBottom}Z`);
    if (this.hoverIndex >= n) this.hoverIndex = n - 1;
    if (this.hoverIndex >= 0) this.positionHover(this.hoverIndex);
  }

  handlePointerMove(event) {
    const g = this.geometry();
    if (!g.n) return;
    const rect = this.svg.getBoundingClientRect();
    const px = clamp(event.clientX - rect.left, 0, rect.width);
    const ratio = rect.width > 0 ? px / rect.width : 0;
    this.setHover(Math.round(ratio * (g.n - 1)));
  }

  handlePointerLeave() {
    this.clearHover();
  }

  handleKeyDown(event) {
    if (!this.values.length) return;
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      const delta = event.key === "ArrowRight" ? 1 : -1;
      const base = this.hoverIndex < 0 ? this.values.length - 1 : this.hoverIndex;
      this.setHover(clamp(base + delta, 0, this.values.length - 1));
    } else if (event.key === "Escape") {
      this.clearHover();
      this.svg.blur();
    }
  }

  setHover(index) {
    if (!this.values.length) return;
    this.hoverIndex = clamp(index, 0, this.values.length - 1);
    this.positionHover(this.hoverIndex);
  }

  positionHover(index) {
    const g = this.geometry();
    if (!g.n) return;
    const cx = g.x(index);
    const cy = g.y(this.values[index]);
    this.crosshair.setAttribute("x1", cx.toFixed(2));
    this.crosshair.setAttribute("x2", cx.toFixed(2));
    this.crosshair.setAttribute("y1", g.padTop - 4);
    this.crosshair.setAttribute("y2", g.height - g.padBottom);
    this.crosshair.style.opacity = "1";
    this.dot.setAttribute("cx", cx.toFixed(2));
    this.dot.setAttribute("cy", cy.toFixed(2));
    this.dot.style.opacity = "1";
    const atEnd = index === g.n - 1;
    if (atEnd && !this.endState.running) {
      // no live number "now" when the session is not running: the metric is "—",
      // so the endpoint reads as the last active moment instead
      this.tipValue.textContent = "—";
      this.tipUnit.style.display = "none";
      this.tipWhen.textContent = this.endState.lastActive
        ? `last active ${fmtAgo(this.endState.lastActive)}`
        : "no recent samples";
      this.dot.style.opacity = "0.45";
      this.svg.setAttribute(
        "aria-label",
        this.endState.lastActive
          ? `Last active ${fmtAgo(this.endState.lastActive)}`
          : "Last active unknown"
      );
    } else {
      const value = fmtTps(this.values[index]);
      this.tipValue.textContent = value;
      this.tipUnit.style.display = "";
      const secondsAgo = g.n - 1 - index;
      this.tipWhen.textContent = fmtRelativeSeconds(secondsAgo);
      this.dot.style.opacity = "1";
      this.svg.setAttribute(
        "aria-label",
        `Throughput ${value} tokens per second at ${secondsAgo} seconds ago`
      );
    }
    const tipWidth = this.tip.offsetWidth || 150;
    const left = clamp(cx, tipWidth / 2 + 6, this.width - tipWidth / 2 - 6);
    this.tip.style.left = `${left.toFixed(1)}px`;
    this.tip.style.opacity = "1";
  }

  clearHover() {
    this.hoverIndex = -1;
    this.crosshair.style.opacity = "0";
    this.dot.style.opacity = "0";
    this.tip.style.opacity = "0";
    this.svg.setAttribute("aria-label", this.describe());
  }

  playEntrance() {
    if (reducedMotion()) return;
    drawIn(this.line, { duration: 600 });
    fadeIn(this.area, { duration: 600 });
  }

  destroy() {
    this.svg.removeEventListener("pointermove", this.onPointerMove);
    this.svg.removeEventListener("pointerleave", this.onPointerLeave);
    this.svg.removeEventListener("keydown", this.onKeyDown);
    this.svg.removeEventListener("focus", this.onFocus);
    this.svg.removeEventListener("blur", this.onBlur);
  }
}

/* ------------------------------------------------------------------ */
/* Context composition: stacked bar + legend                           */
/* ------------------------------------------------------------------ */

const RAMP = [C.ramp1, C.ramp2, C.ramp3, C.ramp4];

/** Composition is only honest when the collector's parts roughly sum to used. */
export function contextComposition(parts, context) {
  const used = Math.max(0, Number(context?.used) || 0);
  const items = (Array.isArray(parts) ? parts : [])
    .map((part, index) => ({
      key: part.key || `part-${index}`,
      label: part.label || part.key || `Part ${index + 1}`,
      tokens: Math.max(0, Number(part.tokens) || 0),
    }))
    .filter((part) => part.tokens > 0);
  const total = items.reduce((acc, part) => acc + part.tokens, 0);
  const valid =
    items.length >= 2 && used > 0 && Math.abs(total - used) <= Math.max(2, used * 0.02);
  return { items, total, used, valid };
}

export class ContextBar {
  constructor(container) {
    this.bar = h("div", { class: "ctx-bar" });
    this.legend = h("div", { class: "ctx-legend" });
    this.total = h("div", { class: "ctx-total" });
    container.append(this.bar, this.legend, this.total);
    this.segments = new Map();
  }

  set(parts, context) {
    const { items, used, valid } = contextComposition(parts, context);
    const limit = Math.max(0, Number(context?.limit) || 0);
    this.bar.replaceChildren();
    this.legend.replaceChildren();
    this.total.replaceChildren();
    this.bar.classList.toggle("is-capacity", !valid);

    if (!valid) {
      // capacity read only: never fabricate a composition from cumulative tokens
      const ratio = limit > 0 ? Math.min(1, used / limit) : 0;
      const segment = h("span", {
        class: "ctx-seg",
        style: {
          flex: limit > 0 ? `${Math.max(ratio, 0.02)} 1 0` : "1 1 0",
          "background-color": limit > 0 ? C.ramp2 : C.lineStrong,
        },
        title: limit > 0
          ? `${fmtTokens(used)} of ${fmtTokens(limit)} context tokens`
          : `${fmtTokens(used)} context tokens, window unknown`,
      });
      this.bar.append(segment);
      this.total.replaceChildren(
        h("span", {
          text: limit > 0 ? `used ${fmtTokens(used)} of ${fmtTokens(limit)}` : `used ${fmtTokens(used)}`,
        }),
        h("span", {
          text: limit > 0 ? `${fmtPct(ratio * 100)} of window` : "window unknown",
        })
      );
      return;
    }

    const sum = items.reduce((acc, item) => acc + item.tokens, 0) || 1;
    items.forEach((item, index) => {
      const color = RAMP[index % RAMP.length];
      const share = item.tokens / sum;
      this.bar.append(
        h("span", {
          class: "ctx-seg",
          style: { flex: `${share} 1 0`, "background-color": color },
          title: `${item.label}: ${fmtTokens(item.tokens)} tokens (${fmtPct(share * 100)})`,
        })
      );
      this.legend.append(
        h("div", { class: "ctx-row" }, [
          h("span", { class: "ctx-swatch", style: { "background-color": color } }),
          h("span", { class: "ctx-label", text: item.label }),
          h("span", { class: "ctx-tokens", text: fmtTokens(item.tokens) }),
          h("span", { class: "ctx-pct", text: fmtPct(share * 100) }),
        ])
      );
    });

    this.total.replaceChildren(
      h("span", { text: `used ${fmtTokens(used)}${limit ? ` of ${fmtTokens(limit)}` : ""}` }),
      h("span", { text: limit ? `${fmtPct((used / limit) * 100)} of window` : "window unknown" })
    );
  }
}

/* ------------------------------------------------------------------ */
/* Subagent node-link tree                                             */
/* ------------------------------------------------------------------ */

export class SubagentTree {
  constructor(container) {
    this.wrap = h("div", { class: "tree-wrap" });
    this.svg = svg("svg", {
      class: "tree-svg",
      role: "img",
      "aria-label": "Subagent tree sized by tokens",
    });
    this.readout = h("div", { class: "tree-readout" });
    this.wrap.append(this.svg);
    container.append(this.wrap, this.readout);
    this.tree = null;
    this.nodes = [];
    this.height = 108;
  }

  set(tree) {
    this.tree = tree || null;
    this.render();
  }

  resize() {
    this.render();
  }

  /** Flatten the contract's subagent_tree into layout nodes (parent-first order). */
  build(raw, depth, out, parent) {
    const entry = {
      id: raw.id || `${depth}-${out.length}`,
      label: raw.label || raw.id || "agent",
      tokens: Math.max(1, Number(raw.tokens) || 1),
      status: raw.status || "idle",
      depth,
      parent,
      children: [],
      x: 0,
      y: 0,
      r: 5,
    };
    out.push(entry);
    for (const child of Array.isArray(raw.children) ? raw.children : []) {
      entry.children.push(this.build(child, depth + 1, out, entry));
    }
    return entry;
  }

  render() {
    const tree = this.tree;
    this.svg.replaceChildren();
    if (!tree) {
      this.svg.style.display = "none";
      this.wrap.classList.add("is-empty");
      this.readout.replaceChildren();
      return;
    }
    this.wrap.classList.remove("is-empty");
    this.svg.style.display = "block";
    const width = Math.max(160, Math.round(this.wrap.clientWidth || 340));
    const height = this.height;
    this.svg.setAttribute("width", String(width));
    this.svg.setAttribute("height", String(height));
    this.svg.setAttribute("viewBox", `0 0 ${width} ${height}`);

    const nodes = [];
    this.build(tree, 0, nodes, null);
    this.nodes = nodes;

    const maxDepth = Math.max(...nodes.map((node) => node.depth));
    const columns = maxDepth + 1;
    const maxTokens = Math.max(1, ...nodes.map((node) => node.tokens));
    const start = 26;
    const end = width - 92;

    for (const node of nodes) {
      node.x = columns === 1 ? start : start + (node.depth / (columns - 1)) * (end - start);
      node.r = 4 + clamp(Math.sqrt(node.tokens / maxTokens), 0, 1) * 8;
    }

    // leaves share the vertical band; parents sit on their children's mean
    const leaves = nodes.filter((node) => !node.children.length);
    const top = 15;
    const bottom = height - 15;
    const gap = leaves.length > 1 ? (bottom - top) / (leaves.length - 1) : 0;
    leaves.forEach((leaf, index) => {
      leaf.y = leaves.length > 1 ? top + index * gap : (top + bottom) / 2;
    });
    const measure = (node) => {
      if (!node.children.length) return node.y;
      const ys = node.children.map((child) => measure(child));
      node.y = ys.reduce((a, b) => a + b, 0) / ys.length;
      return node.y;
    };
    measure(nodes[0]);

    const linkLayer = svg("g");
    const nodeLayer = svg("g");
    const labelLayer = svg("g");

    for (const node of nodes) {
      for (const child of node.children) {
        const x1 = node.x + node.r;
        const y1 = node.y;
        const x2 = child.x - child.r;
        const y2 = child.y;
        const mid = (x1 + x2) / 2;
        linkLayer.append(
          svg("path", {
            class: "tree-link",
            d: `M${x1.toFixed(1)} ${y1.toFixed(1)}C${mid.toFixed(1)} ${y1.toFixed(1)} ${mid.toFixed(1)} ${y2.toFixed(1)} ${x2.toFixed(1)} ${y2.toFixed(1)}`,
          })
        );
      }
    }

    for (const node of nodes) {
      const group = svg("g", {
        class: "tree-node",
        "data-status": node.status,
        transform: `translate(${node.x.toFixed(2)} ${node.y.toFixed(2)})`,
      });
      group.append(svg("circle", { class: "node", r: node.r.toFixed(2), cx: 0, cy: 0 }));
      if (node.status === "running") {
        group.append(svg("circle", { class: "tree-pulse", r: (node.r + 3).toFixed(2), cx: 0, cy: 0 }));
      }
      group.append(
        svg("title", { text: `${node.label}, ${fmtTokens(node.tokens)} tokens, ${node.status}` })
      );
      group.addEventListener("pointerenter", () => this.setHover(node, group), { passive: true });
      group.addEventListener("pointerleave", () => this.clearHover(group), { passive: true });
      nodeLayer.append(group);
    }

    for (const node of nodes) {
      const available = Math.max(40, width - (node.x + node.r + 6) - 4);
      labelLayer.append(
        svg("text", {
          x: (node.x + node.r + 6).toFixed(2),
          y: (node.y + 3).toFixed(2),
          text: this.truncate(node.label, available),
        })
      );
    }

    this.svg.append(linkLayer, nodeLayer, labelLayer);
    this.readout.replaceChildren(
      h("span", { class: "tree-readout-k", text: "main" }),
      h("span", { text: `${nodes.length - 1} subagents` }),
      h("span", { class: "tree-readout-k", text: "/" }),
      h("span", { text: `${fmtTokens(nodes.reduce((acc, node, index) => acc + (index ? node.tokens : 0), 0))} tokens delegated` })
    );
  }

  truncate(label, px) {
    const maxChars = Math.max(4, Math.floor(px / 5.1));
    const text = String(label || "");
    return text.length > maxChars ? `${text.slice(0, Math.max(1, maxChars - 1))}…` : text;
  }

  setHover(node, group) {
    group.classList.add("is-hover");
    this.readout.replaceChildren(
      h("span", { class: "tree-readout-k", text: "node" }),
      h("span", { text: node.label }),
      h("span", { class: "tree-readout-k", text: "/" }),
      h("span", { text: `${fmtTokens(node.tokens)} tokens` }),
      h("span", { class: "tree-readout-k", text: "/" }),
      h("span", { class: `status-word-${node.status}`, text: node.status })
    );
  }

  clearHover(group) {
    group.classList.remove("is-hover");
    if (!this.nodes.length) return;
    this.readout.replaceChildren(
      h("span", { class: "tree-readout-k", text: "main" }),
      h("span", { text: `${this.nodes.length - 1} subagents` }),
      h("span", { class: "tree-readout-k", text: "/" }),
      h("span", { text: `${fmtTokens(this.nodes.reduce((acc, node, index) => acc + (index ? node.tokens : 0), 0))} tokens delegated` })
    );
  }
}

/* ------------------------------------------------------------------ */
/* Run trace waterfall                                                 */
/* ------------------------------------------------------------------ */

export class Waterfall {
  constructor(container) {
    this.el = h("div", { class: "trace" });
    container.append(this.el);
  }

  set(steps, { maxRows = 8 } = {}) {
    this.el.replaceChildren();
    if (!Array.isArray(steps) || !steps.length) {
      this.el.append(h("p", { class: "insp-note", text: "No step trace for this session." }));
      return;
    }
    const total = Math.max(
      1,
      ...steps.map((step) => (Number(step.start_ms) || 0) + (Number(step.dur_ms) || 0))
    );
    const rows = steps.slice(0, maxRows);
    for (const step of rows) {
      const start = Math.max(0, Number(step.start_ms) || 0);
      const duration = Math.max(1, Number(step.dur_ms) || 1);
      const kind = ["think", "tool", "message", "subagent"].includes(step.kind) ? step.kind : "tool";
      const label = String(step.label || "step");
      const row = h("div", { class: "trace-row", title: `${label}: ${fmtDurationMs(duration)}` }, [
        h("span", { class: "trace-label" }, [
          h("span", { class: `trace-kind kind-${kind}` }),
          h("span", { text: label }),
        ]),
        h("span", { class: "trace-lane" }, [
          h("span", {
            class: `trace-bar kind-${kind}`,
            style: { left: `${(start / total) * 100}%`, width: `${Math.max(1.5, (duration / total) * 100)}%` },
          }),
        ]),
        h("span", { class: "trace-dur", text: fmtDurationMs(duration) }),
      ]);
      this.el.append(row);
    }
    if (steps.length > maxRows) {
      this.el.append(
        h("div", { class: "trace-more", text: `+${steps.length - maxRows} more steps` })
      );
    }
  }
}

/* ------------------------------------------------------------------ */
/* Recent events list                                                  */
/* ------------------------------------------------------------------ */

export function renderEvents(container, events, { max = 3 } = {}) {
  container.replaceChildren();
  const list = Array.isArray(events) ? events.slice(0, max) : [];
  if (!list.length) {
    container.append(h("p", { class: "insp-note", text: "No recent activity recorded." }));
    return;
  }
  for (const event of list) {
    const kind = ["tool", "think", "message", "subagent"].includes(event.kind) ? event.kind : "tool";
    container.append(
      h("div", { class: "event" }, [
        h("span", { class: "event-time", text: event.at ? fmtClock(event.at) : "—" }),
        h("span", { class: `event-dot kind-${kind}` }),
        h("span", { class: "event-text", text: String(event.text || "") }),
      ])
    );
  }
}
