"""
lyric_alignment.py

Overlays user-provided (correct) lyrics onto Whisper's detected timing
structure, instead of trusting Whisper's own transcribed words. This
matters most for Sheng/Swahili content, where Whisper's word-level
guesses are much less reliable than its segment-level timing (where the
pauses/line breaks are).

Approach: match Whisper segments to user lyric lines by ORDER (assumes
the pasted lyrics are in the same sequence as the song, which they will
be). Within each matched segment, distribute that line's words evenly
across the segment's [start, end] window, weighted slightly by word
length so short and long words don't get identical durations.

This is a heuristic, not true forced alignment - it doesn't listen to the
audio to place each word precisely, it assumes Whisper's segment-level
pause detection is roughly right and slots the correct words into that
skeleton. Good enough for karaoke-style display; not frame-perfect.
"""

import re


def clean_lyric_lines(raw_text: str) -> list:
    """
    Split pasted lyrics into lines, dropping empty lines and bracketed
    annotations/section markers (e.g. "[Verse 1]", "[Rain tapping against
    glass...]") that Suno and similar tools embed alongside the actual
    sung lyrics - these aren't sung, so they shouldn't be timed.
    """
    lines = raw_text.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if re.fullmatch(r"\[.*\]", stripped):
            continue
        cleaned.append(stripped)
    return cleaned


def align_lyrics(whisper_segments: list, user_lyrics_text: str) -> dict:
    """
    Align user-provided lyric lines onto Whisper's segment timing.

    Returns:
        {
            "segments": [...],       # same shape as Whisper segments, ready
                                       # to feed into the existing render
                                       # pipeline (lyric_lines.py, render_video.py)
            "user_line_count": int,
            "whisper_segment_count": int,
            "matched_count": int,     # how many lines actually got aligned
        }

    If user_lyrics has more or fewer lines than Whisper detected segments,
    only the overlapping portion is aligned - the counts are returned so
    the caller can warn about a mismatch.
    """
    user_lines = clean_lyric_lines(user_lyrics_text)
    matched_count = min(len(user_lines), len(whisper_segments))

    aligned_segments = []
    for i in range(matched_count):
        seg = whisper_segments[i]
        line_text = user_lines[i]
        words_text = line_text.split()

        start, end = seg["start"], seg["end"]
        duration = max(end - start, 0.01)

        if not words_text:
            continue

        # weight each word's share of the line's duration by its length,
        # so short words don't linger as long as long words
        weights = [len(w) + 1 for w in words_text]
        total_weight = sum(weights)

        words = []
        cursor = start
        for w, wt in zip(words_text, weights):
            w_duration = duration * (wt / total_weight)
            words.append({"word": w, "start": cursor, "end": cursor + w_duration})
            cursor += w_duration

        aligned_segments.append({
            "start": start,
            "end": end,
            "text": line_text,
            "words": words,
        })

    return {
        "segments": aligned_segments,
        "user_line_count": len(user_lines),
        "whisper_segment_count": len(whisper_segments),
        "matched_count": matched_count,
    }


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 3:
        print("Usage: python lyric_alignment.py <transcript.json> <lyrics.txt>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        transcript = json.load(f)
    with open(sys.argv[2]) as f:
        lyrics_text = f.read()

    result = align_lyrics(transcript["segments"], lyrics_text)
    print(f"User lines: {result['user_line_count']}")
    print(f"Whisper segments: {result['whisper_segment_count']}")
    print(f"Matched: {result['matched_count']}")
    if result["user_line_count"] != result["whisper_segment_count"]:
        print("\nWARNING: line count mismatch - only the overlapping portion was aligned.")
    print()
    for seg in result["segments"][:10]:
        print(f"  [{seg['start']:.1f}-{seg['end']:.1f}] {seg['text']}")
