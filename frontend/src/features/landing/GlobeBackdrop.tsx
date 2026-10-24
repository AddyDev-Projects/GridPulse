import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "../../hooks/useReducedMotion";
import { cx } from "../../components/ui";
import { GlobeParticles } from "./GlobeParticles";

export const GLOBE_STILL =
  "https://d2ol7oe51mr4n9.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/f1631235-0a76-464d-8f1d-2c4b48a997d5.jpg";
export const GLOBE_VIDEO =
  "https://d2ol7oe51mr4n9.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/e71aea27-a808-4ae3-8231-a469d49285d4.mp4";

/**
 * Supplied globe media, layered: the still sits underneath and the muted forward-looping video fades
 * in only once it has a decodable frame and is playing. Both layers share identical geometry so the
 * hand-off is invisible. Reduced motion → still only. Any load failure → the other layer or a CSS glow.
 */
export function GlobeBackdrop() {
  const reduced = useReducedMotion();
  const parallaxRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [videoReady, setVideoReady] = useState(false);
  const [videoFailed, setVideoFailed] = useState(false);
  const [stillFailed, setStillFailed] = useState(false);

  useEffect(() => {
    const root = parallaxRef.current;
    if (!root || reduced || typeof window === "undefined") return;
    let visible = true;
    let frame = 0;
    let currentX = 0;
    let currentY = 0;
    let targetX = 0;
    let targetY = 0;
    const settle = () => {
      if (!visible) return;
      currentX += (targetX - currentX) * 0.11;
      currentY += (targetY - currentY) * 0.11;
      root.style.setProperty("--globe-x", `${currentX.toFixed(2)}px`);
      root.style.setProperty("--globe-y", `${currentY.toFixed(2)}px`);
      root.style.setProperty("--globe-orbit-x", `${(currentX * 0.72).toFixed(2)}px`);
      root.style.setProperty("--globe-orbit-y", `${(currentY * 0.72).toFixed(2)}px`);
      root.style.setProperty("--globe-particle-x", `${(currentX * 1.18).toFixed(2)}px`);
      root.style.setProperty("--globe-particle-y", `${(currentY * 1.18).toFixed(2)}px`);
      if (Math.abs(targetX - currentX) > 0.08 || Math.abs(targetY - currentY) > 0.08) frame = window.requestAnimationFrame(settle);
      else frame = 0;
    };
    const wake = () => { if (!frame) frame = window.requestAnimationFrame(settle); };
    const move = (event: PointerEvent) => {
      if (!visible || event.pointerType === "touch") return;
      const rect = root.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) {
        targetX = 0;
        targetY = 0;
      } else {
        const x = (event.clientX - rect.left) / rect.width - 0.5;
        const y = (event.clientY - rect.top) / rect.height - 0.5;
        targetX = x * 22;
        targetY = y * 14;
      }
      wake();
    };
    const leave = () => { targetX = 0; targetY = 0; wake(); };
    const visibility = () => {
      visible = document.visibilityState === "visible";
      if (!visible && frame) { window.cancelAnimationFrame(frame); frame = 0; }
      if (visible) wake();
    };
    const observer = typeof IntersectionObserver === "function"
      ? new IntersectionObserver(([entry]) => { visible = entry.isIntersecting && document.visibilityState === "visible"; if (visible) wake(); }, { threshold: 0.01 })
      : null;
    observer?.observe(root);
    window.addEventListener("pointermove", move, { passive: true });
    window.addEventListener("pointerleave", leave, { passive: true });
    document.addEventListener("visibilitychange", visibility);
    return () => {
      observer?.disconnect();
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerleave", leave);
      document.removeEventListener("visibilitychange", visibility);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [reduced]);

  useEffect(() => {
    const v = videoRef.current;
    if (!v || reduced) return;
    const markReady = () => {
      if (v.readyState >= 2 && !v.paused) setVideoReady(true);
    };
    const fail = () => setVideoFailed(true);
    v.addEventListener("playing", markReady);
    v.addEventListener("timeupdate", markReady);
    v.addEventListener("error", fail);
    v.play().catch(() => {
      /* Autoplay refused: keep the still. */
    });
    return () => {
      v.removeEventListener("playing", markReady);
      v.removeEventListener("timeupdate", markReady);
      v.removeEventListener("error", fail);
      v.pause();
    };
  }, [reduced]);

  const layer = "absolute inset-0 h-full w-full object-cover object-[50%_100%]";

  return (
    <div ref={parallaxRef} aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden globe-parallax-root">
      {/* Globe stage: anchored to the bottom so the planet rises behind the hero copy. */}
      <div className="globe-parallax-stage absolute inset-x-0 bottom-0 h-[78%] sm:h-[86%] lg:h-[94%]">
        <div className="globe-orbits absolute inset-0" aria-hidden>
          <span className="globe-orbit globe-orbit-a" />
          <span className="globe-orbit globe-orbit-b" />
          <span className="globe-orbit globe-orbit-c" />
          <i className="globe-particle globe-particle-a" />
          <i className="globe-particle globe-particle-b" />
          <i className="globe-particle globe-particle-c" />
          <i className="globe-particle globe-particle-d" />
          {!reduced && <GlobeParticles />}
        </div>
        {/* Fallback glow when neither media layer is usable. */}
        {stillFailed && (videoFailed || reduced) && (
          <div className="absolute inset-x-[-10%] bottom-[-40%] h-[110%] rounded-[50%] bg-[radial-gradient(closest-side,rgba(10,8,16,1)_78%,rgba(240,171,252,0.5)_88%,rgba(106,168,255,0.25)_94%,transparent)]" />
        )}
        {!stillFailed && (
          <img src={GLOBE_STILL} alt="" className={layer} fetchPriority="high" onError={() => setStillFailed(true)} />
        )}
        {!reduced && !videoFailed && (
          <video
            ref={videoRef}
            className={cx(layer, "transition-opacity duration-[1200ms] ease-out", videoReady ? "opacity-100" : "opacity-0")}
            src={GLOBE_VIDEO}
            muted
            loop
            playsInline
            autoPlay
            preload="auto"
            poster={stillFailed ? undefined : GLOBE_STILL}
          />
        )}
        {/* Blend the media's top and sides into the page. */}
        <div className="absolute inset-x-0 top-0 h-[38%] bg-gradient-to-b from-ink-950 via-ink-950/70 to-transparent" />
        <div className="absolute inset-y-0 left-0 w-[12%] bg-gradient-to-r from-ink-950 to-transparent" />
        <div className="absolute inset-y-0 right-0 w-[12%] bg-gradient-to-l from-ink-950 to-transparent" />
        <div className="absolute inset-x-0 bottom-0 h-[16%] bg-gradient-to-t from-ink-950 to-transparent" />
      </div>
      {/* Soft scrim behind the copy for contrast over the globe's bright rim. */}
      <div className="absolute left-1/2 top-[14%] h-[58%] w-[min(980px,110vw)] -translate-x-1/2 rounded-[50%] bg-[radial-gradient(closest-side,rgba(7,6,11,0.82),rgba(7,6,11,0.4)_60%,transparent)]" />
    </div>
  );
}
