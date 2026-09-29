"""
word_timing.py

Shared primitives for placing a list of words onto real audio timing.
Used by both lyric_align.py (the whole-song auto-alignment path) and
tap_sync.py (the manual per-line correction path) - previously each kept
its own copy of distribute_words_over_span, which had already drifted
into being byte-for-byte duplicated code. Extracted here instead of
consolidating in either direction, so neither module depends on the
other's internals.

Core idea (word time-warping): assuming lyrics flow at a constant, even
pace drifts audibly out of sync, since real singing has pauses, held
notes, and faster/slower sections. Instead, this uses Whisper's own
DETECTED WORD TIMINGS as a pacing curve: even where Whisper mis-
transcribes WHAT was sung (common on Sheng/Swahili), it's usually still
roughly right about WHEN something is being sung - detecting that a
sound event occurred doesn't require correctly recognizing the language.
The real (user-provided or tapped) words get resampled onto that real
timing curve instead of assumed to flow at a flat, constant rate.
"""


def distribute_words_over_span(words: list[str], start: float, end: float) -> list[dict]:
    """
    Split [start, end] across `words` evenly by character length. Only
    a last-resort fallback for when there's no real word timing at all
    to warp onto (Whisper detected literally nothing in this span) -
    every other case should prefer time_warp_words below.
    """
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


def time_warp_words(
    real_words: list[str],
    reference_words: list[dict],
    start_bound: float | None = None,
    end_bound: float | None = None,
) -> list[dict]:
    """
    Map `real_words` (the words that should actually be displayed - user-
    corrected lyrics, or a manually tapped line's text) onto real audio
    time using `reference_words`' own detected timings as a warp curve,
    instead of assuming a flat constant pace.

    reference_words is normally Whisper's own word-level output for
    whatever span is being aligned - its transcribed WORDS may be wrong
    (unreliable on Sheng/Swahili), but its detected WHENs are usually
    still roughly right, which is all this needs.

    Builds n+1 "checkpoint" times bounding reference_words' n detected
    words (checkpoint[k] = the time at which the k-th word-slot begins).
    Each real word gets placed at the equivalent FRACTIONAL position
    along that checkpoint curve (e.g. real word 10 of 40 total maps to
    roughly 25% through the reference timing curve, landing wherever
    that 25% point falls in real time - which naturally reflects pauses
    and pacing changes actually detected, not a flat guess).

    start_bound/end_bound, if given, override the first/last checkpoint -
    for lyric_align.py's whole-song case this covers likely intro/outro
    instrumental sections Whisper didn't detect any words in (pass
    start_bound=0.0, end_bound=total_duration); for tap_sync.py's
    per-line case this clamps the warp to the tapped line's own [start,
    end] span so it can't bleed into a neighbouring line or a silent gap
    beyond what was actually tapped. Either way, the real internal pacing
    shape between the bounds - the part reference_words actually knows
    something about - is preserved.
    """
    n = len(reference_words)
    m = len(real_words)
    if n == 0 or m == 0:
        return []

    checkpoints = [reference_words[0]["start"]]
    for w in reference_words[1:]:
        checkpoints.append(w["start"])
    checkpoints.append(reference_words[-1]["end"])

    if start_bound is not None:
        checkpoints[0] = start_bound
    if end_bound is not None:
        checkpoints[-1] = end_bound

    def time_at(virtual_index: float) -> float:
        virtual_index = max(0.0, min(virtual_index, n))
        lo = int(virtual_index)
        hi = min(lo + 1, n)
        frac = virtual_index - lo
        return checkpoints[lo] + (checkpoints[hi] - checkpoints[lo]) * frac

    result = []
    for i, word in enumerate(real_words):
        v_start = (i / m) * n
        v_end = ((i + 1) / m) * n
        start = time_at(v_start)
        end = time_at(v_end)
        if end <= start:
            end = start + 0.05
        result.append({"word": word, "start": start, "end": end})
    return result
