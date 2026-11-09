/**
 * inspector.js — the right-hand session drawer.
 * Hairline sections, no nested cards. Charts are hand-rolled (charts.js).
 * Opens with a 280ms panel transition; the card's title block morphs into the
 * drawer header (persistent object, brief §4.10); Esc closes and focus returns.
 */

import { h, nextFrame } from "./dom.js";
import { fmtRuntime, fmtInt, fmtTps, fmtPct, fmtTokens, fmtClock, agentName } from "./format.js";
import {
  AreaChart,
  ContextBar,
  SubagentTree,
  Waterfall,
  renderEvents,
  windowSeries,
  contextComposition,
} from "./charts.js";
import { reducedMotion, NumberTween, DUR, EASE } from "./motion.js";
import { sessionContextRatio, latestTps, latestSampleTime } from "./data.js";

function section(title, noteEl, children) {
  return h("section", { class: "insp-section" }, [
    h("div", { class: "insp-section-head" }, [
      h("h3", { class: "insp-section-title", text: title }),
      noteEl || null,
    ]),
    ...children,
  ]);
}

function metric(label, unit = "") {
  const number = h("span", { class: "insp-metric-num", text: "—" });
  const unitEl = unit ? h("span", { class: "u", text: unit }) : null;
  const value = h("div", { class: "insp-metric-v" }, [number, unitEl]);
  return {
    number,
    unit: unitEl,
    el: h("div", { class: "insp-metric" }, [value, h("div", { class: "insp-metric-k", text: label })]),
  };
}

export class Inspector {
  constructor({ root, scrim, onClose, getMachine }) {
    this.root = root;
    this.scrim = scrim;
    this.onClose = onClose;
    this.getMachine = getMachine;
    this.session = null;
    this.returnFocus = null;
    this.isOpen = false;
    this.tweens = new Map();

    this.titleEl = root.querySelector("#inspector-title");
    this.statusEl = root.querySelector("#insp-status");
    this.statuslineEl = root.querySelector("#insp-statusline");
    this.metaEl = root.querySelector("#insp-meta");
    this.bodyEl = root.querySelector("#insp-body");
    this.closeBtn = root.querySelector("#inspector-close");
    this.headEl = root.querySelector(".insp-head");

    this.root.setAttribute("tabindex", "-1");
    this.closeBtn.addEventListener("click", () => this.close());
    this.scrim.addEventListener("click", () => this.close());
    this.onKeyDown = this.handleKeyDown.bind(this);
    document.addEventListener("keydown", this.onKeyDown);

    this.root.removeAttribute("hidden");
    this.root.setAttribute("aria-hidden", "true");
    this.root.setAttribute("inert", "");
    this.scrim.hidden = true;
  }

  open(session, { sourceEl = null } = {}) {
    this.session = session;
    this.returnFocus = sourceEl || document.activeElement;

    // make the drawer part of layout before measuring charts against it
    this.root.removeAttribute("inert");
    this.root.setAttribute("aria-hidden", "false");
    this.root.setAttribute("open", "");
    this.root.hidden = false;
    this.scrim.hidden = false;
    document.body.classList.add("has-drawer");
    if (sourceEl) sourceEl.classList.add("is-source");
    this.isOpen = true;

    this.render(session);

    requestAnimationFrame(() => {
      this.root.dataset.open = "true";
      this.scrim.dataset.open = "true";
      this.chart?.resize();
      this.tree?.resize();
    });

    if (sourceEl && !reducedMotion()) this.morphTitle(sourceEl);
    // focus the dialog container: keyboard users still reach the controls with Tab,
    // while a pointer-driven open does not paint a focus ring on the close button
    requestAnimationFrame(() => {
      try {
        this.root.focus({ preventScroll: true });
      } catch {
        /* ignore */
      }
    });
  }

  /** Re-measure the charts when the viewport (and drawer width) changes. */
  resize() {
    this.chart?.resize();
    this.tree?.resize();
  }

