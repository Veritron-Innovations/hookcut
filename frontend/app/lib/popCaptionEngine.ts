/**
 * popCaptionEngine.ts
 *
 * Client-side port of src/pop_captions.py's core logic, so the timeline
 * editor can show a live preview of what the pop-caption export will
 * actually look like - without needing a server round-trip or ffmpeg
 * render for every edit. This mirrors (not wraps) the Python engine:
 * word grouping into "cards", energy-tier dynamics, and the theme
 * palettes are reimplemented here in TypeScript so they can run in the
 * browser against audio already decoded for the waveform.
 *
 * KNOWN DIVERGENCE FROM THE REAL EXPORT, stated plainly:
 * - Per-word timing within a line here uses a flat, even spread across
 *   that line's [start, end] - the same fallback the Python backend
 *   itself uses when it has no better signal. The real export can
 *   additionally run per-word forced alignment (espeak-ng + DTW) to
 *   refine individual word timing beyond that flat spread - this
 *   preview does NOT replicate that step (running real forced alignment
 *   in-browser isn't practical). This means the exact on-screen moment
 *   a specific word pops in can differ slightly between this preview and
 *   the final rendered video, particularly on lines with uneven natural
 *   pacing (see forced_align.py's refine_words_against_audio for what's
 *   not replicated here). The CARD grouping, energy tiers, and overall
 *   timing envelope match exactly; only sub-line word placement can
 *   drift.
 * - Text layout uses the browser's real canvas.measureText() rather
 *   than pop_captions.py's character-count width estimate - this is
 *   actually MORE accurate here than in the Python export (which can't
 *   assume a specific font is installed on the rendering machine), so
 *   layout may be very slightly tighter/looser here than in the final
 *   video, not less accurate.
 *
 * Keep the constants and logic below in sync with pop_captions.py by
 * hand if either changes - there is no shared source of truth between
 * the Python and TypeScript versions.
 */

export type Word = { word: string; start: number; end: number };
export type Card = Word[];
export type ThemeName = "default" | "comic";

// --- Reference tuning (mirrors pop_captions.py) -----------------------------
export const REF_TIGHT = 1080;
export const REF_FONT_BASE = 120;
export const MIN_DISPLAY_SECONDS = 0.15;
export const POP_MS = 90;
export const SETTLE_MS = 60;

const MIN_SOLO_MS = 220;
const MAX_CARD_WORDS = 3;
const MAX_CARD_SPAN_MS = 1400;
const PAUSE_GAP_S = 0.35;

export const GROUP_SIZE_SCALE: Record<number, number> = { 1: 1.0, 2: 0.88, 3: 0.78 };

// (upper energy bound, size multiplier, overshoot %, rotation jitter deg)
const TIER_DYNAMICS: [number, number, number, number][] = [
  [0.33, 0.82, 108, 0],
  [0.66, 1.0, 120, 2],
  [1.01, 1.3, 145, 5],
];

export function tierIndexAndDynamics(energy: number): { idx: number; sizeMult: number; overshoot: number; jitter: number } {
  for (let i = 0; i < TIER_DYNAMICS.length; i++) {
    const [maxE, sizeMult, overshoot, jitter] = TIER_DYNAMICS[i];
    if (energy <= maxE) return { idx: i, sizeMult, overshoot, jitter };
  }
  const last = TIER_DYNAMICS[TIER_DYNAMICS.length - 1];
  return { idx: TIER_DYNAMICS.length - 1, sizeMult: last[1], overshoot: last[2], jitter: last[3] };
}

export type Theme = {
  font: string;
  tierColours: [string, string, string]; // CSS colors for calm/mid/hot
  outlineWidth: number;
  hotShear: number;
  burst: boolean;
  burstColour?: string;
  burstOutline?: string;
};

export const THEMES: Record<ThemeName, Theme> = {
  default: {
    font: '"Arial Black", sans-serif',
    tierColours: ["#FFFFFF", "#FFFF00", "#FF6000"],
    outlineWidth: 6,
    hotShear: 0,
    burst: false,
  },
  comic: {
    font: "Impact, sans-serif",
    tierColours: ["#0074D9", "#ED1D24", "#FFDE00"],
    outlineWidth: 7,
    hotShear: -0.18,
    burst: true,
    burstColour: "#ED1D24",
    burstOutline: "#000000",
  },
};

/**
 * Same algorithm as pop_captions.py's _group_into_cards: a card grows
 * past one word only while the most recently added word was itself too
 * fast to read solo. See that function's docstring for why there is
 * deliberately no separate "minimum cumulative card duration" gate.
 */
