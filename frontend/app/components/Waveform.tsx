"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Waveform - the whole track on one line, with every picked moment marked.
 *
 * This is the review screen's overview: all moments visible at once (so they
 * can be compared), positioned where they actually fall in the song, playable
 * so the cut can be heard, and - for the active moment - draggable at both
 * edges so a cut that starts mid-syllable can be nudged.
 *
 * Peaks are decoded from the file the artist just dropped, so there is no
 * upload round trip. With no file (mock data) a deterministic synthetic
 * waveform stands in, so the screen can still be designed against.
 */

export type Region = {
  index: number;
  start: number;
  end: number;
  hue: number;
  kept: boolean;
  label: string;
};

type Props = {
  audioUrl: string | null;
  regions: Region[];
  activeIndex: number;
  playheadAt: number | null;
  duration: number;
  onSelect: (index: number) => void;
  onTrim: (index: number, start: number, end: number) => void;
  /** No trim handles, no click-to-select - used while a job is still running. */
  readOnly?: boolean;
  /** Overrides the footer line. */
  note?: string;
};

const BUCKETS = 900;
const MAX_DECODE_BYTES = 120 * 1024 * 1024;

/** Short-form clips live in this range; the handles won't leave it. */
export const MIN_CLIP = 5;
export const MAX_CLIP = 60;

const HUES = ["#ff4d8d", "#ff9f1c", "#2ec4b6", "#7c5cff", "#4cc9f0"];

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** Deterministic stand-in peaks, shaped roughly like a song. */
function syntheticPeaks(n: number): Float32Array {
  const peaks = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const t = i / n;
    const envelope = 0.35 + 0.65 * Math.sin(Math.PI * Math.min(1, t * 1.15));
    const wobble =
      Math.sin(i * 0.31) * 0.22 + Math.sin(i * 0.09) * 0.3 + Math.sin(i * 1.7) * 0.14;
    peaks[i] = Math.min(1, Math.max(0.06, envelope * (0.62 + wobble)));
  }
  return peaks;
}

async function decodePeaks(url: string): Promise<{ peaks: Float32Array; duration: number } | null> {
  try {
    const res = await fetch(url);
    const buf = await res.arrayBuffer();
    if (buf.byteLength > MAX_DECODE_BYTES) return null;

    const Ctx: typeof AudioContext = window.AudioContext ?? (window as any).webkitAudioContext;
    const ctx = new Ctx();
    const audio = await ctx.decodeAudioData(buf);
    const data = audio.getChannelData(0);

    const block = Math.max(1, Math.floor(data.length / BUCKETS));
    const peaks = new Float32Array(BUCKETS);
    let loudest = 0;
    for (let i = 0; i < BUCKETS; i++) {
      const from = i * block;
      let max = 0;
      for (let j = 0; j < block; j += 32) {
        const v = Math.abs(data[from + j] ?? 0);
        if (v > max) max = v;
      }
      peaks[i] = max;
      if (max > loudest) loudest = max;
    }
    if (loudest > 0) for (let i = 0; i < BUCKETS; i++) peaks[i] /= loudest;

    const duration = audio.duration;
    void ctx.close();
    return { peaks, duration };
  } catch {
    return null;
  }
}

type DragMode = "start" | "end" | "move";

