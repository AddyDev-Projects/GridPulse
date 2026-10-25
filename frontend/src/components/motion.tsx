import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useReducedMotion } from "../hooks/useReducedMotion";
import { cx } from "./ui";

/**
 * Motion is decorative only: every component here renders its final state at once when the user prefers
 * reduced motion, and also where the platform cannot report that preference (no matchMedia, e.g. jsdom).
 */
function useMotionAllowed() {
  const reduced = useReducedMotion();
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && !reduced;
}

const ACTIVE = '[aria-current="page"], [aria-selected="true"], [aria-pressed="true"]';

/**
 * A highlight that glides to whichever child is active (aria-current, aria-selected or aria-pressed).
 * Place it inside the group; the group needs `relative isolate` so the pill sits behind the labels.
 */
export function Indicator({ variant = "pill", className }: { variant?: "pill" | "underline"; className?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    const host = el?.parentElement;
    if (!el || !host) return;
    let frame = 0;
    const place = () => {
      const active = host.querySelector<HTMLElement>(ACTIVE);
      if (!active) {
        el.style.opacity = "0";
        return;
      }
      el.style.width = `${active.offsetWidth}px`;
      if (variant === "pill") {
        el.style.height = `${active.offsetHeight}px`;
        el.style.transform = `translate(${active.offsetLeft}px, ${active.offsetTop}px)`;
      } else {
        el.style.transform = `translateX(${active.offsetLeft}px)`;
      }
      el.style.opacity = "1";
      // The first placement jumps into position; only later changes glide.
      if (!("ready" in el.dataset)) frame = requestAnimationFrame(() => { el.dataset.ready = ""; });
    };
    place();
    const mutations = new MutationObserver(place);
    mutations.observe(host, { subtree: true, childList: true, characterData: true, attributes: true,
      attributeFilter: ["aria-current", "aria-selected", "aria-pressed"] });
    const sizes = typeof ResizeObserver === "function" ? new ResizeObserver(place) : null;
    if (sizes) [host, ...Array.from(host.children)].forEach((c) => sizes.observe(c));
    return () => {
      cancelAnimationFrame(frame);
      mutations.disconnect();
      sizes?.disconnect();
    };
  }, [variant]);
  return <span ref={ref} aria-hidden className={cx("gp-indicator", `gp-indicator-${variant}`, className)} />;
}

const COLLAPSE_MS = 340;

/** Height-animated disclosure. Closed content is not rendered, exactly as with `{open && …}`. */
export function Collapse({ open, id, className, children }: { open: boolean; id?: string; className?: string; children: ReactNode }) {
  const motion = useMotionAllowed();
  const [rendered, setRendered] = useState(open);
  const [expanded, setExpanded] = useState(open);
  const [settled, setSettled] = useState(open);
  const previous = useRef(open);
  useEffect(() => {
    if (previous.current === open) return;
    previous.current = open;
    if (!motion) {
      setRendered(open);
      setExpanded(open);
      setSettled(open);
      return;
    }
    setSettled(false);
    if (open) {
      setRendered(true);
      // Two frames: the closed layout must be painted once for the height transition to run.
      let inner = 0;
      const outer = requestAnimationFrame(() => { inner = requestAnimationFrame(() => setExpanded(true)); });
      const done = window.setTimeout(() => setSettled(true), COLLAPSE_MS + 40);
      return () => { cancelAnimationFrame(outer); cancelAnimationFrame(inner); window.clearTimeout(done); };
    }
    setExpanded(false);
    const gone = window.setTimeout(() => setRendered(false), COLLAPSE_MS);
    return () => window.clearTimeout(gone);
  }, [open, motion]);
  if (!rendered) return null;
  return (
    <div id={id} className={cx("gp-collapse", className)} data-open={expanded ? "" : undefined}
      data-settled={settled ? "" : undefined} inert={!open || undefined}>
      <div>{children}</div>
    </div>
  );
}

/** A count that rolls to its new value and pulses once when it changes (never on first render). */
export function AnimatedNumber({ value, className }: { value: number; className?: string }) {
  const motion = useMotionAllowed();
  const [shown, setShown] = useState(value);
  const [pulse, setPulse] = useState(0);
  const current = useRef(value);
  useEffect(() => {
    const from = current.current;
    if (from === value) return;
    if (!motion) {
      current.current = value;
      setShown(value);
      return;
    }
    setPulse((p) => p + 1);
    const start = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      const k = Math.min(1, (now - start) / 520);
      const next = Math.round(from + (value - from) * (1 - (1 - k) ** 3));
      current.current = next;
      setShown(next);
      if (k < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value, motion]);
  return <span key={pulse} className={cx("inline-block tabular-nums", pulse > 0 && "num-pulse", className)}>{shown}</span>;
}
