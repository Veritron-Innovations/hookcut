"use client";

import { useState, useRef } from "react";
import SnakeGame from "./SnakeGame";
import TapSync from "./TapSync";
import { splitIntoTapPhrases } from "./lib/tapPhraseSplit";

const API_BASE = "http://localhost:8000";

type Mode = "clips" | "lyric_video";
type Aspect = "16:9" | "9:16" | "1:1";

type Concept = {
  angle_name: string;
  start_timestamp: string;
  end_timestamp: string;
  source_text: string;
  text_overlay_options: string[];
  tiktok_caption: string;
  ig_caption: string;
  clip_url: string;

};

type JobState = {
  stage: string;
  results: Concept[] | null;
  error: string | null;
  mode?: Mode;
  progress?: { current: number; total: number } | null;
  started_at?: number;
  audio_url?: string | null;
};

const STAGE_LABELS: Record<string, string> = {
  queued: "Queued...",
  transcribing: "Transcribing audio — this can take a minute...",
  analyzing: "Finding the best moments...",
  resolving_cover_art: "Preparing cover art...",
  cutting_clips: "Rendering...",
  done: "Done",
  error: "Something went wrong",
};

const TIPS = [
  "Karaoke sync uses word-level timestamps pulled straight from your audio.",
  "Pasted lyrics beat auto-transcription for Sheng/Swahili verses — Whisper isn't great at either yet.",
  "Each clip targets a different angle — relatability, curiosity, or a controversial take.",
  "The chorus almost always gets picked as one of the clips — it's usually the most quotable part.",
  "Cover art comes straight from your file's embedded metadata if you don't upload your own.",
  "Longer files take longer to transcribe, not to render — the actual clip cutting is fast.",
];

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function Home() {
  const [mode, setMode] = useState<Mode>("clips");
  const [file, setFile] = useState<File | null>(null);
  const [coverImage, setCoverImage] = useState<File | null>(null);
  const [genre, setGenre] = useState("");
  const [mood, setMood] = useState("");
  const [numConcepts, setNumConcepts] = useState(5);
  const [aspect, setAspect] = useState<Aspect>("16:9");
  const [lyrics, setLyrics] = useState(true);
  const [lyricsText, setLyricsText] = useState("");
  const [lyricsStyle, setLyricsStyle] = useState<"karaoke" | "pop">("karaoke");
  const [probingLyrics, setProbingLyrics] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<JobState | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [tipIndex, setTipIndex] = useState(0);
  const [fixingSection, setFixingSection] = useState(false);
  const [fixLinesText, setFixLinesText] = useState("");
  const [tapping, setTapping] = useState(false);
  const [patching, setPatching] = useState(false);
  const [patchConceptIndex, setPatchConceptIndex] = useState<number | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const tipRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startTimeRef = useRef<number>(0);

  const handleFileChange = async (selected: File | null) => {
    setFile(selected);
    setLyricsText("");
    if (!selected) return;

    setProbingLyrics(true);
    try {
      const formData = new FormData();
      formData.append("file", selected);
      const res = await fetch(`${API_BASE}/api/embedded-lyrics`, { method: "POST", body: formData });
      const data = await res.json();
      if (data.lyrics) setLyricsText(data.lyrics);
    } catch {
      // silent - lyrics box just stays empty, user can paste manually
    }
    setProbingLyrics(false);
  };

  const pollJob = (id: string) => {
    pollRef.current = setInterval(async () => {
      const res = await fetch(`${API_BASE}/api/jobs/${id}`);
      const data: JobState = await res.json();
      setJob(data);
      setElapsed((Date.now() - startTimeRef.current) / 1000);
      if (data.stage === "done" || data.stage === "error") {
        if (pollRef.current) clearInterval(pollRef.current);
        if (tipRef.current) clearInterval(tipRef.current);
      }
    }, 2000);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) return;

    setSubmitting(true);
    const formData = new FormData();
    formData.append("file", file);
    formData.append("mode", mode);
    formData.append("lyrics", String(lyrics));
    if (lyricsText.trim()) formData.append("lyrics_text", lyricsText);
    if (coverImage) formData.append("cover_image", coverImage);

    if (mode === "clips") {
      formData.append("genre", genre || "music");
      formData.append("mood", mood || "moody");
      formData.append("num_concepts", String(numConcepts));
    } else {
      formData.append("aspect", aspect);
      formData.append("lyrics_style", lyricsStyle);
    }

    const res = await fetch(`${API_BASE}/api/jobs`, { method: "POST", body: formData });
    const data = await res.json();
    setSubmitting(false);
    setJobId(data.job_id);
    setJob({ stage: "queued", results: [], error: null, mode });
    startTimeRef.current = Date.now();
    setElapsed(0);
    setTipIndex(0);
    pollJob(data.job_id);
    tipRef.current = setInterval(() => {
      setTipIndex((i) => (i + 1) % TIPS.length);
    }, 6000);
  };

  const handlePatchComplete = async (taps: number[]) => {
    if (!jobId) return;
    const lines = splitIntoTapPhrases(fixLinesText);

    setPatching(true);
    if (job?.mode === "lyric_video") {
      const fd = new FormData();
      fd.append("lines", JSON.stringify(lines));
      fd.append("taps", JSON.stringify(taps));
      if (patchConceptIndex !== null) fd.append("concept_index", String(patchConceptIndex));
      if (coverImage) fd.append("cover_image", coverImage);
      fd.append("lyrics_style", lyricsStyle);

      await fetch(`${API_BASE}/api/jobs/${jobId}/patch-section`, {
        method: "POST",
        body: fd,
      });
    } else {
      await fetch(`${API_BASE}/api/jobs/${jobId}/patch-section`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lines,
          taps,
          ...(patchConceptIndex !== null ? { concept_index: patchConceptIndex } : {}),
        }),
      });
    }
    setPatching(false);
    setTapping(false);
    setFixingSection(false);
    setFixLinesText("");
    setPatchConceptIndex(null);

    setJob((prev) => (prev ? { ...prev, stage: "cutting_clips" } : prev));
    startTimeRef.current = Date.now();
    setElapsed(0);
    setTipIndex(0);
    pollJob(jobId);
    tipRef.current = setInterval(() => {
      setTipIndex((i) => (i + 1) % TIPS.length);
    }, 6000);
  };

  const startFixingSection = (conceptIndex: number | null) => {
    setPatchConceptIndex(conceptIndex);
    setFixingSection(true);
    setTapping(false);
    setFixLinesText("");
  };

  const cancelFixingSection = () => {
    setFixingSection(false);
    setTapping(false);
    setFixLinesText("");
    setPatchConceptIndex(null);
  };

  const reset = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (tipRef.current) clearInterval(tipRef.current);
    setJobId(null);
    setJob(null);
    setFile(null);
    setCoverImage(null);
    setLyricsText("");
  };

  return (
    <main style={{ maxWidth: 860, margin: "0 auto", padding: "56px 24px 96px" }}>
      <header style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 48 }}>
        <img src="/icon-192.png" alt="" width={44} height={44} style={{ borderRadius: 12 }} />
        <span style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 26, letterSpacing: "-0.02em" }}>
          hookcut
        </span>
        <a href="/tap-sync" style={{ marginLeft: 12, padding: "8px 12px", borderRadius: 8, background: "transparent", border: "1px solid var(--border)", color: "var(--muted)", textDecoration: "none", fontSize: 13 }}>
          Tap-sync demo
        </a>
      </header>

      {!jobId && (
        <>
          <h1 style={{ fontSize: 44, lineHeight: 1.1, marginBottom: 12, maxWidth: 600 }}>
            Your song.{" "}
            <span style={{ background: "var(--gradient-signature)", WebkitBackgroundClip: "text", backgroundClip: "text", color: "transparent" }}>
              Their attention.
            </span>
          </h1>
          <p style={{ color: "var(--muted)", fontSize: 17, marginBottom: 32, maxWidth: 480 }}>
            Upload the real thing. We find the hook, cut the clip, and write the caption —
            in the time it takes to make coffee.
          </p>

          <div style={{ display: "flex", gap: 8, marginBottom: 24 }}>
            <ModeTab active={mode === "clips"} onClick={() => setMode("clips")}>
              Short clips
            </ModeTab>
            <ModeTab active={mode === "lyric_video"} onClick={() => setMode("lyric_video")}>
              Full lyric video
            </ModeTab>
          </div>

          <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 18, background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 20, padding: 28 }}>
            <Field label="Audio or video file">
              <input
                type="file"
                accept="audio/*,video/*"
                required
                onChange={(e) => handleFileChange(e.target.files?.[0] ?? null)}
                style={inputStyle}
              />
            </Field>

            <Field
              label="Lyrics"
              hint={
                probingLyrics
                  ? "checking file for embedded lyrics..."
                  : "shown on screen for karaoke sync — edit freely, especially for Sheng/Swahili where auto-transcription is less reliable"
              }
            >
              <textarea
                value={lyricsText}
                onChange={(e) => setLyricsText(e.target.value)}
                placeholder="Paste or edit lyrics here. Leave blank to let transcription handle it automatically."
                rows={6}
                style={{ ...inputStyle, fontFamily: "var(--font-mono)", resize: "vertical" }}
              />
            </Field>

            <Field label="Cover art" hint="optional — falls back to embedded art if your file has any">
              <input
                type="file"
                accept="image/*"
                onChange={(e) => setCoverImage(e.target.files?.[0] ?? null)}
                style={inputStyle}
              />
            </Field>

            {mode === "clips" ? (
              <>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
                  <Field label="Genre">
                    <input
                      type="text"
                      placeholder="e.g. afro rnb"
                      value={genre}
                      onChange={(e) => setGenre(e.target.value)}
                      style={inputStyle}
                    />
                  </Field>
                  <Field label="Mood">
                    <input
                      type="text"
                      placeholder="e.g. reflective"
                      value={mood}
                      onChange={(e) => setMood(e.target.value)}
                      style={inputStyle}
                    />
                  </Field>
                </div>

                <Field label="Number of clips">
                  <input
                    type="number"
                    min={1}
                    max={10}
                    value={numConcepts}
                    onChange={(e) => setNumConcepts(Number(e.target.value))}
                    style={inputStyle}
                  />
                </Field>
              </>
            ) : (
              <Field label="Aspect ratio">
                <select
                  value={aspect}
                  onChange={(e) => setAspect(e.target.value as Aspect)}
                  style={inputStyle}
                >
                  <option value="16:9">16:9 (landscape, classic YouTube)</option>
                  <option value="9:16">9:16 (vertical, Reels/Shorts)</option>
                  <option value="1:1">1:1 (square)</option>
                </select>
              </Field>
              <Field label="Lyrics style" hint="Karaoke burns in synced lyrics; Pop lyrics places styled captions">
                <select value={lyricsStyle} onChange={(e) => setLyricsStyle(e.target.value as any)} style={inputStyle}>
                  <option value="karaoke">Karaoke (burn-in)</option>
                  <option value="pop">Pop lyrics</option>
                </select>
              </Field>
            )}

            <label style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 14, color: "var(--paper)" }}>
              <input type="checkbox" checked={lyrics} onChange={(e) => setLyrics(e.target.checked)} />
              Burn in karaoke-style lyrics (audio-only sources)
            </label>

            <button type="submit" disabled={submitting || !file} style={buttonStyle}>
              {submitting ? "Uploading..." : mode === "clips" ? "Generate clips" : "Generate lyric video"}
            </button>
          </form>
        </>
      )}

      {job && job.stage !== "done" && job.stage !== "error" && (
        <div style={{ marginTop: 40 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 12 }}>
            <p style={{ fontSize: 15, color: "var(--paper)" }}>
              {job.stage === "cutting_clips" && job.progress && job.progress.total > 0
                ? `Rendering clip ${Math.min(job.progress.current + 1, job.progress.total)} of ${job.progress.total}...`
                : STAGE_LABELS[job.stage] ?? job.stage}
            </p>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--muted)" }}>
              {formatElapsed(elapsed)}
            </span>
          </div>
          <div className="progress-track">
            <div className="progress-sweep" />
          </div>
          <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 16, minHeight: 18 }}>
            {TIPS[tipIndex]}
          </p>
          <div style={{ marginTop: 24, display: "flex", justifyContent: "center" }}>
            <SnakeGame />
          </div>
          {job.mode !== "lyric_video" && job.results && job.results.length > 0 && (
            <div style={{ marginTop: 32 }}>
              <p style={{ ...labelStyle, marginBottom: 12 }}>
                {job.results.length} clip{job.results.length > 1 ? "s" : ""} ready so far
              </p>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(290px, 1fr))", gap: 24 }}>
                {job.results.map((concept, i) => (
                  <ClipCard key={i} concept={concept} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {job && job.stage === "error" && (
        <div style={{ marginTop: 40 }}>
          <p style={{ color: "var(--signal)", marginBottom: 16 }}>Error: {job.error}</p>
          <button onClick={reset} style={buttonStyle}>Try again</button>
        </div>
      )}

      {job && job.stage === "done" && job.results && job.mode === "lyric_video" && (
        <div style={{ marginTop: 40 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 28 }}>
            <h2 style={{ fontSize: 24 }}>Lyric video ready</h2>
            <button onClick={reset} style={buttonStyle}>Start another</button>
          </div>
          <div className="phone-frame" style={{ maxWidth: 640, margin: "0 auto" }}>
            <video
              src={`${API_BASE}${job.results[0].clip_url}`}
              controls
              style={{ width: "100%", background: "#000" }}
            />
          </div>
          <div style={{ display: "flex", gap: 12, maxWidth: 640, margin: "18px auto 0" }}>
            <a
              href={`${API_BASE}${job.results[0].clip_url}`}
              download
              style={{ ...buttonStyle, flex: 1, textAlign: "center", textDecoration: "none" }}
            >
              Download
            </a>
            {job.audio_url && !fixingSection && (
              <button onClick={() => startFixingSection(null)} style={{ ...secondaryButtonStyle, flex: 1 }}>
                Fix a section
              </button>
            )}
          </div>
          {fixingSection && job.audio_url && (
            <div style={{ maxWidth: 640, margin: "18px auto 0" }}>
              <FixSectionPanel
                audioUrl={`${API_BASE}${job.audio_url}`}
                fixLinesText={fixLinesText}
                setFixLinesText={setFixLinesText}
                tapping={tapping}
                setTapping={setTapping}
                patching={patching}
                onComplete={handlePatchComplete}
                onCancel={cancelFixingSection}
              />
            </div>
          )}
        </div>
      )}

      {job && job.stage === "done" && job.results && job.mode !== "lyric_video" && (
        <div style={{ marginTop: 40 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 28 }}>
            <h2 style={{ fontSize: 24 }}>{job.results.length} clips ready</h2>
            <button onClick={reset} style={buttonStyle}>Start another</button>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(290px, 1fr))", gap: 24 }}>
            {job.results.map((concept, i) => (
              <ClipCard
                key={i}
                concept={concept}
                onFixClick={job.audio_url ? () => startFixingSection(i) : undefined}
              />
            ))}
          </div>
          {fixingSection && job.audio_url && patchConceptIndex !== null && (
            <div style={{ maxWidth: 480, margin: "24px auto 0" }}>
              <p style={{ ...labelStyle, marginBottom: 8, textAlign: "center" }}>
                Fixing: {job.results[patchConceptIndex].angle_name}
              </p>
              <FixSectionPanel
                audioUrl={`${API_BASE}${job.audio_url}`}
                fixLinesText={fixLinesText}
                setFixLinesText={setFixLinesText}
                tapping={tapping}
                setTapping={setTapping}
                patching={patching}
                onComplete={handlePatchComplete}
                onCancel={cancelFixingSection}
              />
            </div>
          )}
        </div>
      )}
    </main>
  );
}

function ModeTab({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        padding: "8px 16px",
        borderRadius: 999,
        border: active ? "1px solid transparent" : "1px solid var(--border)",
        background: active ? "var(--gradient-signature)" : "transparent",
        color: active ? "#0c0b12" : "var(--muted)",
        fontFamily: "var(--font-display)",
        fontWeight: 700,
        fontSize: 14,
        cursor: "pointer",
      }}
    >
      {children}
    </button>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "block", fontSize: 13, color: "var(--muted)" }}>
      {label}
      {hint && <span style={{ color: "var(--muted)", fontWeight: 400 }}> — {hint}</span>}
      {children}
    </label>
  );
}

