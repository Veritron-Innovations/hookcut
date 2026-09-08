"""
hybrid_align.py

Combines the cheap Whisper-checkpoint heuristic (lyric_align.py) with
targeted LOCAL forced alignment (forced_align.py) - using Whisper's word
timing everywhere it's working normally, and only falling back to real
audio-based DTW refinement in the SPECIFIC stretches where Whisper's word
detection collapsed (a large gap between consecutive detected words - a
strong signal it silently skipped content it couldn't parse, common on
heavily Sheng/Swahili passages).

Why this instead of pure global DTW (forced_align.py alone): comparing a
flat TTS voice against a full produced song via DTW over several minutes
doesn't work well in practice - the timbral gap between synthetic speech
and real, produced singing (with instrumentation mixed in) is too large
for reliable matching at that scale, as observed directly on a real track.
DTW over a SHORT (few-second) window, bounded on both sides by Whisper's
own reasonably-trustworthy pause/segment boundaries, is a much more
tractable problem - there's far less room for the alignment to drift
badly, even with an imperfect acoustic match.

Why this instead of the pure Whisper-checkpoint heuristic alone
(lyric_align.py): that approach assumes word-detection density stays
roughly constant throughout the song. Where it collapses (e.g. a fully
Swahili chorus Whisper can't parse), linear interpolation across that gap
doesn't reflect the section's real internal pacing - producing a
"frozen, then jumps" symptom.
"""

import math
from pathlib import Path

import librosa
import soundfile as sf

from lyric_align import (
    clean_lyrics_for_alignment,
    _chunk_words_into_lines,
    _distribute_words_over_span,
    _time_warp_words,
)
from forced_align import build_reference_track, align_reference_to_real_audio, check_espeak_available

DEFAULT_GAP_THRESHOLD = 2.5  # seconds - a gap this large between consecutive
                              # Whisper word detections is treated as a
                              # "coverage collapsed here" zone worth refining


def _find_large_gaps(whisper_words: list[dict], gap_threshold: float) -> list[dict]:
    """
    Find stretches where Whisper's word-level detection likely collapsed:
    gaps between consecutive detected words exceeding gap_threshold.
    Returns a list of {"start", "end", "before_idx", "after_idx"} dicts in
    chronological order - before_idx/after_idx bound the gap in
    whisper_words.
    """
    gaps = []
    for i in range(len(whisper_words) - 1):
        gap_start = whisper_words[i]["end"]
        gap_end = whisper_words[i + 1]["start"]
        if gap_end - gap_start > gap_threshold:
            gaps.append({"start": gap_start, "end": gap_end, "before_idx": i, "after_idx": i + 1})
    return gaps


