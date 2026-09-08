/**
 * The one place the frontend talks to the backend.
 *
 * Set NEXT_PUBLIC_MOCK=1 to run entirely against in-memory fake data - no
 * Python, no ffmpeg, no backend process. The mock walks through the same
 * stages on a compressed timeline so every screen is reachable while the
 * real backend is being built elsewhere.
 */

import type { Concept, JobState } from "./types";
import { SAMPLE_CONCEPTS } from "./fixtures";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export const MOCK = process.env.NEXT_PUBLIC_MOCK === "1";

/** An error carrying a message already written for a person to read. */
export class ApiError extends Error {}

export function clipSrc(clipUrl: string | null | undefined): string | null {
  return clipUrl ? `${API_BASE}${clipUrl}` : null;
}

/* ------------------------------------------------------------------ real */

async function detailOf(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => null);
  return body?.detail ?? fallback;
}

async function realCreateJob(form: FormData): Promise<string> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/jobs`, { method: "POST", body: form });
  } catch {
    throw new ApiError(
      "Couldn't reach the backend. Start it with `uvicorn main:app --port 8000`, " +
        "or run the frontend with NEXT_PUBLIC_MOCK=1 to work without it."
    );
  }
  if (!res.ok) throw new ApiError(await detailOf(res, `The server answered with ${res.status}.`));
  const data = await res.json();
  return data.job_id;
}

async function realGetJob(id: string): Promise<JobState> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/jobs/${id}`);
  } catch {
    throw new ApiError("Lost contact with the backend. Is it still running on port 8000?");
  }
  if (!res.ok) throw new ApiError(await detailOf(res, `The server answered with ${res.status}.`));
  return res.json();
}

/**
 * Cut points the artist adjusted, keyed by concept index, in seconds.
 * Optional and additive: a backend that ignores `trims` still renders the
 * originally chosen timestamps rather than failing.
 */
export type Trims = Record<number, { start: number; end: number }>;

async function realRenderJob(id: string, approved: number[], trims: Trims = {}): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/jobs/${id}/render`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved, trims }),
    });
  } catch {
    throw new ApiError("Lost contact with the backend. Is it still running on port 8000?");
  }
  if (!res.ok) throw new ApiError(await detailOf(res, `The server answered with ${res.status}.`));
}

/* ------------------------------------------------------------------ mock */

type MockJob = {
  createdAt: number;
  renderStartedAt: number | null;
  approved: number[];
  trims: Trims;
  sourceKind: "audio" | "video";
  forcedError?: string;
};

function stamp(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

const mockJobs = new Map<string, MockJob>();

// Compressed so the whole flow is watchable in under 20 seconds.
const ANALYSIS_TIMELINE: Array<[number, string]> = [
  [0, "queued"],
  [1200, "transcribing"],
  [6000, "analyzing"],
  [9500, "resolving_cover_art"],
  [11000, "awaiting_review"],
];

const RENDER_MS = 6000;

function stageAt(elapsed: number): string {
  let current = ANALYSIS_TIMELINE[0][1];
  for (const [at, stage] of ANALYSIS_TIMELINE) {
    if (elapsed >= at) current = stage;
  }
  return current;
}

async function mockCreateJob(form: FormData): Promise<string> {
  const id = `mock-${Math.random().toString(36).slice(2, 10)}`;
  const file = form.get("file");
  const name = file instanceof File ? file.name.toLowerCase() : "";
  const isVideo = /\.(mp4|mov|mkv|webm|avi)$/.test(name);

  mockJobs.set(id, {
    createdAt: Date.now(),
    renderStartedAt: null,
    approved: [],
    trims: {},
    sourceKind: isVideo ? "video" : "audio",
  });
  return id;
}

async function mockGetJob(id: string): Promise<JobState> {
  const job = mockJobs.get(id);
  if (!job) throw new ApiError("Job not found. The server may have restarted since it started.");

  const base = {
    source_kind: job.sourceKind,
    concepts: null,
    results: null,
    error: null,
  } as JobState;

  if (job.forcedError) {
    return { ...base, stage: "error", error: job.forcedError };
  }

  if (job.renderStartedAt !== null) {
    const done = Date.now() - job.renderStartedAt >= RENDER_MS;
    if (!done) {
      return { ...base, stage: "cutting_clips", total_to_render: job.approved.length };
    }
    const results: Concept[] = job.approved.map((i) => {
      const trim = job.trims[i];
      return {
        ...SAMPLE_CONCEPTS[i],
        // Show the cut the artist actually asked for, not the LLM's original.
        ...(trim
          ? { start_timestamp: stamp(trim.start), end_timestamp: stamp(trim.end) }
          : {}),
        clip_url: null,
      };
    });
    return { ...base, stage: "done", results, total_to_render: job.approved.length };
  }

  const stage = stageAt(Date.now() - job.createdAt);
  if (stage === "awaiting_review") {
    return { ...base, stage: "awaiting_review", concepts: SAMPLE_CONCEPTS };
  }
  return { ...base, stage: stage as JobState["stage"] };
}

async function mockRenderJob(id: string, approved: number[], trims: Trims = {}): Promise<void> {
  const job = mockJobs.get(id);
  if (!job) throw new ApiError("Job not found. The server may have restarted since it started.");
  if (approved.length === 0) throw new ApiError("Pick at least one moment to render.");
  job.approved = approved;
  job.trims = trims;
  job.renderStartedAt = Date.now();
}

/* ----------------------------------------------------------- design jumps */

/**
 * Screens you can jump straight to with ?state=... in mock mode, so a screen
 * can be worked on without sitting through the flow that leads to it.
 */
export type MockSeed = "working" | "review" | "rendering" | "done" | "error";

export const MOCK_SEEDS: MockSeed[] = ["working", "review", "rendering", "done", "error"];

export function isMockSeed(value: string | null): value is MockSeed {
  return value !== null && (MOCK_SEEDS as string[]).includes(value);
}

/** Create a mock job already sitting in the given state. Returns its id. */
export function seedMockJob(seed: MockSeed): string | null {
  if (!MOCK) return null;

  const id = `mock-${seed}-${Math.random().toString(36).slice(2, 8)}`;
  const now = Date.now();
  const reviewReached = ANALYSIS_TIMELINE[ANALYSIS_TIMELINE.length - 1][0];

  const job: MockJob = {
    createdAt: now,
    renderStartedAt: null,
    approved: [],
    trims: {},
    sourceKind: "audio",
  };

  if (seed === "working") {
    job.createdAt = now - 2500; // mid-transcription
  } else if (seed === "review") {
    job.createdAt = now - reviewReached;
  } else if (seed === "rendering") {
    job.createdAt = now - reviewReached;
    job.renderStartedAt = now;
    job.approved = [0, 1, 2];
  } else if (seed === "done") {
    job.createdAt = now - reviewReached;
    job.renderStartedAt = now - RENDER_MS - 1;
    job.approved = [0, 1, 2];
  } else if (seed === "error") {
    job.forcedError =
      "ffmpeg failed while rendering clip 2: no audio stream found in the source file.";
  }

  mockJobs.set(id, job);
  return id;
}

/* ---------------------------------------------------------------- export */

export const createJob = MOCK ? mockCreateJob : realCreateJob;
export const getJob = MOCK ? mockGetJob : realGetJob;
export const renderJob = MOCK ? mockRenderJob : realRenderJob;
