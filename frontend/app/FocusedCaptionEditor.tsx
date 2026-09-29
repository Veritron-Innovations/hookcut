"use client";

import { useEffect, useRef, useMemo } from "react";

type Props = {
  block: { id: string; text: string; start: number; end: number };
  channelData: Float32Array | null;
  sampleRate: number;
  currentTime: number;
  onSeek: (time: number) => void;
  onUpdateTiming: (id: string, field: "start" | "end", value: number) => void;
};

const FOCUS_PIXELS_PER_SECOND = 220; // much tighter than the main overview timeline
const FOCUS_HEIGHT = 90;
const PAD_SECONDS = 2; // context shown on either side of the caption itself

function formatTime(t: number): string {
  if (!Number.isFinite(t) || t < 0) t = 0;
  const m = Math.floor(t / 60);
  const s = (t % 60).toFixed(2).padStart(5, "0");
  return `${m}:${s}`;
}

/**
 * A dedicated, high-zoom waveform showing ONLY the currently selected
 * caption's own neighbourhood (a couple of seconds either side) -
 * computed fresh from the already-decoded full-song audio, not a slice
 * of the pre-rendered overview waveform (which is at far too low a
 * resolution per pixel to drag against precisely).
 *
 * This exists because dragging against the compressed, whole-song
 * timeline is how "close enough" retiming turns into a mess of
 * near-misses - professional tools (CapCut, Apple's Compressor) all
 * scope precise retiming to one selected caption, zoomed in, rather
 * than asking you to drag a small box among many on a zoomed-out
 * timeline. Only ever shows one caption's drag handles - nothing to
 * misclick here even with a genuinely bad baseline alignment.
 */
export default function FocusedCaptionEditor({ block, channelData, sampleRate, currentTime, onSeek, onUpdateTiming }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const windowStart = Math.max(0, block.start - PAD_SECONDS);
  const windowEnd = block.end + PAD_SECONDS;
  const windowDuration = windowEnd - windowStart;
  const width = Math.max(1, Math.ceil(windowDuration * FOCUS_PIXELS_PER_SECOND));

  const peaks = useMemo(() => {
    if (!channelData) return null;
    const startSample = Math.floor(windowStart * sampleRate);
    const endSample = Math.min(channelData.length, Math.ceil(windowEnd * sampleRate));
    const totalSamples = Math.max(1, endSample - startSample);
    const samplesPerPixel = Math.max(1, Math.floor(totalSamples / width));
    const result = new Array(width).fill(0);
    for (let i = 0; i < width; i++) {
      let max = 0;
      const s = startSample + i * samplesPerPixel;
      const e = Math.min(s + samplesPerPixel, endSample);
      for (let j = s; j < e; j++) {
        const v = Math.abs(channelData[j] ?? 0);
        if (v > max) max = v;
      }
      result[i] = max;
    }
    return result;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channelData, sampleRate, block.id, windowStart, windowEnd, width]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    canvas.width = width;
    canvas.height = FOCUS_HEIGHT;
    ctx.clearRect(0, 0, width, FOCUS_HEIGHT);
    ctx.fillStyle = "#211f2c";
    ctx.fillRect(0, 0, width, FOCUS_HEIGHT);

    if (peaks) {
      const mid = FOCUS_HEIGHT / 2;
      ctx.fillStyle = "#8b7fd6";
      for (let i = 0; i < peaks.length; i++) {
        const h = Math.max(1, peaks[i] * mid * 0.95);
        ctx.fillRect(i, mid - h, 1, h * 2);
      }
    }

    // Highlight the selected caption's own region within this window.
    const capStartX = (block.start - windowStart) * FOCUS_PIXELS_PER_SECOND;
    const capEndX = (block.end - windowStart) * FOCUS_PIXELS_PER_SECOND;
    ctx.fillStyle = "rgba(255, 197, 61, 0.22)";
    ctx.fillRect(capStartX, 0, capEndX - capStartX, FOCUS_HEIGHT);
    ctx.strokeStyle = "#ffc53d";
    ctx.lineWidth = 2;
    ctx.strokeRect(capStartX, 1, capEndX - capStartX, FOCUS_HEIGHT - 2);

    // Playhead, only drawn when it's actually within this focused window.
    if (currentTime >= windowStart && currentTime <= windowEnd) {
      const playheadX = (currentTime - windowStart) * FOCUS_PIXELS_PER_SECOND;
      ctx.fillStyle = "#ff5d5d";
      ctx.fillRect(Math.max(0, playheadX - 1), 0, 2, FOCUS_HEIGHT);
    }
  }, [peaks, width, block.start, block.end, windowStart, windowEnd, currentTime]);

  type DragMode = "start" | "end" | null;
  const dragRef = useRef<DragMode>(null);

  const xToTime = (clientX: number, rect: DOMRect) => windowStart + (clientX - rect.left) / FOCUS_PIXELS_PER_SECOND;

  const onHandleDown = (mode: DragMode) => (e: React.MouseEvent) => {
    e.stopPropagation();
    dragRef.current = mode;
    const onMove = (ev: MouseEvent) => {
      const canvas = canvasRef.current;
      if (!canvas || !dragRef.current) return;
      const rect = canvas.getBoundingClientRect();
      const t = xToTime(ev.clientX, rect);
      onUpdateTiming(block.id, dragRef.current, Math.round(t * 100) / 100);
    };
    const onUp = () => {
      dragRef.current = null;
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const nudge = (field: "start" | "end", deltaSeconds: number) => {
    const current = field === "start" ? block.start : block.end;
    onUpdateTiming(block.id, field, Math.round((current + deltaSeconds) * 100) / 100);
  };

  const capStartX = (block.start - windowStart) * FOCUS_PIXELS_PER_SECOND;
  const capEndX = (block.end - windowStart) * FOCUS_PIXELS_PER_SECOND;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <p style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: 0.6, color: "var(--muted)", fontWeight: 600, margin: 0 }}>
        Fine-tune &quot;{block.text}&quot; - drag the yellow edges against the waveform, or nudge below
      </p>
      <div
        style={{ position: "relative", width, cursor: "pointer" }}
        onClick={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          onSeek(xToTime(e.clientX, rect));
        }}
      >
        <canvas ref={canvasRef} style={{ display: "block", width, height: FOCUS_HEIGHT, borderRadius: 8 }} />
        <div onMouseDown={onHandleDown("start")} style={{ position: "absolute", left: capStartX - 4, top: 0, bottom: 0, width: 8, cursor: "ew-resize" }} />
        <div onMouseDown={onHandleDown("end")} style={{ position: "absolute", left: capEndX - 4, top: 0, bottom: 0, width: 8, cursor: "ew-resize" }} />
      </div>
      <div style={{ display: "flex", gap: 16, fontSize: 12, color: "var(--muted)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span>start {formatTime(block.start)}</span>
          <button onClick={() => nudge("start", -0.1)} style={nudgeButtonStyle}>−0.1s</button>
          <button onClick={() => nudge("start", 0.1)} style={nudgeButtonStyle}>+0.1s</button>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span>end {formatTime(block.end)}</span>
          <button onClick={() => nudge("end", -0.1)} style={nudgeButtonStyle}>−0.1s</button>
          <button onClick={() => nudge("end", 0.1)} style={nudgeButtonStyle}>+0.1s</button>
        </div>
      </div>
    </div>
  );
}

const nudgeButtonStyle: React.CSSProperties = {
  padding: "2px 8px",
  fontSize: 11,
  background: "var(--surface-2)",
  border: "1px solid var(--border)",
  borderRadius: 6,
  color: "var(--paper)",
  cursor: "pointer",
};
