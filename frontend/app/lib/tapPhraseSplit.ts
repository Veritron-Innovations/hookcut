/**
 * tapPhraseSplit.ts
 *
 * Turns raw pasted lyrics into short, individually-tappable phrases for
 * the manual tap-sync flow, instead of requiring the user to have
 * already reformatted them into one-phrase-per-line by hand.
 *
 * Real pasted lyrics (Suno output, lyric sites, etc.) are usually written
 * as prose - a whole verse as one line, sentences separated by periods,
 * not newlines. Tapping against that as-is means ONE tap has to cover an
 * entire verse, which then gets flatly time-spread across however long
 * that verse actually took to sing/rap - nowhere close to the real
 * pacing, and the exact thing tap-sync exists to avoid.
 *
 * Shared by both the main app (page.tsx) and the standalone tap-sync demo
 * page (tap-sync/page.tsx) - deliberately not duplicated between them.
 */

// Matches src/lyric_lines.py's MAX_WORDS_PER_LINE convention in spirit,
// but tuned higher - that constant is a DISPLAY cap (how much fits
// readably on screen), whereas this is a TAP-ergonomics cap (how long a
// real sung/rapped phrase runs before a natural pause). Tested against
// actual pasted verses: real phrases run 8-12 words; capping at the
// display value forced pause points that don't match how lines are
// actually delivered.
const MAX_TAP_PHRASE_WORDS = 12;
const MIN_TAP_SPLIT_SIDE_WORDS = 3;

function evenWordChunks(words: string[], maxWords: number): string[] {
  const numChunks = Math.ceil(words.length / maxWords);
  const chunkSize = Math.ceil(words.length / numChunks);
  const out: string[] = [];
  for (let i = 0; i < words.length; i += chunkSize) {
    out.push(words.slice(i, i + chunkSize).join(" "));
  }
  return out;
}

/**
 * Splits one already-sentence-scoped clause down to tap-sized phrases.
 * Tries the comma nearest the clause's midpoint first (real bar breaks
 * tend to fall roughly mid-sentence); only accepts that split if both
 * halves are substantial (MIN_TAP_SPLIT_SIDE_WORDS) - otherwise a lone
 * trailing comma (e.g. "...in this place,") would strip off a near-empty
 * orphan fragment instead of landing on a real pause. Falls back to
 * distributing words as evenly as possible across the minimum number of
 * chunks needed when no good comma split exists, rather than greedily
 * filling word-count chunks and dumping a small remainder into its own
 * fragment.
 */
function splitLongClause(text: string, maxWords: number): string[] {
  const words = text.split(/\s+/).filter(Boolean);
  if (words.length <= maxWords) return [text];

  const commaPositions = Array.from(text.matchAll(/,\s*/g)).map((m) => (m.index ?? 0) + m[0].length);
  if (commaPositions.length > 0) {
    const mid = text.length / 2;
    const best = commaPositions.reduce((a, b) => (Math.abs(b - mid) < Math.abs(a - mid) ? b : a));
    const left = text.slice(0, best).trim();
    const right = text.slice(best).trim();
    const leftWords = left.split(/\s+/).filter(Boolean).length;
    const rightWords = right.split(/\s+/).filter(Boolean).length;
    if (left && right && leftWords >= MIN_TAP_SPLIT_SIDE_WORDS && rightWords >= MIN_TAP_SPLIT_SIDE_WORDS) {
      return [...splitLongClause(left, maxWords), ...splitLongClause(right, maxWords)];
    }
  }

  return evenWordChunks(words, maxWords);
}

/**
 * This still respects any newlines the user DID add (never merges lines
 * back together), drops bracketed section/SFX markers (e.g. "[Verse 1 -
 * ...]", "[SFX: ...]") since those aren't sung, splits primarily at
 * sentence-ending punctuation (the clearest, least ambiguous pause
 * signal), and only reaches for a comma or a hard word-count cut when a
 * sentence is still too long to tap as one phrase - see splitLongClause.
 */
export function splitIntoTapPhrases(raw: string): string[] {
  const phrases: string[] = [];

  for (const rawLine of raw.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;
    if (/^\[.*\]$/.test(line)) continue; // section/SFX marker, not sung

    const sentences = line
      .split(/(?<=[.!?])\s+/)
      .map((s) => s.trim())
      .filter(Boolean);

    for (const sentence of sentences) {
      phrases.push(...splitLongClause(sentence, MAX_TAP_PHRASE_WORDS));
    }
  }

  return phrases;
}
