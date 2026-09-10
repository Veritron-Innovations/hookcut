# hookcut

Turn a finished song or podcast episode into a ready-to-post short-form clip plan —
automatically, from the artist's own audio/video, not generated media.

## Pipeline

1. **Transcribe** (`src/transcribe.py`) — Whisper turns the uploaded audio/video into
   a timestamped transcript.
2. **Analyze** (`src/analyze.py`) — An LLM scans the real transcript for the highest
   scroll-stop moments (hooks, punchlines, emotional peaks) and returns structured
   JSON: timestamp ranges, suggested text overlays, captions.
3. **Cut** (`src/cut.py`) — ffmpeg cuts the actual clip(s) out of the source file
   at the timestamps the LLM picked, using stream-copy for speed.

Output: real short-form clips cut from the artist's own media, plus a JSON/markdown
brief with captions and on-screen text hook options.

## Status

Early prototype. Testing with Whisper (local/Colab) + Gemini free tier for the LLM
layer to keep dev cost near $0.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate      # on Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Requires `ffmpeg` installed on the system (not just the Python binding) — e.g.
`sudo apt install ffmpeg` on Linux, `brew install ffmpeg` on macOS.

Deactivate the venv when done with `deactivate`.

## Running the UI

Two servers, run in separate terminals from the `hookcut/` root.

**Backend (FastAPI):**
```bash
cd backend
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
pip install -r ../requirements.txt   # pipeline deps (whisper, gemini, etc.)
uvicorn main:app --reload --port 8000
```

**Frontend (Next.js):**
```bash
cd frontend
npm install
npm run dev
```

Then open http://localhost:3000 — choose "Short clips" or "Full lyric video"
at the top of the form. In clips mode: upload a song or podcast, set
genre/mood, and it'll transcribe, analyze, and render clips. In lyric video
mode: upload a song, pick an aspect ratio, and it renders the whole track
as one lyric video. Both poll for progress and show results when done.

If you provide corrected lyrics (pasted or auto-filled from embedded
metadata), they're aligned to the audio BEFORE moment-selection runs, not
just used for the final video - this matters for Sheng/Swahili content,
where Gemini can't find the real hook/chorus in Whisper's own (less
reliable) transcription. See `src/lyric_align.py`.

Requires the backend's `GEMINI_API_KEY` env var set the same way as the CLI
(`.env` file in the repo root, or exported in the shell running uvicorn).

## Correcting lyrics for Sheng/Swahili content

Whisper's transcription accuracy drops on Sheng (Nairobi slang) and is only
middling on standard Swahili, especially for sung/melodic audio. Both the
web UI and CLI let you supply the correct lyrics text - it's used for the
on-screen words, while Whisper's detected audio timing (pauses, line
boundaries) is still used for sync. See `src/lyric_align.py` for how the
two are combined.

- **Web UI**: the lyrics box is always shown, auto-filled from the file's
  embedded ID3 lyrics (Suno tracks commonly have these) if present - edit
  it before generating.
- **CLI**: pass `--lyrics-file path/to/lyrics.txt` to `pipeline.py` or
  `lyric_video.py`. Without it, embedded ID3 lyrics are used automatically
  if present, otherwise falls back to Whisper's own transcription.

## Experimental: real forced alignment (instead of the Whisper heuristic)

The lyrics-correction feature above still relies on Whisper's own audio
timing as an anchor, even when you supply the correct text. `forced_align.py`
replaces that with real forced alignment via `espeak-ng` + dynamic time
warping (DTW).

Two modes:
- **hybrid (default)**: uses Whisper's segment/pause boundaries as a coarse
  skeleton (Whisper's timing of WHEN something is sung is more reliable
  than its guess at WHAT is sung, even in Sheng/Swahili), then runs DTW
  *locally* within each small segment window. Much more robust than one
  DTW pass across a whole song, which compares a flat TTS voice against
  the full mixed/produced track and can drift badly on sung content.
- **whole**: one DTW pass across the entire song, no Whisper needed at
  all. Simpler, kept for comparison, but prone to drift on real singing.

Requires `espeak-ng` installed and on PATH:
https://github.com/espeak-ng/espeak-ng/releases (Windows installer available)

```bash
python src/lyric_video.py --input samples/song.mp3 --forced-alignment
python src/lyric_video.py --input samples/song.mp3 --forced-alignment --alignment-mode whole
```

`--lang` is an espeak-ng voice code (`sw` for Swahili by default). Requires
lyrics text from somewhere - pasted via `--lyrics-file`, or embedded in the
file's metadata.

## Full lyric video (whole song, not a short clip)

```bash
python src/lyric_video.py --input samples/song.mp3 --aspect 16:9
```

Renders the entire song with karaoke-style synced lyrics over album art -
same two-line highlight style as the short clips, just for the full track.
`--aspect` supports `16:9` (landscape, classic YouTube lyric video), `9:16`
(vertical), or `1:1` (square). Also supports `--cover-image` and
`--no-lyrics`, same as the short-clip pipeline.

## CLI usage (original, still works)

```bash
python src/pipeline.py --input path/to/podcast.mp4 --genre "true crime" --mood "moody"
```

## Structure

```
src/
  transcribe.py   # Whisper wrapper -> timestamped transcript
  analyze.py       # LLM prompt layer -> structured brief JSON
  cut.py            # ffmpeg clip extraction
  pipeline.py      # ties it all together
samples/           # test audio/video files (gitignored)
output/            # generated clips + briefs (gitignored)
tests/
```
