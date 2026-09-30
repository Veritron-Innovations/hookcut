"""
tap_sync.py

Manual tap-to-sync: for sections where automated alignment (Whisper
heuristic, forced alignment, hybrid) can't reliably place lyric timing -
notably a fully Swahili/Sheng chorus - a human taps once per LINE while
listening, and that becomes ground truth for roughly where each line
starts. Word-level timing WITHIN each tapped line still needs to come
from somewhere: rather than assuming words flow at a flat constant pace
across the tap-to-tap span (which ignores instrumental pauses, held
notes, and pacing changes - exactly the kind of drift tap-sync exists to
fix), this warps the line's real words onto whatever pacing curve
Whisper itself detected within that span - same technique
lyric_align.py uses for the whole song, applied here per line. Whisper's
TRANSCRIPTION may be wrong for Sheng/Swahili, but its TIMING (when a
sound event happened) is usually still roughly right, which is all a
warp curve needs. Only falls back to a flat spread when Whisper
detected literally nothing in that specific span.

This module now shares its core word-timing primitives with
lyric_align.py via word_timing.py, rather than keeping its own diverged
copy (see word_timing.py's docstring).
"""

import re

from word_timing import distribute_words_over_span, time_warp_words

SECTION_MARKER_RE = re.compile(r"^\[.*\]$")

# Tapping along to a cue you HEAR has a built-in lag before you physically
# press the button - auditory reaction time research puts simple reaction
# to a sound at roughly 150-200ms for most people. Critically, this is a
# SYSTEMATIC bias, not random jitter: every tap lands consistently late
# relative to the real onset, in the same direction, by roughly the same
# amount. That means it's correctable with a fixed offset (the same
# "audio/input offset" calibration rhythm games apply), unlike the
# per-word jitter from Whisper's own timestamps, which has no fixed
# direction to correct for. This default is a reasonable starting point,
# not a personally-measured value for any specific user - pass
# tap_latency_s explicitly if a user's own lag is known to differ.
DEFAULT_TAP_LATENCY_S = 0.15


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


def _whisper_words_in_span(segments: list, start: float, end: float) -> list[dict]:
    """
    Every Whisper-detected word whose midpoint falls within [start, end] -
    used as the local pacing curve to warp a tapped line's real words
    onto. Midpoint (not just start) so a word straddling a span boundary
    lands with whichever span it mostly belongs to, rather than being
    excluded entirely on a technicality.
    """
    words = []
    for seg in segments:
        for w in seg.get("words", []):
            mid = (w["start"] + w["end"]) / 2
            if start <= mid <= end:
                words.append(w)
    return words


