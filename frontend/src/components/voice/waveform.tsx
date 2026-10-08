"use client";
import { useEffect, useRef } from "react";
import type { VoiceState } from "@/lib/voice/machine";

const BARS = 48;

/**
 * Minimal state-driven waveform inside a thin aperture ring. It is the only ambient motion in the product,
 * and it always encodes state: listening (cool, follows level), thinking (slow travelling crest),
 * speaking (bright, follows level), interrupted (collapse), muted/ended (flat).
 */
export function Waveform({ state, level, muted, authRequired }: { state: VoiceState; level: number; muted: boolean; authRequired: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const levelRef = useRef(level);
  const stateRef = useRef({ state, muted, authRequired });
  useEffect(() => {
    levelRef.current = level;
    stateRef.current = { state, muted, authRequired };
  }, [level, state, muted, authRequired]);

  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const size = c.clientWidth;
    c.width = size * dpr;
    c.height = size * dpr;
    ctx.scale(dpr, dpr);
    const heights = new Array(BARS).fill(0.04);
    let raf = 0;
    let t = 0;
    const draw = () => {
      t += 1;
      const { state: s, muted: m, authRequired: a } = stateRef.current;
      const lvl = levelRef.current;
      ctx.clearRect(0, 0, size, size);
      const cx = size / 2, cy = size / 2, r = size / 2 - 2;
      // aperture ring
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.strokeStyle = a ? "rgba(217,164,65,.55)" : s === "handoff" ? "rgba(122,150,207,.6)" : s === "speaking" ? "rgba(255,255,255,.22)" : "rgba(255,255,255,.10)";
      ctx.lineWidth = 1;
      ctx.stroke();
      const width = r * 1.35;
      const gap = width / BARS;
      for (let i = 0; i < BARS; i++) {
        const x = cx - width / 2 + i * gap + gap / 2;
        const edge = Math.sin((i / (BARS - 1)) * Math.PI); // taper towards the ring
        let target = 0.03;
        if (!m && (s === "speaking" || s === "listening" || s === "interrupted" || s === "connected")) {
          const n = reduced ? 0.5 : 0.55 + 0.45 * Math.sin(t / 7 + i * 0.9) * Math.cos(t / 11 + i * 0.37);
          target = s === "interrupted" ? 0.02 : Math.max(0.03, lvl * edge * n * (s === "speaking" ? 1 : 0.8));
        } else if (s === "processing") {
          const crest = reduced ? 0 : Math.exp(-(((i - ((t / 2) % (BARS + 16)) + 8) / 5) ** 2));
          target = 0.05 + 0.18 * crest * edge;
        } else if (s === "connecting" || s === "handoff") {
          target = 0.04 + (reduced ? 0 : 0.04 * Math.abs(Math.sin(t / 18 + i / 6))) * edge;
        }
        heights[i] += (target - heights[i]) * (s === "interrupted" ? 0.45 : 0.22);
        const h = Math.max(1.5, heights[i] * r * 1.1);
        ctx.fillStyle = s === "speaking" ? "rgba(255,255,255,.92)" : s === "listening" || s === "connected" ? "rgba(168,192,255,.85)" : "rgba(160,160,160,.55)";
        ctx.fillRect(x - 1, cy - h / 2, 2, h);
      }
      raf = requestAnimationFrame(draw);
    };
    draw();
    return () => cancelAnimationFrame(raf);
  }, []);

  return <canvas ref={canvas} aria-hidden className="aspect-square w-[min(72vw,300px)]" />;
}
