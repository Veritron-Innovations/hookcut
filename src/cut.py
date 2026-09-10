"""
cut.py

Cuts real clips out of the source audio/video at the timestamps chosen by
analyze.py.

- Video sources (podcasts, music videos): stream-copy cut (-c copy) for a
  fast initial trim, then always center-cropped to vertical 9:16 (regardless
  of the source's original aspect ratio) via
  render_video.reformat_and_caption_video_clip - lyric/caption subtitles are
  burned in during that same pass if lyrics_enabled (this pass re-encodes
  either way, since cropping and subtitle burn-in both modify pixels - only
  the initial stream-copy trim stays fast).
- Audio-only sources (songs): no video exists to cut, so instead we render a
  new 9:16 vertical video per clip - album art background + optional
  karaoke-synced lyrics - via render_video.py.
"""

import subprocess
import re
from pathlib import Path

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"}

# Whitelist, not blacklist: this filename doesn't just need to survive the
# OS filesystem (Windows forbids < > : " / \ | ? *) - it also gets embedded
# directly into ffmpeg's own filter-string syntax (subtitles='path'), which
# breaks on a different set of characters entirely (an apostrophe in the
# name prematurely closes that quoted string and corrupts the path ffmpeg
# tries to open). Rather than chase individual unsafe characters across two
# unrelated parsers one at a time, only ever allow characters known to be
# safe in both: letters, digits, underscore, hyphen.
_SAFE_CHARS_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def safe_filename(name: str, max_len: int = 40) -> str:
    """
    Turn arbitrary text (e.g. an LLM-generated concept name) into a string
    safe to use in a filename AND inside an ffmpeg filter argument, on
    Windows, macOS, and Linux alike. Whitelists a small safe character set
    rather than blacklisting known-bad ones, since this name flows into
    ffmpeg filter strings (e.g. the subtitles filter) where the unsafe
    character set is different from - and stricter than - the OS
    filesystem's own rules.
    """
    cleaned = name.lower().replace(" ", "_")
    cleaned = _SAFE_CHARS_RE.sub("_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned[:max_len] or "clip"


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


def recut_one_concept(
    input_path: str,
    concept: dict,
    output_dir: str,
    cover_path: str | None,
    segments: list | None,
    precomputed_lines: list,
    safe_name: str,
) -> str:
    """
    Re-render a SINGLE clip with precomputed (manually patched) lyric
    lines - used by the tap-sync "fix a section" flow, where only one
    clip's timing needs correcting, not the whole batch. Mirrors the
    per-concept logic inside cut_all_concepts, but takes lines directly
    instead of computing alignment from lyrics_text.
    """
    from render_video import make_vertical_clip, reformat_and_caption_video_clip

    start = concept["start_timestamp"]
    end = concept["end_timestamp"]
    audio_source = is_audio_only(input_path)

    if audio_source:
        audio_seg_path = f"{output_dir}/clip_{safe_name}_audio.m4a"
        cut_audio_segment(input_path, start, end, audio_seg_path)

        out_path = f"{output_dir}/clip_{safe_name}.mp4"
        make_vertical_clip(
            audio_clip_path=audio_seg_path,
            cover_path=cover_path,
            segments=segments or [],
            clip_start=timestamp_to_seconds(start),
            clip_end=timestamp_to_seconds(end),
            output_path=out_path,
            lyrics_enabled=True,
            precomputed_lines=precomputed_lines,
        )
    else:
        raw_path = f"{output_dir}/clip_{safe_name}_raw.mp4"
        cut_clip(input_path, start, end, raw_path)

        out_path = f"{output_dir}/clip_{safe_name}.mp4"
        reformat_and_caption_video_clip(
            video_clip_path=raw_path,
            segments=segments or [],
            clip_start=timestamp_to_seconds(start),
            clip_end=timestamp_to_seconds(end),
            output_path=out_path,
            lyrics_enabled=True,
            precomputed_lines=precomputed_lines,
        )

    return out_path


def cut_all_concepts(
    input_path: str,
    brief: dict,
    output_dir: str = "output",
    cover_path: str | None = None,
    segments: list | None = None,
    lyrics_enabled: bool = True,
    lyrics_text: str | None = None,
    on_clip_done=None,
) -> list:
    """
    Produce a final clip for every concept in a brief (as produced by
    analyze.py).

    For video sources: stream-copy cut, then always center-cropped to
    vertical 9:16 (regardless of the source's original aspect ratio),
    with lyric/caption subtitles burned in during that same pass if
    lyrics_enabled.
    For audio-only sources: renders a 9:16 vertical video with album art
    background and optional karaoke-synced lyrics.

    lyrics_text, if provided, is user-corrected lyrics aligned to real
    audio timing instead of trusting Whisper's own transcription - see
    lyric_align.py.

    on_clip_done, if provided, is called as (index, total, concept,
    output_path) right after each individual clip finishes rendering - lets
    a caller (e.g. the web backend) surface progress/results incrementally
    instead of only after every clip is done.

    Returns list of output file paths.
    """
    from render_video import make_vertical_clip, reformat_and_caption_video_clip, get_audio_duration

    audio_source = is_audio_only(input_path)
    output_paths = []
    total = len(brief["concepts"])

    total_duration = None
    if lyrics_text:
        total_duration = get_audio_duration(input_path)

    for i, concept in enumerate(brief["concepts"]):
        safe_name = safe_filename(concept["angle_name"])
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
                lyrics_text=lyrics_text,
                total_duration=total_duration,
            )
        else:
            raw_path = f"{output_dir}/clip_{i}_{safe_name}_raw.mp4"
            cut_clip(input_path, start, end, raw_path)

            out_path = f"{output_dir}/clip_{i}_{safe_name}.mp4"
            reformat_and_caption_video_clip(
                video_clip_path=raw_path,
                segments=segments or [],
                clip_start=timestamp_to_seconds(start),
                clip_end=timestamp_to_seconds(end),
                output_path=out_path,
                lyrics_text=lyrics_text,
                total_duration=total_duration,
                lyrics_enabled=lyrics_enabled,
            )

        output_paths.append(out_path)
        print(f"Cut: {out_path} ({start} - {end})")

        if on_clip_done:
            on_clip_done(i, total, concept, out_path)

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
