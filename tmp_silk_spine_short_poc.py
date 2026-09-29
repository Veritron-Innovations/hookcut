import subprocess
import sys
from pathlib import Path

root = Path(r"C:\Users\chazc\hookcut")
sys.path.insert(0, str(root / "src"))

from cover_art import resolve_cover_art, extract_embedded_lyrics
from render_video import make_vertical_clip, get_audio_duration
from transcribe import transcribe

src = root / "samples" / "Silk & Spine.mp3"
out_dir = root / "output"
out_dir.mkdir(parents=True, exist_ok=True)
clip_mp3 = out_dir / "Silk_Spine_30s_poc.mp3"
final_mp4 = out_dir / "Silk_Spine_comic_poc.mp4"

if not clip_mp3.exists():
    cmd = [
        "ffmpeg", "-y", "-ss", "0", "-t", "30",
        "-i", str(src),
        "-acodec", "libmp3lame",
        str(clip_mp3),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)

cover_path = resolve_cover_art(str(src), None, str(out_dir))
lyrics_text = extract_embedded_lyrics(str(src))
transcript = transcribe(str(clip_mp3), "tiny")
duration = get_audio_duration(str(clip_mp3))

make_vertical_clip(
    audio_clip_path=str(clip_mp3),
    cover_path=cover_path,
    segments=transcript["segments"],
    clip_start=0.0,
    clip_end=duration,
    output_path=str(final_mp4),
    lyrics_enabled=True,
    lyrics_text=lyrics_text,
    total_duration=duration,
    caption_style="pop_word",
    caption_theme="comic",
)

print(f"COVER_PATH={cover_path}")
print(f"HAS_LYRICS={bool(lyrics_text)}")
print(f"CLIP_PATH={clip_mp3}")
print(f"OUTPUT_PATH={final_mp4}")
print(f"OUTPUT_EXISTS={final_mp4.exists()}")
