"""
main.py

FastAPI backend for hookcut. The pipeline runs in two phases so the artist can
see what the LLM picked before spending minutes rendering it:

    POST /api/embedded-lyrics - quick probe: does this file have embedded
                                                             lyrics? Used to pre-fill the lyrics box.
    POST /api/jobs             - upload media + params, starts a job, returns job_id
    GET  /api/jobs/{id}         - poll job status/progress, and final result when done
    GET  /clips/{id}/{file}     - serves generated clip files

Jobs run in a background thread per request (fine for local/single-user use;
swap for a real task queue like Celery/RQ before multi-user production use).

Two job modes (mode="clips" or mode="lyric_video"):
    clips: transcribe -> [align corrected lyrics if provided] -> analyze
                 (moment selection) -> cut short clips.
    lyric_video: transcribe -> [align corrected lyrics] -> render the WHOLE
                 song as one video. No moment selection - the whole track.
"""

import sys
import uuid
import shutil
import threading
import tempfile
import time
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Make the existing pipeline modules importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from transcribe import transcribe, save_transcript
from analyze import analyze
from cut import cut_all_concepts, is_audio_only
from cover_art import resolve_cover_art, extract_embedded_lyrics
from render_video import make_full_lyric_video, get_audio_duration
from lyric_align import align_lyrics_to_audio
from tap_sync import build_lines_from_taps, merge_manual_and_auto_lines
from fastapi import Body

app = FastAPI(title="hookcut API")

app.add_middleware(
    CORSMiddleware,
    # Next.js falls back to 3001, 3002, etc. if 3000 is already taken -
    # matching any localhost port here avoids CORS breaking silently
    # whenever that happens, instead of hardcoding one specific port.
    allow_origin_regex=r"http://localhost:\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)

JOBS_DIR = Path(__file__).parent / "jobs"
JOBS_DIR.mkdir(exist_ok=True)

app.mount("/clips", StaticFiles(directory=str(JOBS_DIR)), name="clips")

ASPECT_DIMENSIONS = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
}

# In-memory job state. Fine for local/dev use - swap for a real DB/queue
# before running this for multiple simultaneous users.
jobs: dict = {}