def hybrid_align(
    lyrics_text: str,
    segments: list,
    audio_path: str,
    total_duration: float,
    work_dir: str,
    lang: str = "sw",
    gap_threshold: float = DEFAULT_GAP_THRESHOLD,
) -> list[dict]:
    """
    Full hybrid alignment: Whisper's own word timing everywhere it's
    reasonably dense, real LOCAL forced alignment (espeak + DTW) only in
    the specific stretches where Whisper's detection collapsed.

    Returns a flat list of {"word","start","end"} dicts across the WHOLE
    song, in order - pass through lyric_align._chunk_words_into_lines to
    get short display-friendly lines for rendering.
    """
    user_lines = clean_lyrics_for_alignment(lyrics_text)
    user_words: list[str] = []
    for line in user_lines:
        user_words.extend(line.split())
    if not user_words:
        return []

    whisper_words: list[dict] = []
    for seg in segments:
        whisper_words.extend(seg.get("words", []))

    if not whisper_words:
        # Nothing to anchor to at all - fall back to flat proportional.
        return _distribute_words_over_span(user_words, 0.0, total_duration)

    # Global pass: cheap Whisper-checkpoint interpolation for every word,
    # same as the existing heuristic. This is the baseline we refine on top of.
    global_timings = _time_warp_words(user_words, whisper_words, total_duration)

    gaps = _find_large_gaps(whisper_words, gap_threshold)
    if not gaps or not check_espeak_available():
        return global_timings

    n = len(whisper_words)
    m = len(user_words)

    Path(work_dir).mkdir(parents=True, exist_ok=True)
    refined_timings = list(global_timings)

    for gi, gap in enumerate(gaps):
        # Which user-word indices fall in this gap's virtual-index range?
        # (inverse of the v = (i/m)*n mapping _time_warp_words uses).
        # A 1-slot buffer on each side avoids under-selecting: the exact
        # slot boundaries only bound where the GAP itself sits in
        # Whisper's index space, but the compression it causes can spill
        # into neighboring user words too, since m and n rarely divide
        # evenly - better to over-include a word or two at the edges
        # (re-aligning an already-fine word is harmless) than to miss part
        # of the actual gap.
        buffer_slots = 1.0
        slot_lo = gap["before_idx"] - buffer_slots
        slot_hi = gap["after_idx"] + buffer_slots
        user_start_idx = max(0, int(slot_lo * m / n))
        user_end_idx = min(m, math.ceil(slot_hi * m / n))

        if user_end_idx <= user_start_idx:
            continue  # no user words actually fall in this gap

        gap_words = user_words[user_start_idx:user_end_idx]
        if not gap_words:
            continue

        try:
            # Anchor the slice to the REAL timestamps of the words Whisper
            # actually detected bounding this gap - not padding math, which
            # can truncate mid-word (the reference is synthesized as a
            # complete word; a truncated real-audio counterpart throws off
            # DTW's alignment right from the start, since it processes
            # sequentially and an early mismatch propagates forward).
            clip_start = whisper_words[gap["before_idx"]]["start"]
            clip_end = whisper_words[gap["after_idx"]]["end"]
            clip_duration = clip_end - clip_start
            if clip_duration <= 0.2:
                continue

            local_audio, sr = librosa.load(audio_path, sr=16000, offset=clip_start, duration=clip_duration)
            local_audio_path = f"{work_dir}/_gap_{gi}_real.wav"
            sf.write(local_audio_path, local_audio, sr)

            local_ref_dir = f"{work_dir}/_gap_{gi}_ref"
            ref_path, boundaries = build_reference_track(gap_words, local_ref_dir, lang)
            local_aligned = align_reference_to_real_audio(ref_path, local_audio_path, boundaries)

            # offset local (clip-relative) timings back into the full song's timeline
            for w in local_aligned:
                w["start"] += clip_start
                w["end"] += clip_start

            refined_timings[user_start_idx:user_end_idx] = local_aligned

        except Exception:
            # Local refinement failed for this one gap (e.g. an audio edge
            # case) - keep the global interpolation's result for these
            # words rather than crashing the whole alignment over one
            # problem section.
            continue

    return refined_timings


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 4:
        print("Usage: python hybrid_align.py <audio_path> <transcript.json> <lyrics.txt> [total_duration] [lang]")
        sys.exit(1)

    audio_path = sys.argv[1]
    with open(sys.argv[2]) as f:
        transcript = json.load(f)
    with open(sys.argv[3], encoding="utf-8") as f:
        lyrics_text = f.read()
    total_duration = float(sys.argv[4]) if len(sys.argv) > 4 else None
    lang = sys.argv[5] if len(sys.argv) > 5 else "sw"

    if total_duration is None:
        from render_video import get_audio_duration
        total_duration = get_audio_duration(audio_path)

    result = hybrid_align(lyrics_text, transcript["segments"], audio_path, total_duration, "output/hybrid_work", lang)
    lines = _chunk_words_into_lines(result)
    print(f"Aligned into {len(lines)} display lines:\n")
    for line in lines[:20]:
        text = " ".join(w["word"] for w in line["words"])
        print(f"  [{line['start']:.1f}s - {line['end']:.1f}s] {text}")
