"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import type { Concept, JobState, SourceKind } from "./lib/types";
import { STAGE_LABELS, isWorking } from "./lib/types";
import {
  MOCK, ApiError, clipSrc, createJob, getJob, renderJob,
  seedMockJob, isMockSeed, type Trims,
} from "./lib/api";
import StageRail, { PIPELINE, pipelineIndex } from "./components/StageRail";
import DevStates from "./components/DevStates";
import Waveform, { type Region } from "./components/Waveform";
import ClipPreview from "./components/ClipPreview";
import HeroWave from "./components/HeroWave";
import { useClipPlayer } from "./lib/useClipPlayer";

const HUES = ["#ff4d8d", "#ff9f1c", "#2ec4b6", "#7c5cff", "#4cc9f0"];

/* Genre tells the model whether to hunt for a chorus or for spoken beats, and
   both of these set the register the captions get written in. Presets exist
   so the artist is recognising rather than composing. */
const GENRES = ["Afro R&B", "Drill", "Gospel", "Amapiano", "Podcast", "Interview", "Comedy"];
const MOODS = ["Reflective", "Hyped", "Moody", "Playful", "Raw"];

/* Ask for a generous spread - the review screen is where they get narrowed. */
const MOMENTS_TO_FIND = 6;

/* A real run's output, shown before upload so the promise is concrete rather
   than described. Labelled as an example - these are not the user's track. */
const EXAMPLE_FINDS = [
  { time: "00:41", dur: "17s", name: "The chorus",
    line: "And I keep the porch light on for nobody" },
  { time: "02:03", dur: "18s", name: "The confession",
    line: "I told my mother I was fine on the phone" },
  { time: "00:08", dur: "21s", name: "The opening hook",
    line: "There's a version of me that never left" },
];

/* ------------------------------------------------------------- helpers */

function secondsOf(ts: string): number {
  const parts = ts.split(":").map(Number);
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  return 0;
}

function durationOf(c: Concept): string {
  const d = Math.round(secondsOf(c.end_timestamp) - secondsOf(c.start_timestamp));
  return d > 0 ? `${d}s` : "—";
}

