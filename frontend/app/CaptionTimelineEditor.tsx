"use client";

import { useState, useRef, useEffect } from "react";
import { inputStyle, buttonStyle, secondaryButtonStyle, labelStyle } from "./styles";
import PopCaptionPreview from "./PopCaptionPreview";
import type { ThemeName } from "./lib/popCaptionEngine";

type CaptionBlock = {
  id: string;
  text: string;
  start: number;
  end: number;
};

type ExistingLine = {
  start: number;
  end: number;
  words: { word: string }[];
};

type Props = {
  audioUrl: string;
  existingLines?: ExistingLine[];
  onSave: (blocks: { text: string; start: number; end: number }[]) => void;
  onCancel: () => void;
  saving: boolean;
  captionTheme?: ThemeName;
  coverImageUrl?: string;
  previewWidth?: number;
  previewHeight?: number;
};

const PIXELS_PER_SECOND = 45;
const WAVEFORM_HEIGHT = 72;
const TRACK_HEIGHT = 40;
const MIN_BLOCK_SECONDS = 0.15;

function formatTime(t: number): string {
  if (!Number.isFinite(t) || t < 0) t = 0;
  const m = Math.floor(t / 60);
  const s = (t % 60).toFixed(1).padStart(4, "0");
  return `${m}:${s}`;
}

/**
 * Non-linear caption editor: a real waveform, a playhead you scrub or
 * click to move, and caption blocks placed directly on a timeline with
 * their own independent start/end - drag to adjust, or type what's
 * actually said at the current playhead and click "insert here".
 *
 * This is the direct answer to the gap in TapSync's sequential
 * tap-through-a-span model: fixing one mistranscribed word here means
 * finding its exact moment by ear, pausing, and inserting a caption
 * right there - not re-tapping through everything around it.
 *
 * Waveform is decoded client-side via the Web Audio API (fetch + fully
 * decode the audio, extract per-pixel peak amplitude) rather than
 * requiring a backend-generated waveform image - keeps this self-
 * contained and works with whatever audio_url the job already serves.
 * Full-file decoding is fine at song length; would need a streaming
 * approach for something much longer than a few minutes.
 */
