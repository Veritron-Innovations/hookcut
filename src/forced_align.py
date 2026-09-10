"""
forced_align.py

Real forced alignment: given known-correct lyrics text and the real audio,
figure out WHEN each word occurs - without guessing WHAT was said (unlike
Whisper-based transcription, which has to guess vocabulary and is
unreliable on Sheng/Swahili).

Approach (classic forced-alignment via DTW - the same underlying technique
tools like aeneas use internally, built here directly on espeak-ng +
librosa to avoid aeneas's notoriously fragile compiled-extension install):

1. Synthesize each word of the known lyrics as a short TTS clip via
   espeak-ng, concatenating them into one "reference" track. Because we
   generate this audio ourselves, we know EXACTLY where each word starts
   and ends within it.
2. Extract MFCC audio features from both the synthesized reference and
   the real song audio.
3. Use dynamic time warping (DTW) to find the best-matching alignment path
   between the two feature sequences - this stretches/compresses the
   reference timeline to match the real audio's actual pacing.
4. Map each known word's reference-timeline boundary through that DTW
   path to get its corresponding timestamp in the REAL audio.

KNOWN LIMITATION: this compares a flat, robotic TTS voice against sung,
produced audio (often with instrumentation mixed in). DTW match quality on
singing is inherently less certain than on clean spoken audio, which is
what this technique is usually built/tested for. Treat this as an
experimental improvement over the Whisper-timestamp heuristic in
lyric_align.py, not a guaranteed fix - validate it against real tracks.

Requires the `espeak-ng` command-line tool installed and on PATH.
"""

import subprocess
import os
from pathlib import Path

import numpy as np
import librosa
import soundfile as sf


