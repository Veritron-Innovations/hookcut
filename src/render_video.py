"""
render_video.py

Turns an audio file + album art into a video with optional karaoke-style
synced lyrics burned in. Supports both vertical (9:16, short clips) and
landscape (16:9, full lyric videos) output.

Approach:
- Album art is centered on the canvas. A blurred, scaled-up copy of the
  same art fills the background behind it so there's no letterboxing.
- Lyrics (if enabled) are rendered as ASS subtitles with per-line timing
  pulled from Whisper's word-level timestamps, then burned in via ffmpeg's
  subtitles filter. Font/margin sizes scale off the canvas's tighter
  dimension, so a 1080x1920 vertical clip and a 1920x1080 landscape video
  (both with a 1080 tight side) render lyrics at matching visual size.
"""

import subprocess
from pathlib import Path
from PIL import Image, ImageFilter

# Defaults preserve the original vertical short-clip behavior.
CANVAS_W, CANVAS_H = 1080, 1920

# Reference tuning: these values were dialed in against a 1080px tight
# dimension (the vertical clip's width). Other canvas sizes scale from this.
_REF_TIGHT = 1080
_REF_FONT_ACTIVE = 68
_REF_FONT_PREVIEW = 48
_REF_MARGINV_ACTIVE = 300
_REF_MARGINV_PREVIEW = 190


def build_background(
    cover_path: str | None,
    output_path: str,
    width: int = CANVAS_W,
    height: int = CANVAS_H,
) -> str:
    """
    Compose a background image at the given canvas size: blurred/scaled
    cover art fills the frame, a sharp centered copy sits on top. Falls
    back to a plain dark solid if no cover art is available.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if cover_path is None:
        canvas = Image.new("RGB", (width, height), (18, 18, 22))
        canvas.save(output_path)
        return output_path

    art = Image.open(cover_path).convert("RGB")

    # Blurred fill layer - scale to cover the full canvas
    fill_ratio = max(width / art.width, height / art.height)
    fill_size = (int(art.width * fill_ratio), int(art.height * fill_ratio))
    fill = art.resize(fill_size).filter(ImageFilter.GaussianBlur(40))
    canvas = Image.new("RGB", (width, height))
    fx = (fill.width - width) // 2
    fy = (fill.height - height) // 2
    canvas.paste(fill, (-fx, -fy))

    # Sharp centered square art on top, sized to the canvas's tighter dimension
    tight = min(width, height)
    sharp_size = tight - 160
    sharp = art.resize((sharp_size, sharp_size))
    sx = (width - sharp_size) // 2
    sy = (height - sharp_size) // 2 - int(height * 0.08)  # slightly above center, leaves room for lyrics
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
    width: int = CANVAS_W,
    height: int = CANVAS_H,
    lines_override: list | None = None,
) -> str:
    """
    Build a karaoke-style .ass subtitle file: the current lyric line shows
    large and highlights word-by-word as it's sung (via ASS \\k karaoke
    tags), with the next line visible smaller/dimmer underneath as a
    preview - similar to Spotify/Apple Music lyric displays.

    Font and margin sizes scale off the canvas's tighter dimension, so this
    looks visually consistent whether it's rendering a vertical short clip
    or a landscape full lyric video.

    By default, lines are grouped from Whisper's own word-level timestamps
    (segments[i]["words"]). Pass lines_override (from lyric_align.py) to
    use user-corrected lyrics instead - useful for Sheng/Swahili content
    where Whisper's own transcription is less reliable.
    """
    from lyric_lines import group_into_lines

    tight = min(width, height)
    scale = tight / _REF_TIGHT
    font_active = round(_REF_FONT_ACTIVE * scale)
    font_preview = round(_REF_FONT_PREVIEW * scale)
    marginv_active = round(_REF_MARGINV_ACTIVE * scale)
    marginv_preview = round(_REF_MARGINV_PREVIEW * scale)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Active,Arial Black,{font_active},&H0000D7FF,&H00E8E8E8,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,4,2,2,60,60,{marginv_active},1
Style: Preview,Arial Black,{font_preview},&H00999999,&H00999999,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,2,2,60,60,{marginv_preview},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    all_lines = lines_override if lines_override is not None else group_into_lines(segments)

    if not all_lines:
        # No word-level timing available (e.g. an older transcript) -
        # fall back to plain per-segment lines, no karaoke/preview.
        events = []
        for seg in segments:
            if seg["end"] < clip_start or seg["start"] > clip_end:
                continue
            rel_start = max(seg["start"] - clip_start, 0)
            rel_end = min(seg["end"] - clip_start, clip_end - clip_start)
            if rel_end <= rel_start:
                continue
            text = seg["text"].strip().replace("\n", " ")
            events.append(
                f"Dialogue: 0,{seconds_to_ass_time(rel_start)},{seconds_to_ass_time(rel_end)},"
                f"Active,,0,0,0,,{text}"
            )
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(header + "\n".join(events), encoding="utf-8")
        return output_path

    clip_lines = [
        l for l in all_lines
        if l["end"] >= clip_start and l["start"] <= clip_end and l["words"]
    ]

    from lyric_lines import pair_current_next
    paired = pair_current_next(clip_lines)

    events = []
    for line in paired:
        rel_start = max(line["start"] - clip_start, 0)
        rel_end = min(line["end"] - clip_start, clip_end - clip_start)
        if rel_end <= rel_start:
            continue

        # Active line: word-by-word karaoke highlight
        karaoke_text = " ".join(
            f"{{\\k{max(1, round((w['end'] - w['start']) * 100))}}}{w['word']}"
            for w in line["words"]
        )
        events.append(
            f"Dialogue: 1,{seconds_to_ass_time(rel_start)},{seconds_to_ass_time(rel_end)},"
            f"Active,,0,0,0,,{karaoke_text}"
        )

        # Preview: the next line, shown dim/static for the same duration
        next_line = line.get("next_line")
        if next_line:
            preview_text = " ".join(w["word"] for w in next_line["words"])
            events.append(
                f"Dialogue: 0,{seconds_to_ass_time(rel_start)},{seconds_to_ass_time(rel_end)},"
                f"Preview,,0,0,0,,{preview_text}"
            )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(header + "\n".join(events), encoding="utf-8")
    return output_path


def get_audio_duration(audio_path: str) -> float:
    """Get the duration (in seconds) of an audio/video file via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        audio_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    return float(result.stdout.strip())