export default function CaptionTimelineEditor({
  audioUrl,
  existingLines,
  onSave,
  onCancel,
  saving,
  captionTheme = "default",
  coverImageUrl,
  previewWidth = 1080,
  previewHeight = 1920,
}: Props) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [waveformPeaks, setWaveformPeaks] = useState<number[] | null>(null);
  const [waveformError, setWaveformError] = useState(false);
  const [channelData, setChannelData] = useState<Float32Array | null>(null);
  const [sampleRate, setSampleRate] = useState(44100);

  const [blocks, setBlocks] = useState<CaptionBlock[]>(() =>
    (existingLines || []).map((l, i) => ({
      id: `existing-${i}`,
      text: l.words.map((w) => w.word).join(" "),
      start: l.start,
      end: l.end,
    }))
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [newCaptionText, setNewCaptionText] = useState("");

  type DragState = { id: string; mode: "move" | "start" | "end"; startX: number; origStart: number; origEnd: number };
  const [drag, setDrag] = useState<DragState | null>(null);

  // Decode the audio once to extract waveform peaks. Client-side only -
  // window.AudioContext doesn't exist during SSR, but this whole
  // component only ever mounts after a user action (opening the
  // editor), never during an initial/server render, so no SSR guard
  // needed beyond the "use client" directive already on this file.
  useEffect(() => {
    let cancelled = false;
    async function loadWaveform() {
      try {
        const AudioContextClass = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
        const ctx = new AudioContextClass();
        const res = await fetch(audioUrl);
        const buf = await res.arrayBuffer();
        const audioBuffer = await ctx.decodeAudioData(buf);
        if (cancelled) return;
        const channel = audioBuffer.getChannelData(0);
        setChannelData(channel);
        setSampleRate(audioBuffer.sampleRate);
        const totalWidth = Math.max(1, Math.ceil(audioBuffer.duration * PIXELS_PER_SECOND));
        const samplesPerPixel = Math.max(1, Math.floor(channel.length / totalWidth));
        const peaks: number[] = new Array(totalWidth);
        for (let i = 0; i < totalWidth; i++) {
          let max = 0;
          const startIdx = i * samplesPerPixel;
          const endIdx = Math.min(startIdx + samplesPerPixel, channel.length);
          for (let j = startIdx; j < endIdx; j++) {
            const v = Math.abs(channel[j]);
            if (v > max) max = v;
          }
          peaks[i] = max;
        }
        setWaveformPeaks(peaks);
        void ctx.close();
      } catch {
        // Waveform is a visual aid, not required for the editor to
        // function - scrubbing/seeking/inserting all still work off the
        // <audio> element alone, just without the visual reference.
        setWaveformError(true);
      }
    }
    void loadWaveform();
    return () => {
      cancelled = true;
    };
  }, [audioUrl]);

  // Wire the <audio> element's own events - single source of truth for
  // currentTime/duration/playing state, rather than tracking them
  // separately and risking drift from what's actually playing.
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const onTimeUpdate = () => setCurrentTime(audio.currentTime);
    const onLoadedMeta = () => setDuration(audio.duration || 0);
    const onPlay = () => setIsPlaying(true);
    const onPause = () => setIsPlaying(false);
    audio.addEventListener("timeupdate", onTimeUpdate);
    audio.addEventListener("loadedmetadata", onLoadedMeta);
    audio.addEventListener("play", onPlay);
    audio.addEventListener("pause", onPause);
    return () => {
      audio.removeEventListener("timeupdate", onTimeUpdate);
      audio.removeEventListener("loadedmetadata", onLoadedMeta);
      audio.removeEventListener("play", onPlay);
      audio.removeEventListener("pause", onPause);
    };
  }, []);

  // Redraw the waveform + playhead whenever either changes. Canvas
  // rather than SVG/DOM bars - a multi-minute song at 45px/sec is
  // thousands of bars, cheap on canvas, expensive as DOM nodes.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !waveformPeaks) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const width = waveformPeaks.length;
    const height = WAVEFORM_HEIGHT;
    canvas.width = width;
    canvas.height = height;
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#211f2c";
    ctx.fillRect(0, 0, width, height);
    const mid = height / 2;
    ctx.fillStyle = "#8b7fd6";
    for (let i = 0; i < waveformPeaks.length; i++) {
      const h = Math.max(1, waveformPeaks[i] * mid * 0.95);
      ctx.fillRect(i, mid - h, 1, h * 2);
    }
    const playheadX = currentTime * PIXELS_PER_SECOND;
    ctx.fillStyle = "#ff5d5d";
    ctx.fillRect(Math.max(0, playheadX - 1), 0, 2, height);
  }, [waveformPeaks, currentTime]);

  const togglePlay = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) void audio.play();
    else audio.pause();
  };

  const seekTo = (time: number) => {
    const audio = audioRef.current;
    if (!audio) return;
    const clamped = Math.max(0, Math.min(time, duration || time));
    audio.currentTime = clamped;
    setCurrentTime(clamped);
  };

  const handleTimelineClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (drag) return; // a drag's mouseup shouldn't also register as a seek click
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    seekTo(x / PIXELS_PER_SECOND);
  };

  const insertCaptionHere = () => {
    const text = newCaptionText.trim();
    if (!text) return;
    const id = `new-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const start = currentTime;
    const end = duration ? Math.min(duration, start + 1.5) : start + 1.5;
    setBlocks((prev) => [...prev, { id, text, start, end }].sort((a, b) => a.start - b.start));
    setNewCaptionText("");
    setSelectedId(id);
  };

  const deleteBlock = (id: string) => {
    setBlocks((prev) => prev.filter((b) => b.id !== id));
    if (selectedId === id) setSelectedId(null);
  };

  const updateBlockText = (id: string, text: string) => {
    setBlocks((prev) => prev.map((b) => (b.id === id ? { ...b, text } : b)));
  };

  const startDrag = (e: React.MouseEvent, id: string, mode: DragState["mode"]) => {
    e.stopPropagation();
    const block = blocks.find((b) => b.id === id);
    if (!block) return;
    setDrag({ id, mode, startX: e.clientX, origStart: block.start, origEnd: block.end });
  };

  useEffect(() => {
    if (!drag) return;
    const onMove = (e: MouseEvent) => {
      const dx = (e.clientX - drag.startX) / PIXELS_PER_SECOND;
      setBlocks((prev) =>
        prev.map((b) => {
          if (b.id !== drag.id) return b;
          if (drag.mode === "move") {
            const len = drag.origEnd - drag.origStart;
            const newStart = Math.max(0, drag.origStart + dx);
            return { ...b, start: newStart, end: newStart + len };
          }
          if (drag.mode === "start") {
            const newStart = Math.max(0, Math.min(drag.origStart + dx, drag.origEnd - MIN_BLOCK_SECONDS));
            return { ...b, start: newStart };
          }
          const newEnd = Math.max(drag.origStart + MIN_BLOCK_SECONDS, drag.origEnd + dx);
          return { ...b, end: newEnd };
        })
      );
    };
    const onUp = () => setDrag(null);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [drag]);

  const timelineWidth = Math.max(waveformPeaks?.length ?? 0, Math.ceil((duration || 0) * PIXELS_PER_SECOND), 1);
  const selectedBlock = blocks.find((b) => b.id === selectedId) || null;

  return (
    <div style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 20, padding: 20, display: "flex", flexDirection: "column", gap: 14 }}>
      <audio ref={audioRef} src={audioUrl} preload="auto" />

      <div>
        <strong style={{ fontFamily: "var(--font-display)", fontSize: 16 }}>Timeline editor</strong>
        <p style={{ color: "var(--muted)", fontSize: 13, marginTop: 4 }}>
          Play or click the waveform to find a moment, type what&apos;s actually said, and insert a caption right there.
          Drag a caption&apos;s edges to adjust its timing, or drag its middle to move it. The preview below is live -
          nothing here re-renders until you hit Save.
        </p>
      </div>

      <div style={{ maxWidth: 320, margin: "0 auto", width: "100%" }}>
        <PopCaptionPreview
          blocks={blocks}
          currentTime={currentTime}
          theme={captionTheme}
          channelData={channelData}
          sampleRate={sampleRate}
          backgroundImageUrl={coverImageUrl}
          width={previewWidth}
          height={previewHeight}
        />
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <button onClick={togglePlay} style={{ ...buttonStyle, padding: "8px 16px", fontSize: 13 }}>
          {isPlaying ? "Pause" : "Play"}
        </button>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--muted)" }}>
          {formatTime(currentTime)} / {formatTime(duration)}
        </span>
        {waveformError && (
          <span style={{ fontSize: 12, color: "var(--muted)" }}>(waveform preview unavailable - scrubbing still works)</span>
        )}
      </div>

      <div style={{ overflowX: "auto", borderRadius: 10, border: "1px solid var(--border)" }}>
        <div
          onClick={handleTimelineClick}
          style={{ position: "relative", width: timelineWidth, cursor: "pointer" }}
        >
          {waveformPeaks ? (
            <canvas ref={canvasRef} style={{ display: "block", width: timelineWidth, height: WAVEFORM_HEIGHT }} />
          ) : (
            <div style={{ width: timelineWidth, height: WAVEFORM_HEIGHT, background: "var(--surface-2)" }} />
          )}
          <div style={{ position: "relative", height: TRACK_HEIGHT, background: "var(--surface-2)", borderTop: "1px solid var(--border)" }}>
            {blocks.map((b) => (
              <div
                key={b.id}
                onMouseDown={(e) => startDrag(e, b.id, "move")}
                onClick={(e) => {
                  e.stopPropagation();
                  setSelectedId(b.id);
                }}
                style={{
                  position: "absolute",
                  left: b.start * PIXELS_PER_SECOND,
                  width: Math.max(6, (b.end - b.start) * PIXELS_PER_SECOND),
                  top: 4,
                  bottom: 4,
                  background: selectedId === b.id ? "#ffc53d" : "#8b7fd6",
                  borderRadius: 4,
                  cursor: "grab",
                  display: "flex",
                  alignItems: "center",
                  overflow: "hidden",
                  paddingLeft: 6,
                  fontSize: 11,
                  color: "#181622",
                  fontWeight: 600,
                  whiteSpace: "nowrap",
                }}
                title={b.text}
              >
                <div onMouseDown={(e) => startDrag(e, b.id, "start")} style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 6, cursor: "ew-resize" }} />
                {b.text}
                <div onMouseDown={(e) => startDrag(e, b.id, "end")} style={{ position: "absolute", right: 0, top: 0, bottom: 0, width: 6, cursor: "ew-resize" }} />
              </div>
            ))}
          </div>
        </div>
      </div>

      <div>
        <p style={labelStyle}>Insert at {formatTime(currentTime)}</p>
        <div style={{ display: "flex", gap: 8 }}>
          <input
            value={newCaptionText}
            onChange={(e) => setNewCaptionText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") insertCaptionHere();
            }}
            placeholder="What's actually said here?"
            style={{ ...inputStyle, flex: 1, marginTop: 0 }}
          />
          <button onClick={insertCaptionHere} style={{ ...buttonStyle, padding: "10px 18px" }}>
            Insert here
          </button>
        </div>
      </div>

      {selectedBlock && (
        <div>
          <p style={labelStyle}>
            Editing caption at {formatTime(selectedBlock.start)}–{formatTime(selectedBlock.end)}
          </p>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              value={selectedBlock.text}
              onChange={(e) => updateBlockText(selectedBlock.id, e.target.value)}
              style={{ ...inputStyle, flex: 1, marginTop: 0 }}
            />
            <button onClick={() => deleteBlock(selectedBlock.id)} style={{ ...secondaryButtonStyle, padding: "10px 18px" }}>
              Delete
            </button>
          </div>
        </div>
      )}

      <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
        <button onClick={onCancel} style={secondaryButtonStyle}>
          Cancel
        </button>
        <button
          onClick={() => onSave(blocks.map(({ text, start, end }) => ({ text, start, end })))}
          disabled={saving || blocks.length === 0}
          style={{ ...buttonStyle, opacity: saving || blocks.length === 0 ? 0.5 : 1 }}
        >
          {saving ? "Applying..." : `Save & re-render (${blocks.length} caption${blocks.length === 1 ? "" : "s"})`}
        </button>
      </div>
    </div>
  );
}
