"""
lyric_align.py

Aligns user-supplied (correct) lyric text to audio timing derived from
Whisper's word-level transcription. This exists because Whisper is
noticeably less reliable on Sheng, and to a lesser extent Swahili -
especially on sung, code-switched, or slang-heavy lyrics - so the WORDS
shown on screen come from the user, while the TIMING still comes from what
Whisper actually detected in the audio.

Core idea (word time-warping): assuming lyrics flow at a constant, even
pace (evenly spread across a line or the whole song) drifts audibly out of
sync, since real singing has pauses, held notes, and faster/slower
sections - and karaoke-style per-word highlighting makes any drift very
obvious. Instead, this uses Whisper's own DETECTED WORD TIMINGS as a
pacing curve: even where Whisper mis-transcribes WHAT was sung (common on
Sheng/Swahili), it's usually still roughly right about WHEN something is
being sung - detecting that a sound event occurred doesn't require
correctly recognizing the language. The user's correct words get resampled
onto that real timing curve instead of assumed to flow at a flat, constant
rate.
"""

import re

from lyric_lines import MAX_WORDS_PER_LINE

SECTION_MARKER_RE = re.compile(r"^\[.*\]$")


def clean_lyrics_for_alignment(raw_text: str) -> list[str]:
    """
    Split raw pasted/embedded lyrics into sung lines only - drops blank
    lines and bracketed section markers or stage directions (e.g.
    "[Chorus]", "[Rain tapping against glass...]"), which aren't sung and
    shouldn't be time-aligned or displayed as lyrics.
    """
    lines = []
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if SECTION_MARKER_RE.match(line):
            continue
        lines.append(line)
    return lines


def _line_to_words(line: str) -> list[str]:
    return line.split()


def _distribute_words_over_span(words: list[str], start: float, end: float) -> list[dict]:
    """Split [start, end] across `words` evenly by character length. Only
    used as a last-resort fallback when there's no Whisper word timing at
    all to warp onto."""
    if not words:
        return []
    weights = [max(len(w), 1) for w in words]
    total_weight = sum(weights)
    span = max(end - start, 0.01)

    result = []
    cursor = start
    for word, weight in zip(words, weights):
        dur = span * (weight / total_weight)
        result.append({"word": word, "start": cursor, "end": cursor + dur})
        cursor += dur
    return result


def _enforce_monotonic_words(words: list[dict], max_word_duration: float = 8.0) -> list[dict]:
    """
    Defensive hardening pass: guarantees word timings are non-overlapping
    and strictly increasing, and caps any single word's duration at a sane
    maximum.

    Some alignment methods stitch together multiple independently-computed
    pieces (e.g. hybrid_align's per-segment local DTW, where each segment's
    padded window can overlap its neighbors by design). Individually
    reasonable pieces can combine into a small overlap or, rarely, an
    oversized duration at a stitch point - which doesn't just look visually
    wrong, it can produce a malformed subtitle event that makes ffmpeg's
    renderer choke or crash outright rather than just render badly. This is
    cheap insurance applied uniformly, regardless of which alignment method
    produced the input.
    """
    if not words:
        return []
    hardened = []
    prev_end = 0.0
    for w in words:
        start = max(w["start"], prev_end)
        end = max(w["end"], start + 0.05)
        if end - start > max_word_duration:
            end = start + max_word_duration
        hardened.append({"word": w["word"], "start": start, "end": end})
        prev_end = end
    return hardened


def _chunk_words_into_lines(words: list[dict], max_words: int = MAX_WORDS_PER_LINE) -> list[dict]:
    """
    Break a long list of already-timed words into short display-friendly
    lines (max_words each), preserving each word's own timing.

    User-pasted or embedded lyrics are often written as full sentences
    (natural for reading, especially for narrative/story-driven lyrics) -
    not pre-broken into short karaoke-display lines. Without this step, a
    25-word sentence becomes ONE subtitle event; ffmpeg's renderer then
    auto-wraps that into a multi-line block filling the screen instead of
    a short line. This keeps display chunks the same size as the
    whisper-only path (lyric_lines.group_into_lines) for visual
    consistency between the two.

    Every alignment path funnels through here, so _enforce_monotonic_words
    is applied here too as a single choke point - guarantees sane,
    non-overlapping timing regardless of which alignment method (Whisper
    heuristic, whole-song forced alignment, or hybrid) produced the input.
    """
    words = _enforce_monotonic_words(words)
    if not words:
        return []
    chunks = []
    for i in range(0, len(words), max_words):
        chunk = words[i:i + max_words]
        chunks.append({"start": chunk[0]["start"], "end": chunk[-1]["end"], "words": chunk})
    return chunks


