"""
cut.py

Cuts real clips out of the source audio/video at the timestamps chosen by
analyze.py.

- Video sources (podcasts): stream-copy cut (-c copy) for near-instant cuts.
- Audio-only sources (songs): no video exists to cut, so instead we render a
  new 9:16 vertical video per clip - album art background + optional
  karaoke-synced lyrics - via render_video.py.
"""

import subprocess
from pathlib import Path

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"}


def is_audio_only(path: str) -> bool:
    return Path(path).suffix.lower() in AUDIO_EXTENSIONS


def timestamp_to_seconds(ts: str) -> float:
    """Convert 'mm:ss' string to seconds."""
    parts = ts.split(":")
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    elif len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    raise ValueError(f"Unrecognized timestamp format: {ts}")


def cut_clip(
    input_path: str,
    start: str,
    end: str,
    output_path: str,
    reencode: bool = False,
) -> str:
    """
    Cut a clip from input_path between start and end timestamps.
    Used for video sources. For audio-only sources, use render_video.py
    via cut_all_concepts instead.

    Args:
        input_path: source audio/video file
        start: start timestamp, "mm:ss"
        end: end timestamp, "mm:ss"
        output_path: where to save the cut clip
        reencode: if True, re-encode for frame-accurate cuts (slower).
                  If False (default), use stream copy - fast but may snap
                  to the nearest keyframe.

    Returns:
        output_path on success
    """
    start_sec = timestamp_to_seconds(start)
    end_sec = timestamp_to_seconds(end)
    duration = end_sec - start_sec

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_sec),
        "-i", input_path,
        "-t", str(duration),
    ]

    if reencode:
        cmd += ["-c:v", "libx264", "-c:a", "aac"]
    else:
        cmd += ["-c", "copy"]

    cmd.append(output_path)

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")

    return output_path


def cut_audio_segment(input_path: str, start: str, end: str, output_path: str) -> str:
    """Extract just the audio segment (re-encoded, since audio-only cuts
    need accurate boundaries for the video render step)."""
    start_sec = timestamp_to_seconds(start)
    end_sec = timestamp_to_seconds(end)
    duration = end_sec - start_sec

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_sec),
        "-i", input_path,
        "-t", str(duration),
        "-map", "0:a:0",
        "-vn",
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio cut failed: {result.stderr}")

    return output_path


def cut_all_concepts(
    input_path: str,
    brief: dict,
    output_dir: str = "output",
    cover_path: str | None = None,
    segments: list | None = None,
    lyrics_enabled: bool = True,
) -> list:
    """
    Produce a final clip for every concept in a brief (as produced by
    analyze.py).

    For video sources: stream-copy cut of the original video.
    For audio-only sources: renders a 9:16 vertical video with album art
    background and optional karaoke-synced lyrics.

    Returns list of output file paths.
    """
    from render_video import make_vertical_clip

    audio_source = is_audio_only(input_path)
    output_paths = []

    for i, concept in enumerate(brief["concepts"]):
        safe_name = concept["angle_name"].lower().replace(" ", "_")[:40]
        start = concept["start_timestamp"]
        end = concept["end_timestamp"]

        if audio_source:
            audio_seg_path = f"{output_dir}/clip_{i}_{safe_name}_audio.m4a"
            cut_audio_segment(input_path, start, end, audio_seg_path)

            out_path = f"{output_dir}/clip_{i}_{safe_name}.mp4"
            make_vertical_clip(
                audio_clip_path=audio_seg_path,
                cover_path=cover_path,
                segments=segments or [],
                clip_start=timestamp_to_seconds(start),
                clip_end=timestamp_to_seconds(end),
                output_path=out_path,
                lyrics_enabled=lyrics_enabled,
            )
        else:
            out_path = f"{output_dir}/clip_{i}_{safe_name}.mp4"
            cut_clip(input_path, start, end, out_path)

        output_paths.append(out_path)
        print(f"Cut: {out_path} ({start} - {end})")

    return output_paths


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 3:
        print("Usage: python cut.py <input_media> <brief.json> [output_dir]")
        sys.exit(1)

    input_media = sys.argv[1]
    brief_path = sys.argv[2]
    output_dir = sys.argv[3] if len(sys.argv) > 3 else "output"

    with open(brief_path) as f:
        brief = json.load(f)

    paths = cut_all_concepts(input_media, brief, output_dir)
    print(f"\nCut {len(paths)} clips into {output_dir}/")
