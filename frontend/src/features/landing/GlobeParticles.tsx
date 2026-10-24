import { useEffect, useRef } from "react";

/**
 * Particles travelling along the three orbit rings drawn in CSS (.globe-orbit-a/b/c). Near the
 * pointer they are pushed away with a smooth falloff, then spring back to their orbit when it leaves.
 *
 * Everything runs in one requestAnimationFrame loop on refs: pointer movement never re-renders React.
 * The loop sleeps while the hero is off-screen or the tab is hidden. The parent only mounts this when
 * the user has not asked for reduced motion.
 */

// Orbit ellipses: semi-axes as (vw fraction, px cap) pairs and tilt in degrees, matching index.css.
const RINGS = [
  { rx: [0.37, 380], ry: [0.125, 130], tilt: -18, color: "207,194,255" },
  { rx: [0.44, 470], ry: [0.165, 175], tilt: 22, color: "243,184,245" },
  { rx: [0.31, 320], ry: [0.09, 95], tilt: 66, color: "124,199,255" },
] as const;
const RADIUS = 150; // px of pointer influence
const PUSH = 2600; // repulsion strength (px/s² at the pointer)
const SPRING = 7; // pull back towards the orbit position (1/s²)
const DAMPING = 5.5; // velocity damping (1/s); with SPRING this is close to critically damped: no wobble

interface Particle {
  ring: number;
  angle: number;
  speed: number; // rad/s
  jitter: number; // radial offset as a fraction of the ring
  size: number;
  dx: number;
  dy: number;
  vx: number;
  vy: number;
}

function makeParticles(count: number): Particle[] {
  return Array.from({ length: count }, (_, i) => ({
    ring: i % RINGS.length,
    angle: Math.random() * Math.PI * 2,
    speed: (0.035 + Math.random() * 0.05) * (i % 2 ? 1 : -1) * (i % RINGS.length === 1 ? -1 : 1),
    jitter: (Math.random() - 0.5) * 0.08,
    size: 0.6 + Math.random() * 1.3,
    dx: 0, dy: 0, vx: 0, vy: 0,
  }));
}

export function GlobeParticles() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    let width = 0;
    let height = 0;
    let particles: Particle[] = [];
    let pointer: { x: number; y: number } | null = null;
    let visible = true;
    let frame = 0;
    let last = 0;

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = rect.width;
      height = rect.height;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const count = width < 640 ? 54 : 108;
      if (particles.length !== count) particles = makeParticles(count);
    };

    const step = (now: number) => {
      frame = 0;
      if (!visible) return;
      const dt = Math.min((now - (last || now)) / 1000, 1 / 20); // clamp after a stall so nothing jumps
      last = now;
      ctx.clearRect(0, 0, width, height);
      const cx = width / 2;
      const cy = height / 2;
      const vw = window.innerWidth;
      for (const p of particles) {
        const ring = RINGS[p.ring];
        const rx = Math.min(ring.rx[0] * vw, ring.rx[1]) * (1 + p.jitter);
        const ry = Math.min(ring.ry[0] * vw, ring.ry[1]) * (1 + p.jitter);
        const t = (ring.tilt * Math.PI) / 180;
        p.angle += p.speed * dt;
        const ex = Math.cos(p.angle) * rx;
        const ey = Math.sin(p.angle) * ry;
        const hx = cx + ex * Math.cos(t) - ey * Math.sin(t);
        const hy = cy + ex * Math.sin(t) + ey * Math.cos(t);

        // Repulsion from the pointer with a smooth (quadratic) falloff, plus a spring back to the orbit.
        let ax = -SPRING * p.dx - DAMPING * p.vx;
        let ay = -SPRING * p.dy - DAMPING * p.vy;
        if (pointer) {
          const ox = hx + p.dx - pointer.x;
          const oy = hy + p.dy - pointer.y;
          const d = Math.hypot(ox, oy);
          if (d < RADIUS && d > 0.01) {
            const f = PUSH * (1 - d / RADIUS) ** 2;
            ax += (ox / d) * f;
            ay += (oy / d) * f;
          }
        }
        p.vx += ax * dt;
        p.vy += ay * dt;
        p.dx += p.vx * dt;
        p.dy += p.vy * dt;

        // Depth: the back half of each orbit is smaller and dimmer, which reads as a tilted ring.
        const depth = (Math.sin(p.angle) + 1) / 2;
        const r = p.size * (0.65 + depth * 0.7);
        ctx.fillStyle = `rgba(${ring.color},${(0.25 + depth * 0.6).toFixed(3)})`;
        ctx.beginPath();
        ctx.arc(hx + p.dx, hy + p.dy, r, 0, Math.PI * 2);
        ctx.fill();
      }
      frame = window.requestAnimationFrame(step);
    };
    const wake = () => {
      if (!frame && visible) {
        last = 0;
        frame = window.requestAnimationFrame(step);
      }
    };

    const move = (e: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      pointer = x >= -RADIUS && y >= -RADIUS && x <= rect.width + RADIUS && y <= rect.height + RADIUS ? { x, y } : null;
    };
    const clear = () => { pointer = null; };
    const end = (e: PointerEvent) => { if (e.pointerType !== "mouse") pointer = null; };
    const onVisibility = () => {
      visible = document.visibilityState === "visible";
      if (visible) wake();
    };

    resize();
    const ro = typeof ResizeObserver === "function" ? new ResizeObserver(resize) : null;
    ro?.observe(canvas);
    const io = typeof IntersectionObserver === "function"
      ? new IntersectionObserver(([entry]) => {
          visible = entry.isIntersecting && document.visibilityState === "visible";
          if (visible) wake();
        }, { threshold: 0.01 })
      : null;
    io?.observe(canvas);
    window.addEventListener("pointermove", move, { passive: true });
    window.addEventListener("pointerdown", move, { passive: true });
    window.addEventListener("pointerup", end, { passive: true });
    window.addEventListener("pointercancel", clear, { passive: true });
    document.documentElement.addEventListener("pointerleave", clear);
    window.addEventListener("blur", clear);
    document.addEventListener("visibilitychange", onVisibility);
    wake();

    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      ro?.disconnect();
      io?.disconnect();
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerdown", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", clear);
      document.documentElement.removeEventListener("pointerleave", clear);
      window.removeEventListener("blur", clear);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return <canvas ref={canvasRef} aria-hidden className="pointer-events-none absolute inset-0 h-full w-full" />;
}