def build_lines_from_taps(
    lines_text: list[str],
    tap_timestamps: list[float],
    fallback_line_duration: float = 3.0,
    segments: list | None = None,
    tap_latency_s: float = DEFAULT_TAP_LATENCY_S,
) -> list[dict]:
    """
    Convert tapped line-start timestamps into the standard "lines" format
    ({start, end, words: [{word, start, end}]}) used throughout the
    rendering pipeline.

    lines_text and tap_timestamps must be the same length and in order -
    tap_timestamps[i] is when lines_text[i] starts. Each line's end is the
    next line's tapped start, except the last line, which gets
    fallback_line_duration (there's no next tap to bound it).

    tap_latency_s is subtracted from every tap before anything else runs -
    see DEFAULT_TAP_LATENCY_S above for why. Set to 0.0 to disable if a
    specific tapping setup (e.g. a visual metronome instead of audio-only)
    doesn't have this bias.

    segments, if provided (Whisper's raw transcription segments - same
    shape as elsewhere in this pipeline), lets word-level timing within
    each tapped line follow Whisper's own detected pacing in that span
    instead of a flat, constant-rate guess - see module docstring. Pass
    the job's segments whenever they're available; omit only if truly
    unavailable, in which case every line falls back to a flat spread.
    """
    if len(lines_text) != len(tap_timestamps):
        raise ValueError(
            f"lines_text ({len(lines_text)}) and tap_timestamps "
            f"({len(tap_timestamps)}) must be the same length"
        )
    if not lines_text:
        return []

    tap_timestamps = [max(0.0, t - tap_latency_s) for t in tap_timestamps]

    lines = []
    for i, (text, start) in enumerate(zip(lines_text, tap_timestamps)):
        end = tap_timestamps[i + 1] if i + 1 < len(tap_timestamps) else start + fallback_line_duration
        words = text.split()

        reference_words = _whisper_words_in_span(segments, start, end) if segments else []
        if reference_words:
            # start_bound=start (the tap) is trustworthy - that's exactly
            # what was just tapped. end_bound is deliberately left alone
            # (not forced to the span's end/next tap): unlike lyric_align's
            # whole-song case, where stretching to total_duration covers
            # a genuinely unknown intro/outro, here `end` is just the NEXT
            # line's tap - forcing the warp to stretch to it would smear
            # the last word across any trailing instrumental gap before
            # the next line starts, which is the exact bug this function
            # exists to fix. Whisper's own last detected word-end within
            # the span is better ground truth for where this line's
            # audio actually stops.
            word_timings = time_warp_words(words, reference_words, start_bound=start, end_bound=None)
            # Safety clamp, not a behavior change for the normal case: a
            # word can qualify for reference_words by MIDPOINT falling
            # within [start, end] while its raw END timestamp extends
            # slightly past `end` (the next tap). Cap it so it can't
            # bleed into the next line - this only ever fires on that
            # boundary-overshoot edge case, since in the ordinary case
            # the last word already ends well before `end` (that's the
            # whole point of the fix above).
            if word_timings and word_timings[-1]["end"] > end:
                word_timings[-1]["end"] = end
        else:
            # Whisper detected nothing at all in this span (plausible for
            # a heavily Sheng/Swahili stretch it mistranscribed as noise,
            # or a span tapped slightly wrong) - no real pacing signal to
            # warp onto, so fall back to a flat spread rather than fail.
            word_timings = distribute_words_over_span(words, start, end)

        lines.append({"start": start, "end": end, "words": word_timings})

    return lines


def build_lines_from_explicit_timestamps(
    blocks: list[dict],
    segments: list | None = None,
    audio_path: str | None = None,
    work_dir: str | None = None,
    lang: str = "en",
) -> list[dict]:
    """
    Convert user-placed caption blocks - each with an EXPLICIT start/end
    the user set directly (e.g. via a timeline editor: find the exact
    moment by listening, pause, insert a caption right there) - into the
    standard lines format. This is the non-linear counterpart to
    build_lines_from_taps: a tap implies "this line starts here, the next
    one starts at the next tap" (sequential, has to go in order); this
    instead trusts each block's start AND end as independently given, so
    fixing one mistranscribed word doesn't require re-doing everything
    around it - exactly the workflow gap this function exists to close.

    blocks: [{"text": str, "start": float, "end": float}, ...] in any
    order (sorted by start here).

    Word timing within each block: same fallback chain as
    build_lines_from_taps - real per-word forced alignment (DTW against
    audio_path, when given and espeak-ng is available) is most accurate;
    Whisper-checkpoint interpolation (when segments overlap the block) is
    the fallback; a flat character-weighted spread is the last resort
    when neither is available. Unlike build_lines_from_taps, there's no
    tap-latency correction here - these timestamps came from the user
    directly placing a caption at a moment they identified by ear and
    confirmed visually on a waveform/playhead, not from a reaction-time-
    limited tap along to playback, so that systematic-lag correction
    doesn't apply to this input method.
    """
    blocks = sorted(blocks, key=lambda b: b["start"])
    lines = []

    for b in blocks:
        words = b["text"].split()
        if not words:
            continue
        start, end = b["start"], b["end"]

        reference_words = _whisper_words_in_span(segments, start, end) if segments else []
        if reference_words:
            word_timings = time_warp_words(words, reference_words, start_bound=start, end_bound=end)
        else:
            word_timings = distribute_words_over_span(words, start, end)

        if audio_path and work_dir and len(word_timings) > 1:
            from forced_align import refine_words_against_audio
            word_timings = refine_words_against_audio(word_timings, audio_path, work_dir, lang=lang)

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
    print("Manual lines from taps (no segments - flat spread fallback):")
    for l in manual:
        text = " ".join(w["word"] for w in l["words"])
        print(f"  [{l['start']:.2f}s - {l['end']:.2f}s] {text}")

