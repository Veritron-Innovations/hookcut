"""
transcribe.py

Wraps Whisper to turn an audio/video file into a timestamped transcript.
Works on video files too (Whisper reads the audio track directly via ffmpeg).
"""

import whisper
import json
from pathlib import Path


def transcribe(input_path: str, model_size: str = "base", word_timestamps: bool = True) -> dict:
    """
    Transcribe an audio/video file, optionally with word-level timestamps.

    Args:
        input_path: path to audio or video file (mp3, wav, mp4, mov, etc.)
        model_size: whisper model size - "tiny", "base", "small", "medium", "large"
                    "base" is a good speed/accuracy tradeoff for testing.
        word_timestamps: whether to compute per-word timing within each
            segment. This is a genuinely more expensive step on top of
            plain segment transcription (Whisper does extra cross-attention
            alignment work for it) - skip it (pass False) whenever nothing
            downstream needs word-level timing, e.g. hook/clip selection
            only reads each segment's TEXT, never its words. Leave True
            whenever captions/lyrics will be rendered.

    Returns:
        dict with "text" (full transcript) and "segments" (list of
        {start, end, text, words} timestamped chunks). Each segment's
        "words" is a list of {word, start, end} - needed for karaoke-style
        word-by-word lyric highlighting - and is an empty list when
        word_timestamps=False. Word-level timing is noisier than
        line-level timing, especially on sung (vs spoken) audio - expect
        close-but-not-frame-perfect accuracy (see lyric_align.py /
        forced_align.py for real forced-alignment options that do better).
    """
    model = whisper.load_model(model_size)
    result = model.transcribe(input_path, verbose=False, word_timestamps=word_timestamps)

    segments = []
    for seg in result["segments"]:
        words = [
            {
                "word": w["word"].strip(),
                "start": w["start"],
                "end": w["end"],
            }
            for w in seg.get("words", [])
        ]
        segments.append({
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"].strip(),
            "words": words,
        })

    return {
        "text": result["text"].strip(),
        "segments": segments,
        "language": result.get("language", "unknown"),
    }


def save_transcript(transcript: dict, output_path: str) -> None:
    """Save transcript dict to a JSON file."""
    Path(output_path).write_text(json.dumps(transcript, indent=2, ensure_ascii=False), encoding="utf-8")


def format_timestamp(seconds: float) -> str:
    """Convert seconds to mm:ss format."""
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python transcribe.py <input_file> [model_size]")
        sys.exit(1)

    input_file = sys.argv[1]
    model_size = sys.argv[2] if len(sys.argv) > 2 else "base"

    print(f"Transcribing {input_file} with '{model_size}' model...")
    transcript = transcribe(input_file, model_size)

    out_path = Path("output") / (Path(input_file).stem + "_transcript.json")
    save_transcript(transcript, str(out_path))
    print(f"Saved transcript to {out_path}")
    print(f"\nFirst 3 segments:")
    for seg in transcript["segments"][:3]:
        print(f"  [{format_timestamp(seg['start'])} - {format_timestamp(seg['end'])}] {seg['text']}")