  close({ restoreFocus = true } = {}) {
    if (!this.isOpen) return;
    const previous = this.session;
    this.isOpen = false;
    this.session = null;
    this.root.dataset.open = "false";
    this.root.removeAttribute("open");
    this.scrim.dataset.open = "false";
    document.body.classList.remove("has-drawer");
    if (this.returnFocus && this.returnFocus.classList) {
      this.returnFocus.classList.remove("is-source");
    }
    const delay = reducedMotion() ? 0 : DUR.drawerClose + 20;
    window.setTimeout(() => {
      if (!this.isOpen) {
        this.root.hidden = true;
        this.scrim.hidden = true;
      }
      if (restoreFocus) {
        const target = this.returnFocus;
        this.returnFocus = null;
        if (target && target.isConnected) {
          try {
            target.focus({ preventScroll: true });
          } catch {
            target.focus();
          }
        } else {
          document.querySelector(".card")?.focus({ preventScroll: true });
        }
      }
    }, delay);
    this.onClose?.(previous);
  }

  handleKeyDown(event) {
    if (!this.isOpen) return;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      this.close();
      return;
    }
    if (event.key !== "Tab") return;
    const focusables = Array.from(
      this.root.querySelectorAll(
        'button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])'
      )
    ).filter((el) => el.offsetParent !== null || el === this.closeBtn);
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  dispose() {
    document.removeEventListener("keydown", this.onKeyDown);
    this.chart?.destroy?.();
    for (const tween of this.tweens.values()) tween.destroy();
  }

  /* ---------------------------------------------------------------- */
  /* Render                                                           */
  /* ---------------------------------------------------------------- */