def _format_timestamp(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


def run_clips_job(
    job_id: str,
    input_path: str,
    genre: str,
    mood: str,
    num_concepts: int,
    lyrics: bool,
    cover_image_path: Optional[str],
    lyrics_text: Optional[str],
    model_size: str = "base",
):
    """Transcribe, analyze, cut clips, and stream progress into `jobs` state."""
    jobs[job_id]["input_path"] = input_path
    job_dir = JOBS_DIR / job_id

    try:
        jobs[job_id]["stage"] = "transcribing"
        transcript = transcribe(input_path, model_size)
        save_transcript(transcript, str(job_dir / "transcript.json"))
        jobs[job_id]["transcript"] = transcript

        cover_path = None
        corrected_lines = None
        if is_audio_only(input_path):
            jobs[job_id]["stage"] = "resolving_cover_art"
            cover_path = resolve_cover_art(input_path, cover_image_path, str(job_dir))

            if not lyrics_text:
                embedded = extract_embedded_lyrics(input_path)
                if embedded:
                    lyrics_text = embedded

            if lyrics_text:
                total_duration = get_audio_duration(input_path)
                corrected_lines = align_lyrics_to_audio(lyrics_text, transcript["segments"], total_duration)

        jobs[job_id]["stage"] = "analyzing"
        brief = analyze(transcript, genre, mood, num_concepts, corrected_lines=corrected_lines)

        jobs[job_id]["stage"] = "cutting_clips"
        total_concepts = len(brief["concepts"])
        jobs[job_id]["progress"] = {"current": 0, "total": total_concepts}
        jobs[job_id]["results"] = []

        def _on_clip_done(index, total, concept, output_path):
            filename = Path(output_path).name
            jobs[job_id]["results"].append({
                **concept,
                "clip_url": f"/clips/{job_id}/{filename}",
            })
            jobs[job_id]["progress"] = {"current": index + 1, "total": total}

        cut_all_concepts(
            input_path,
            brief,
            str(job_dir),
            cover_path=cover_path,
            segments=transcript["segments"],
            lyrics_enabled=lyrics,
            lyrics_text=lyrics_text or None,
            on_clip_done=_on_clip_done,
        )

        jobs[job_id]["stage"] = "done"

    except Exception as e:
        jobs[job_id]["stage"] = "error"
        jobs[job_id]["error"] = str(e)


def run_lyric_video_job(
    job_id: str,
    input_path: str,
    aspect: str,
    lyrics: bool,
    cover_image_path: Optional[str],
    lyrics_text: Optional[str],
    model_size: str = "base",
):
    job_dir = JOBS_DIR / job_id
    try:
        width, height = ASPECT_DIMENSIONS.get(aspect, (1920, 1080))

        jobs[job_id]["stage"] = "transcribing"
        transcript = transcribe(input_path, model_size)
        save_transcript(transcript, str(job_dir / "transcript.json"))

        jobs[job_id]["stage"] = "resolving_cover_art"
        cover_path = resolve_cover_art(input_path, cover_image_path, str(job_dir))

        if not lyrics_text:
            embedded = extract_embedded_lyrics(input_path)
            if embedded:
                lyrics_text = embedded

        jobs[job_id]["stage"] = "cutting_clips"  # reuse existing stage label for the progress bar
        output_path = str(job_dir / f"{Path(input_path).stem}_lyric_video.mp4")
        make_full_lyric_video(
            audio_path=input_path,
            cover_path=cover_path,
            segments=transcript["segments"],
            output_path=output_path,
            width=width,
            height=height,
            lyrics_enabled=lyrics,
            lyrics_text=lyrics_text or None,
        )

        duration = get_audio_duration(input_path)
        results = [{
            "angle_name": "Full Lyric Video",
            "start_timestamp": "00:00",
            "end_timestamp": _format_timestamp(duration),
            "source_text": "",
            "text_overlay_options": [],
            "tiktok_caption": "",
            "ig_caption": "",
            "clip_url": f"/clips/{job_id}/{Path(output_path).name}",
        }]

        jobs[job_id]["results"] = results
        jobs[job_id]["stage"] = "done"

    except Exception as e:
        jobs[job_id]["stage"] = "error"
        jobs[job_id]["error"] = str(e)

        cover_path = None
        corrected_lines = None
        if is_audio_only(input_path):
            jobs[job_id]["stage"] = "resolving_cover_art"
            cover_path = resolve_cover_art(input_path, cover_image_path, str(job_dir))

            if not lyrics_text:
                embedded = extract_embedded_lyrics(input_path)
                if embedded:
                    lyrics_text = embedded

            if lyrics_text:
                total_duration = get_audio_duration(input_path)
                corrected_lines = align_lyrics_to_audio(lyrics_text, transcript["segments"], total_duration)

        jobs[job_id]["stage"] = "analyzing"
        # corrected_lines (accurate words + real timing) feeds hook-finding
        # when available, instead of Whisper's own (less reliable on
        # Sheng/Swahili) transcript - so both selection and rendering agree.
        brief = analyze(transcript, genre, mood, num_concepts, corrected_lines=corrected_lines)

        jobs[job_id]["stage"] = "cutting_clips"
        total_concepts = len(brief["concepts"])
        jobs[job_id]["progress"] = {"current": 0, "total": total_concepts}

        def _on_clip_done(index, total, concept, output_path):
            filename = Path(output_path).name
            jobs[job_id]["results"].append({
                **concept,
                "clip_url": f"/clips/{job_id}/{filename}",
            })
            jobs[job_id]["progress"] = {"current": index + 1, "total": total}

        cut_all_concepts(
            input_path,
            brief,
            str(job_dir),
            cover_path=cover_path,
            segments=transcript["segments"],
            lyrics_enabled=lyrics,
            lyrics_text=lyrics_text or None,
            on_clip_done=_on_clip_done,
        )

        jobs[job_id]["stage"] = "done"

    except Exception as e:
        jobs[job_id]["stage"] = "error"
        jobs[job_id]["error"] = str(e)


def run_lyric_video_job(
    job_id: str,
    input_path: str,
    aspect: str,
    lyrics: bool,
    cover_image_path: Optional[str],
    lyrics_text: Optional[str],
    model_size: str = "base",
):
    job_dir = JOBS_DIR / job_id
    try:
        width, height = ASPECT_DIMENSIONS.get(aspect, (1920, 1080))

        jobs[job_id]["stage"] = "transcribing"
        transcript = transcribe(input_path, model_size)
        save_transcript(transcript, str(job_dir / "transcript.json"))

        jobs[job_id]["stage"] = "resolving_cover_art"
        cover_path = resolve_cover_art(input_path, cover_image_path, str(job_dir))

        if not lyrics_text:
            embedded = extract_embedded_lyrics(input_path)
            if embedded:
                lyrics_text = embedded

        jobs[job_id]["stage"] = "cutting_clips"  # reuse existing stage label for the progress bar
        output_path = str(job_dir / f"{Path(input_path).stem}_lyric_video.mp4")
        make_full_lyric_video(
            audio_path=input_path,
            cover_path=cover_path,
            segments=transcript["segments"],
            output_path=output_path,
            width=width,
            height=height,
            lyrics_enabled=lyrics,
            lyrics_text=lyrics_text or None,
        )

        duration = get_audio_duration(input_path)
        results = [{
            "angle_name": "Full Lyric Video",
            "start_timestamp": "00:00",
            "end_timestamp": _format_timestamp(duration),
            "source_text": "",
            "text_overlay_options": [],
            "tiktok_caption": "",
            "ig_caption": "",
            "clip_url": f"/clips/{job_id}/{Path(output_path).name}",
        }]
>>>>>>> Stashed changes

        job["results"] = results
        job["stage"] = "done"

    except Exception as e:
        job["stage"] = "error"
        job["error"] = str(e)


<<<<<<< Updated upstream
# ---------------------------------------------------------------- routes
=======
@app.post("/api/embedded-lyrics")
async def probe_embedded_lyrics(file: UploadFile = File(...)):
    """
    Quick check for embedded ID3 lyrics (e.g. Suno's USLT tag) without
    running the full pipeline - used to pre-fill the lyrics textarea right
    after the user picks a file.
    """
    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        lyrics = extract_embedded_lyrics(tmp_path)
    except Exception:
        lyrics = None
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return {"lyrics": lyrics}

>>>>>>> Stashed changes

@app.post("/api/jobs")
async def create_job(
    file: UploadFile = File(...),
    mode: str = Form("clips"),
    genre: str = Form("music"),
    mood: str = Form("moody"),
    num_concepts: int = Form(5),
    aspect: str = Form("16:9"),
    lyrics: bool = Form(True),
    lyrics_text: Optional[str] = Form(None),
    cover_image: Optional[UploadFile] = File(None),
):
    job_id = str(uuid.uuid4())
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # Keep only the final path component - a client-supplied filename must
    # never be able to write outside this job's directory.
    input_path = job_dir / Path(file.filename).name
    with open(input_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    cover_image_path = None
    if cover_image is not None:
        cover_path = job_dir / Path(cover_image.filename).name
        with open(cover_path, "wb") as f:
            shutil.copyfileobj(cover_image.file, f)
        cover_image_path = str(cover_path)

    jobs[job_id] = {
        "stage": "queued",
<<<<<<< Updated upstream
        "source_kind": "audio" if is_audio_only(str(input_path)) else "video",
        "input_path": str(input_path),
        "lyrics": lyrics,
        "concepts": None,
        "results": None,
        "error": None,
    }

    threading.Thread(
        target=run_analysis,
        args=(job_id, genre, mood, num_concepts, cover_image_path),
        daemon=True,
    ).start()
=======
        "results": [],
        "error": None,
        "mode": mode,
        "progress": None,
        "started_at": time.time(),
    }

    if mode == "lyric_video":
        thread = threading.Thread(
            target=run_lyric_video_job,
            args=(job_id, str(input_path), aspect, lyrics, cover_image_path, lyrics_text),
            daemon=True,
        )
    else:
        thread = threading.Thread(
            target=run_clips_job,
            args=(job_id, str(input_path), genre, mood, num_concepts, lyrics, cover_image_path, lyrics_text),
            daemon=True,
        )
    thread.start()
>>>>>>> Stashed changes

    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Job not found. The server may have restarted since it started.",
        )
    return public_view(job)


