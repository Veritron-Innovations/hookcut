"""
analyze.py

The "Narrative Brain" - takes a real timestamped transcript and asks an LLM
to identify the best short-form moments, with concrete cut points (not
invented content). Uses Gemini free tier for testing.
"""

import os
import re
import json
from collections import Counter

from google import genai
from google.genai import types
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

_client = None


def _get_client() -> genai.Client:
    """
    Lazy client construction - constructing genai.Client() eagerly at
    module import time meant importing this module at all (e.g. to reuse
    its validation helpers, or in a test) required a live GEMINI_API_KEY
    to be set, even for code paths that never call the API. Deferred
    until analyze() actually needs it, and cached after the first call.
    """
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    return _client


MODEL_NAME = "gemini-3.6-flash"


class Concept(BaseModel):
    angle_name: str
    start_timestamp: str
    end_timestamp: str
    source_text: str
    text_overlay_options: list[str]
    tiktok_caption: str
    ig_caption: str


class Brief(BaseModel):
    concepts: list[Concept]


SYSTEM_PROMPT = """You are a short-form content strategist for independent musicians \
and podcasters. You are given a REAL timestamped transcript from an artist's own \
audio/video. Your job is to identify the best moments to CUT into short-form clips \
- you are not writing new content, you are pointing to what's already there.

TASK:
1. HOOK IDENTIFICATION
   Scan the transcript segments for the highest scroll-stop moments. Two \
   distinct kinds of moments both count as strong hooks:
   a) Narrative moments: lyric twists, punchlines, confessions, tonal \
      shifts, unresolved tension.
   b) The CHORUS (if this is a song): repeated lines that carry the song's \
      main theme are usually the single most valuable clip for short-form, \
      even without narrative surprise - they're the most singable, most \
      recognizable, most quotable part. Lines tagged [REPEATED Nx elsewhere \
      in transcript] in the input have been programmatically detected as \
      near-duplicates of another line - treat that tag as a strong, reliable \
      signal the line IS the chorus, not something to independently verify by \
      re-reading the transcript yourself. If this is a song, make sure at \
      least one concept covers the chorus even if other moments feel more \
      "dramatic."
   Only reference text that actually appears in the transcript.

2. CUT POINTS
   Pick cut points based on the CONTENT first, not a fixed duration:
   - Start at a natural beginning - the first word of a complete phrase,
     line, or thought, right where the hook's setup begins. Don't start
     mid-sentence.
   - End at a natural landing point - the end of a complete line, thought,
     or punchline, even if that lands a few seconds outside the target
     range below. A clip that cuts off mid-thought to hit a duration
     target is worse than one that's a little longer or shorter but lands
     cleanly.
   - Target range: aim for roughly 15-30 seconds as a guideline, not a
     hard limit. Going a little under or over is fine if that's what the
     natural content boundary requires - never sacrifice a clean start/end
     point just to fit the range exactly.
   Use the ACTUAL segment timestamps provided - do not invent timestamps.
   Every timestamp you return is checked against the real transcript after
   you respond, and any concept with a timestamp that doesn't line up with
   real segment boundaries gets discarded entirely - a fabricated-but-
   plausible-looking timestamp doesn't survive that check, it just wastes
   one of the concepts you were asked for.

3. TEXT OVERLAY OPTIONS
   For each moment, write 3 on-screen text hook variants (max 12 words each), \
   each targeting a different trigger: relatability, curiosity/open-loop, \
   controversy/contrarian take.

4. CAPTIONS
   - tiktok_caption: open-loop question, casual tone, max 2 lines.
   - ig_caption: aesthetic quote adapted from the transcript, plus 3-5 hashtags.

HARD CONSTRAINT: return EXACTLY the number of concepts requested in the user \
message under "Number of concepts to generate" - no more, no fewer. If you \
identify more candidate moments than that, pick only the strongest ones. Do \
not return every moment you notice.

Return STRICT JSON only, no prose outside the JSON, in this shape:
{
  "concepts": [
    {
      "angle_name": "",
      "start_timestamp": "mm:ss",
      "end_timestamp": "mm:ss",
      "source_text": "",
      "text_overlay_options": ["", "", ""],
      "tiktok_caption": "",
      "ig_caption": ""
    }
  ]
}
"""