  render(session) {
    const machine = this.getMachine?.(session.machine_id);
    const machineName = machine?.name || session.machine_id || "unknown machine";

    /* ---- header (persistent block) */
    this.statusEl.replaceChildren(
      h("span", { class: `dot status-${session.status}` }),
      h("span", { class: `status-word-${session.status}`, text: session.status })
    );
    this.titleEl.textContent = session.title;
    this.statuslineEl.replaceChildren(
      h("span", { class: "insp-agent", text: session.agent_name || agentName(session.agent) }),
      h("span", { class: "insp-sep", text: "/" }),
      h("span", { text: machineName })
    );

    const metaBits = [];
    if (session.project) metaBits.push(h("span", { class: "insp-meta-strong", text: session.project }));
    if (session.branch) metaBits.push(h("span", { text: session.branch }));
    if (session.model) metaBits.push(h("span", { text: session.model }));
    if (session.started_at) metaBits.push(h("span", { text: `started ${fmtClock(session.started_at)}` }));
    this.metaEl.replaceChildren(...metaBits);

    let idRow = this.headEl.querySelector(".insp-id");
    if (!idRow) {
      idRow = h("div", { class: "insp-id" });
      this.headEl.append(idRow);
    }
    const copyButton = this.buildCopyButton(session.id);
    idRow.replaceChildren(h("code", { text: session.id }), copyButton);

    /* ---- body */
    this.bodyEl.replaceChildren();
    this.chart?.destroy?.();
    for (const tween of this.tweens.values()) tween.destroy();
    this.tweens.clear();

    const runtime = metric("runtime");
    const requests = metric("requests");
    const tps = metric("throughput", "t/s");
    this.contextKnown = Number(session.context.limit) > 0;
    const context = metric(this.contextKnown ? "context" : "context used");
    this.tweens.set("runtime", new NumberTween(runtime.number, (v) => fmtRuntime(v)));
    this.tweens.set("requests", new NumberTween(requests.number, (v) => fmtInt(v)));
    this.tweens.set("tps", new NumberTween(tps.number, (v) => fmtTps(v)));
    this.tpsUnit = tps.unit;
    this.tpsNumber = tps.number;
    this.tweens.set(
      "context",
      new NumberTween(context.number, this.contextKnown ? (v) => fmtPct(v) : (v) => fmtTokens(v))
    );
    this.metricsBand = h("div", { class: "insp-metrics", "aria-label": "Session metrics" }, [
      runtime.el,
      requests.el,
      tps.el,
      context.el,
    ]);
    this.bodyEl.append(this.metricsBand);

    /* ---- throughput */
    const { values } = windowSeries(session.tps_series);
    const peak = values.length ? Math.max(...values) : 0;
    this.throughputNote = h("span", {
      class: "insp-section-note",
      text: peak ? `peak ${fmtTps(peak)} t/s` : "no samples",
    });
    this.throughputHost = h("div", { class: "throughput-host" });
    this.bodyEl.append(section("Throughput", this.throughputNote, [this.throughputHost]));
    this.chart = new AreaChart(this.throughputHost, { height: 112 });
    this.chart.setSeries(session.tps_series);
    this.chart.setEndpointState({
      running: session.status === "running",
      lastActive: latestSampleTime(session),
    });
    if (!reducedMotion()) this.chart.playEntrance();

    /* ---- context: composition when the parts are consistent, otherwise capacity */
    this.contextHost = h("div", { class: "context-host" });
    const limitText = session.context.limit
      ? `limit ${fmtTokens(session.context.limit)}`
      : "limit unknown";
    const composition = contextComposition(session.context_parts, session.context);
    this.bodyEl.append(
      section(
        composition.valid ? "Context composition" : "Context usage",
        h("span", { class: "insp-section-note", text: limitText }),
        [this.contextHost]
      )
    );
    this.contextBar = new ContextBar(this.contextHost);
    this.contextBar.set(session.context_parts, session.context);

    /* ---- subagents */
    const treeCount = countTree(session.subagent_tree);
    const hasTree = treeCount > 0;
    this.treeHost = hasTree ? h("div", { class: "subagent-host" }) : null;
    const subagentNote = hasTree
      ? `${Math.max(0, session.subagents.count || treeCount - 1)} total, ${session.subagents.active || 0} active`
      : "none";
    this.bodyEl.append(
      section("Subagents", h("span", { class: "insp-section-note", text: subagentNote }), hasTree ? [this.treeHost] : [])
    );
    this.tree = null;
    if (hasTree) {
      this.tree = new SubagentTree(this.treeHost);
      this.tree.set(session.subagent_tree);
    }

    /* ---- run trace */
    this.traceHost = h("div", { class: "trace-host" });
    this.bodyEl.append(
      section(
        "Run trace",
        session.steps.length ? h("span", { class: "insp-section-note", text: `${session.steps.length} steps` }) : null,
        [this.traceHost]
      )
    );
    this.waterfall = new Waterfall(this.traceHost);
    this.waterfall.set(session.steps);

    /* ---- recent activity */
    this.eventsHost = h("div", { class: "events" });
    this.bodyEl.append(section("Recent activity", null, [this.eventsHost]));
    renderEvents(this.eventsHost, session.events, { max: 3 });

    this.patch(session, { instant: true });
  }