export function groupIntoCards(words: Word[]): Card[] {
  const cards: Card[] = [];
  let i = 0;
  const n = words.length;
  while (i < n) {
    const card: Word[] = [words[i]];
    i++;
    while (i < n) {
      const prevWord = card[card.length - 1];
      const nextWord = words[i];
      const gap = nextWord.start - prevWord.end;
      const spanIfAddedMs = (nextWord.end - card[0].start) * 1000;

      if (gap > PAUSE_GAP_S) break;
      if (card.length >= MAX_CARD_WORDS) break;
      if (spanIfAddedMs > MAX_CARD_SPAN_MS) break;

      const prevWordDurMs = (prevWord.end - prevWord.start) * 1000;
      if (prevWordDurMs >= MIN_SOLO_MS) break;

      card.push(words[i]);
      i++;
    }
    cards.push(card);
  }
  return cards;
}

/**
 * Same non-overlap clamp as pop_captions.py's build_pop_captions_ass -
 * forces every card's display start to be no earlier than the previous
 * card's display end, regardless of what the underlying word timing
 * looks like (upstream alignment is not guaranteed to be perfectly
 * monotonic - see pop_captions.py for the real overlap case this fixes).
 * Returns [start, end] display windows, one per card, same length/order
 * as the input cards array.
 */
export function clampCardWindows(cards: Card[]): [number, number][] {
  const windows: [number, number][] = [];
  let prevEnd = 0.0;
  for (const card of cards) {
    let start = Math.max(card[0].start, prevEnd);
    let end = Math.max(card[card.length - 1].end, start + MIN_DISPLAY_SECONDS);
    windows.push([start, end]);
    prevEnd = end;
  }
  return windows;
}

/**
 * Short-time RMS energy envelope from already-decoded PCM data (e.g. the
 * same Float32Array already extracted for the waveform) - mirrors
 * pop_captions.py's extract_rms_envelope, minus the ffmpeg decode step
 * (the caller already has decoded audio).
 */
export function extractRmsEnvelope(channelData: Float32Array, sampleRate: number, hopLength = 512): { rms: Float32Array; times: Float32Array } {
  const numFrames = Math.max(1, Math.floor(channelData.length / hopLength));
  const rms = new Float32Array(numFrames);
  const times = new Float32Array(numFrames);
  for (let i = 0; i < numFrames; i++) {
    let sumSq = 0;
    const start = i * hopLength;
    const end = Math.min(start + hopLength, channelData.length);
    for (let j = start; j < end; j++) {
      const v = channelData[j];
      sumSq += v * v;
    }
    rms[i] = Math.sqrt(sumSq / Math.max(1, end - start));
    times[i] = start / sampleRate;
  }
  return { rms, times };
}

/** Mirrors pop_captions.py's score_words: per-word mean RMS, normalized
 * 0..1 via 5th-95th percentile clipping across the words passed in. */
export function scoreWords(words: Word[], rms: Float32Array, times: Float32Array): number[] {
  const raw = words.map((w) => {
    let sum = 0;
    let count = 0;
    const wEnd = Math.max(w.end, w.start + 0.01);
    for (let i = 0; i < times.length; i++) {
      if (times[i] >= w.start && times[i] <= wEnd) {
        sum += rms[i];
        count++;
      }
    }
    return count > 0 ? sum / count : 0;
  });
  if (raw.length === 0) return [];

  const sorted = [...raw].sort((a, b) => a - b);
  const percentile = (p: number) => {
    const idx = (p / 100) * (sorted.length - 1);
    const lo = Math.floor(idx);
    const hi = Math.ceil(idx);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
  };
  const lo = percentile(5);
  const hi = percentile(95);
  if (hi - lo < 1e-6) return raw.map(() => 0.5);
  return raw.map((v) => Math.max(0, Math.min(1, (v - lo) / (hi - lo))));
}

/**
 * Flat, even word-timing spread within [start, end] - the same fallback
 * pop_captions.py's caller chain uses when no better per-word signal is
 * available. Splitting caption-block text into per-word timing for the
 * live preview, since a timeline-editor block only has line-level
 * start/end, not per-word timing (see this file's top docstring for what
 * that means for preview accuracy).
 */
export function distributeWordsOverSpan(text: string, start: number, end: number): Word[] {
  const words = text.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [];
  const weights = words.map((w) => Math.max(w.length, 1));
  const totalWeight = weights.reduce((a, b) => a + b, 0);
  const span = Math.max(end - start, 0.01);
  let cursor = start;
  return words.map((word, i) => {
    const dur = span * (weights[i] / totalWeight);
    const w = { word, start: cursor, end: cursor + dur };
    cursor += dur;
    return w;
  });
}
