"use client";

import { useState, useRef, useEffect } from "react";

type TapSyncProps = {
  audioUrl: string;
  lines: string[];
  onComplete: (taps: number[]) => void;
  onCancel: () => void;
};

export default function TapSync({ audioUrl, lines, onComplete, onCancel }: TapSyncProps) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [taps, setTaps] = useState<number[]>([]);
  const [playing, setPlaying] = useState(false);

  const currentIndex = taps.length;
  const done = currentIndex >= lines.length;

  const tap = () => {
    const audio = audioRef.current;
    if (!audio || done) return;
    setTaps((prev) => [...prev, audio.currentTime]);
  };

  const undo = () => {
    setTaps((prev) => prev.slice(0, -1));
  };

  const restart = () => {
    setTaps([]);
    if (audioRef.current) {
      audioRef.current.currentTime = 0;
      audioRef.current.pause();
      setPlaying(false);
    }
  };

  const togglePlay = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) {
      audio.play();
      setPlaying(true);
    } else {
      audio.pause();
      setPlaying(false);
    }
  };

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.code === "Space") {
        e.preventDefault();
        tap();
      } else if (e.key === "z" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        undo();
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [taps, done]);

  return (
    <div style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 20, padding: 28, display: "flex", flexDirection: "column", gap: 20 }}>
      <div>
        <h3 style={{ fontFamily: "var(--font-display)", fontSize: 20, marginBottom: 4 }}>
          Tap-sync this section
        </h3>
        <p style={{ color: "var(--muted)", fontSize: 14 }}>
          Play the audio, press <strong>Space</strong> the instant each line starts.
        </p>
      </div>

      <audio ref={audioRef} src={audioUrl} onEnded={() => setPlaying(false)} />

      <div style={{ display: "flex", gap: 10 }}>
        <button onClick={togglePlay} style={buttonStyle}>
          {playing ? "Pause" : "Play"}
        </button>
        <button onClick={tap} disabled={done} style={{ ...buttonStyle, opacity: done ? 0.4 : 1 }}>
          Tap (Space)
        </button>
        <button onClick={undo} disabled={taps.length === 0} style={{ ...secondaryButtonStyle, opacity: taps.length === 0 ? 0.4 : 1 }}>
          Undo
        </button>
        <button onClick={restart} style={secondaryButtonStyle}>
          Restart
        </button>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 280, overflowY: "auto" }}>
        {lines.map((line, i) => {
          const tapped = i < taps.length;
          const isCurrent = i === currentIndex;
          return (
            <div
              key={i}
              style={{
                padding: "8px 12px",
                borderRadius: 8,
                background: isCurrent ? "var(--surface-2)" : "transparent",
                border: isCurrent ? "1px solid var(--cue)" : "1px solid transparent",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <span style={{ color: tapped ? "var(--muted)" : isCurrent ? "var(--paper)" : "var(--muted)", fontSize: 14 }}>
                {line}
              </span>
              {tapped && (
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--cue)" }}>
                  {taps[i].toFixed(2)}s
                </span>
              )}
            </div>
          );
        })}
      </div>

      <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
        <button onClick={onCancel} style={secondaryButtonStyle}>
          Cancel
        </button>
        <button
          onClick={() => onComplete(taps)}
          disabled={!done}
          style={{ ...buttonStyle, opacity: done ? 1 : 0.4 }}
        >
          {done ? "Use this timing" : `${lines.length - taps.length} lines left`}
        </button>
      </div>
    </div>
  );
}

const buttonStyle: React.CSSProperties = {
  padding: "10px 18px",
  background: "var(--gradient-signature)",
  border: "none",
  borderRadius: 10,
  color: "#0c0b12",
  fontWeight: 700,
  fontFamily: "var(--font-display)",
  cursor: "pointer",
  fontSize: 14,
};

const secondaryButtonStyle: React.CSSProperties = {
  padding: "10px 18px",
  background: "transparent",
  border: "1px solid var(--border)",
  borderRadius: 10,
  color: "var(--paper)",
  fontWeight: 600,
  fontFamily: "var(--font-body)",
  cursor: "pointer",
  fontSize: 14,
};