/** Seconds back to the mm:ss the rest of the app speaks in. */
function stamp(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function elapsedLabel(ms: number): string {
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m > 0 ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}

function fileSize(bytes: number): string {
  if (bytes > 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes > 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1e3))} KB`;
}

/** The mark is four waveform bars - the same object the product works on. */
function Wordmark() {
  return (
    <>
      <span className="wordmark__mark" aria-hidden="true">
        <span /><span /><span /><span />
      </span>
      hookcut
    </>
  );
}

/**
 * Headline words that light up one after another on load - the same gesture
 * the rendered clips make with their lyrics, so the page opens by doing the
 * thing the product does.
 */
function Sung({ text, from }: { text: string; from: number }) {
  return (
    <>
      {text.split(" ").map((word, i) => (
        <span key={i} className="sung" style={{ animationDelay: `${(from + i) * 0.085}s` }}>
          {word}{i < text.split(" ").length - 1 ? " " : ""}
        </span>
      ))}
    </>
  );
}

/** Move through a deck with the arrow keys. */
function useArrowKeys(onPrev: () => void, onNext: () => void, active: boolean) {
  useEffect(() => {
    if (!active) return;
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      // A focused trim handle uses the arrow keys to nudge its own edge.
      if (target?.closest?.("[data-nokeys]")) return;
      if (e.key === "ArrowLeft") { e.preventDefault(); onPrev(); }
      if (e.key === "ArrowRight") { e.preventDefault(); onNext(); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onPrev, onNext, active]);
}

/* ---------------------------------------------------------------- page */

export default function Home() {
  const [file, setFile] = useState<File | null>(null);
  const [coverImage, setCoverImage] = useState<File | null>(null);
  const [genre, setGenre] = useState("");
  const [mood, setMood] = useState("");
  const [lyrics, setLyrics] = useState(true);

  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<JobState | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [sending, setSending] = useState(false);
  const [approved, setApproved] = useState<Set<number>>(new Set());

  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [coverUrl, setCoverUrl] = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // The artist just handed us the file - play it straight from memory rather
  // than fetching it back off the server.
  useEffect(() => {
    if (!file) { setAudioUrl(null); return; }
    const url = URL.createObjectURL(file);
    setAudioUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  useEffect(() => {
    if (!coverImage) { setCoverUrl(null); return; }
    const url = URL.createObjectURL(coverImage);
    setCoverUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [coverImage]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }, []);

  const fail = useCallback((message: string) => {
    stopPolling();
    setStartedAt(null);
    setJob((prev) => ({
      stage: "error",
      source_kind: prev?.source_kind ?? null,
      concepts: prev?.concepts ?? null,
      results: prev?.results ?? null,
      error: message,
    }));
  }, [stopPolling]);

  const poll = useCallback((id: string) => {
    stopPolling();
    const tick = async () => {
      try {
        const data = await getJob(id);
        setJob(data);
        if (!isWorking(data.stage)) { stopPolling(); setStartedAt(null); }
      } catch (err) {
        fail(err instanceof ApiError ? err.message : "Something went wrong talking to the server.");
      }
    };
    pollRef.current = setInterval(tick, 2000);
    void tick();
  }, [fail, stopPolling]);

  useEffect(() => {
    if (startedAt === null) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [startedAt]);

  useEffect(() => stopPolling, [stopPolling]);

  useEffect(() => {
    if (!MOCK) return;
    const seed = new URLSearchParams(window.location.search).get("state");
    if (!isMockSeed(seed)) return;
    const id = seedMockJob(seed);
    if (!id) return;
    setJobId(id);
    setStartedAt(Date.now());
    setNow(Date.now());
    poll(id);
  }, [poll]);

  useEffect(() => {
    if (job?.stage === "awaiting_review" && job.concepts) {
      setApproved(new Set(job.concepts.map((c) => c.index)));
    }
  }, [job?.stage]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) return;

    setSubmitting(true);
    const form = new FormData();
    form.append("file", file);
    form.append("genre", genre || "music");
    form.append("mood", mood || "moody");
    form.append("num_concepts", String(MOMENTS_TO_FIND));
    form.append("lyrics", String(lyrics));
    if (coverImage) form.append("cover_image", coverImage);

    try {
      const id = await createJob(form);
      setJobId(id);
      setJob({ stage: "queued", source_kind: null, concepts: null, results: null, error: null });
      setStartedAt(Date.now());
      setNow(Date.now());
      poll(id);
    } catch (err) {
      fail(err instanceof ApiError ? err.message : "Couldn't start the job.");
    } finally {
      setSubmitting(false);
    }
  };

  const startRender = async (trims: Trims = {}) => {
    if (!jobId || approved.size === 0) return;
    setSending(true);
    try {
      await renderJob(jobId, Array.from(approved).sort((a, b) => a - b), trims);
      setJob((prev) => (prev ? { ...prev, stage: "cutting_clips" } : prev));
      setStartedAt(Date.now());
      setNow(Date.now());
      poll(jobId);
    } catch (err) {
      fail(err instanceof ApiError ? err.message : "Couldn't start rendering.");
    } finally {
      setSending(false);
    }
  };

  const reset = () => {
    stopPolling();
    setJobId(null); setJob(null); setFile(null); setCoverImage(null);
    setApproved(new Set()); setStartedAt(null);
  };

  const toggle = (index: number) =>
    setApproved((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });

  const stage = job?.stage;
  const wide = stage === "awaiting_review" || stage === "done";
  const intake = !jobId;

  return (
    <div className="app">
      <header className="masthead">
        {/* The hero carries the big lockup on the intake screen, so the small
            one only appears once the hero is gone. */}
        {intake ? <span /> : <a className="wordmark" href="/"><Wordmark /></a>}
        <div className="masthead__right">
          {jobId && (
            <button className="btn btn--ghost btn--sm" onClick={reset}>Start over</button>
          )}
          {MOCK && <DevStates />}
        </div>
      </header>

      {/* Where the artist is, on the five steps that actually exist. */}
      {jobId ? (
        <StageRail
          segments={PIPELINE}
          activeIndex={pipelineIndex(stage)}
          tone={isWorking(stage) ? "warm" : "cool"}
          live={isWorking(stage)}
          tag="Pipeline"
        />
      ) : (
        <div />
      )}

      <div className="app__main">
        <div className={`center${wide ? " center--wide" : ""}${intake ? " center--intake" : ""}`}
          style={wide ? { height: "100%" } : undefined}>

          {!jobId && (
            <div className="intake">
              <div className="hero">
                {/* Centres in the space above without moving anything below. */}
                <div className="hero__intro">
                  <a className="wordmark wordmark--lg" href="/"><Wordmark /></a>
                </div>
                <div className="hero__body">
                <div className="hero__band">
                  <HeroWave />
                  <h1 className="hero__line">
                    {/* The marked phrase is styled exactly like a kept moment
                        on the review screen - same tint, same edge caps. */}
                    <Sung text="Your track already has" from={0} />{" "}
                    <span className="hero__cut">
                      <span className="hero__cut-time mono">00:41 – 00:58</span>
                      <Sung text="the clip" from={4} />
                    </span>{" "}
                    <Sung text="in it." from={6} />
                  </h1>
                </div>
                <p className="hero__sub">
                  Drop a song or an episode. hookcut finds the moments that stop a scroll and
                  cuts them straight out of your own audio.
                </p>
                <ul className="hero__facts">
                  <li>No generated audio</li>
                  <li>No stock footage</li>
                  <li>Your file, your voice</li>
                </ul>

                {/* What a real run returns, so the promise is concrete. */}
                <div className="finds">
                  <div className="finds__head">
                    <span className="eyebrow">What it finds</span>
                    <span className="finds__src mono">example · 3:24 afro r&amp;b track</span>
                  </div>
                  {EXAMPLE_FINDS.map((f, i) => (
                    <div className="finds__row" key={f.time}
                      style={{ ["--hue" as any]: `var(--hue-${i})`,
                               animationDelay: `${1.3 + i * 0.12}s` }}>
                      <span className="finds__time mono">{f.time}</span>
                      <span className="finds__dur mono">{f.dur}</span>
                      <span className="finds__name">{f.name}</span>
                      <span className="finds__line">&ldquo;{f.line}&rdquo;</span>
                    </div>
                  ))}
                </div>
                </div>
                {/* Mirrors hero__intro so the body stays exactly centred. */}
                <div className="hero__tail" aria-hidden="true" />
              </div>

              <UploadForm
                {...{ file, setFile, coverImage, setCoverImage, genre, setGenre, mood, setMood,
                      lyrics, setLyrics, submitting }}
                onSubmit={handleSubmit}
              />
            </div>
          )}

          {isWorking(stage) && (
            <div className="panel status">
              {/* The artist's own track, decoded locally - real from the first
                  second. Moments light up on it as the backend reports them;
                  until it streams partials, this simply stays empty. */}
              {audioUrl ? (
                <Waveform
                  audioUrl={audioUrl}
                  regions={(job?.concepts ?? []).map((c, i) => ({
                    index: c.index,
                    start: secondsOf(c.start_timestamp),
                    end: secondsOf(c.end_timestamp),
                    hue: i % 5,
                    kept: true,
                    label: c.angle_name,
                  }))}
                  activeIndex={-1}
                  playheadAt={null}
                  duration={1}
                  onSelect={() => {}}
                  onTrim={() => {}}
                  readOnly
                  note={
                    (job?.concepts?.length ?? 0) > 0
                      ? `${job!.concepts!.length} moment${job!.concepts!.length === 1 ? "" : "s"} so far`
                      : "Listening to your track"
                  }
                />
              ) : (
                <div className="wave" aria-hidden="true">
                  {Array.from({ length: 24 }).map((_, i) => (
                    <span key={i} style={{ animationDelay: `${(i % 12) * 0.08}s` }} />
                  ))}
                </div>
              )}
              <p className="status__stage" role="status" aria-live="polite">
                {STAGE_LABELS[stage!]}…
              </p>
              {startedAt !== null && (
                <p className="status__meta mono">{elapsedLabel(now - startedAt)} elapsed</p>
              )}
              <p className="status__meta" style={{ maxWidth: "44ch" }}>
                {stage === "transcribing"
                  ? "Whisper reads the whole file — a few minutes is normal."
                  : stage === "cutting_clips"
                  ? `Rendering ${job?.total_to_render ?? approved.size} clip${
                      (job?.total_to_render ?? approved.size) === 1 ? "" : "s"}.`
                  : " "}
              </p>
            </div>
          )}

          {stage === "error" && (
            <div className="notice">
              <p style={{ flex: "1 1 280px" }}>{job?.error ?? "Something went wrong."}</p>
              <button className="btn btn--ghost" onClick={reset}>Start over</button>
            </div>
          )}

          {stage === "awaiting_review" && job?.concepts && (
            <ReviewDeck
              concepts={job.concepts}
              sourceKind={job.source_kind}
              audioUrl={audioUrl}
              coverUrl={coverUrl}
              lyricsOn={lyrics}
              approved={approved}
              onToggle={toggle}
              onAll={() => setApproved(new Set(job.concepts!.map((c) => c.index)))}
              onNone={() => setApproved(new Set())}
              onRender={startRender}
              sending={sending}
            />
          )}

          {stage === "done" && job?.results && (
            <ResultsDeck results={job.results} sourceKind={job.source_kind} onReset={reset} />
          )}
        </div>
      </div>

    </div>
  );
}

/* ----------------------------------------------------------------- form */

function UploadForm(props: any) {
  const { file, setFile, coverImage, setCoverImage, genre, setGenre, mood, setMood,
          lyrics, setLyrics, submitting, onSubmit } = props;
  const [over, setOver] = useState(false);

  return (
    <form onSubmit={onSubmit}>
      <label
        className="drop" data-over={over} data-filled={Boolean(file)}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault(); setOver(false);
          const dropped = e.dataTransfer.files?.[0];
          if (dropped) setFile(dropped);
        }}
      >
        <input type="file" accept="audio/*,video/*" required
          onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        {file ? (
          <>
            <p className="drop__title">Ready to listen</p>
            <span className="drop__file"><WaveIcon /> {file.name} · {fileSize(file.size)}</span>
          </>
        ) : (
          <>
            <p className="drop__title">Drop your track or episode</p>
            <p className="drop__hint">mp3, wav, m4a, mp4, mov — or click to browse</p>
          </>
        )}
      </label>

      <div className="tune">
        <p className="tune__why">
          These shape how the captions and hook lines are <em>written</em> — not which
          moments get found. You choose those yourself in a moment.
        </p>

        <div className="field">
          <span className="field__label">What is this?</span>
          <ChipRow options={GENRES} value={genre} onChange={setGenre} />
          <input className="input input--slim" type="text" value={genre}
            placeholder="or type your own — soukous, sermon, true crime…"
            onChange={(e) => setGenre(e.target.value)} />
        </div>

        <div className="field">
          <span className="field__label">How should the captions sound?</span>
          <ChipRow options={MOODS} value={mood} onChange={setMood} />
        </div>
      </div>

      <div className="fields__row2">
        <label className="switch">
          <input type="checkbox" checked={lyrics}
            onChange={(e) => setLyrics(e.target.checked)} />
          <span className="switch__track"><span className="switch__thumb" /></span>
          <span className="switch__text">
            Burn in karaoke lyrics
            <small>Audio sources only — video keeps its own picture.</small>
          </span>
        </label>

        <button type="submit" className="btn btn--primary" disabled={submitting || !file}>
          {submitting ? "Uploading…" : "Find the moments"}
        </button>
      </div>

      <label className="coverline">
        Cover art — optional, falls back to your mp3&apos;s embedded art
        <input type="file" accept="image/*"
          onChange={(e) => setCoverImage(e.target.files?.[0] ?? null)} />
        {coverImage && <span className="mono">{coverImage.name}</span>}
      </label>
    </form>
  );
}

/** Tap to pick, tap again to clear. Recognising beats composing. */
function ChipRow({ options, value, onChange }:
  { options: string[]; value: string; onChange: (v: string) => void }) {
  const norm = value.trim().toLowerCase();
  return (
    <div className="chips">
      {options.map((opt) => {
        const on = norm === opt.toLowerCase();
        return (
          <button key={opt} type="button" className="chip-opt" aria-pressed={on}
            onClick={() => onChange(on ? "" : opt)}>
            {opt}
          </button>
        );
      })}
    </div>
  );
}

/* ---------------------------------------------------------- review deck */

function ReviewDeck({ concepts, sourceKind, audioUrl, coverUrl, lyricsOn,
                     approved, onToggle, onAll, onNone, onRender, sending }: {
  concepts: Concept[];
  sourceKind: SourceKind;
  audioUrl: string | null;
  coverUrl: string | null;
  lyricsOn: boolean;
  approved: Set<number>;
  onToggle: (i: number) => void;
  onAll: () => void;
  onNone: () => void;
  onRender: (trims: Trims) => void;
  sending: boolean;
}) {
  const [cursor, setCursor] = useState(0);
  const [trims, setTrims] = useState<Trims>({});
  const count = approved.size;
  const current = concepts[cursor];
  const mediaRef = useRef<HTMLVideoElement | null>(null);
  const player = useClipPlayer(mediaRef, audioUrl);

  const prev = useCallback(() => setCursor((c) => Math.max(0, c - 1)), []);
  const next = useCallback(() => setCursor((c) => Math.min(concepts.length - 1, c + 1)), [concepts.length]);
  useArrowKeys(prev, next, true);

  /** Where a moment actually starts and ends, after any trimming. */
  const rangeOf = useCallback((c: Concept) => {
    const trim = trims[c.index];
    return trim ?? { start: secondsOf(c.start_timestamp), end: secondsOf(c.end_timestamp) };
  }, [trims]);

  // Leave a little air after the last moment so it isn't flush to the edge.
  const trackLength = Math.max(...concepts.map((c) => secondsOf(c.end_timestamp))) * 1.12;

  const regions: Region[] = concepts.map((c, i) => ({
    index: c.index,
    ...rangeOf(c),
    hue: i % 5,
    kept: approved.has(c.index),
    label: c.angle_name,
  }));

  const onTrim = (index: number, start: number, end: number) =>
    setTrims((prevTrims) => ({ ...prevTrims, [index]: { start, end } }));

  const resetTrim = (index: number) =>
    setTrims((prevTrims) => {
      const nextTrims = { ...prevTrims };
      delete nextTrims[index];
      return nextTrims;
    });

  const audition = (index: number) => {
    const at = concepts.findIndex((c) => c.index === index);
    if (at >= 0) setCursor(at);
    const c = concepts[at >= 0 ? at : cursor];
    const { start, end } = rangeOf(c);
    if (player.available) player.playRange(start, end);
  };

  const toggleCurrentAudio = () => {
    if (player.playing) { player.stop(); return; }
    const { start, end } = rangeOf(current);
    player.playRange(start, end);
  };

  // Stop playback when moving off the moment being auditioned.
  useEffect(() => { player.stop(); /* eslint-disable-next-line */ }, [cursor]);

  const trimmedCount = Object.keys(trims).length;

  return (
    <div className="deck deck--review">
      <div className="deck__head">
        <div>
          <p className="eyebrow" style={{ marginBottom: 7 }}>
            Moment {cursor + 1} of {concepts.length} · nothing rendered yet
          </p>
          <h2>{concepts.length} moments worth cutting</h2>
          <p>
            Hear each cut before you spend time rendering it.{" "}
            {sourceKind === "audio"
              ? "Kept moments become 9:16 videos with your cover art and synced lyrics."
              : sourceKind === "video"
              ? "Kept moments are cut straight from your video."
              : ""}
          </p>
        </div>
        <div className="btn-row">
          <button className="btn btn--ghost btn--sm" onClick={onAll}>Keep all</button>
          <button className="btn btn--ghost btn--sm" onClick={onNone}>Clear</button>
        </div>
      </div>

      {/* The whole track at a glance - every moment visible and comparable. */}
      <Waveform
        audioUrl={audioUrl}
        regions={regions}
        activeIndex={current.index}
        playheadAt={player.playing ? player.position : null}
        duration={trackLength}
        onSelect={audition}
        onTrim={onTrim}
      />

      <div className="deck__stage">
        <button className="deck__arrow" onClick={prev} disabled={cursor === 0}
          aria-label="Previous moment"><Chevron dir="left" /></button>

        <div className="studio">
          {/* The clip itself, live, before ffmpeg is asked for anything. */}
          <ClipPreview
            mediaRef={mediaRef}
            src={audioUrl}
            sourceKind={sourceKind}
            coverUrl={coverUrl}
            hue={HUES[cursor % 5]}
            text={current.source_text}
            start={rangeOf(current).start}
            end={rangeOf(current).end}
            position={player.position}
            playing={player.playing}
            lyricsOn={lyricsOn}
            onToggle={toggleCurrentAudio}
          />

          <MomentCard
            concept={current}
            hue={cursor % 5}
            kept={approved.has(current.index)}
            onToggle={() => onToggle(current.index)}
            range={rangeOf(current)}
            trimmed={Boolean(trims[current.index])}
            onResetTrim={() => resetTrim(current.index)}
          />
        </div>

        <button className="deck__arrow" onClick={next} disabled={cursor === concepts.length - 1}
          aria-label="Next moment"><Chevron dir="right" /></button>
      </div>

      <div className="dock">
        <span className="dock__note">
          {count === 0 ? "Nothing kept yet." : `${count} of ${concepts.length} kept`}
          {trimmedCount > 0 && `, ${trimmedCount} trimmed`}
          {count > 0 && "."}
        </span>
        <span className="dock__spacer" />
        <button className="btn btn--primary" onClick={() => onRender(trims)}
          disabled={count === 0 || sending}>
          {sending ? "Starting…" : `Render ${count} clip${count === 1 ? "" : "s"}`}
        </button>
      </div>
    </div>
  );
}

function MomentCard({ concept, hue, kept, onToggle, range, trimmed, onResetTrim }: {
  concept: Concept;
  hue: number;
  kept: boolean;
  onToggle: () => void;
  range: { start: number; end: number };
  trimmed: boolean;
  onResetTrim: () => void;
}) {
  return (
    <article className="card" data-kept={kept} style={{ ["--hue" as any]: `var(--hue-${hue})` }}>
      <div className="card__main">
        <div className="card__meta">
          <span className="card__name">{concept.angle_name}</span>
          <span className="card__time mono">
            {stamp(range.start)}–{stamp(range.end)} · {Math.round(range.end - range.start)}s
          </span>
          {trimmed && (
            <button className="card__reset" onClick={onResetTrim}
              title={`Back to ${concept.start_timestamp}–${concept.end_timestamp}`}>
              trimmed · reset
            </button>
          )}
        </div>

        {/* The transcript line this moment was chosen for. */}
        <p className="card__line">“{concept.source_text}”</p>

        <div className="card__actions">
          <button className="keep" onClick={onToggle} aria-pressed={kept}>
            {kept ? <><Check /> Keeping this</> : "Skip this one"}
          </button>
        </div>
      </div>

      <div className="card__aside">
        <div className="block">
          <h4>Text overlay options</h4>
          {concept.text_overlay_options.map((opt, i) => <p key={i}>{opt}</p>)}
        </div>
        <div className="block">
          <h4>TikTok</h4>
          <p>{concept.tiktok_caption}</p>
        </div>
        <div className="block block--caption">
          <h4>Instagram</h4>
          <p>{concept.ig_caption}</p>
        </div>
      </div>
    </article>
  );
}

/* --------------------------------------------------------- results deck */

function ResultsDeck({ results, sourceKind, onReset }:
  { results: Concept[]; sourceKind: SourceKind; onReset: () => void }) {
  const [cursor, setCursor] = useState(0);
  const concept = results[cursor];
  const src = clipSrc(concept.clip_url);
  const ratio = sourceKind === "audio" ? "9 / 16" : "16 / 9";

  const prev = useCallback(() => setCursor((c) => Math.max(0, c - 1)), []);
  const next = useCallback(() => setCursor((c) => Math.min(results.length - 1, c + 1)), [results.length]);
  useArrowKeys(prev, next, true);

  return (
    <div className="deck">
      <div className="deck__head">
        <div>
          <p className="eyebrow" style={{ marginBottom: 7 }}>
            Clip {cursor + 1} of {results.length} · cut from your own audio
          </p>
          <h2>{results.length} clip{results.length === 1 ? "" : "s"} ready</h2>
        </div>
        <button className="btn btn--ghost btn--sm" onClick={onReset}>Start another</button>
      </div>

      <div className="deck__stage">
        <button className="deck__arrow" onClick={prev} disabled={cursor === 0}
          aria-label="Previous clip"><Chevron dir="left" /></button>

        <div className="clipstage">
          {src ? (
            <video src={src} controls style={{ aspectRatio: ratio }} />
          ) : (
            <div className="clipstage__placeholder" style={{ aspectRatio: ratio }}>
              No clip file — mock data
            </div>
          )}

          <div className="clipstage__info">
            <div>
              <p className="eyebrow" style={{ marginBottom: 5 }}>{concept.angle_name}</p>
              <p className="mono" style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
                {concept.start_timestamp}–{concept.end_timestamp} · {durationOf(concept)}
              </p>
            </div>

            <p className="clipstage__line">“{concept.source_text}”</p>

            <div className="block">
              <h4>Text overlay options</h4>
              {concept.text_overlay_options.map((opt, i) => <p key={i}>{opt}</p>)}
            </div>

            <div className="btn-row">
              {src ? (
                <a className="btn btn--primary" href={src} download>Download</a>
              ) : (
                <button className="btn btn--primary" disabled>Download</button>
              )}
            </div>
          </div>
        </div>

        <button className="deck__arrow" onClick={next} disabled={cursor === results.length - 1}
          aria-label="Next clip"><Chevron dir="right" /></button>
      </div>

      <div className="dock">
        <div className="rail">
          {results.map((c, i) => (
            <button key={c.index} className="rail__pip" data-kept={true} data-current={i === cursor}
              onClick={() => setCursor(i)} aria-label={`Go to clip ${i + 1}`}
              style={{ ["--hue" as any]: `var(--hue-${i % 5})` }} />
          ))}
        </div>
        <span className="dock__note dock__spacer">Use ← → to move between clips</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- icons */

function Chevron({ dir }: { dir: "left" | "right" }) {
  return (
    <svg width="17" height="17" viewBox="0 0 16 16" fill="none" aria-hidden="true"
      style={{ transform: dir === "left" ? "rotate(180deg)" : undefined }}>
      <path d="M6 3l5 5-5 5" stroke="currentColor" strokeWidth="2"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Check() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M3 8.5l3.2 3.2L13 5" stroke="currentColor" strokeWidth="2.6"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}



function WaveIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M1 8h2M5 3.5v9M8 5.5v5M11 2v12M14.5 6.5v3" stroke="currentColor"
        strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}