@app.post("/api/jobs/{job_id}/render")
async def start_render(job_id: str, request: RenderRequest):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Job not found. The server may have restarted since it started.",
        )
    if job["stage"] != "awaiting_review":
        raise HTTPException(
            status_code=409,
            detail=f"This job is not waiting for review (it is {job['stage']}).",
        )

    total = len(job["brief"]["concepts"])
    approved = sorted({i for i in request.approved if 0 <= i < total})
    if not approved:
        raise HTTPException(
            status_code=400,
            detail="Pick at least one moment to render.",
        )

    job["total_to_render"] = len(approved)
    threading.Thread(
        target=run_render,
        args=(job_id, approved),
        daemon=True,
    ).start()

    return {"job_id": job_id, "rendering": len(approved)}


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/tap-sync")
async def tap_sync_endpoint(payload: dict = Body(...)):
    """Accepts JSON with `lines` (list of str) and `taps` (list of floats).
    Optionally accepts `auto_lines` (list of line dicts) and
    `fallback_line_duration` (float). Returns `lines` in the standard
    format used by the renderer (list of {start,end,words:[{word,start,end}]}).
    """
    lines_text = payload.get("lines", [])
    taps = payload.get("taps", [])
    fallback = payload.get("fallback_line_duration", 3.0)

    manual_lines = build_lines_from_taps(lines_text, taps, fallback)

    auto_lines = payload.get("auto_lines")
    if auto_lines:
        merged = merge_manual_and_auto_lines(auto_lines, manual_lines)
        return {"lines": merged}

    return {"lines": manual_lines}