function ClipCard({ concept, onFixClick }: { concept: Concept; onFixClick?: () => void }) {
  return (
    <div style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 20, padding: 18, display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="phone-frame">
        <video
          src={`${API_BASE}${concept.clip_url}`}
          controls
          style={{ width: "100%", aspectRatio: "9/16", background: "#000" }}
        />
      </div>

      <div>
        <strong style={{ fontFamily: "var(--font-display)", fontSize: 17 }}>{concept.angle_name}</strong>
        <p style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--cue)", marginTop: 4 }}>
          {concept.start_timestamp} – {concept.end_timestamp}
        </p>
      </div>

      <div>
        <p style={labelStyle}>Text overlay options</p>
        {concept.text_overlay_options.map((opt, i) => (
          <p key={i} style={{ fontSize: 14, margin: "4px 0", color: "var(--paper)" }}>• {opt}</p>
        ))}
      </div>

      <div>
        <p style={labelStyle}>TikTok caption</p>
        <p style={{ fontSize: 14, color: "var(--paper)" }}>{concept.tiktok_caption}</p>
      </div>

      <div>
        <p style={labelStyle}>IG caption</p>
        <p style={{ fontSize: 14, color: "var(--paper)" }}>{concept.ig_caption}</p>
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <a href={`${API_BASE}${concept.clip_url}`} download style={{ ...buttonStyle, flex: 1, textAlign: "center", textDecoration: "none", display: "block" }}>
          Download
        </a>
        {onFixClick && (
          <button onClick={onFixClick} style={{ ...secondaryButtonStyle, flex: 1 }}>
            Fix lyrics
          </button>
        )}
      </div>
    </div>
  );
}

