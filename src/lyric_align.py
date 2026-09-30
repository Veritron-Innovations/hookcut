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
from word_timing import distribute_words_over_span, time_warp_words

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

    NOTE: this blind, fixed-word-count version has a real flaw - see
    _chunk_respecting_real_lines below, which should be preferred
    whenever the original lyrics_text (with its real line breaks) is
    available. Kept here as the fallback for the rare case where word
    counts don't line up cleanly with the source text (see that
    function's docstring).
    """
    words = _enforce_monotonic_words(words)
    if not words:
        return []
    chunks = []
    for i in range(0, len(words), max_words):
        chunk = words[i:i + max_words]
        chunks.append({"start": chunk[0]["start"], "end": chunk[-1]["end"], "words": chunk})
    return chunks


# "I"/"I'm"/"I've" etc. are capitalized in English regardless of sentence
# position - unlike every other capitalized word, they're not a reliable
# signal that a new clause is starting. Without this exclusion, a comma
# before "I" (extremely common in first-person lyrics) gets mistaken for
# a real split point.
_ALWAYS_CAPITALIZED_NON_SIGNAL = {"i", "i'm", "i've", "i'd", "i'll", "i'ma"}
_MIN_SMART_SPLIT_SIDE_WORDS = 3


def _find_smart_split_index(words: list[dict]) -> int | None:
    """
    Given an over-long list of already-timed words from a SINGLE real
    lyric line, find the best word index to split it at for display -
    the same punctuation-aware heuristic already proven in the tap-sync
    UI (frontend/app/lib/tapPhraseSplit.ts): prefer a comma followed by a
    genuinely capitalized new-clause word (excluding bare "I" forms - see
    above), falling back to the comma nearest the midpoint. Returns None
    if this line has no usable comma to split on at all (the caller falls
    back to even word-count chunks in that case).
    """
    candidates_capital = []
    candidates_any = []
    for idx in range(len(words) - 1):
        if not words[idx]["word"].endswith(","):
            continue
        candidates_any.append(idx)
        next_word = words[idx + 1]["word"]
        bare = next_word.strip(".,!?\"'\u2019").lower()
        if bare not in _ALWAYS_CAPITALIZED_NON_SIGNAL and next_word[:1].isupper():
            candidates_capital.append(idx)

    mid = len(words) / 2

    def pick(candidates):
        if not candidates:
            return None
        best = min(candidates, key=lambda i: abs(i - mid))
        left_n, right_n = best + 1, len(words) - (best + 1)
        if left_n >= _MIN_SMART_SPLIT_SIDE_WORDS and right_n >= _MIN_SMART_SPLIT_SIDE_WORDS:
            return best + 1
        return None

    return pick(candidates_capital) if pick(candidates_capital) is not None else pick(candidates_any)


def _split_one_real_line(words: list[dict], max_words: int) -> list[dict]:
    """
    Split ONE real lyric line's words down to display-sized chunks. Never
    merges with any other real line by construction - the caller
    guarantees `words` only ever contains words from a single real line.
    """
    if not words:
        return []
    if len(words) <= max_words:
        return [{"start": words[0]["start"], "end": words[-1]["end"], "words": words}]

    split_idx = _find_smart_split_index(words)
    if split_idx is not None:
        return _split_one_real_line(words[:split_idx], max_words) + _split_one_real_line(words[split_idx:], max_words)

    chunks = []
    for i in range(0, len(words), max_words):
        chunk = words[i:i + max_words]
        chunks.append({"start": chunk[0]["start"], "end": chunk[-1]["end"], "words": chunk})
    return chunks


def _chunk_respecting_real_lines(lyrics_text: str, word_timings: list[dict], max_words: int = MAX_WORDS_PER_LINE) -> list[dict]:
    """
    Groups already-timed words into short display chunks the same way
    _chunk_words_into_lines does, but a chunk can NEVER span two
    different real lyric lines from the original pasted text - only ever
    split ONE over-long real line into shorter pieces.

    Without this, blind fixed-word-count chunking freely merges the end
    of one real line with the start of the next (e.g. "...I could feel
    it. Substance in the") - which has nothing to do with the song's
    actual phrasing or pauses, and is a direct, structural cause of
    visible sync drift even when the underlying word timing is accurate.
    The lyrics the user pasted already ARE the correct line structure;
    the display should never contradict it.

    Requires word_timings to have the SAME word count, in the SAME
    order, as flattening lyrics_text's real lines would produce - true
    by construction for both align_lyrics_to_audio and hybrid_align,
    since every user word gets exactly one timing entry, in order. Falls
    back to the old blind chunking if the counts ever don't line up
    (should not normally happen, but a mismatch means we can no longer
    trust which timing entry belongs to which real line, so respecting
    boundaries we're not sure of would be worse than not trying).
    """
    user_lines_text = clean_lyrics_for_alignment(lyrics_text)
    word_timings = _enforce_monotonic_words(word_timings)

    line_word_counts = [len(_line_to_words(line)) for line in user_lines_text]
    if sum(line_word_counts) != len(word_timings):
        chunks = []
        for i in range(0, len(word_timings), max_words):
            chunk = word_timings[i:i + max_words]
            chunks.append({"start": chunk[0]["start"], "end": chunk[-1]["end"], "words": chunk})
        return chunks

    all_chunks = []
    cursor = 0
    for count in line_word_counts:
        line_words = word_timings[cursor:cursor + count]
        cursor += count
        all_chunks.extend(_split_one_real_line(line_words, max_words))
    return all_chunks


def align_lyrics_to_audio(user_lyrics_text: str | None, segments: list, total_duration: float | None = None) -> list[dict]:
    """
    Produce a list of "lines" (same shape as lyric_lines.group_into_lines
    output: {start, end, words: [{word, start, end}]}) using the user's
    correct lyric text for word identity, resampled onto Whisper's real
    detected word-timing curve (see word_timing.time_warp_words) instead of assumed
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
        start_bound = 0.0 if total_duration is not None else None
        word_timings = time_warp_words(user_words_flat, whisper_words_flat, start_bound=start_bound, end_bound=total_duration)
    elif total_duration is not None:
        # Degenerate case: Whisper detected no words at all anywhere -
        # nothing to warp onto, spread evenly across the real duration.
        word_timings = distribute_words_over_span(user_words_flat, 0.0, total_duration)
    else:
        return []

    return _chunk_respecting_real_lines(user_lyrics_text, word_timings)


def align_lyrics_best_effort(
    lyrics_text: str,
    segments: list,
    audio_path: str,
    work_dir: str,
    language: str = "en",
    total_duration: float | None = None,
) -> list[dict]:
    """
    Best available lyric alignment: tries REAL forced alignment first
    (hybrid_align in forced_align.py - phoneme-level DTW against the
    actual audio, bounded by Whisper's own segment structure so it can't
    drift across the whole song), falling back to align_lyrics_to_audio
    above (the Whisper-native-word-timestamp warp) only if forced
    alignment isn't available or fails outright.

    Forced alignment is meaningfully more accurate - Whisper's own
    word-level timestamps are a known-imprecise heuristic (cross-attention
    based), which is exactly why professional captioning tools use real
    forced alignment instead. This is the whole reason forced_align.py
    exists. It requires espeak-ng installed, and DTW can occasionally fail
    or misalign badly on unusual audio (long instrumental-only stretches,
    heavily processed vocals) - this tries it and only falls back on an
    actual failure, never silently prefers the weaker method when the
    better one is available and working.

    language should be the ACTUAL language of the lyrics (e.g. Whisper's
    own detected transcript["language"], or a user-specified override) -
    passed straight through to espeak-ng's TTS voice selection. Passing
    the wrong language here (e.g. Swahili phonetics for English lyrics)
    makes forced alignment actively worse than the fallback, not just
    less accurate - the reference track would be mispronounced from the
    start.
    """
    from hybrid_align import hybrid_align
    from forced_align import check_espeak_available

    if check_espeak_available():
        try:
            word_timings = hybrid_align(lyrics_text, segments, audio_path, total_duration, work_dir, lang=language)
            if word_timings:
                return _chunk_respecting_real_lines(lyrics_text, word_timings)
        except Exception:
            pass  # fall through to the Whisper-native method below - never let a DTW/espeak failure break the job

    return align_lyrics_to_audio(lyrics_text, segments, total_duration)


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