def render_clip(
    audio_clip_path: str,
    background_path: str,
    output_path: str,
    ass_path: str | None = None,
) -> str:
    """
    Combine a static background image + audio into a video, optionally
    burning in ASS subtitles for lyrics. Output length matches the audio
    (via -shortest), so this works for both a short trimmed clip and a
    full-length song.
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
        vf = f"subtitles='{escaped_ass}':charenc=UTF-8"
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


def get_video_dimensions(path: str) -> tuple[int, int]:
    """Get (width, height) of a video's first video stream via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    w_str, h_str = result.stdout.strip().split("x")
    return int(w_str), int(h_str)


def reformat_and_caption_video_clip(
    video_clip_path: str,
    segments: list,
    clip_start: float,
    clip_end: float,
    output_path: str,
    lyrics_text: str | None = None,
    total_duration: float | None = None,
    lyrics_enabled: bool = True,
    width: int = CANVAS_W,
    height: int = CANVAS_H,
    fit_mode: str = "letterbox",
    bar_color: str = "black",
    precomputed_lines: list | None = None,
) -> str:
    """
    Reformat an existing video clip to vertical (default 1080x1920), and
    optionally burn in karaoke-style lyric/caption subtitles in the SAME
    ffmpeg pass.

    Used for video sources (podcasts, music videos) - unlike
    make_vertical_clip (which builds a brand new video from a static cover
    image), this keeps the source video's own visuals but reframes them:
    short-form platforms expect 9:16 regardless of whether the original
    footage was landscape.

    fit_mode controls how that reframing happens:
    - "letterbox" (default): scales the WHOLE original frame down to fit
      inside the vertical canvas - nothing is cropped out, at the cost of
      solid-color bars filling the empty space above/below.
    - "crop": scales up and crops the sides off to fill the whole vertical
      frame - no bars, but anything not centered in the original shot gets
      cut off.

    precomputed_lines, if provided (e.g. manually tap-synced lines merged
    with auto-alignment via tap_sync.merge_manual_and_auto_lines), is used
    directly instead of computing alignment here.

    Necessarily re-encodes the video stream (both the reframe and any
    subtitle burn-in modify pixels); audio is copied through unchanged.
    """
    work_dir = str(Path(output_path).parent)
    stem = Path(output_path).stem

    if fit_mode == "crop":
        # "cover crop": scale so both dimensions are at least the target
        # size (preserving aspect ratio), then crop the excess from the
        # center.
        vf_parts = [
            f"scale={width}:{height}:force_original_aspect_ratio=increase",
            f"crop={width}:{height}",
        ]
    elif fit_mode == "letterbox":
        # "contain": scale so both dimensions fit WITHIN the target size
        # (preserving aspect ratio, nothing cropped), then pad the
        # leftover space with solid-color bars, centered.
        vf_parts = [
            f"scale={width}:{height}:force_original_aspect_ratio=decrease",
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={bar_color}",
        ]
    else:
        raise ValueError(f"Unknown fit_mode '{fit_mode}' - use 'letterbox' or 'crop'")

    ass_path = None
    if lyrics_enabled and (segments or precomputed_lines is not None):
        lines_override = precomputed_lines
        if lines_override is None and lyrics_text:
            from lyric_align import align_lyrics_to_audio
            lines_override = align_lyrics_to_audio(lyrics_text, segments, total_duration)

        # Subtitles are built at the TARGET (post-reframe) canvas size,
        # since the scale/pad or crop filter runs first in the chain
        # below - the subtitle overlay applies to the already-vertical
        # frame, not the source's original (possibly landscape) dimensions.
        ass_path = build_ass_subtitles(
            segments, clip_start, clip_end, f"{work_dir}/{stem}.ass", width, height,
            lines_override=lines_override,
        )
        escaped_ass = ass_path.replace("\\", "/").replace(":", "\\:")
        vf_parts.append(f"subtitles='{escaped_ass}':charenc=UTF-8")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", video_clip_path,
        "-vf", ",".join(vf_parts),
        "-c:v", "libx264",
        "-c:a", "copy",
        "-pix_fmt", "yuv420p",
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
    lyrics_text: str | None = None,
    total_duration: float | None = None,
    precomputed_lines: list | None = None,
) -> str:
    """
    Short-clip flow (9:16): build background from cover art, optionally
    build synced lyric subtitles for the clip's time range, then render.

    If lyrics_text is provided, it's aligned to real audio timing (see
    lyric_align.py) and used instead of Whisper's own transcription -
    useful for Sheng/Swahili content where Whisper is less reliable.
    total_duration should be the FULL SONG's duration (not just this
    clip's) - alignment runs across the whole song's lyrics/timing before
    this clip's range gets filtered out of it, and needs the real total
    duration to avoid badly compressing lines into whatever narrow span
    Whisper happened to detect words in.

    precomputed_lines, if provided (e.g. manually tap-synced lines merged
    with auto-alignment via tap_sync.merge_manual_and_auto_lines), is used
    directly instead of computing alignment here.
    """
    work_dir = str(Path(output_path).parent)
    stem = Path(output_path).stem

    bg_path = build_background(cover_path, f"{work_dir}/{stem}_bg.jpg", CANVAS_W, CANVAS_H)

    ass_path = None
    if lyrics_enabled and (segments or precomputed_lines is not None):
        lines_override = precomputed_lines
        if lines_override is None and lyrics_text:
            from lyric_align import align_lyrics_to_audio
            lines_override = align_lyrics_to_audio(lyrics_text, segments, total_duration)
        ass_path = build_ass_subtitles(
            segments, clip_start, clip_end, f"{work_dir}/{stem}.ass", CANVAS_W, CANVAS_H,
            lines_override=lines_override,
        )

    return render_clip(audio_clip_path, bg_path, output_path, ass_path)


