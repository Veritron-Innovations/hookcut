"""
embedded_lyrics.py

Pulls plain lyrics text out of an mp3's ID3 tags (USLT frames - "Unsynced
Lyrics/Text"), if present. Suno-generated tracks commonly embed full,
accurate lyrics here even though there's no timing data attached - useful
as a starting point for the paste-lyrics box, since it's likely more
accurate than a fresh Whisper transcription, especially for Sheng/Swahili
content Whisper struggles with.
"""

from pathlib import Path
from mutagen.id3 import ID3


def extract_embedded_lyrics(mp3_path: str) -> str | None:
    """
    Look for a USLT (unsynced lyrics) ID3 frame and return its text.
    Returns None if the file has no ID3 tags or no lyrics frame.
    """
    try:
        audio = ID3(mp3_path)
    except Exception:
        return None

    for key, frame in audio.items():
        if key.startswith("USLT"):
            text = getattr(frame, "text", None)
            if text:
                return text.strip()

    return None


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python embedded_lyrics.py <mp3_path>")
        sys.exit(1)

    result = extract_embedded_lyrics(sys.argv[1])
    if result:
        print(result)
    else:
        print("No embedded lyrics found in this file.")
