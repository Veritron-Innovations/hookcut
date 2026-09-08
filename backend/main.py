"""
main.py

FastAPI backend for hookcut. The pipeline runs in two phases so the artist can
see what the LLM picked before spending minutes rendering it:

  Phase 1 - transcribe -> analyze -> resolve cover art, then stop at
            "awaiting_review" and hand the concepts to the frontend.
  Phase 2 - cut and render only the concepts the artist approved.

  POST /api/jobs              - upload media + params, starts phase 1
  GET  /api/jobs/{id}         - poll stage, review concepts, final results
  POST /api/jobs/{id}/render  - approve a subset of concepts, starts phase 2
  GET  /clips/{id}/{file}     - serves generated clip files

Each phase runs in a background thread per request (fine for local/single-user
use; swap for a real task queue like Celery/RQ before multi-user production
use).
"""

import sys
import uuid
import shutil
import threading
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


class RenderRequest(BaseModel):
    """Which concepts the artist approved, by their index in the brief."""
    approved: List[int]


def public_view(job: dict) -> dict:
    """
    The subset of job state the frontend is allowed to see.

    The job dict also holds the full transcript and local filesystem paths;
    neither belongs in an HTTP response, so the view is built explicitly
    rather than by returning the dict.
    """
    return {
        "stage": job["stage"],
        "source_kind": job["source_kind"],
        "concepts": job.get("concepts"),
        "results": job.get("results"),
        "error": job.get("error"),
        "total_to_render": job.get("total_to_render"),
    }


# ---------------------------------------------------------------- phase 1

def run_analysis(
    job_id: str,
    genre: str,
    mood: str,
    num_concepts: int,
    cover_image_path: Optional[str],
    model_size: str = "base",
):
    """Transcribe and analyze, then stop and wait for the artist to review."""
    job = jobs[job_id]
    job_dir = JOBS_DIR / job_id

    try:
        job["stage"] = "transcribing"
        transcript = transcribe(job["input_path"], model_size)
        save_transcript(transcript, str(job_dir / "transcript.json"))
        job["transcript"] = transcript

        job["stage"] = "analyzing"
        brief = analyze(transcript, genre, mood, num_concepts)

        if job["source_kind"] == "audio":
            job["stage"] = "resolving_cover_art"
            job["cover_path"] = resolve_cover_art(
                job["input_path"], cover_image_path, str(job_dir)
            )

        job["brief"] = brief
        # Stable index per concept so the render request can name exactly
        # which ones were approved.
        job["concepts"] = [
            {**concept, "index": i}
            for i, concept in enumerate(brief["concepts"])
        ]
        job["stage"] = "awaiting_review"

    except Exception as e:
        job["stage"] = "error"
        job["error"] = str(e)


# ---------------------------------------------------------------- phase 2

def run_render(job_id: str, approved: List[int]):
    """Cut and render only the approved concepts."""
    job = jobs[job_id]
    job_dir = JOBS_DIR / job_id

    try:
        job["stage"] = "cutting_clips"
        selected = [job["brief"]["concepts"][i] for i in approved]

        clip_paths = cut_all_concepts(
            job["input_path"],
            {"concepts": selected},
            str(job_dir),
            cover_path=job.get("cover_path"),
            segments=job["transcript"]["segments"],
            lyrics_enabled=job["lyrics"],
        )

        # attach public URLs + concept metadata together for the frontend
        results = []
        for index, concept, clip_path in zip(approved, selected, clip_paths):
            results.append({
                **concept,
                "index": index,
                "clip_url": f"/clips/{job_id}/{Path(clip_path).name}",
            })

        job["results"] = results
        job["stage"] = "done"

    except Exception as e:
        job["stage"] = "error"
        job["error"] = str(e)


# ---------------------------------------------------------------- routes

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
