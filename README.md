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

Then open http://localhost:3000 — upload a song or podcast, set genre/mood,
and it'll transcribe, analyze, and render clips, polling for progress and
showing a results gallery with playable clips, captions, and downloads.

Requires the backend's `GEMINI_API_KEY` env var set the same way as the CLI
(`.env` file in the repo root, or exported in the shell running uvicorn).

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
