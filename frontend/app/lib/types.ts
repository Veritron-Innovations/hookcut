/**
 * Shared types for the hookcut frontend.
 *
 * These mirror what the backend returns from GET /api/jobs/{id}. If the API
 * changes, change it here first - the mock layer in api.ts is typed against
 * these too, so a mismatch shows up at compile time rather than at runtime.
 */

export type SourceKind = "audio" | "video" | null;

export type Concept = {
  index: number;
  angle_name: string;
  start_timestamp: string;
  end_timestamp: string;
  source_text: string;
  text_overlay_options: string[];
  tiktok_caption: string;
  ig_caption: string;
  /** Only present once the clip has been rendered. */
  clip_url?: string | null;
};

export type JobState = {
  stage: Stage;
  source_kind: SourceKind;
  concepts: Concept[] | null;
  results: Concept[] | null;
  error: string | null;
  total_to_render?: number | null;
};

export type Stage =
  | "queued"
  | "transcribing"
  | "analyzing"
  | "resolving_cover_art"
  | "awaiting_review"
  | "cutting_clips"
  | "done"
  | "error";

export const STAGE_LABELS: Record<string, string> = {
  queued: "Getting your file ready",
  transcribing: "Transcribing the audio",
  analyzing: "Reading the transcript for the strongest moments",
  resolving_cover_art: "Finding your cover art",
  cutting_clips: "Rendering your clips",
};

/** Stages where the server is still working and we should keep polling. */
export const WORKING_STAGES = new Set(Object.keys(STAGE_LABELS));

export function isWorking(stage: string | undefined): boolean {
  return stage !== undefined && WORKING_STAGES.has(stage);
}