def _time_warp_words(
    user_words: list[str],
    whisper_words: list[dict],
    total_duration: float | None,
) -> list[dict]:
    """
    Map user words onto real audio time using Whisper's own detected word
    timings as a warp curve, instead of assuming a flat constant pace.

    Builds n+1 "checkpoint" times bounding Whisper's n detected words
    (checkpoint[k] = the time at which the k-th word-slot begins). Each
    user word gets placed at the equivalent FRACTIONAL position along that
    checkpoint curve (e.g. user word 10 of 40 total maps to roughly 25%
    through Whisper's detected timing curve, landing wherever that 25%
    point falls in real time - which naturally reflects pauses and pacing
    changes Whisper actually detected, not a flat guess).

    If total_duration is given, the very first and last checkpoints are
    stretched to 0 and total_duration - this covers likely intro/outro
    instrumental sections Whisper didn't detect any words in, while
    keeping Whisper's real internal pacing shape for everything between.
    """
    n = len(whisper_words)
    m = len(user_words)
    if n == 0 or m == 0:
        return []

    checkpoints = [whisper_words[0]["start"]]
    for w in whisper_words[1:]:
        checkpoints.append(w["start"])
    checkpoints.append(whisper_words[-1]["end"])

    if total_duration is not None:
        checkpoints[0] = 0.0
        checkpoints[-1] = total_duration

    def time_at(virtual_index: float) -> float:
        virtual_index = max(0.0, min(virtual_index, n))
        lo = int(virtual_index)
        hi = min(lo + 1, n)
        frac = virtual_index - lo
        return checkpoints[lo] + (checkpoints[hi] - checkpoints[lo]) * frac

    result = []
    for i, word in enumerate(user_words):
        v_start = (i / m) * n
        v_end = ((i + 1) / m) * n
        start = time_at(v_start)
        end = time_at(v_end)
        if end <= start:
            end = start + 0.05
        result.append({"word": word, "start": start, "end": end})
    return result


def align_lyrics_to_audio(user_lyrics_text: str | None, segments: list, total_duration: float | None = None) -> list[dict]:
    """
    Produce a list of "lines" (same shape as lyric_lines.group_into_lines
    output: {start, end, words: [{word, start, end}]}) using the user's
    correct lyric text for word identity, resampled onto Whisper's real
    detected word-timing curve (see _time_warp_words) instead of assumed
    constant pacing.

    total_duration: the real audio duration in seconds (from ffprobe).
    Strongly recommended when available - covers likely intro/outro gaps
    Whisper didn't detect words in.

    If user_lyrics_text is empty/None, falls back to Whisper's own
    transcription (via group_into_lines) - the original whisper-only
    behavior.
    """
    from lyric_lines import group_into_lines

    if not user_lyrics_text or not user_lyrics_text.strip():
        return group_into_lines(segments)

    user_lines_text = clean_lyrics_for_alignment(user_lyrics_text)
    if not user_lines_text:
        return group_into_lines(segments)

    user_words_flat: list[str] = []
    for line in user_lines_text:
        user_words_flat.extend(_line_to_words(line))
    if not user_words_flat:
        return group_into_lines(segments)

    whisper_words_flat: list[dict] = []
    for seg in segments:
        whisper_words_flat.extend(seg.get("words", []))

    if whisper_words_flat:
        word_timings = _time_warp_words(user_words_flat, whisper_words_flat, total_duration)
    elif total_duration is not None:
        # Degenerate case: Whisper detected no words at all anywhere -
        # nothing to warp onto, spread evenly across the real duration.
        word_timings = _distribute_words_over_span(user_words_flat, 0.0, total_duration)
    else:
        return []

    return _chunk_words_into_lines(word_timings)


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 3:
        print("Usage: python lyric_align.py <transcript.json> <lyrics.txt> [total_duration_seconds]")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        transcript = json.load(f)
    with open(sys.argv[2]) as f:
        lyrics_text = f.read()
    total_duration = float(sys.argv[3]) if len(sys.argv) > 3 else None

    aligned = align_lyrics_to_audio(lyrics_text, transcript["segments"], total_duration)
    print(f"Aligned into {len(aligned)} lines:\n")
    for line in aligned[:15]:
        text = " ".join(w["word"] for w in line["words"])
        print(f"  [{line['start']:.1f}s - {line['end']:.1f}s] {text}")
