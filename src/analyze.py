"""
analyze.py

The "Narrative Brain" - takes a real timestamped transcript and asks an LLM
to identify the best short-form moments, with concrete cut points (not
invented content). Uses Gemini free tier for testing.
"""

import os
import json
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

SYSTEM_PROMPT = """You are a short-form content strategist for independent musicians \
and podcasters. You are given a REAL timestamped transcript from an artist's own \
audio/video. Your job is to identify the best moments to CUT into short-form clips \
- you are not writing new content, you are pointing to what's already there.

TASK:
1. HOOK IDENTIFICATION
   Scan the transcript segments for the highest scroll-stop moments. Two \
   distinct kinds of moments both count as strong hooks:
   a) Narrative moments: lyric twists, punchlines, confessions, tonal \
      shifts, unresolved tension.
   b) The CHORUS (if this is a song): repeated lines that carry the song's \
      main theme are usually the single most valuable clip for short-form, \
      even without narrative surprise - they're the most singable, most \
      recognizable, most quotable part, and repetition across the \
      transcript is a strong signal a segment IS the chorus. If this is a \
      song, make sure at least one concept covers the chorus even if other \
      moments feel more "dramatic."
   Only reference text that actually appears in the transcript.

2. CUT POINTS
   For each selected moment, pick a timestamp range from the ACTUAL segment \
   timestamps provided (do not invent timestamps). Keep clips 15-30 seconds.

3. TEXT OVERLAY OPTIONS
   For each moment, write 3 on-screen text hook variants (max 12 words each), \
   each targeting a different trigger: relatability, curiosity/open-loop, \
   controversy/contrarian take.

4. CAPTIONS
   - tiktok_caption: open-loop question, casual tone, max 2 lines.
   - ig_caption: aesthetic quote adapted from the transcript, plus 3-5 hashtags.

HARD CONSTRAINT: return EXACTLY the number of concepts requested in the user \
message under "Number of concepts to generate" - no more, no fewer. If you \
identify more candidate moments than that, pick only the strongest ones. Do \
not return every moment you notice.

Return STRICT JSON only, no prose outside the JSON, in this shape:
{
  "concepts": [
    {
      "angle_name": "",
      "start_timestamp": "mm:ss",
      "end_timestamp": "mm:ss",
      "source_text": "",
      "text_overlay_options": ["", "", ""],
      "tiktok_caption": "",
      "ig_caption": ""
    }
  ]
}
"""


def analyze(transcript: dict, genre: str, mood: str, num_concepts: int = 5) -> dict:
    """
    Send a transcript to the LLM and get back structured short-form concepts.

    Args:
        transcript: dict from transcribe.py with "segments" list
        genre: e.g. "indie folk", "true crime podcast"
        mood: e.g. "Late-night / Moody / Narrative"
        num_concepts: how many angles to generate

    Returns:
        dict with "concepts" list matching the schema above
    """
    segments_text = "\n".join(
        f"[{s['start']:.1f}s - {s['end']:.1f}s] {s['text']}"
        for s in transcript["segments"]
    )

    user_prompt = f"""
Genre: {genre}
Mood: {mood}
Number of concepts to generate: {num_concepts}

Transcript segments (real timestamps, do not invent new ones):
{segments_text}
"""

    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=[SYSTEM_PROMPT, user_prompt],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )

    result = json.loads(response.text)

    # Safety net: enforce the count even if the LLM overshoots
    if "concepts" in result and len(result["concepts"]) > num_concepts:
        result["concepts"] = result["concepts"][:num_concepts]

    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python analyze.py <transcript.json> [genre] [mood] [num_concepts]")
        sys.exit(1)

    transcript_path = sys.argv[1]
    genre = sys.argv[2] if len(sys.argv) > 2 else "music"
    mood = sys.argv[3] if len(sys.argv) > 3 else "moody"
    num_concepts = int(sys.argv[4]) if len(sys.argv) > 4 else 5

    with open(transcript_path) as f:
        transcript = json.load(f)

    result = analyze(transcript, genre, mood, num_concepts)

    out_path = transcript_path.replace("_transcript.json", "_brief.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Saved brief to {out_path}")
    print(json.dumps(result, indent=2))
