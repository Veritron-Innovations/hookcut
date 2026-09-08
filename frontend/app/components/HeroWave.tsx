"use client";

import { useEffect, useRef } from "react";

/**
 * HeroWave - a track, drawn behind the headline.
 *
 * The hero claims the clip is already inside your track, so the hero shows a
 * track. The words sit in it, and one phrase is marked the way a kept moment
 * is marked on the review screen - same tint, same edge caps. The claim and
 * the picture are the same object.
 */
export default function HeroWave() {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;

    const draw = () => {
      const w = wrap.clientWidth;
      const h = wrap.clientHeight;
      if (!w || !h) return;

      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;

      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      const mid = h / 2;
      const barW = 2;
      const step = 5;
      const count = Math.floor(w / step);

      for (let i = 0; i < count; i++) {
        // Deterministic, so it never flickers between renders.
        const a = Math.sin(i * 0.27) * 0.3 + Math.sin(i * 0.071) * 0.34 + Math.sin(i * 1.31) * 0.16;
        const envelope = 0.34 + 0.66 * Math.sin((Math.PI * i) / count);
        const p = Math.min(1, Math.max(0.05, envelope * (0.6 + a)));
        const amp = Math.max(1, p * (mid - 2));

        ctx.fillStyle = "#3b3557";
        ctx.fillRect(i * step, mid - amp, barW, amp * 2);
      }
    };

    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(wrap);
    return () => ro.disconnect();
  }, []);

  return (
    <div className="herowave" ref={wrapRef} aria-hidden="true">
      <canvas ref={canvasRef} />
    </div>
  );
}