  buildCopyButton(value) {
    const button = h("button", { class: "copy-btn", type: "button", "aria-label": "Copy session id" }, [
      h("span", { text: "copy" }),
    ]);
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(value);
      } catch {
        const area = h("textarea", { style: { position: "fixed", opacity: "0", top: "0" } });
        area.value = value;
        document.body.append(area);
        area.select();
        try {
          document.execCommand("copy");
        } catch {
          /* ignore */
        }
        area.remove();
      }
      button.classList.add("is-copied");
      button.textContent = "copied";
      window.setTimeout(() => {
        button.classList.remove("is-copied");
        button.textContent = "copy";
      }, 1400);
    });
    return button;
  }

  /** Patch live values; called once per demo tick / live poll while open. */
  patch(session, { instant = false } = {}) {
    if (!this.session || session.id !== this.session.id) return;
    this.session = session;
    const animate = !instant;
    const duration = 360;
    this.tweens.get("runtime")?.set(session.duration_ms, { animate, duration });
    this.tweens.get("requests")?.set(session.requests, { animate, duration });
    const runningNow = session.status === "running";
    if (runningNow) {
      this.tweens.get("tps")?.set(latestTps(session), { animate, duration: 400 });
      if (this.tpsUnit) this.tpsUnit.style.display = "";
    } else {
      this.tweens.get("tps")?.destroy();
      const tween = this.tweens.get("tps");
      if (tween) tween.current = null;
      if (this.tpsNumber) this.tpsNumber.textContent = "—";
      if (this.tpsUnit) this.tpsUnit.style.display = "none";
    }
    this.tweens
      .get("context")
      ?.set(
        this.contextKnown ? sessionContextRatio(session) * 100 : session.context.used,
        { animate, duration }
      );

    this.chart?.setEndpointState({
      running: runningNow,
      lastActive: latestSampleTime(session),
    });

    if (!instant) {
      this.chart?.setSeries(session.tps_series);
      this.statusEl.replaceChildren(
        h("span", { class: `dot status-${session.status}` }),
        h("span", { class: `status-word-${session.status}`, text: session.status })
      );
      const used = session.context.used;
      if (!this.lastUsed || Math.abs(used - this.lastUsed) > Math.max(120, (session.context.limit || 200000) * 0.0015)) {
        this.contextBar?.set(session.context_parts, session.context);
        this.lastUsed = used;
      }
      const peakNow = Math.max(...windowSeries(session.tps_series).values, 0);
      if (this.throughputNote && peakNow) {
        this.throughputNote.textContent = `peak ${fmtTps(peakNow)} t/s`;
      }
    } else {
      this.lastUsed = session.context.used;
    }
  }

  /* ---------------------------------------------------------------- */
  /* Persistent title block: card title -> drawer header              */
  /* ---------------------------------------------------------------- */

  async morphTitle(sourceEl) {
    const sourceTitle = sourceEl.querySelector(".card-title");
    if (!sourceTitle || !this.titleEl) return;
    const sourceRect = sourceTitle.getBoundingClientRect();
    if (!sourceRect.width) return;
    await nextFrame();
    if (!this.isOpen) return;
    const titleRect = this.titleEl.getBoundingClientRect();
    if (!titleRect.width) return;
    const rootRect = this.root.getBoundingClientRect();
    const offset = rootRect.left - (window.innerWidth - rootRect.width);
    const targetLeft = titleRect.left - offset;
    const targetTop = titleRect.top;

    const sourceFont = parseFloat(getComputedStyle(sourceTitle).fontSize) || 15;
    const targetFont = parseFloat(getComputedStyle(this.titleEl).fontSize) || 21;
    const scale = targetFont / sourceFont;

    const clone = h("div", {
      class: "morph-title",
      text: sourceTitle.textContent,
      "aria-hidden": "true",
      style: {
        left: `${sourceRect.left}px`,
        top: `${sourceRect.top}px`,
        "font-size": `${sourceFont}px`,
      },
    });
    document.body.append(clone);
    // the source title must not stay visible under the clone (ghost double text)
    const sourceCard = sourceEl.closest(".card") || sourceEl;
    sourceCard.classList.add("is-morph-source");
    this.headEl.classList.add("is-morphing");

    const dx = targetLeft - sourceRect.left;
    const dy = targetTop - sourceRect.top;
    const duration = 320;
    try {
      const animation = clone.animate(
        [
          { transform: "translate(0px, 0px) scale(1)", opacity: 1 },
          { transform: `translate(${dx}px, ${dy}px) scale(${scale})`, opacity: 1 },
        ],
        { duration, easing: EASE.state }
      );
      await animation.finished;
    } catch {
      /* ignore */
    } finally {
      clone.remove();
      sourceCard.classList.remove("is-morph-source");
      // hand off with the real header title at full opacity: no blank beat
      this.titleEl.style.transition = "none";
      this.headEl.classList.remove("is-morphing");
      this.titleEl.style.opacity = "1";
      this.titleEl.getBoundingClientRect();
      requestAnimationFrame(() => {
        this.titleEl.style.transition = "";
        this.titleEl.style.opacity = "";
      });
    }
  }
}

function countTree(tree) {
  if (!tree) return 0;
  let count = 1;
  for (const child of Array.isArray(tree.children) ? tree.children : []) count += countTree(child);
  return count;
}
