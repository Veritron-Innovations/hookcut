"""
cover_art.py

Resolves the background image for a vertical video clip:
1. If the user provided an image explicitly, use that.
2. Otherwise, try to extract embedded cover art from the mp3's ID3 tags.
3. If neither exists, return None (caller falls back to a plain background).
"""

from pathlib import Path
from mutagen.id3 import ID3
from mutagen.mp3 import MP3


def extract_embedded_art(mp3_path: str, output_path: str) -> str | None:
    """
    Pull embedded cover art (APIC frame) out of an mp3's ID3 tags and save
    it as an image file.

    Returns output_path if art was found and saved, else None.
    """
    try:
        audio = MP3(mp3_path, ID3=ID3)
    except Exception:
        return None

    if audio.tags is None:
        return None

    for tag in audio.tags.values():
        if tag.FrameID == "APIC":
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(tag.data)
            return output_path

    return None


def resolve_cover_art(
    mp3_path: str,
    user_provided_path: str | None,
    extract_dir: str = "output",
) -> str | None:
    """
    Decide which image to use as the background, following the priority:
    user-provided image > embedded ID3 art > None.

    Returns a path to an image file, or None if no art is available at all.
    """
    if user_provided_path and Path(user_provided_path).exists():
        return user_provided_path

    stem = Path(mp3_path).stem
    extracted_path = f"{extract_dir}/{stem}_cover.jpg"
    extracted = extract_embedded_art(mp3_path, extracted_path)
    if extracted:
        return extracted

    return None


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python cover_art.py <mp3_path> [output_path]")
        sys.exit(1)

    mp3_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else "output/cover.jpg"

    result = extract_embedded_art(mp3_path, output_path)
    if result:
        print(f"Extracted cover art to {result}")
    else:
        print("No embedded cover art found in this mp3.")
