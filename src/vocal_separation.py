"""
vocal_separation.py

Isolates vocals from a full mix BEFORE transcription/alignment, instead of
running Whisper and forced alignment directly against a mixed track
(vocals + drums + bass + synths all together). This is explicitly the
core technique professional lyric-alignment services use - AudioShake's
own public documentation states it plainly: "AudioShake first isolates
the vocal... Accuracy comes from transcribing the isolated vocal rather
than the full mix. Removing instrumentation before transcription gives
the model a much cleaner signal, which produces noticeably better
results than transcribing a mixed recording." Every alignment
improvement built elsewhere in this pipeline (forced alignment, gap
detection, per-word refinement) has been operating on a noisier signal
than necessary - this is the missing step underneath all of them, not a
replacement for any of them.

Uses Demucs (Meta/Facebook Research) - the current open-source
state-of-the-art source separator. Not the identical model AudioShake
uses internally (that's proprietary), but the same category of
technology solving the same problem.

IMPORTANT - separated vocals are for INTERNAL transcription/alignment use
ONLY. The final rendered video/clip audio must always be the ORIGINAL
full mix - nobody wants an acapella-only version of their song on the
actual output. This module never touches what gets rendered, only what
gets fed to Whisper and the forced-alignment DTW. Callers are responsible
for keeping these two audio references separate - see backend/main.py
for how the split is threaded through.

INSTALL NOTE, learned the hard way: `pip install demucs` pulls in torch
as a dependency, and a PLAIN `pip install torch` defaults to the full
CUDA/GPU build - several GIGABYTES of NVIDIA libraries that a normal
laptop without a matching high-end GPU setup doesn't need and often
doesn't have disk space for (this failed outright with "No space left on
device" pulling ~3GB+ of CUDA packages when tested). Install the CPU-only
build FIRST, then demucs on top of it - see README for the exact command.
Without this, `pip install demucs` is likely to fail or stall part-way
through on an ordinary machine, which silently defeats this entire
feature: every failure mode below (not installed, install incomplete,
model download blocked) falls back to the original mix with NO error
surfaced anywhere - so a failed install looks identical to "vocal
separation quietly decided it wasn't needed" instead of "vocal
separation never actually worked." status_reason below exists
specifically to stop that from being invisible - callers should log or
display it, not just check whether vocal_path is None.

CAVEAT, stated plainly: separation quality was NOT empirically validated
against a real produced track in the environment this was built in - the
model weights (~80MB, one-time download) come from
dl.fbaipublicfiles.com, which wasn't reachable from that sandbox. The
integration is complete and the technique is well-evidenced (this is
literally what AudioShake says it does), but "this measurably improves
alignment on a real song" has not been proven the way most other fixes
in this codebase were - that first real run is the actual test.
"""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SeparationResult:
    vocal_path: str | None
    status_reason: str


def check_demucs_available() -> bool:
    return shutil.which("demucs") is not None


def separate_vocals(audio_path: str, work_dir: str, model: str = "htdemucs") -> SeparationResult:
    """
    Runs Demucs vocal separation on audio_path.

    Always returns a SeparationResult - vocal_path is None (graceful
    fallback to the original full mix) if Demucs isn't installed or
    separation fails for any reason (not installed, first-run model
    download blocked, subprocess timeout, corrupted/unsupported audio,
    etc.) - callers must never hard-fail a job over this being
    unavailable, but SHOULD log/surface status_reason so a failure is
    never silent (see the INSTALL NOTE above for why this matters - a
    failed torch/demucs install looks IDENTICAL to "not needed" without
    this).

    The model weights download once on first use and are cached locally
    after that (~/.cache/torch/hub/checkpoints) - only the very first
    call on a fresh machine needs internet access for this step; every
    call after that runs fully offline.

    --two-stems vocals asks Demucs for just {vocals, everything else}
    instead of the full 4-way {vocals, drums, bass, other} split - faster,
    and this pipeline only ever needs the vocal stem.
    """
    if not check_demucs_available():
        return SeparationResult(None, "demucs not installed (not found on PATH) - falling back to the original mix")

    try:
        work_path = Path(work_dir)
        work_path.mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            ["demucs", "--two-stems", "vocals", "-n", model, "-o", str(work_path), audio_path],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            stderr_tail = (result.stderr or "").strip()[-400:]
            return SeparationResult(
                None,
                f"demucs exited with an error (falling back to the original mix): {stderr_tail or 'no error output captured'}",
            )

        stem = Path(audio_path).stem
        vocal_path = work_path / model / stem / "vocals.wav"
        if vocal_path.exists():
            return SeparationResult(str(vocal_path), "vocal separation succeeded")
        return SeparationResult(None, "demucs ran but the expected output file was not found - falling back to the original mix")

    except subprocess.TimeoutExpired:
        return SeparationResult(None, "demucs timed out after 600s (falling back to the original mix)")
    except Exception as e:
        return SeparationResult(None, f"vocal separation failed unexpectedly: {e} (falling back to the original mix)")