def make_full_lyric_video(
    audio_path: str,
    cover_path: str | None,
    segments: list,
    output_path: str,
    width: int = 1920,
    height: int = 1080,
    lyrics_enabled: bool = True,
    lyrics_text: str | None = None,
    precomputed_lines: list | None = None,
) -> str:
    """
    Full-length lyric video flow (default 16:9 landscape): renders the
    ENTIRE song, not a picked moment - background from cover art, karaoke
    lyrics synced across the full duration.

    If lyrics_text is provided, it's aligned to real audio timing (see
    lyric_align.py) and used instead of Whisper's own transcription -
    useful for Sheng/Swahili content where Whisper is less reliable.

    precomputed_lines, if provided (e.g. from forced_align.py), is used
    directly instead of computing alignment here - lets a caller skip
    Whisper's transcription entirely when using real forced alignment,
    since segments aren't needed in that case.
    """
    work_dir = str(Path(output_path).parent)
    stem = Path(output_path).stem

    duration = get_audio_duration(audio_path)

    bg_path = build_background(cover_path, f"{work_dir}/{stem}_bg.jpg", width, height)

    ass_path = None
    if lyrics_enabled and (segments or precomputed_lines is not None):
        lines_override = precomputed_lines
        if lines_override is None and lyrics_text:
            from lyric_align import align_lyrics_to_audio
            lines_override = align_lyrics_to_audio(lyrics_text, segments, duration)
        ass_path = build_ass_subtitles(
            segments, 0.0, duration, f"{work_dir}/{stem}.ass", width, height,
            lines_override=lines_override,
        )

    return render_clip(audio_path, bg_path, output_path, ass_path)