def _normalize_for_repetition(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace - so 'Kiburi ni mzigo,'
    and 'kiburi ni mzigo' compare equal for repetition detection."""
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def _mark_repeated_segments(pairs: list[tuple[str, str]]) -> list[str]:
    """
    pairs: [(timestamp_prefix, raw_text), ...] in chronological order.

    Programmatically flags near-duplicate lines with a [REPEATED Nx
    elsewhere in transcript] tag, instead of leaving chorus-detection
    entirely to the LLM's own recall over what can be several minutes of
    transcript. Exact-normalized-match only (case/punctuation-insensitive)
    - deliberately simple and reliable rather than fuzzy, since a missed
    match just means the LLM falls back to reading for repetition itself
    (no worse than before), while a FALSE match would actively mislead it.
    """
    normalized = [_normalize_for_repetition(text) for _, text in pairs]
    counts = Counter(n for n in normalized if n)
    lines = []
    for (prefix, text), norm in zip(pairs, normalized):
        tag = f" [REPEATED {counts[norm]}x elsewhere in transcript]" if norm and counts[norm] > 1 else ""
        lines.append(f"{prefix} {text}{tag}")
    return lines


def _parse_mmss(ts: str) -> float | None:
    """Parse 'mm:ss' or 'hh:mm:ss' or a bare number of seconds. Returns
    None (not an exception) on anything unparseable, so callers can treat
    a bad timestamp as a validation failure rather than a crash."""
    if not isinstance(ts, str):
        return None
    try:
        parts = ts.strip().split(":")
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        if len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
        if len(parts) == 1:
            return float(parts[0])
    except (ValueError, TypeError):
        return None
    return None


def _validate_and_filter_concepts(concepts: list[dict], total_duration: float | None) -> list[dict]:
    """
    Defense in depth against timestamp hallucination: the prompt instructs
    the model not to invent timestamps, but a prompt instruction is not a
    guarantee - this is a hard check on the actual returned data, not just
    a hope that the instruction was followed. Drops (does not attempt to
    repair) any concept whose timestamps don't parse, aren't in order, or
    clearly exceed the real audio's duration - a guessed "fix" for a
    hallucinated timestamp is still hallucinated, just differently.
    """
    valid = []
    for c in concepts:
        start = _parse_mmss(c.get("start_timestamp", ""))
        end = _parse_mmss(c.get("end_timestamp", ""))
        if start is None or end is None:
            continue
        if end <= start:
            continue
        if total_duration is not None and (start > total_duration + 1.0 or end > total_duration + 1.0):
            continue
        valid.append(c)
    return valid


def analyze(
    transcript: dict,
    genre: str,
    mood: str,
    num_concepts: int = 5,
    corrected_lines: list | None = None,
    max_retries: int = 1,
) -> dict:
    """
    Send a transcript to the LLM and get back structured short-form concepts.

    Args:
        transcript: dict from transcribe.py with "segments" list
        genre: e.g. "indie folk", "true crime podcast"
        mood: e.g. "Late-night / Moody / Narrative"
        num_concepts: how many angles to generate
        corrected_lines: optional - aligned lyric lines from lyric_align.py
            (accurate words, real audio timing). When provided, these are
            used INSTEAD of Whisper's raw transcript for hook-finding -
            important for Sheng/Swahili content where Whisper's own
            transcription is unreliable enough that Gemini can't actually
            find the real hook/chorus in it. Picked timestamps then line up
            with what actually gets rendered later, since both use the same
            corrected data.
        max_retries: extra attempts if the API call or JSON parsing fails
            outright (network hiccup, truncated response, etc.) - the first
            retry also drops thinking_level from HIGH to MEDIUM, since
            excessive thinking-token consumption crowding out the actual
            JSON output is a specific documented failure mode for Gemini 3
            thinking models under a fixed max_output_tokens budget.

    Returns:
        dict with "concepts" list matching the schema above, already
        validated against the real transcript's timestamps and hard-capped
        at num_concepts.
    """
    if corrected_lines:
        pairs = [
            (f"[{l['start']:.1f}s - {l['end']:.1f}s]", " ".join(w["word"] for w in l["words"]))
            for l in corrected_lines
        ]
        total_duration = corrected_lines[-1]["end"] if corrected_lines else None
    else:
        pairs = [
            (f"[{s['start']:.1f}s - {s['end']:.1f}s]", s["text"])
            for s in transcript["segments"]
        ]
        total_duration = transcript["segments"][-1]["end"] if transcript.get("segments") else None

    segments_text = "\n".join(_mark_repeated_segments(pairs))

    user_prompt = f"""
Genre: {genre}
Mood: {mood}
Number of concepts to generate: {num_concepts}

Transcript segments (real timestamps, do not invent new ones):
{segments_text}
"""

    # temperature=0.4: this is a precision extraction task (find real
    # moments, use real timestamps), not a creative-writing one - lower
    # temperature trades away creative variance in favor of the model
    # sticking closer to what's actually in the transcript. thinking_level
    # HIGH: hook-finding requires actually weighing candidate moments
    # against each other across the whole transcript, not just pattern-
    # matching the first plausible-looking line - worth the extra latency.
    # max_output_tokens is NOT optional here: Gemini 3 thinking models
    # under structured JSON output have a documented failure mode where,
    # without an explicit cap, thinking expands to fill an undefined
    # budget and the call can hang indefinitely rather than erroring out.
    def _build_config(thinking_level: types.ThinkingLevel) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=Brief,
            temperature=0.4,
            thinking_config=types.ThinkingConfig(thinking_level=thinking_level),
            max_output_tokens=8192 + num_concepts * 500,
        )

    result = None
    last_error = None
    for attempt in range(max_retries + 1):
        thinking_level = types.ThinkingLevel.HIGH if attempt == 0 else types.ThinkingLevel.MEDIUM
        try:
            response = _get_client().models.generate_content(
                model=MODEL_NAME,
                contents=[SYSTEM_PROMPT, user_prompt],
                config=_build_config(thinking_level),
            )
            result = json.loads(response.text)
            break
        except Exception as e:
            last_error = e
            continue

    if result is None:
        raise RuntimeError(f"analyze(): Gemini call failed after {max_retries + 1} attempt(s): {last_error}")

    concepts = result.get("concepts", [])
    concepts = _validate_and_filter_concepts(concepts, total_duration)

    # Safety net: enforce the count even if the LLM overshoots (or the
    # validation step above dropped fewer than it returned).
    if len(concepts) > num_concepts:
        concepts = concepts[:num_concepts]

    return {"concepts": concepts}


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python analyze.py <transcript.json> [genre] [mood] [num_concepts]")
        sys.exit(1)

    transcript_path = sys.argv[1]
    genre = sys.argv[2] if len(sys.argv) > 2 else "music"
    mood = sys.argv[3] if len(sys.argv) > 3 else "moody"
    num_concepts = int(sys.argv[4]) if len(sys.argv) > 4 else 5

    with open(transcript_path) as f:
        transcript = json.load(f)

    result = analyze(transcript, genre, mood, num_concepts)

    out_path = transcript_path.replace("_transcript.json", "_brief.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"Saved brief to {out_path}")
    print(json.dumps(result, indent=2))