export default function Waveform({
  audioUrl, regions, activeIndex, playheadAt, duration, onSelect, onTrim,
  readOnly = false, note,
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const dragRef = useRef<{ mode: DragMode; t0: number; s0: number; e0: number } | null>(null);

  const [peaks, setPeaks] = useState<Float32Array>(() => syntheticPeaks(BUCKETS));
  const [real, setReal] = useState(false);
  const [span, setSpan] = useState(duration);
  const [dragging, setDragging] = useState(false);

  const active = readOnly ? null : (regions.find((r) => r.index === activeIndex) ?? null);

  /* ---- peaks ---- */
  useEffect(() => {
    let cancelled = false;
    if (!audioUrl) {
      setPeaks(syntheticPeaks(BUCKETS));
      setReal(false);
      setSpan(duration);
      return;
    }
    decodePeaks(audioUrl).then((result) => {
      if (cancelled) return;
      if (result) {
        setPeaks(result.peaks);
        setReal(true);
        setSpan(result.duration || duration);
      } else {
        setPeaks(syntheticPeaks(BUCKETS));
        setReal(false);
        setSpan(duration);
      }
    });
    return () => { cancelled = true; };
  }, [audioUrl, duration]);

  /* ---- draw ---- */
  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;

    const draw = () => {
      const w = wrap.clientWidth;
      const h = wrap.clientHeight;
      if (w === 0 || h === 0) return;

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
      const total = span || 1;
      const xOf = (t: number) => (t / total) * w;

      for (const r of regions) {
        const x0 = xOf(r.start);
        const x1 = xOf(r.end);
        const hue = HUES[r.hue % HUES.length];
        ctx.fillStyle = hue;
        ctx.globalAlpha = r.kept ? 0.17 : 0.06;
        ctx.fillRect(x0, 0, Math.max(2, x1 - x0), h);
        ctx.globalAlpha = r.kept ? 0.95 : 0.3;
        ctx.fillRect(x0, 0, Math.max(2, x1 - x0), 2.5);
        ctx.globalAlpha = 1;
      }

      const barW = 2;
      const step = barW + 1;
      const count = Math.floor(w / step);
      for (let i = 0; i < count; i++) {
        const x = i * step;
        const t = (x / w) * total;
        const p = peaks[Math.floor((i / count) * peaks.length)] ?? 0;
        const amp = Math.max(1.5, p * (h / 2 - 6));

        const region = regions.find((r) => t >= r.start && t <= r.end);
        if (region) {
          ctx.fillStyle = HUES[region.hue % HUES.length];
          ctx.globalAlpha = region.kept ? 1 : 0.4;
        } else {
          ctx.fillStyle = "#3b3557";
          ctx.globalAlpha = 1;
        }
        ctx.fillRect(x, mid - amp, barW, amp * 2);
      }
      ctx.globalAlpha = 1;

      if (active) {
        const x0 = xOf(active.start);
        const x1 = xOf(active.end);
        ctx.strokeStyle = dragging ? "rgba(255,255,255,0.9)" : "rgba(255,255,255,0.55)";
        ctx.lineWidth = 1.5;
        ctx.strokeRect(x0 + 0.75, 0.75, Math.max(2, x1 - x0) - 1.5, h - 1.5);
      }

      if (playheadAt !== null) {
        const x = xOf(playheadAt);
        ctx.fillStyle = "#ffffff";
        ctx.shadowColor = "rgba(255,255,255,0.8)";
        ctx.shadowBlur = 8;
        ctx.fillRect(x - 1, 0, 2, h);
        ctx.shadowBlur = 0;
      }
    };

    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(wrap);
    return () => ro.disconnect();
  }, [peaks, regions, active, activeIndex, playheadAt, span, dragging]);

  /* ---- dragging the active region ---- */
  const timeAt = useCallback((clientX: number) => {
    const wrap = wrapRef.current;
    if (!wrap) return 0;
    const rect = wrap.getBoundingClientRect();
    return clamp(((clientX - rect.left) / rect.width) * (span || 1), 0, span || 1);
  }, [span]);

  const applyDrag = useCallback((clientX: number) => {
    const d = dragRef.current;
    if (!d || !active) return;
    const delta = timeAt(clientX) - d.t0;

    let s = d.s0;
    let e = d.e0;

    if (d.mode === "start") {
      s = clamp(d.s0 + delta, Math.max(0, d.e0 - MAX_CLIP), d.e0 - MIN_CLIP);
    } else if (d.mode === "end") {
      e = clamp(d.e0 + delta, d.s0 + MIN_CLIP, Math.min(span, d.s0 + MAX_CLIP));
    } else {
      const len = d.e0 - d.s0;
      s = clamp(d.s0 + delta, 0, Math.max(0, span - len));
      e = s + len;
    }
    onTrim(active.index, s, e);
  }, [active, onTrim, span, timeAt]);

  const beginDrag = (mode: DragMode) => (e: React.PointerEvent) => {
    if (!active) return;
    e.preventDefault();
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    dragRef.current = { mode, t0: timeAt(e.clientX), s0: active.start, e0: active.end };
    setDragging(true);
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!dragRef.current) return;
    applyDrag(e.clientX);
  };

  const endDrag = (e: React.PointerEvent) => {
    if (!dragRef.current) return;
    (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
    dragRef.current = null;
    setDragging(false);
  };

  /* Arrow keys nudge a focused handle. Shift makes it coarser. */
  const nudge = (mode: "start" | "end") => (e: React.KeyboardEvent) => {
    if (!active) return;
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    e.stopPropagation();
    const step = (e.shiftKey ? 1 : 0.25) * (e.key === "ArrowLeft" ? -1 : 1);
    if (mode === "start") {
      const s = clamp(active.start + step, Math.max(0, active.end - MAX_CLIP), active.end - MIN_CLIP);
      onTrim(active.index, s, active.end);
    } else {
      const en = clamp(active.end + step, active.start + MIN_CLIP, Math.min(span, active.start + MAX_CLIP));
      onTrim(active.index, active.start, en);
    }
  };

  const onCanvasClick = (e: React.MouseEvent) => {
    if (readOnly || dragRef.current) return;
    const t = timeAt(e.clientX);
    const hit = regions.find((r) => t >= r.start && t <= r.end);
    if (hit) onSelect(hit.index);
  };

  const pct = (t: number) => `${((t / (span || 1)) * 100).toFixed(3)}%`;

  return (
    <div className="wf" data-dragging={dragging} data-readonly={readOnly}>
      <div className="wf__canvas" ref={wrapRef} onClick={onCanvasClick}
        role="group" aria-label="Track waveform with picked moments">
        <canvas ref={canvasRef} />

        {active && (
          <div className="wf__trim" data-nokeys
            style={{
              left: pct(active.start),
              width: `calc(${pct(active.end)} - ${pct(active.start)})`,
              ["--hue" as any]: HUES[active.hue % HUES.length],
            }}
          >
            <button className="wf__grip wf__grip--start"
              onPointerDown={beginDrag("start")} onPointerMove={onPointerMove}
              onPointerUp={endDrag} onPointerCancel={endDrag} onKeyDown={nudge("start")}
              aria-label={`Trim start of ${active.label}. Arrow keys to nudge.`} />

            <button className="wf__body"
              onPointerDown={beginDrag("move")} onPointerMove={onPointerMove}
              onPointerUp={endDrag} onPointerCancel={endDrag}
              aria-label={`Move ${active.label} without changing its length`} />

            <button className="wf__grip wf__grip--end"
              onPointerDown={beginDrag("end")} onPointerMove={onPointerMove}
              onPointerUp={endDrag} onPointerCancel={endDrag} onKeyDown={nudge("end")}
              aria-label={`Trim end of ${active.label}. Arrow keys to nudge.`} />
          </div>
        )}
      </div>

      <div className="wf__foot">
        <span className="mono">0:00</span>
        <span className="wf__note">
          {note ?? (real
            ? "Click a moment to hear it · drag its edges to trim the cut"
            : "Preview waveform — drop a real file to hear the cuts")}
        </span>
        <span className="mono">{fmt(span)}</span>
      </div>
    </div>
  );
}

function fmt(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