function FixSectionPanel({
  audioUrl,
  fixLinesText,
  setFixLinesText,
  tapping,
  setTapping,
  patching,
  onComplete,
  onCancel,
}: {
  audioUrl: string;
  fixLinesText: string;
  setFixLinesText: (v: string) => void;
  tapping: boolean;
  setTapping: (v: boolean) => void;
  patching: boolean;
  onComplete: (taps: number[]) => void;
  onCancel: () => void;
}) {
  const lines = splitIntoTapPhrases(fixLinesText);

  if (tapping) {
    return (
      <TapSync
        audioUrl={audioUrl}
        lines={lines}
        onComplete={onComplete}
        onCancel={() => setTapping(false)}
      />
    );
  }

  return (
    <div style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 20, padding: 24, display: "flex", flexDirection: "column", gap: 14 }}>
      <div>
        <strong style={{ fontFamily: "var(--font-display)", fontSize: 16 }}>Paste the lines to fix</strong>
        <p style={{ color: "var(--muted)", fontSize: 13, marginTop: 4 }}>
          Paste as much as you like, however it's formatted — a whole verse as one block is fine.
          It's automatically split into short phrases below, one per tap. Section tags like
          [Verse 1] or [SFX: ...] are dropped automatically.
        </p>
      </div>
      <textarea
        value={fixLinesText}
        onChange={(e) => setFixLinesText(e.target.value)}
        placeholder={"Kiburi ni mzigo, weka chini usimame...\nBure umepewa, pokea, uishi."}
        rows={5}
        style={{ ...inputStyle, fontFamily: "var(--font-mono)", resize: "vertical" }}
      />
      {lines.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ color: "var(--muted)", fontSize: 12 }}>
            Will tap through {lines.length} phrase{lines.length === 1 ? "" : "s"} — check this looks
            right before you start (edit the text above, adding a line break anywhere you want a
            different split, if a phrase looks off):
          </span>
          <div style={{ display: "flex", flexDirection: "column", gap: 2, maxHeight: 140, overflowY: "auto", background: "var(--surface-2)", borderRadius: 8, padding: 8 }}>
            {lines.map((line, i) => (
              <span key={i} style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--paper)" }}>
                {i + 1}. {line}
              </span>
            ))}
          </div>
        </div>
      )}
      <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
        <button onClick={onCancel} style={secondaryButtonStyle}>
          Cancel
        </button>
        <button
          onClick={() => setTapping(true)}
          disabled={lines.length === 0 || patching}
          style={{ ...buttonStyle, opacity: lines.length === 0 || patching ? 0.4 : 1 }}
        >
          {patching ? "Applying fix..." : `Start tapping (${lines.length} lines)`}
        </button>
      </div>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  display: "block",
  width: "100%",
  padding: "10px 12px",
  marginTop: 6,
  background: "var(--surface-2)",
  border: "1px solid var(--border)",
  borderRadius: 10,
  color: "var(--paper)",
  fontSize: 14,
  boxSizing: "border-box",
};

const buttonStyle: React.CSSProperties = {
  padding: "12px 20px",
  background: "var(--gradient-signature)",
  border: "none",
  borderRadius: 10,
  color: "#0c0b12",
  fontWeight: 700,
  fontFamily: "var(--font-display)",
  cursor: "pointer",
  fontSize: 15,
};

const secondaryButtonStyle: React.CSSProperties = {
  padding: "12px 20px",
  background: "transparent",
  border: "1px solid var(--border)",
  borderRadius: 10,
  color: "var(--paper)",
  fontWeight: 600,
  fontFamily: "var(--font-body)",
  cursor: "pointer",
  fontSize: 15,
};

const labelStyle: React.CSSProperties = {
  fontSize: 11,
  textTransform: "uppercase",
  letterSpacing: 0.6,
  color: "var(--muted)",
  marginBottom: 4,
  fontWeight: 600,
};
