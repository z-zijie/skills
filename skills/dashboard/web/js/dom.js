/**
 * dom.js — tiny DOM/SVG helpers. No framework, no build step.
 */

export const SVG_NS = "http://www.w3.org/2000/svg";

/** Create an HTML element. attrs supports class, text, dataset, aria*, on*, and plain attributes. */
export function h(tag, attrs = {}, children = []) {
  const el = document.createElement(tag);
  applyAttrs(el, attrs);
  append(el, children);
  return el;
}

/** Create an SVG element. */
export function svg(tag, attrs = {}, children = []) {
  const el = document.createElementNS(SVG_NS, tag);
  applyAttrs(el, attrs);
  append(el, children);
  return el;
}

function applyAttrs(el, attrs) {
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class" || key === "className") {
      el.setAttribute("class", value);
    } else if (key === "text") {
      el.textContent = String(value);
    } else if (key === "dataset") {
      for (const [dk, dv] of Object.entries(value)) {
        if (dv !== null && dv !== undefined) el.dataset[dk] = String(dv);
      }
    } else if (key === "style" && typeof value === "object") {
      for (const [sk, sv] of Object.entries(value)) el.style.setProperty(sk, sv);
    } else if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value, { passive: key === "onwheel" ? false : true });
    } else if (key === "html") {
      el.innerHTML = value;
    } else {
      el.setAttribute(key, String(value));
    }
  }
}

function append(el, children) {
  if (!children) return;
  const list = Array.isArray(children) ? children : [children];
  for (const child of list) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
}

export function $(selector, root = document) {
  return root.querySelector(selector);
}

export function $$(selector, root = document) {
  return Array.from(root.querySelectorAll(selector));
}

export function setText(el, text) {
  if (el && el.textContent !== String(text)) el.textContent = String(text);
}

/** rAF-coalesced callback: many calls in a frame produce one run. */
export function rafBatch(fn) {
  let queued = false;
  let arg = null;
  return function schedule(value) {
    arg = value;
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      const next = arg;
      arg = null;
      fn(next);
    });
  };
}

export function on(target, type, handler, options = {}) {
  target.addEventListener(type, handler, options);
  return () => target.removeEventListener(type, handler, options);
}

export function nextFrame() {
  return new Promise((resolve) => requestAnimationFrame(() => resolve()));
}

export function later(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function isNarrow() {
  return window.innerWidth < 900;
}

/* ---------- inline icon set (no network, no icon font) ---------- */

const ICON_PATHS = {
  laptop: [
    ["rect", { x: 2.5, y: 4, width: 11, height: 8, rx: 1.3 }],
    ["path", { d: "M1.4 13.3h13.2" }],
  ],
  server: [
    ["rect", { x: 2.5, y: 3, width: 11, height: 4.3, rx: 1.2 }],
    ["rect", { x: 2.5, y: 8.7, width: 11, height: 4.3, rx: 1.2 }],
    ["path", { d: "M5 5.15h.01M5 10.85h.01" }],
  ],
  window: [
    ["rect", { x: 2.5, y: 3.4, width: 11, height: 9.2, rx: 1.4 }],
    ["path", { d: "M2.5 6.4h11" }],
  ],
  layers: [
    ["path", { d: "M8 2.4l5.7 2.7L8 7.8 2.3 5.1 8 2.4z" }],
    ["path", { d: "M2.3 8.3L8 11l5.7-2.7" }],
    ["path", { d: "M2.3 11.4L8 14.1l5.7-2.7" }],
  ],
  copy: [
    ["rect", { x: 5.2, y: 5.2, width: 8.2, height: 8.2, rx: 1.4 }],
    ["path", { d: "M10.8 5.2V3.9c0-.8-.6-1.4-1.4-1.4H3.9c-.8 0-1.4.6-1.4 1.4v5.5c0 .8.6 1.4 1.4 1.4h1.3" }],
  ],
  check: [["path", { d: "M3.4 8.6l3 3 6.2-7" }]],
};

export function icon(name, { size = 16, className = "" } = {}) {
  const shapes = ICON_PATHS[name] || [];
  const el = svg("svg", {
    viewBox: "0 0 16 16",
    width: size,
    height: size,
    class: className,
    "aria-hidden": "true",
    focusable: "false",
  });
  for (const [tag, attrs] of shapes) {
    el.append(
      svg(tag, {
        ...attrs,
        fill: tag === "rect" ? "none" : "none",
        stroke: "currentColor",
        "stroke-width": 1.3,
        "stroke-linecap": "round",
        "stroke-linejoin": "round",
      })
    );
  }
  return el;
}

export function platformIcon(platform) {
  if (platform === "darwin") return icon("laptop", { size: 15 });
  if (platform === "linux") return icon("server", { size: 15 });
  if (platform === "win32") return icon("window", { size: 15 });
  return icon("layers", { size: 15 });
}
