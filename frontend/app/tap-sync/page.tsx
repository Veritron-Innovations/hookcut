"use client";

import { useState } from "react";
import TapSync from "../TapSync";

const API_BASE = "http://localhost:8000";

export default function DemoTapSyncPage() {
  const [audioUrl, setAudioUrl] = useState("");
  const [linesText, setLinesText] = useState("");
  const [result, setResult] = useState<any | null>(null);

  const lines = linesText.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);

  return (
    <main style={{ maxWidth: 860, margin: "0 auto", padding: 40 }}>
      <h2>Tap-sync demo</h2>

      <div style={{ display: "grid", gap: 12, marginBottom: 18 }}>
        <label style={{ display: "block" }}>
          Audio URL (or relative path to a served clip):
          <input value={audioUrl} onChange={(e) => setAudioUrl(e.target.value)} style={{ display: "block", width: "100%", padding: 8, marginTop: 6 }} />
        </label>

        <label style={{ display: "block" }}>
          Lines (one per row):
          <textarea value={linesText} onChange={(e) => setLinesText(e.target.value)} rows={6} style={{ display: "block", width: "100%", padding: 8, marginTop: 6 }} />
        </label>
      </div>

      {lines.length > 0 && audioUrl ? (
        <TapSync
          audioUrl={audioUrl}
          lines={lines}
          onCancel={() => setResult(null)}
          onComplete={async (taps) => {
            const res = await fetch(`${API_BASE}/api/tap-sync`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ lines, taps }),
            });
            const data = await res.json();
            setResult(data.lines);
          }}
        />
      ) : (
        <p style={{ color: "var(--muted)" }}>Enter an audio URL and at least one line to enable tap-sync.</p>
      )}

      {result && (
        <div style={{ marginTop: 18 }}>
          <h3>Resulting lines</h3>
          <pre style={{ background: "#0b0b10", color: "#e6eef8", padding: 12, borderRadius: 8, overflowX: "auto" }}>{JSON.stringify(result, null, 2)}</pre>
        </div>
      )}
    </main>
  );
}
