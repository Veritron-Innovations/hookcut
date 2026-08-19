"use client";

import { useState, useRef } from "react";

const API_BASE = "http://localhost:8000";

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
};

const STAGE_LABELS: Record<string, string> = {
  queued: "Queued...",
  transcribing: "Transcribing audio (this can take a minute)...",
  analyzing: "Finding the best moments...",
  resolving_cover_art: "Preparing cover art...",
  cutting_clips: "Rendering clips...",
  done: "Done",
  error: "Something went wrong",
};

export default function Home() {
  const [file, setFile] = useState<File | null>(null);
  const [coverImage, setCoverImage] = useState<File | null>(null);
  const [genre, setGenre] = useState("");
  const [mood, setMood] = useState("");
  const [numConcepts, setNumConcepts] = useState(5);
  const [lyrics, setLyrics] = useState(true);
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<JobState | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const pollJob = (id: string) => {
    pollRef.current = setInterval(async () => {
      const res = await fetch(`${API_BASE}/api/jobs/${id}`);
      const data: JobState = await res.json();
      setJob(data);
      if (data.stage === "done" || data.stage === "error") {
        if (pollRef.current) clearInterval(pollRef.current);
      }
    }, 2000);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) return;

    setSubmitting(true);
    const formData = new FormData();
    formData.append("file", file);
    formData.append("genre", genre || "music");
    formData.append("mood", mood || "moody");
    formData.append("num_concepts", String(numConcepts));
    formData.append("lyrics", String(lyrics));
    if (coverImage) formData.append("cover_image", coverImage);

    const res = await fetch(`${API_BASE}/api/jobs`, { method: "POST", body: formData });
    const data = await res.json();
    setSubmitting(false);
    setJobId(data.job_id);
    setJob({ stage: "queued", results: null, error: null });
    pollJob(data.job_id);
  };

  const reset = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    setJobId(null);
    setJob(null);
    setFile(null);
    setCoverImage(null);
  };

  return (
    <main style={{ maxWidth: 720, margin: "0 auto", padding: "48px 24px" }}>
      <h1 style={{ fontSize: 32, marginBottom: 4 }}>hookcut</h1>
      <p style={{ color: "#9a9aa5", marginBottom: 32 }}>
        Turn your song or podcast into ready-to-post short-form clips.
      </p>

      {!jobId && (
        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <label>
            Audio or video file
            <input
              type="file"
              accept="audio/*,video/*"
              required
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              style={inputStyle}
            />
          </label>

          <label>
            Cover art (optional — falls back to embedded art if your mp3 has any)
            <input
              type="file"
              accept="image/*"
              onChange={(e) => setCoverImage(e.target.files?.[0] ?? null)}
              style={inputStyle}
            />
          </label>

          <label>
            Genre
            <input
              type="text"
              placeholder="e.g. afro rnb, true crime podcast"
              value={genre}
              onChange={(e) => setGenre(e.target.value)}
              style={inputStyle}
            />
          </label>

          <label>
            Mood
            <input
              type="text"
              placeholder="e.g. reflective, moody, upbeat"
              value={mood}
              onChange={(e) => setMood(e.target.value)}
              style={inputStyle}
            />
          </label>

          <label>
            Number of clips
            <input
              type="number"
              min={1}
              max={10}
              value={numConcepts}
              onChange={(e) => setNumConcepts(Number(e.target.value))}
              style={inputStyle}
            />
          </label>

          <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input type="checkbox" checked={lyrics} onChange={(e) => setLyrics(e.target.checked)} />
            Burn in karaoke-style lyrics (audio-only sources)
          </label>

          <button type="submit" disabled={submitting || !file} style={buttonStyle}>
            {submitting ? "Uploading..." : "Generate clips"}
          </button>
        </form>
      )}

      {job && job.stage !== "done" && job.stage !== "error" && (
        <div style={{ marginTop: 32 }}>
          <p>{STAGE_LABELS[job.stage] ?? job.stage}</p>
          <div style={{ height: 4, background: "#26262e", borderRadius: 2, overflow: "hidden" }}>
            <div style={{ height: "100%", width: "60%", background: "#6c5ce7", animation: "pulse 1.5s infinite" }} />
          </div>
        </div>
      )}

      {job && job.stage === "error" && (
        <div style={{ marginTop: 32, color: "#ff6b6b" }}>
          <p>Error: {job.error}</p>
          <button onClick={reset} style={buttonStyle}>Try again</button>
        </div>
      )}

      {job && job.stage === "done" && job.results && (
        <div style={{ marginTop: 32 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
            <h2>{job.results.length} clips ready</h2>
            <button onClick={reset} style={buttonStyle}>Start another</button>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 24 }}>
            {job.results.map((concept, i) => (
              <ClipCard key={i} concept={concept} />
            ))}
          </div>
        </div>
      )}
    </main>
  );
}

function ClipCard({ concept }: { concept: Concept }) {
  return (
    <div style={{ background: "#17171d", borderRadius: 12, padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
      <video
        src={`${API_BASE}${concept.clip_url}`}
        controls
        style={{ width: "100%", aspectRatio: "9/16", borderRadius: 8, background: "#000" }}
      />
      <div>
        <strong>{concept.angle_name}</strong>
        <p style={{ fontSize: 13, color: "#9a9aa5" }}>
          {concept.start_timestamp} – {concept.end_timestamp}
        </p>
      </div>

      <div>
        <p style={labelStyle}>Text overlay options</p>
        {concept.text_overlay_options.map((opt, i) => (
          <p key={i} style={{ fontSize: 14, margin: "4px 0" }}>• {opt}</p>
        ))}
      </div>

      <div>
        <p style={labelStyle}>TikTok caption</p>
        <p style={{ fontSize: 14 }}>{concept.tiktok_caption}</p>
      </div>

      <div>
        <p style={labelStyle}>IG caption</p>
        <p style={{ fontSize: 14 }}>{concept.ig_caption}</p>
      </div>

      <a href={`${API_BASE}${concept.clip_url}`} download style={{ ...buttonStyle, textAlign: "center", textDecoration: "none" }}>
        Download
      </a>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  display: "block",
  width: "100%",
  padding: "10px 12px",
  marginTop: 6,
  background: "#17171d",
  border: "1px solid #2a2a33",
  borderRadius: 8,
  color: "#f2f2f5",
  boxSizing: "border-box",
};

const buttonStyle: React.CSSProperties = {
  padding: "10px 20px",
  background: "#6c5ce7",
  border: "none",
  borderRadius: 8,
  color: "white",
  fontWeight: 600,
  cursor: "pointer",
};

const labelStyle: React.CSSProperties = {
  fontSize: 12,
  textTransform: "uppercase",
  letterSpacing: 0.5,
  color: "#9a9aa5",
  marginBottom: 4,
};
