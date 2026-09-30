"""
main.py

FastAPI backend for hookcut. Wraps the existing pipeline (transcribe ->
analyze -> cut) as a job API the frontend can poll:

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
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Make the existing pipeline modules importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from transcribe import transcribe, save_transcript
from vocal_separation import separate_vocals
from analyze import analyze
from cut import cut_all_concepts, is_audio_only, recut_one_concept, safe_filename
from cover_art import resolve_cover_art, extract_embedded_lyrics
from render_video import make_full_lyric_video, get_audio_duration
from lyric_align import align_lyrics_best_effort
from tap_sync import build_lines_from_taps, build_lines_from_explicit_timestamps, merge_manual_and_auto_lines, DEFAULT_TAP_LATENCY_S
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
    caption_style: str = "pop_word",
    caption_theme: str = "default",
):
    job_dir = JOBS_DIR / job_id
    try:
        jobs[job_id]["stage"] = "transcribing"

        # Isolate vocals BEFORE transcribing - transcription and forced
        # alignment both work from a much cleaner signal against just the
        # vocal stem than against the full mix (vocals + drums + bass +
        # synths). This is explicitly what professional lyric-alignment
        # services do - see vocal_separation.py's docstring. vocal_path
        # is None (graceful fallback to the original full mix) if Demucs
        # isn't installed or separation fails for any reason - this must
        # never hard-fail a job. Only used for transcription/alignment -
        # rendering always uses the real input_path, never this.
        separation = separate_vocals(input_path, str(job_dir / "vocals_work"))
        print(f"[{job_id}] vocal separation: {separation.status_reason}")
        jobs[job_id]["vocal_separation_status"] = separation.status_reason
        vocal_path = separation.vocal_path
        align_audio_path = vocal_path or input_path

        # Hook selection (analyze() below) reads every segment's TEXT
        # regardless of the lyrics toggle - it's how clips get picked at
        # all - so transcription itself can't be skipped here the way it
        # can in lyric_video mode (see run_lyric_video_job, which has no
        # hook-selection step). Word-level timestamps specifically are a
        # genuinely more expensive add-on step Whisper does on top of
        # plain segment transcription, and nothing downstream reads a
        # WORD's timing when lyrics=False (captions are the only
        # consumer of word-level timing) - so skip requesting them then.
        transcript = transcribe(align_audio_path, model_size, word_timestamps=lyrics)
        save_transcript(transcript, str(job_dir / "transcript.json"))

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
                corrected_lines = align_lyrics_best_effort(
                    lyrics_text, transcript["segments"], align_audio_path, str(job_dir / "align_work"),
                    language=transcript.get("language", "en"), total_duration=total_duration,
                )

        jobs[job_id]["stage"] = "analyzing"
        # corrected_lines (accurate words + real timing) feeds hook-finding
        # when available, instead of Whisper's own (less reliable on
        # Sheng/Swahili) transcript - so both selection and rendering agree.
        # Falls back to grouping Whisper's own words into lines (same as
        # lyric_video mode) when no corrected lyrics were given, so every
        # clips job - not just ones with pasted lyrics - has aligned_lines
        # to patch later via tap-sync.
        if corrected_lines is None and lyrics:
            from lyric_lines import group_into_lines
            corrected_lines = group_into_lines(transcript["segments"])

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
            caption_style=caption_style,
            caption_theme=caption_theme,
            caption_language=transcript.get("language", "en"),
            alignment_audio_path=vocal_path,
            precomputed_lines=corrected_lines,
        )

        # Stored so a later /api/jobs/{id}/patch-section call can re-render
        # just the affected clip with manually tap-synced timing merged in,
        # without redoing transcription/analysis - same pattern as
        # lyric_video mode's aligned_lines/render_params.
        jobs[job_id]["aligned_lines"] = corrected_lines if lyrics else None
        jobs[job_id]["audio_url"] = f"/clips/{job_id}/{Path(input_path).name}"
        jobs[job_id]["render_params"] = {
            "input_path": input_path,
            "cover_path": cover_path,
            "segments": transcript["segments"],
            "job_dir": str(job_dir),
            "caption_style": caption_style,
            "caption_theme": caption_theme,
            "caption_language": transcript.get("language", "en"),
            "alignment_audio_path": vocal_path,
        }

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
    caption_style: str = "pop_word",
    caption_theme: str = "default",
):
    job_dir = JOBS_DIR / job_id
    try:
        width, height = ASPECT_DIMENSIONS.get(aspect, (1920, 1080))

        jobs[job_id]["stage"] = "transcribing"
        # Unlike run_clips_job, there's no hook-selection step in this
        # mode - the whole song gets rendered, nothing gets "picked" - so
        # when lyrics=False, transcript["segments"]/["language"] aren't
        # read anywhere below at all (aligned_lines just becomes [], see
        # the branch below). Skip transcription (and vocal separation,
        # which only exists to feed transcription/alignment) entirely
        # rather than pay for either when nothing below will use them;
        # stub in the shape downstream code expects so it doesn't need
        # its own lyrics-gating for every transcript access.
        vocal_path = None
        align_audio_path = input_path
        if lyrics:
            separation = separate_vocals(input_path, str(job_dir / "vocals_work"))
            print(f"[{job_id}] vocal separation: {separation.status_reason}")
            jobs[job_id]["vocal_separation_status"] = separation.status_reason
            vocal_path = separation.vocal_path
            align_audio_path = vocal_path or input_path
            transcript = transcribe(align_audio_path, model_size)
            save_transcript(transcript, str(job_dir / "transcript.json"))
        else:
            transcript = {"text": "", "segments": [], "language": "en"}

        jobs[job_id]["stage"] = "resolving_cover_art"
        cover_path = resolve_cover_art(input_path, cover_image_path, str(job_dir))

        if not lyrics_text:
            embedded = extract_embedded_lyrics(input_path)
            if embedded:
                lyrics_text = embedded

        # Compute alignment HERE (not buried inside make_full_lyric_video) so
        # it can be stored on the job and later patched via tap-sync without
        # re-transcribing - transcription is the expensive part regardless.
        # (align_lyrics_best_effort's forced-alignment path is meaningfully
        # more expensive than the old pure-Whisper-timestamp warp it
        # replaces - still much cheaper than re-transcribing, just not the
        # "comparatively cheap" this comment used to claim before forced
        # alignment existed.)
        duration = get_audio_duration(input_path)
        if lyrics and lyrics_text:
            aligned_lines = align_lyrics_best_effort(
                lyrics_text, transcript["segments"], align_audio_path, str(job_dir / "align_work"),
                language=transcript.get("language", "en"), total_duration=duration,
            )
        elif lyrics:
            from lyric_lines import group_into_lines
            aligned_lines = group_into_lines(transcript["segments"])
        else:
            aligned_lines = []

        jobs[job_id]["aligned_lines"] = aligned_lines
        jobs[job_id]["audio_url"] = f"/clips/{job_id}/{Path(input_path).name}"
        jobs[job_id]["render_params"] = {
            "audio_path": input_path,
            "cover_path": cover_path,
            "width": width,
            "height": height,
            "lyrics_enabled": lyrics,
            "caption_style": caption_style,
            "caption_theme": caption_theme,
            "caption_language": transcript.get("language", "en"),
            "alignment_audio_path": vocal_path,
            "segments": transcript["segments"],
        }

        jobs[job_id]["stage"] = "cutting_clips"  # reuse existing stage label for the progress bar
        output_path = str(job_dir / f"{Path(input_path).stem}_lyric_video.mp4")
        jobs[job_id]["render_params"]["output_path"] = output_path

        make_full_lyric_video(
            audio_path=input_path,
            cover_path=cover_path,
            segments=transcript["segments"],
            output_path=output_path,
            width=width,
            height=height,
            lyrics_enabled=lyrics,
            precomputed_lines=aligned_lines,
            caption_style=caption_style,
            caption_theme=caption_theme,
            caption_language=transcript.get("language", "en"),
            alignment_audio_path=vocal_path,
        )

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

        jobs[job_id]["stage"] = "done"
        jobs[job_id]["results"] = results

    except Exception as e:
        jobs[job_id]["stage"] = "error"
        jobs[job_id]["error"] = str(e)


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
    caption_style: str = Form("pop_word"),
    caption_theme: str = Form("default"),
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

    jobs[job_id] = {
        "stage": "queued",
        "results": [],
        "error": None,
        "mode": mode,
        "progress": None,
        "started_at": time.time(),
        "aligned_lines": None,
        "audio_url": None,
        "render_params": None,
        "patch_version": 0,
    }

    if mode == "lyric_video":
        thread = threading.Thread(
            target=run_lyric_video_job,
            args=(job_id, str(input_path), aspect, lyrics, cover_image_path, lyrics_text),
            kwargs={"caption_style": caption_style, "caption_theme": caption_theme},
            daemon=True,
        )
    else:
        thread = threading.Thread(
            target=run_clips_job,
            args=(job_id, str(input_path), genre, mood, num_concepts, lyrics, cover_image_path, lyrics_text),
            kwargs={"caption_style": caption_style, "caption_theme": caption_theme},
            daemon=True,
        )
    thread.start()

    return {"job_id": job_id}


@app.post("/api/tap-sync")
async def tap_sync_endpoint(payload: dict = Body(...)):
    """Accepts JSON with `lines` (list of str) and `taps` (list of floats).
    Optionally accepts `auto_lines` (list of line dicts) and
    `fallback_line_duration` (float). Returns `lines` in the standard
    format used by the renderer (list of {start,end,words:[{word,start,end}]}).

    This is a pure calculator with no job attachment - useful for the
    standalone demo page. For actually correcting a real job's output, use
    POST /api/jobs/{job_id}/patch-section instead, which also re-renders.
    """
    lines_text = payload.get("lines", [])
    taps = payload.get("taps", [])
    fallback = payload.get("fallback_line_duration", 3.0)
    segments = payload.get("segments")  # optional - lets the demo page exercise the same real-timing warp patch_section uses
    tap_latency_s = payload.get("tap_latency_s", DEFAULT_TAP_LATENCY_S)

    manual_lines = build_lines_from_taps(lines_text, taps, fallback, segments=segments, tap_latency_s=tap_latency_s)

    auto_lines = payload.get("auto_lines")
    if auto_lines:
        merged = merge_manual_and_auto_lines(auto_lines, manual_lines)
        return {"lines": merged}

    return {"lines": manual_lines}


def _rerender_lyric_video(job_id: str, version: int):
    """Re-render a lyric_video job's output using its current
    jobs[job_id]["aligned_lines"] - reuses stored render params, so this
    skips transcription and analysis entirely (the expensive parts) and
    only redoes the subtitle build + video encode.

    version gets stamped into the output filename so each patch produces
    a genuinely new URL. Without this, every re-render overwrote the same
    filename - the corrected video really was written to disk, but the
    browser's <video src="..."> never re-fetches a URL it's already
    loaded, so the patch looked like it silently did nothing even though
    it worked.
    """
    try:
        params = jobs[job_id]["render_params"]
        base_path = Path(params["output_path"])
        versioned_path = str(base_path.with_name(f"{base_path.stem}_v{version}{base_path.suffix}"))

        make_full_lyric_video(
            audio_path=params["audio_path"],
            cover_path=params["cover_path"],
            segments=[],
            output_path=versioned_path,
            width=params["width"],
            height=params["height"],
            lyrics_enabled=params["lyrics_enabled"],
            precomputed_lines=jobs[job_id]["aligned_lines"],
            caption_style=params.get("caption_style", "line"),
            caption_theme=params.get("caption_theme", "default"),
            caption_language=params.get("caption_language", "en"),
            alignment_audio_path=params.get("alignment_audio_path"),
        )

        duration = get_audio_duration(params["audio_path"])
        jobs[job_id]["results"] = [{
            "angle_name": "Full Lyric Video",
            "start_timestamp": "00:00",
            "end_timestamp": _format_timestamp(duration),
            "source_text": "",
            "text_overlay_options": [],
            "tiktok_caption": "",
            "ig_caption": "",
            "clip_url": f"/clips/{job_id}/{Path(versioned_path).name}",
        }]
        jobs[job_id]["stage"] = "done"

    except Exception as e:
        jobs[job_id]["stage"] = "error"
        jobs[job_id]["error"] = str(e)


def _rerender_clips(job_id: str, concept_indices: list, version: int):
    """Re-render one or more clips in a clips-mode job using the job's
    current jobs[job_id]["aligned_lines"] - reuses stored render params,
    so this skips transcription and analysis entirely and only redoes the
    subtitle build + video encode for each affected clip.

    Runs sequentially rather than in parallel threads - concurrent ffmpeg
    encodes competing for the same CPU would slow each other down more
    than running them one after another, and it keeps failure handling
    simple: one bad clip doesn't leave a pile of half-finished concurrent
    encodes behind.

    version is stamped into every filename (same reasoning as
    _rerender_lyric_video above) - each patch needs a genuinely new URL
    per clip, not a reused one.
    """
    try:
        params = jobs[job_id]["render_params"]
        for concept_index in concept_indices:
            concept = jobs[job_id]["results"][concept_index]
            safe_name = f"{concept_index}_{safe_filename(concept['angle_name'])}_patched_v{version}"

            out_path = recut_one_concept(
                input_path=params["input_path"],
                concept=concept,
                output_dir=params["job_dir"],
                cover_path=params["cover_path"],
                segments=params["segments"],
                precomputed_lines=jobs[job_id]["aligned_lines"],
                safe_name=safe_name,
                caption_style=params.get("caption_style", "pop_word"),
                caption_theme=params.get("caption_theme", "default"),
                caption_language=params.get("caption_language", "en"),
                alignment_audio_path=params.get("alignment_audio_path"),
            )

            filename = Path(out_path).name
            jobs[job_id]["results"][concept_index]["clip_url"] = f"/clips/{job_id}/{filename}"

        jobs[job_id]["stage"] = "done"

    except Exception as e:
        jobs[job_id]["stage"] = "error"
        jobs[job_id]["error"] = str(e)


@app.post("/api/jobs/{job_id}/patch-section")
async def patch_section(job_id: str, payload: dict = Body(...)):
    """
    Correct a section of a completed job using manually-tapped line
    timing, then re-render (cheaply - no re-transcription).

    Body: {"lines": [str, ...], "taps": [float, ...], "concept_index": int}
    - the problem lines (e.g. a Swahili chorus) and when each one actually
    starts, tapped by ear against the job's own audio (served at
    jobs[job_id]["audio_url"]).

    concept_index is OPTIONAL for clips-mode jobs and ignored for
    lyric_video jobs (only one output file, no index needed). A lyric
    timing correction almost always applies to the whole song, not one
    clip in isolation - most clips were cut from the same mistranscribed
    stretch of audio - so omitting concept_index re-renders EVERY clip in
    the job with the corrected timing in one pass, instead of making the
    user repeat this per clip. Pass concept_index to re-render just that
    one clip instead, if you specifically only want one fixed.
    """
    job = jobs.get(job_id)
    if job is None:
        return {"error": "job not found"}
    if job.get("aligned_lines") is None or job.get("render_params") is None:
        return {"error": "this job has no alignment data to patch (lyrics were disabled, or the job isn't done yet)"}

    lines_text = payload.get("lines", [])
    taps = payload.get("taps", [])
    fallback = payload.get("fallback_line_duration", 3.0)

    # segments is the job's own Whisper transcription - passing it lets
    # word timing within each tapped line warp onto Whisper's real
    # detected pacing in that span (see tap_sync.py), instead of
    # assuming words flow at a flat constant rate across the whole
    # tap-to-tap gap. Without this, a line with real trailing silence
    # before the next line's pickup gets its last word smeared across
    # that silence instead of ending when it's actually sung.
    segments = job["render_params"].get("segments")
    # tap_latency_s: optional per-request override for a specific user's
    # own measured reaction lag (see DEFAULT_TAP_LATENCY_S in tap_sync.py
    # for why this exists at all). Omit to use the default.
    tap_latency_s = payload.get("tap_latency_s", DEFAULT_TAP_LATENCY_S)
    manual_lines = build_lines_from_taps(lines_text, taps, fallback, segments=segments, tap_latency_s=tap_latency_s)
    merged = merge_manual_and_auto_lines(job["aligned_lines"], manual_lines)
    jobs[job_id]["aligned_lines"] = merged

    jobs[job_id]["stage"] = "cutting_clips"
    jobs[job_id]["patch_version"] = jobs[job_id].get("patch_version", 0) + 1
    version = jobs[job_id]["patch_version"]

    if job["mode"] == "lyric_video":
        thread = threading.Thread(target=_rerender_lyric_video, args=(job_id, version), daemon=True)
    else:
        concept_index = payload.get("concept_index")
        if concept_index is not None:
            if not (0 <= concept_index < len(job["results"])):
                jobs[job_id]["stage"] = "done"  # nothing actually started, don't leave it stuck
                return {"error": "concept_index out of range for this job's clips"}
            concept_indices = [concept_index]
        else:
            concept_indices = list(range(len(job["results"])))
        thread = threading.Thread(target=_rerender_clips, args=(job_id, concept_indices, version), daemon=True)

    thread.start()
    return {"status": "patching", "job_id": job_id}


def _start_rerender_thread(job_id: str, job: dict) -> None:
    """
    Shared "apply a correction and kick off the appropriate re-render"
    tail, used by both patch-section (sequential tap input) and
    patch-lines (explicit-timestamp block input) - they differ only in
    how manual_lines gets built, not in what happens once
    jobs[job_id]["aligned_lines"] has been updated.
    """
    jobs[job_id]["stage"] = "cutting_clips"
    jobs[job_id]["patch_version"] = jobs[job_id].get("patch_version", 0) + 1
    version = jobs[job_id]["patch_version"]

    if job["mode"] == "lyric_video":
        thread = threading.Thread(target=_rerender_lyric_video, args=(job_id, version), daemon=True)
    else:
        concept_indices = list(range(len(job["results"])))
        thread = threading.Thread(target=_rerender_clips, args=(job_id, concept_indices, version), daemon=True)
    thread.start()


@app.post("/api/jobs/{job_id}/patch-lines")
async def patch_lines(job_id: str, payload: dict = Body(...)):
    """
    Correct specific captions using EXPLICIT timestamps the user placed
    directly (e.g. via a timeline editor: scrub the waveform, pause at
    the exact moment a word is actually said, insert a caption right
    there) - the non-linear counterpart to patch-section's sequential
    tap-through-a-span input. Fixing one mistranscribed word doesn't
    require re-doing an entire surrounding span with this endpoint.

    Body: {"lines": [{"text": str, "start": float, "end": float}, ...]}
    - each entry is one caption block with its own independently-given
    start and end; order doesn't matter, they get sorted by start.

    Always re-renders every clip (clips-mode) or the one output
    (lyric_video mode) - unlike patch-section there's no concept_index
    option here, since a timeline-editor correction is exactly as likely
    to span multiple clips as a tap-sync one is (see patch-section's
    docstring for the same reasoning).
    """
    job = jobs.get(job_id)
    if job is None:
        return {"error": "job not found"}
    if job.get("aligned_lines") is None or job.get("render_params") is None:
        return {"error": "this job has no alignment data to patch (lyrics were disabled, or the job isn't done yet)"}

    blocks = payload.get("lines", [])
    if not blocks:
        return {"error": "no caption blocks given"}

    params = job["render_params"]
    segments = params.get("segments")
    # Prefer refining against the isolated vocal stem when one exists for
    # this job (see vocal_separation.py) - same reasoning as everywhere
    # else forced alignment runs in this pipeline: a cleaner signal than
    # the full mix gives DTW a much better shot at landing correctly.
    audio_path = params.get("alignment_audio_path") or params.get("audio_path") or params.get("input_path")
    manual_lines = build_lines_from_explicit_timestamps(
        blocks, segments=segments, audio_path=audio_path,
        work_dir=str(JOBS_DIR / job_id / "timeline_edit_work"),
        lang=params.get("caption_language", "en"),
    )
    merged = merge_manual_and_auto_lines(job["aligned_lines"], manual_lines)
    jobs[job_id]["aligned_lines"] = merged

    _start_rerender_thread(job_id, job)
    return {"status": "patching", "job_id": job_id}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        return {"error": "job not found"}
    return job


@app.get("/api/health")
async def health():
    return {"status": "ok"}
