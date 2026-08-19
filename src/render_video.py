"""
render_video.py

Turns an audio clip + album art into a 9:16 vertical video, with optional
karaoke-style synced lyrics burned in.

Approach:
- Album art is centered on a 1080x1920 canvas. A blurred, scaled-up copy of
  the same art fills the background behind it so there's no letterboxing.
- Lyrics (if enabled) are rendered as ASS subtitles with per-line timing
  pulled from the Whisper segments that fall inside this clip's time range,
  then burned in via ffmpeg's subtitles filter.
"""

import subprocess
from pathlib import Path
from PIL import Image, ImageFilter

CANVAS_W, CANVAS_H = 1080, 1920


def build_background(cover_path: str | None, output_path: str) -> str:
    """
    Compose a 1080x1920 background: blurred/scaled cover art fills the frame,
    a sharp centered copy sits on top. If no cover art is available, falls
    back to a plain dark gradient-ish solid.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if cover_path is None:
        canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), (18, 18, 22))
        canvas.save(output_path)
        return output_path

    art = Image.open(cover_path).convert("RGB")

    # Blurred fill layer - scale to cover the full canvas
    fill_ratio = max(CANVAS_W / art.width, CANVAS_H / art.height)
    fill_size = (int(art.width * fill_ratio), int(art.height * fill_ratio))
    fill = art.resize(fill_size).filter(ImageFilter.GaussianBlur(40))
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H))
    fx = (fill.width - CANVAS_W) // 2
    fy = (fill.height - CANVAS_H) // 2
    canvas.paste(fill, (-fx, -fy))

    # Sharp centered square art on top, sized to canvas width
    sharp_size = CANVAS_W - 120
    sharp = art.resize((sharp_size, sharp_size))
    sx = (CANVAS_W - sharp_size) // 2
    sy = (CANVAS_H - sharp_size) // 2 - 150  # slightly above center, leaves room for lyrics
    canvas.paste(sharp, (sx, sy))

    canvas.save(output_path)
    return output_path


def seconds_to_ass_time(seconds: float) -> str:
    """Convert seconds to ASS subtitle time format H:MM:SS.CC"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def build_ass_subtitles(
    segments: list,
    clip_start: float,
    clip_end: float,
    output_path: str,
) -> str:
    """
    Build an .ass subtitle file for karaoke-style lyrics, using only the
    Whisper segments that fall within [clip_start, clip_end], re-timed to
    start at 0 within the clip.
    """
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {CANVAS_W}
PlayResY: {CANVAS_H}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial Black,68,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,4,2,2,60,60,220,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines = []
    for seg in segments:
        if seg["end"] < clip_start or seg["start"] > clip_end:
            continue
        rel_start = max(seg["start"] - clip_start, 0)
        rel_end = min(seg["end"] - clip_start, clip_end - clip_start)
        if rel_end <= rel_start:
            continue
        text = seg["text"].strip().replace("\n", " ")
        lines.append(
            f"Dialogue: 0,{seconds_to_ass_time(rel_start)},{seconds_to_ass_time(rel_end)},"
            f"Default,,0,0,0,,{text}"
        )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(header + "\n".join(lines))
    return output_path


def render_clip(
    audio_clip_path: str,
    background_path: str,
    output_path: str,
    ass_path: str | None = None,
) -> str:
    """
    Combine a static background image + audio clip into a vertical video,
    optionally burning in ASS subtitles for lyrics.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", background_path,
        "-i", audio_clip_path,
    ]

    if ass_path:
        # escape path for ffmpeg filter syntax (Windows drive colons need escaping too)
        escaped_ass = ass_path.replace("\\", "/").replace(":", "\\:")
        vf = f"subtitles='{escaped_ass}'"
        cmd += ["-vf", vf]

    cmd += [
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg render failed: {result.stderr}")

    return output_path


def make_vertical_clip(
    audio_clip_path: str,
    cover_path: str | None,
    segments: list,
    clip_start: float,
    clip_end: float,
    output_path: str,
    lyrics_enabled: bool = True,
) -> str:
    """
    Full flow: build background from cover art, optionally build synced
    lyric subtitles, then render the final vertical video.
    """
    work_dir = str(Path(output_path).parent)
    stem = Path(output_path).stem

    bg_path = build_background(cover_path, f"{work_dir}/{stem}_bg.jpg")

    ass_path = None
    if lyrics_enabled and segments:
        ass_path = build_ass_subtitles(
            segments, clip_start, clip_end, f"{work_dir}/{stem}.ass"
        )

    return render_clip(audio_clip_path, bg_path, output_path, ass_path)