def check_espeak_available() -> bool:
    try:
        subprocess.run(["espeak-ng", "--version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def build_reference_track(
    words: list[str],
    work_dir: str,
    lang: str = "sw",
    gap_seconds: float = 0.08,
) -> tuple[str, list[dict]]:
    """
    Synthesize `words` word-by-word via espeak-ng, concatenate into one
    reference WAV with small silence gaps between words, and return
    (reference_wav_path, word_boundaries) where word_boundaries is
    [{"word": str, "start": float, "end": float}, ...] in the
    REFERENCE track's own timeline (not yet aligned to real audio).
    """
    Path(work_dir).mkdir(parents=True, exist_ok=True)

    all_samples = []
    boundaries = []
    cursor = 0.0
    sample_rate = None

    for i, word in enumerate(words):
        clip_path = f"{work_dir}/_word_{i:04d}.wav"
        cmd = ["espeak-ng", "-v", lang, "-w", clip_path, word]
        subprocess.run(cmd, capture_output=True, timeout=10)

        samples, sr = sf.read(clip_path)
        if samples.ndim > 1:
            samples = samples.mean(axis=1)  # collapse to mono if needed
        if sample_rate is None:
            sample_rate = sr

        duration = len(samples) / sr
        boundaries.append({"word": word, "start": cursor, "end": cursor + duration})
        all_samples.append(samples)

        gap_samples = np.zeros(int(gap_seconds * sr))
        all_samples.append(gap_samples)
        cursor += duration + gap_seconds

        os.remove(clip_path)

    reference_audio = np.concatenate(all_samples) if all_samples else np.zeros(1)
    reference_path = f"{work_dir}/_reference_track.wav"
    sf.write(reference_path, reference_audio, sample_rate or 22050)

    return reference_path, boundaries


def _dtw_align_arrays(
    ref_y: np.ndarray,
    real_y: np.ndarray,
    boundaries: list[dict],
    sr: int = 16000,
    hop_length: int = 512,
) -> list[dict]:
    """
    Core DTW step, operating on already-loaded audio arrays (no file I/O) -
    shared by both the whole-song and per-segment (hybrid) alignment paths.
    """
    if len(ref_y) == 0 or len(real_y) == 0:
        return []

    ref_mfcc = librosa.feature.mfcc(y=ref_y, sr=sr, hop_length=hop_length, n_mfcc=13)
    real_mfcc = librosa.feature.mfcc(y=real_y, sr=sr, hop_length=hop_length, n_mfcc=13)

    _, wp = librosa.sequence.dtw(X=ref_mfcc, Y=real_mfcc, metric="cosine")
    wp = wp[::-1]  # librosa returns the path end-to-start; flip to start-to-end

    ref_frames = wp[:, 0]
    real_frames = wp[:, 1]

    def ref_time_to_real_time(ref_time: float) -> float:
        ref_frame = librosa.time_to_frames(ref_time, sr=sr, hop_length=hop_length)
        idx = np.searchsorted(ref_frames, ref_frame)
        idx = min(idx, len(real_frames) - 1)
        real_frame = real_frames[idx]
        return float(librosa.frames_to_time(real_frame, sr=sr, hop_length=hop_length))

    aligned = []
    for b in boundaries:
        real_start = ref_time_to_real_time(b["start"])
        real_end = ref_time_to_real_time(b["end"])
        if real_end <= real_start:
            real_end = real_start + 0.05
        aligned.append({"word": b["word"], "start": real_start, "end": real_end})

    return aligned


def align_reference_to_real_audio(
    reference_wav_path: str,
    real_audio_path: str,
    boundaries: list[dict],
    sr: int = 16000,
    hop_length: int = 2048,
) -> list[dict]:
    """
    Run DTW between the synthesized reference track and the real audio,
    then map each reference-timeline word boundary through the alignment
    path to get its timestamp in the REAL audio.

    hop_length controls time resolution vs memory: librosa's dtw allocates
    a full (ref_frames x real_frames) cost matrix regardless of any band
    constraint, so for a full song (minutes long) a fine hop_length (e.g.
    512, ~32ms/frame) can require gigabytes of memory and crash. 2048
    (~128ms/frame) keeps the matrix small enough to be safe on ordinary
    hardware.

    NOTE: whole-song DTW like this compares a flat TTS reference against
    the full real audio (including instrumentation) in one shot, which can
    drift badly on sung/produced content - see forced_align_hybrid() for a
    more robust approach that bounds this using Whisper's own segment
    structure.
    """
    ref_y, _ = librosa.load(reference_wav_path, sr=sr)
    real_y, _ = librosa.load(real_audio_path, sr=sr)
    return _dtw_align_arrays(ref_y, real_y, boundaries, sr, hop_length)


def forced_align(
    lyrics_text: str,
    audio_path: str,
    work_dir: str,
    lang: str = "sw",
) -> list[dict]:
    """
    Full forced-alignment pipeline: known lyrics text + real audio -> real
    word-level timestamps. Returns a flat list of {"word","start","end"}
    dicts - pass through lyric_align._chunk_words_into_lines to get short
    display-friendly lines for karaoke rendering.
    """
    from lyric_align import clean_lyrics_for_alignment

    lines = clean_lyrics_for_alignment(lyrics_text)
    words: list[str] = []
    for line in lines:
        words.extend(line.split())
    if not words:
        return []

    ref_path, boundaries = build_reference_track(words, work_dir, lang)
    return align_reference_to_real_audio(ref_path, audio_path, boundaries)


def hybrid_align(
    lyrics_text: str,
    segments: list,
    audio_path: str,
    work_dir: str,
    lang: str = "sw",
    pad_seconds: float = 0.5,
    sr: int = 16000,
) -> list[dict]:
    """
    Hybrid alignment: use Whisper's own segment/pause boundaries as a
    coarse skeleton, then run LOCAL forced alignment (TTS + DTW) within
    each small segment window - instead of one DTW pass across the whole
    song, which compares a flat TTS voice against the full mixed/produced
    track in one shot and can drift badly on sung content (see
    forced_align() above).

    Whisper's timing of WHEN something is sung is usually more reliable
    than its guess at WHAT is sung, even in Sheng/Swahili - detecting a
    sound event doesn't require understanding the language the way
    transcription does. This uses that timing as a skeleton, and only
    trusts DTW to work within a few seconds of audio at a time instead of
    minutes - much less room to drift onto the wrong part of the song, and
    the acoustic mismatch between TTS and singing matters less over a
    short window.

    Steps:
    1. Coarse bucketing: assign user words to Whisper segments proportional
       to each segment's DURATION (not its word count/density) - Whisper's
       word-detection rate varies a lot by section (sparse on hard content,
       denser on easy content), so bucketing by word count would misassign
       words exactly at the segments that need it least forgivingly.
       Segment start/end times are a more reliable signal than word
       content.
    2. Local refinement: for each segment, synthesize just its assigned
       words via espeak-ng and run DTW against only that segment's small,
       padded audio window.
    """
    from lyric_align import clean_lyrics_for_alignment, _distribute_words_over_span

    lines = clean_lyrics_for_alignment(lyrics_text)
    user_words: list[str] = []
    for line in lines:
        user_words.extend(line.split())
    if not user_words:
        return []

    whisper_words_flat = []
    for seg in segments:
        whisper_words_flat.extend(seg.get("words", []))

    if not whisper_words_flat or not segments:
        # No Whisper skeleton to bucket against at all - fall back to the
        # whole-song approach directly.
        return forced_align(lyrics_text, audio_path, work_dir, lang)

    # Bucket user words into segments by DURATION SHARE, not by Whisper's
    # own word count/density within each segment. Whisper's word-detection
    # rate varies a lot by section (sparse on hard Sheng/Swahili passages,
    # denser on easier ones) - bucketing by word count would misassign
    # words at segment boundaries exactly where it matters most (a segment
    # Whisper barely transcribed gets almost no words allocated, even if
    # it's just as long, in real time, as a segment Whisper transcribed
    # densely). Segment START/END TIMES are a more reliable signal than
    # Whisper's word content, so allocate each segment a share of the
    # total word list proportional to how LONG it lasted.
    seg_durations = [max(s["end"] - s["start"], 0.0) for s in segments]
    total_seg_duration = sum(seg_durations)

    total_words = len(user_words)
    if total_seg_duration > 0:
        cum_targets = [0]
        cum_duration = 0.0
        for dur in seg_durations:
            cum_duration += dur
            cum_targets.append(round((cum_duration / total_seg_duration) * total_words))
    else:
        # degenerate: no real duration info, split evenly
        cum_targets = [round(i * total_words / len(segments)) for i in range(len(segments) + 1)]

    buckets: list[list[str]] = [
        user_words[cum_targets[i]:cum_targets[i + 1]] for i in range(len(segments))
    ]

    # Step 2: local refinement, one small window at a time
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    full_audio_y, _ = librosa.load(audio_path, sr=sr)

    all_word_timings: list[dict] = []

    for i, (seg, bucket_words) in enumerate(zip(segments, buckets)):
        if not bucket_words:
            continue

        # Too few words to meaningfully DTW-align on their own - even
        # spread across the segment's own span is simpler and safer.
        if len(bucket_words) <= 2:
            all_word_timings.extend(
                _distribute_words_over_span(bucket_words, seg["start"], seg["end"])
            )
            continue

        window_start = max(0.0, seg["start"] - pad_seconds)
        window_end = seg["end"] + pad_seconds
        start_sample = int(window_start * sr)
        end_sample = min(int(window_end * sr), len(full_audio_y))
        local_audio = full_audio_y[start_sample:end_sample]

        if len(local_audio) < int(sr * 0.2):
            all_word_timings.extend(
                _distribute_words_over_span(bucket_words, seg["start"], seg["end"])
            )
            continue

        ref_path, boundaries = build_reference_track(
            bucket_words, f"{work_dir}/_seg{i:04d}_ref", lang
        )
        try:
            ref_y, _ = librosa.load(ref_path, sr=sr)
            local_aligned = _dtw_align_arrays(ref_y, local_audio, boundaries, sr=sr, hop_length=512)
            if not local_aligned:
                raise ValueError("empty DTW result")
        except Exception:
            all_word_timings.extend(
                _distribute_words_over_span(bucket_words, seg["start"], seg["end"])
            )
            continue

        for w in local_aligned:
            all_word_timings.append({
                "word": w["word"],
                "start": w["start"] + window_start,
                "end": w["end"] + window_start,
            })

    return all_word_timings


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python forced_align.py <audio_path> <lyrics.txt> [lang] [work_dir]")
        sys.exit(1)

    audio_path = sys.argv[1]
    lyrics_path = sys.argv[2]
    lang = sys.argv[3] if len(sys.argv) > 3 else "sw"
    work_dir = sys.argv[4] if len(sys.argv) > 4 else "output/forced_align_work"

    if not check_espeak_available():
        print("espeak-ng not found on PATH. Install it and try again.")
        sys.exit(1)

    with open(lyrics_path, encoding="utf-8") as f:
        lyrics_text = f.read()

    result = forced_align(lyrics_text, audio_path, work_dir, lang)
    print(f"Aligned {len(result)} words:\n")
    for w in result[:30]:
        print(f"  [{w['start']:.2f}s - {w['end']:.2f}s] {w['word']}")
