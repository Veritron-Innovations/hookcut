"""
tap_sync.py

Manual tap-to-sync: for sections where automated alignment (Whisper
heuristic, forced alignment, hybrid) can't reliably place lyric timing -
notably a fully Swahili/Sheng chorus - a human taps once per LINE while
listening, and that becomes ground truth for that stretch. Word-level
timing within each correctly-anchored line is still auto-distributed
(character-weighted spread), since that approximation is small and
unnoticeable once the line-level cadence itself is right - which is the
part that actually goes wrong in automated approaches.

This module is intentionally self-contained (doesn't import from
lyric_align.py) so it doesn't depend on that file's current state - see
the note in the project's process changes about file divergence risk.
If lyric_align.py is confirmed in sync later, the small amount of
duplicated logic here (line cleaning, word-distribution-within-a-span)
could be consolidated back to shared helpers.
"""

import re

SECTION_MARKER_RE = re.compile(r"^\[.*\]$")


def clean_lines_for_tapping(raw_text: str) -> list[str]:
    """
    Split raw lyrics text into sung lines only - drops blank lines and
    bracketed section markers/stage directions (e.g. "[Chorus]"), which
    aren't sung and shouldn't be tapped or displayed.
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


def _distribute_words_over_span(words: list[str], start: float, end: float) -> list[dict]:
    """Split [start, end] across `words`, weighting each word's duration by
    its character length (longer words get proportionally more time)."""
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


def build_lines_from_taps(
    lines_text: list[str],
    tap_timestamps: list[float],
    fallback_line_duration: float = 3.0,
) -> list[dict]:
    """
    Convert tapped line-start timestamps into the standard "lines" format
    ({start, end, words: [{word, start, end}]}) used throughout the
    rendering pipeline.

    lines_text and tap_timestamps must be the same length and in order -
    tap_timestamps[i] is when lines_text[i] starts. Each line's end is the
    next line's tapped start, except the last line, which gets
    fallback_line_duration (there's no next tap to bound it).
    """
    if len(lines_text) != len(tap_timestamps):
        raise ValueError(
            f"lines_text ({len(lines_text)}) and tap_timestamps "
            f"({len(tap_timestamps)}) must be the same length"
        )
    if not lines_text:
        return []

    lines = []
    for i, (text, start) in enumerate(zip(lines_text, tap_timestamps)):
        end = tap_timestamps[i + 1] if i + 1 < len(tap_timestamps) else start + fallback_line_duration
        words = text.split()
        word_timings = _distribute_words_over_span(words, start, end)
        lines.append({"start": start, "end": end, "words": word_timings})

    return lines


def merge_manual_and_auto_lines(
    auto_lines: list[dict],
    manual_lines: list[dict],
) -> list[dict]:
    """
    Merge manually-tapped lines into a full set of auto-generated lines,
    replacing whatever auto lines fall within the manual lines' time range
    with the manual ones - keeps automated timing everywhere else, patches
    in human-verified timing just for the tapped stretch.

    Returns a single time-ordered list combining both.
    """
    if not manual_lines:
        return auto_lines
    if not auto_lines:
        return manual_lines

    manual_start = manual_lines[0]["start"]
    manual_end = manual_lines[-1]["end"]

    before = [l for l in auto_lines if l["end"] <= manual_start]
    after = [l for l in auto_lines if l["start"] >= manual_end]

    return before + manual_lines + after


if __name__ == "__main__":
    lines_text = ["Kiburi ni mzigo", "Weka chini usimame", "Bure umepewa"]
    taps = [10.2, 13.5, 16.8]
    manual = build_lines_from_taps(lines_text, taps, fallback_line_duration=3.0)
    print("Manual lines from taps:")
    for l in manual:
        text = " ".join(w["word"] for w in l["words"]) 
        print(f"  [{l['start']:.2f}s - {l['end']:.2f}s] {text}")
