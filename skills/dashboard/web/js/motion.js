/**
 * motion.js — the motion layer: easing tokens, WAAPI entrances, number tweens,
 * FLIP reorder, chart draw-in, view changes, ambient pause.
 * Only transform/opacity and SVG attributes are animated (brief §4.10).
 */

export const EASE = {
  out: "cubic-bezier(.16,1,.3,1)",
  in: "cubic-bezier(.6,0,.9,.35)",
  state: "cubic-bezier(.65,0,.35,1)",
  panel: "cubic-bezier(.2,.8,.2,1)",
};

export const DUR = {
  hoverIn: 90,
  hoverOut: 160,
  press: 80,
  release: 150,
  drawer: 280,
  drawerClose: 200,
  flip: 320,
  draw: 600,
  enter: 320,
  stagger: 32,
};

const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");

export function reducedMotion() {
  return motionQuery.matches;
}

export function onReducedMotionChange(handler) {
  const listener = (event) => handler(event.matches);
  motionQuery.addEventListener("change", listener);
  return () => motionQuery.removeEventListener("change", listener);
}

export function animate(el, frames, options = {}) {
  if (!el || reducedMotion()) return null;
  try {
    return el.animate(frames, { duration: DUR.enter, easing: EASE.out, fill: "backwards", ...options });
  } catch {
    return null;
  }
}

/**
 * Staggered entrance. Elements get translateY(distance)+opacity; delay grows by
 * `step` so the whole cascade stays short (brief §4.10: 32ms stagger).
 */
export function enterStagger(elements, { delay = 0, step = DUR.stagger, duration = DUR.enter, distance = 8 } = {}) {
  if (reducedMotion()) {
    for (const el of elements) el.style.opacity = "";
    return [];
  }
  const animations = [];
  elements.forEach((el, index) => {
    if (!el) return;
    const animation = animate(
      el,
      [
        { opacity: 0, transform: `translateY(${distance}px)` },
        { opacity: 1, transform: "translateY(0px)" },
      ],
      { duration, delay: delay + index * step, easing: EASE.out, fill: "backwards" }
    );
    if (animation) animations.push(animation);
  });
  return animations;
}

export function fadeIn(el, { duration = 240, delay = 0 } = {}) {
  return animate(el, [{ opacity: 0 }, { opacity: 1 }], { duration, delay, easing: EASE.out, fill: "backwards" });
}

/** Animate a number from its current displayed value to `to`. */
export class NumberTween {
  constructor(el, formatter = (v) => String(v)) {
    this.el = el;
    this.format = formatter;
    this.current = null;
    this.frame = 0;
    this.target = 0;
  }

  set(value, { animate: shouldAnimate = true, duration = 420 } = {}) {
    const to = Number.isFinite(Number(value)) ? Number(value) : 0;
    this.target = to;
    if (this.current === null || !shouldAnimate || reducedMotion() || duration <= 0) {
      this.current = to;
      this.render();
      return;
    }
    const from = this.current;
    if (Math.abs(from - to) < 0.0001) {
      this.render();
      return;
    }
    cancelAnimationFrame(this.frame);
    const started = performance.now();
    const tick = (now) => {
      const t = Math.min(1, (now - started) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      this.current = from + (to - from) * eased;
      this.render();
      if (t < 1) this.frame = requestAnimationFrame(tick);
      else this.current = to;
    };
    this.frame = requestAnimationFrame(tick);
  }

  render() {
    if (!this.el) return;
    const text = this.format(this.current ?? this.target);
    if (this.el.textContent !== text) this.el.textContent = text;
  }

  destroy() {
    cancelAnimationFrame(this.frame);
  }
}

/**
 * FLIP: capture positions before a DOM change, then play translation from the
 * old position. Newly shown elements fade in; hidden ones are skipped.
 */
export class Flip {
  constructor(container) {
    this.container = container;
    this.previous = new Map();
  }

  capture(selector) {
    this.previous.clear();
    if (!this.container) return;
    for (const el of this.container.querySelectorAll(selector)) {
      // measure the *visual* position first (mid-animation included), then stop the
      // old FLIP so the next one can start exactly where the eye already is
      const visual = el.getBoundingClientRect();
      el.getAnimations().forEach((animation) => animation.cancel());
      const key = el.dataset.flipId;
      if (!key) continue;
      this.previous.set(key, visual);
    }
  }

  play(selector, { duration = DUR.flip } = {}) {
    if (!this.container) return;
    if (reducedMotion()) {
      this.previous.clear();
      return;
    }
    for (const el of this.container.querySelectorAll(selector)) {
      if (el.dataset.hidden === "true") continue;
      const key = el.dataset.flipId;
      const before = this.previous.get(key);
      const after = el.getBoundingClientRect();
      if (!before || (before.width === 0 && before.height === 0)) {
        animate(el, [{ opacity: 0, transform: "translateY(6px)" }, { opacity: 1, transform: "none" }], {
          duration: 260,
          easing: EASE.out,
        });
        continue;
      }
      const dx = before.left - after.left;
      const dy = before.top - after.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
      el.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "translate(0px, 0px)" }], {
        duration,
        easing: EASE.state,
      });
    }
    this.previous.clear();
  }
}

/** SVG stroke draw-in (600ms) for sparklines and charts. */
export function drawIn(pathEl, { duration = DUR.draw, delay = 0 } = {}) {
  if (!pathEl || reducedMotion() || typeof pathEl.getTotalLength !== "function") return;
  let length = 0;
  try {
    length = pathEl.getTotalLength();
  } catch {
    return;
  }
  if (!length) return;
  pathEl.style.strokeDasharray = `${length}`;
  pathEl.style.strokeDashoffset = `${length}`;
  pathEl.getBoundingClientRect();
  pathEl.style.transitionProperty = "stroke-dashoffset";
  pathEl.style.transitionDuration = `${duration}ms`;
  pathEl.style.transitionTimingFunction = EASE.out;
  pathEl.style.transitionDelay = `${delay}ms`;
  requestAnimationFrame(() => {
    pathEl.style.strokeDashoffset = "0";
  });
  window.setTimeout(() => {
    pathEl.style.strokeDasharray = "";
    pathEl.style.strokeDashoffset = "";
    pathEl.style.transitionProperty = "";
    pathEl.style.transitionDuration = "";
    pathEl.style.transitionTimingFunction = "";
    pathEl.style.transitionDelay = "";
  }, duration + delay + 60);
}

/** Cross-fade a view swap when the View Transition API exists. */
export function withViewTransition(apply) {
  if (reducedMotion() || typeof document.startViewTransition !== "function") {
    apply();
    return;
  }
  try {
    const transition = document.startViewTransition(() => apply());
    transition.finished.catch(() => {});
  } catch {
    apply();
  }
}

/** Pause CSS ambient loops (visibility) without touching rAF loops. */
export function setAmbientPaused(paused) {
  document.body.classList.toggle("is-paused", Boolean(paused));
}

/** Move focus after an element settles; respects reduced motion. */
export function focusAfter(el, ms = 0) {
  window.setTimeout(() => {
    if (!el) return;
    try {
      el.focus({ preventScroll: true });
    } catch {
      el.focus();
    }
  }, ms);
}

export function waitForTransition(el, timeout = 400) {
  if (!el || reducedMotion()) return Promise.resolve();
  return new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      el.removeEventListener("transitionend", onEnd);
      resolve();
    };
    const onEnd = (event) => {
      if (event.target === el) finish();
    };
    el.addEventListener("transitionend", onEnd);
    window.setTimeout(finish, timeout);
  });
}
