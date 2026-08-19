"""
strip_metadata.py

Utility to clean AI-generation metadata (e.g. Suno's comment/lyrics tags)
out of an mp3's ID3 tags. Useful before distributing tracks where you don't
want the generation tool's fingerprint left in the file metadata.

Note: this only touches ID3 tags (comment, lyrics, encoder fields etc.) -
it does not and cannot remove any audio watermarking a platform might embed
in the audio signal itself, if it uses one.
"""

from mutagen.id3 import ID3, COMM, USLT, TXXX


def inspect_tags(mp3_path: str) -> None:
    """Print all ID3 frames so you can see what's actually in the file."""
    audio = ID3(mp3_path)
    for key, frame in audio.items():
        preview = str(frame)[:80]
        print(f"{key}: {preview}")


def strip_ai_metadata(mp3_path: str, output_path: str | None = None) -> str:
    """
    Remove comment, lyrics, and free-text (TXXX) frames that commonly carry
    generation-tool references, while leaving title/artist/cover art intact.

    Returns the path written to (same as input if output_path is None,
    i.e. edits in place).
    """
    audio = ID3(mp3_path)

    # Remove COMM (comments), USLT (lyrics), TXXX (free text) frames -
    # these are where "made with suno", generation ids, etc. tend to live.
    for frame_class in (COMM, USLT, TXXX):
        keys_to_delete = [k for k in audio.keys() if k.startswith(frame_class.__name__)]
        for k in keys_to_delete:
            del audio[k]

    target = output_path or mp3_path
    if output_path and output_path != mp3_path:
        import shutil
        shutil.copy(mp3_path, output_path)
        audio.save(output_path)
    else:
        audio.save(mp3_path)

    return target


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python strip_metadata.py inspect <mp3_path>")
        print("  python strip_metadata.py strip <mp3_path> [output_path]")
        sys.exit(1)

    command = sys.argv[1]

    if command == "inspect":
        inspect_tags(sys.argv[2])
    elif command == "strip":
        mp3_path = sys.argv[2]
        output_path = sys.argv[3] if len(sys.argv) > 3 else None
        result = strip_ai_metadata(mp3_path, output_path)
        print(f"Stripped AI-related metadata. Saved to: {result}")
    else:
        print(f"Unknown command: {command}")
