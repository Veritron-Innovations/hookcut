import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\chazc\hookcut\src")
from vocal_separation import separate_vocals

sample = r"C:\Users\chazc\hookcut\samples\Silk & Spine.mp3"
work = r"C:\Users\chazc\hookcut\tmp_vocal_check"

p = separate_vocals(sample, work)
print(f"VOCAL_PATH={p}")
print(f"EXISTS={bool(p and Path(p).exists())}")
