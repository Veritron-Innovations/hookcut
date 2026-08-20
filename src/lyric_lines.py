"""
lyric_lines.py

Groups Whisper's word-level timestamps into short, screen-friendly lyric
lines (Whisper's own segments are often full sentences - too long to read
comfortably on a 9:16 clip). Also builds the "current + next line" pairing
used for the Spotify-style two-line display.
"""

MAX_WORDS_PER_LINE = 7
# A gap this long between two words usually means a natural phrase break
# (breath, pause, line change) even mid-segment.
PAUSE_GAP_SECONDS = 0.6


def flatten_words(segments: list) -> list:
    """Pull every word (with timing) out of all segments into one flat list,
    in chronological order."""
    words = []
    for seg in segments:
        words.extend(seg.get("words", []))
    return words


def group_into_lines(segments: list, max_words: int = MAX_WORDS_PER_LINE) -> list:
    """
    Convert word-level timestamps into short display lines.

    Returns a list of lines, each:
        {"start": float, "end": float, "words": [{"word", "start", "end"}, ...]}

    Breaks a new line when either:
    - the current line hits max_words, or
    - there's a pause longer than PAUSE_GAP_SECONDS between words (likely a
      natural phrase/line boundary), or
    - the underlying Whisper segment ends (sentence boundary)
    """
    lines = []
    current_words: list = []

    def flush():
        if current_words:
            lines.append({
                "start": current_words[0]["start"],
                "end": current_words[-1]["end"],
                "words": current_words.copy(),
            })
            current_words.clear()

    for seg in segments:
        seg_words = seg.get("words", [])
        for i, w in enumerate(seg_words):
            if current_words:
                gap = w["start"] - current_words[-1]["end"]
                if gap > PAUSE_GAP_SECONDS or len(current_words) >= max_words:
                    flush()
            current_words.append(w)
        # end of a Whisper segment is a natural line break too
        flush()

    return lines


def line_text(line: dict) -> str:
    return " ".join(w["word"] for w in line["words"])


def pair_current_next(lines: list) -> list:
    """
    Build the (current, next) pairing each line needs for the two-line
    display: while `line` is active, the following line shows dim below it
    as a preview.

    Returns a list matching `lines`, each augmented with "next_line" (or
    None for the last line).
    """
    paired = []
    for i, line in enumerate(lines):
        next_line = lines[i + 1] if i + 1 < len(lines) else None
        paired.append({**line, "next_line": next_line})
    return paired


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python lyric_lines.py <transcript.json>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        transcript = json.load(f)

    lines = group_into_lines(transcript["segments"])
    print(f"Grouped into {len(lines)} display lines:\n")
    for line in lines[:15]:
        print(f"  [{line['start']:.1f}s - {line['end']:.1f}s] {line_text(line)}")
