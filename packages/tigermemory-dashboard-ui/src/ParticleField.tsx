import { useEffect, useRef } from "react";

/**
 * Shared animated particle background for TigerMemory pages.
 *
 * Design intent: a calm, premium atmosphere that continues the tiger-stripe
 * motif without distracting from content. Canvas-based for low CPU cost.
 *
 * Performance budget:
 *   - particle count scales with viewport but is capped at MAX_PARTICLES
 *   - a single requestAnimationFrame loop, throttled via visibility + reduced motion
 *   - DPR-aware but clamped to 2 to avoid retina overhead
 *   - stops entirely when the tab is hidden
 */
type Particle = {
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
  baseAlpha: number;
  phase: number;
};

const MAX_PARTICLES = 30;
const CONNECT_DIST = 130;

export function ParticleField() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduceMotion) return; // static bg only; honor accessibility

    let width = 0;
    let height = 0;
    let dpr = Math.min(window.devicePixelRatio || 1, 2);
    let particles: Particle[] = [];
    let raf = 0;
    let running = true;

    function resize() {
      if (!canvas) return;
      width = canvas.clientWidth;
      height = canvas.clientHeight;
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
      seed();
    }

    function seed() {
      // density: ~1 particle per 55k px², capped
      const target = Math.min(MAX_PARTICLES, Math.max(12, Math.floor((width * height) / 55000)));
      particles = Array.from({ length: target }, () => ({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.16,
        vy: (Math.random() - 0.5) * 0.16,
        r: 1 + Math.random() * 1.8,
        baseAlpha: 0.12 + Math.random() * 0.28,
        phase: Math.random() * Math.PI * 2,
      }));
    }

    function frame(t: number) {
      if (!running) return;
      ctx!.clearRect(0, 0, width, height);

      // connection lines first (behind particles)
      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const a = particles[i]!;
          const b = particles[j]!;
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const dist = Math.hypot(dx, dy);
          if (dist < CONNECT_DIST) {
            const alpha = (1 - dist / CONNECT_DIST) * 0.12;
            ctx!.strokeStyle = `rgba(200, 165, 96, ${alpha})`;
            ctx!.lineWidth = 0.6;
            ctx!.beginPath();
            ctx!.moveTo(a.x, a.y);
            ctx!.lineTo(b.x, b.y);
            ctx!.stroke();
          }
        }
      }

      for (const p of particles) {
        p.x += p.vx;
        p.y += p.vy;
        if (p.x < -10) p.x = width + 10;
        if (p.x > width + 10) p.x = -10;
        if (p.y < -10) p.y = height + 10;
        if (p.y > height + 10) p.y = -10;

        const flicker = 0.7 + 0.3 * Math.sin(t * 0.0008 + p.phase);
        const alpha = p.baseAlpha * flicker;
        ctx!.beginPath();
        ctx!.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx!.fillStyle = `rgba(200, 165, 96, ${alpha})`;
        ctx!.fill();
      }

      raf = requestAnimationFrame(frame);
    }

    function onVisibility() {
      if (document.hidden) {
        running = false;
        cancelAnimationFrame(raf);
      } else if (!running) {
        running = true;
        raf = requestAnimationFrame(frame);
      }
    }

    resize();
    raf = requestAnimationFrame(frame);
    window.addEventListener("resize", resize);
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      running = false;
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 h-full w-full"
    />
  );
}
