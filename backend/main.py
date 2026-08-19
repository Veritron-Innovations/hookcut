"""
main.py

FastAPI backend for hookcut. Wraps the existing pipeline (transcribe ->
analyze -> cut) as a job API the frontend can poll:

  POST /api/jobs        - upload media + params, starts a job, returns job_id
  GET  /api/jobs/{id}    - poll job status/progress, and final result when done
  GET  /clips/{id}/{file} - serves generated clip files

Jobs run in a background thread per request (fine for local/single-user use;
swap for a real task queue like Celery/RQ before multi-user production use).
"""

import sys
import uuid
import shutil
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Make the existing pipeline modules importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from transcribe import transcribe, save_transcript
from analyze import analyze
from cut import cut_all_concepts, is_audio_only
from cover_art import resolve_cover_art

app = FastAPI(title="hookcut API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

JOBS_DIR = Path(__file__).parent / "jobs"
JOBS_DIR.mkdir(exist_ok=True)

app.mount("/clips", StaticFiles(directory=str(JOBS_DIR)), name="clips")

# In-memory job state. Fine for local/dev use - swap for a real DB/queue
# before running this for multiple simultaneous users.
jobs: dict = {}


def run_job(
    job_id: str,
    input_path: str,
    genre: str,
    mood: str,
    num_concepts: int,
    lyrics: bool,
    cover_image_path: Optional[str],
    model_size: str = "base",
):
    job_dir = JOBS_DIR / job_id
    try:
        jobs[job_id]["stage"] = "transcribing"
        transcript = transcribe(input_path, model_size)
        save_transcript(transcript, str(job_dir / "transcript.json"))

        jobs[job_id]["stage"] = "analyzing"
        brief = analyze(transcript, genre, mood, num_concepts)

        cover_path = None
        if is_audio_only(input_path):
            jobs[job_id]["stage"] = "resolving_cover_art"
            cover_path = resolve_cover_art(input_path, cover_image_path, str(job_dir))

        jobs[job_id]["stage"] = "cutting_clips"
        clip_paths = cut_all_concepts(
            input_path,
            brief,
            str(job_dir),
            cover_path=cover_path,
            segments=transcript["segments"],
            lyrics_enabled=lyrics,
        )

        # attach public URLs + concept metadata together for the frontend
        results = []
        for concept, clip_path in zip(brief["concepts"], clip_paths):
            filename = Path(clip_path).name
            results.append({
                **concept,
                "clip_url": f"/clips/{job_id}/{filename}",
            })

        jobs[job_id]["stage"] = "done"
        jobs[job_id]["results"] = results

    except Exception as e:
        jobs[job_id]["stage"] = "error"
        jobs[job_id]["error"] = str(e)


@app.post("/api/jobs")
async def create_job(
    file: UploadFile = File(...),
    genre: str = Form("music"),
    mood: str = Form("moody"),
    num_concepts: int = Form(5),
    lyrics: bool = Form(True),
    cover_image: Optional[UploadFile] = File(None),
):
    job_id = str(uuid.uuid4())
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    input_path = job_dir / file.filename
    with open(input_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    cover_image_path = None
    if cover_image is not None:
        cover_image_path = job_dir / cover_image.filename
        with open(cover_image_path, "wb") as f:
            shutil.copyfileobj(cover_image.file, f)
        cover_image_path = str(cover_image_path)

    jobs[job_id] = {"stage": "queued", "results": None, "error": None}

    thread = threading.Thread(
        target=run_job,
        args=(job_id, str(input_path), genre, mood, num_concepts, lyrics, cover_image_path),
        daemon=True,
    )
    thread.start()

    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        return {"error": "job not found"}
    return job


@app.get("/api/health")
async def health():
    return {"status": "ok"}
