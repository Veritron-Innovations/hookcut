import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from cut import cut_clip, timestamp_to_seconds
from render_video import reformat_and_caption_video_clip

SRC = Path(__file__).resolve().parents[1]
SAMPLES = SRC / 'samples' / 'Across the Room.mp4'
OUT = SRC / 'output' / 'Across_the_Room_smart_crop'
OUT.mkdir(parents=True, exist_ok=True)

clips = [
    ('00:53', '01:14', 'the_safe_place_chorus'),
    ('01:27', '01:45', 'cozy_nostalgia_future_dreams'),
    ('02:52', '03:13', 'the_stranger_plot_twist'),
    ('03:17', '03:34', 'vulnerable_confession_of_loneliness'),
    ('03:24', '03:46', 'building_worlds_out_of_strangers'),
]

for i, (start, end, name) in enumerate(clips):
    raw = OUT / f'clip_{i}_{name}_raw.mp4'
    out = OUT / f'clip_{i}_{name}.mp4'
    print('Cutting', raw)
    cut_clip(str(SAMPLES), start, end, str(raw))
    print('Rendering smart crop ->', out)
    reformat_and_caption_video_clip(
        str(raw), [], timestamp_to_seconds(start), timestamp_to_seconds(end), str(out),
        lyrics_enabled=False, smart_crop=True, face_center=(1100, 540)
    )

print('Done')
