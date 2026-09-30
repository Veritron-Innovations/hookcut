"""
lyric_video.py

Generates a full-length lyric video for an entire song - not a short clip,
the whole track, with karaoke-style synced lyrics over album art.

Usage:
    python src/lyric_video.py --input samples/song.mp3 --aspect 16:9
    python src/lyric_video.py --input samples/song.mp3 --lyrics-file samples/song_lyrics.txt

    # Real forced alignment instead of the Whisper-timestamp heuristic.
    # Requires espeak-ng installed, and requires lyrics (pasted,
    # --lyrics-file, or embedded in the file).
    #
    # --alignment-mode hybrid (default): uses Whisper's segment/pause
    # boundaries as a coarse skeleton, then runs local DTW refinement
    # within each small segment window. Much more robust on sung/produced
    # audio than one DTW pass across the whole song - see forced_align.py.
    #
    # --alignment-mode whole: one DTW pass across the entire song. Simpler,
    # but can drift badly comparing a flat TTS voice against a full
    # produced track over several minutes. Kept for comparison.
    python src/lyric_video.py --input samples/song.mp3 --forced-alignment --lang sw
"""

import argparse
from pathlib import Path

from transcribe import transcribe, save_transcript
from cover_art import resolve_cover_art, extract_embedded_lyrics
from render_video import make_full_lyric_video, get_audio_duration

ASPECT_DIMENSIONS = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
}


def run_lyric_video(
    input_path: str,
    aspect: str = "16:9",
    cover_image: str | None = None,
    lyrics: bool = True,
    lyrics_text: str | None = None,
    model_size: str = "base",
    output_dir: str = "output",
    use_forced_alignment: bool = False,
    alignment_mode: str = "hybrid",
    lang: str = "sw",
):
    if aspect not in ASPECT_DIMENSIONS:
        raise ValueError(f"Unsupported aspect '{aspect}'. Choose from: {list(ASPECT_DIMENSIONS)}")
    width, height = ASPECT_DIMENSIONS[aspect]

    stem = Path(input_path).stem
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    print(f"[1/3] Resolving cover art and lyrics...")
    cover_path = resolve_cover_art(input_path, cover_image, output_dir)
    if cover_path:
        print(f"      Using cover art: {cover_path}")
    else:
        print(f"      No cover art found - using plain background")

    if lyrics_text is None:
        embedded = extract_embedded_lyrics(input_path)
        if embedded:
            print(f"      Found embedded lyrics in file metadata - using them for on-screen text")
            lyrics_text = embedded

    segments = []
    precomputed_lines = None

    if use_forced_alignment:
        if not lyrics_text:
            raise ValueError("--forced-alignment requires lyrics (paste some, use --lyrics-file, or a file with embedded lyrics)")

        from forced_align import check_espeak_available
        if not check_espeak_available():
            raise RuntimeError(
                "espeak-ng not found on PATH. Install it (e.g. from "
                "https://github.com/espeak-ng/espeak-ng/releases) and try again."
            )

        align_work_dir = f"{output_dir}/{stem}_align_work"

        if alignment_mode == "whole":
            print(f"[2/3] Running forced alignment (whole-song DTW, lang={lang})...")
            from forced_align import forced_align
            word_timings = forced_align(lyrics_text, input_path, align_work_dir, lang)
        else:
            print(f"[2/3] Transcribing for segment structure...")
            transcript = transcribe(input_path, model_size)
            save_transcript(transcript, f"{output_dir}/{stem}_transcript.json")

            print(f"      Running hybrid alignment (Whisper structure + local DTW, lang={lang})...")
            from hybrid_align import hybrid_align
            total_duration = get_audio_duration(input_path)
            word_timings = hybrid_align(lyrics_text, transcript["segments"], input_path, total_duration, align_work_dir, lang)

        from lyric_align import _chunk_words_into_lines
        precomputed_lines = _chunk_words_into_lines(word_timings)
        print(f"      Aligned {len(word_timings)} words")
    else:
        print(f"[2/3] Transcribing {input_path} (this covers the full song, can take a while)...")
        transcript = transcribe(input_path, model_size)
        transcript_path = f"{output_dir}/{stem}_transcript.json"
        save_transcript(transcript, transcript_path)
        print(f"      Saved: {transcript_path}")
        segments = transcript["segments"]

    print(f"[3/3] Rendering full lyric video ({aspect})...")
    output_path = f"{output_dir}/{stem}_lyric_video.mp4"
    make_full_lyric_video(
        audio_path=input_path,
        cover_path=cover_path,
        segments=segments,
        output_path=output_path,
        width=width,
        height=height,
        lyrics_enabled=lyrics,
        lyrics_text=lyrics_text,
        precomputed_lines=precomputed_lines,
    )
    print(f"      Saved: {output_path}")

    return output_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a full-length lyric video")
    parser.add_argument("--input", required=True, help="Path to the song (audio or video)")
    parser.add_argument("--aspect", default="16:9", choices=list(ASPECT_DIMENSIONS.keys()))
    parser.add_argument("--cover-image", default=None, help="Custom cover art (overrides embedded art)")
    parser.add_argument("--no-lyrics", action="store_true", help="Disable karaoke lyric overlay")
    parser.add_argument("--lyrics-file", default=None, help="Path to a text file with correct lyrics (overrides embedded/Whisper transcription)")
    parser.add_argument("--forced-alignment", action="store_true", help="Use real forced alignment instead of the Whisper-timestamp heuristic. Requires lyrics and espeak-ng installed.")
    parser.add_argument("--alignment-mode", default="hybrid", choices=["hybrid", "whole"], help="hybrid (default): Whisper structure + local DTW, more robust. whole: one DTW pass across the whole song, kept for comparison.")
    parser.add_argument("--lang", default="sw", help="espeak-ng language code for forced alignment (default: sw for Swahili)")
    parser.add_argument("--model-size", default="base")
    parser.add_argument("--output-dir", default="output")

    args = parser.parse_args()

    lyrics_text = None
    if args.lyrics_file:
        lyrics_text = Path(args.lyrics_file).read_text(encoding="utf-8")

    run_lyric_video(
        input_path=args.input,
        aspect=args.aspect,
        cover_image=args.cover_image,
        lyrics=not args.no_lyrics,
        lyrics_text=lyrics_text,
        model_size=args.model_size,
        output_dir=args.output_dir,
        use_forced_alignment=args.forced_alignment,
        alignment_mode=args.alignment_mode,
        lang=args.lang,
    )
