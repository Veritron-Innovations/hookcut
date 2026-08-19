"""
pipeline.py

Ties transcribe -> analyze -> cut into one command.

Usage:
    python src/pipeline.py --input samples/podcast.mp4 --genre "true crime" --mood "moody"
"""

import argparse
import json
from pathlib import Path

from transcribe import transcribe, save_transcript
from analyze import analyze
from cut import cut_all_concepts, is_audio_only
from cover_art import resolve_cover_art


def run_pipeline(
    input_path: str,
    genre: str,
    mood: str,
    num_concepts: int = 5,
    model_size: str = "base",
    output_dir: str = "output",
    cover_image: str | None = None,
    lyrics: bool = True,
):
    stem = Path(input_path).stem
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    print(f"[1/3] Transcribing {input_path}...")
    transcript = transcribe(input_path, model_size)
    transcript_path = f"{output_dir}/{stem}_transcript.json"
    save_transcript(transcript, transcript_path)
    print(f"      Saved: {transcript_path}")

    print(f"[2/3] Analyzing for short-form concepts...")
    brief = analyze(transcript, genre, mood, num_concepts)
    brief_path = f"{output_dir}/{stem}_brief.json"
    with open(brief_path, "w") as f:
        json.dump(brief, f, indent=2)
    print(f"      Saved: {brief_path}")
    print(f"      Found {len(brief['concepts'])} concepts")

    cover_path = None
    if is_audio_only(input_path):
        cover_path = resolve_cover_art(input_path, cover_image, output_dir)
        if cover_path:
            print(f"      Using cover art: {cover_path}")
        else:
            print(f"      No cover art found (no --cover-image, none embedded) - using plain background")

    print(f"[3/3] Cutting clips...")
    clip_paths = cut_all_concepts(
        input_path,
        brief,
        output_dir,
        cover_path=cover_path,
        segments=transcript["segments"],
        lyrics_enabled=lyrics,
    )
    print(f"      Cut {len(clip_paths)} clips")

    print(f"\nDone. Outputs in {output_dir}/")
    return {
        "transcript_path": transcript_path,
        "brief_path": brief_path,
        "clip_paths": clip_paths,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="hookcut pipeline")
    parser.add_argument("--input", required=True, help="Path to audio/video file")
    parser.add_argument("--genre", default="music", help="Genre, e.g. 'indie folk'")
    parser.add_argument("--mood", default="moody", help="Target mood")
    parser.add_argument("--num-concepts", type=int, default=5)
    parser.add_argument("--model-size", default="base", help="Whisper model size")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--cover-image", default=None, help="Path to a custom cover art image (overrides embedded mp3 art)")
    parser.add_argument("--no-lyrics", action="store_true", help="Disable karaoke-style lyric overlay for audio sources")

    args = parser.parse_args()

    run_pipeline(
        input_path=args.input,
        genre=args.genre,
        mood=args.mood,
        num_concepts=args.num_concepts,
        model_size=args.model_size,
        output_dir=args.output_dir,
        cover_image=args.cover_image,
        lyrics=not args.no_lyrics,
    )
