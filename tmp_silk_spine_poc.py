import sys
from pathlib import Path

root = Path(r"C:\Users\chazc\hookcut")
sys.path.insert(0, str(root / "src"))

from cover_art import resolve_cover_art, extract_embedded_lyrics
from render_video import make_vertical_clip, get_audio_duration
from transcribe import transcribe
from vocal_separation import separate_vocals

input_path = root / "samples" / "Silk & Spine.mp3"
output_dir = root / "output"
output_dir.mkdir(parents=True, exist_ok=True)

cover_path = resolve_cover_art(str(input_path), None, str(output_dir))
lyrics_text = extract_embedded_lyrics(str(input_path))
transcript = transcribe(str(input_path), "base")
duration = get_audio_duration(str(input_path))
vocal_path = separate_vocals(str(input_path), str(output_dir / "silk_spine_vocals"))

output_path = output_dir / "Silk_Spine_comic_poc.mp4"
make_vertical_clip(
    audio_clip_path=str(input_path),
    cover_path=cover_path,
    segments=transcript["segments"],
    clip_start=0.0,
    clip_end=duration,
    output_path=str(output_path),
    lyrics_enabled=True,
    lyrics_text=lyrics_text,
    total_duration=duration,
    caption_style="pop_word",
    caption_theme="comic",
    alignment_audio_path=vocal_path,
)

print(f"COVER_PATH={cover_path}")
print(f"HAS_LYRICS={bool(lyrics_text)}")
print(f"VOCAL_PATH={vocal_path}")
print(f"OUTPUT_PATH={output_path}")
print(f"OUTPUT_EXISTS={output_path.exists()}")
