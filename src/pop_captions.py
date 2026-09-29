"""
pop_captions.py

TikTok/Reels-style "pop-on" word captions - the style used by Opus Clip /
Submagic / CapCut auto-captions. Words appear large, high-contrast,
center screen, each animating in exactly as it's spoken.

Two things drive how a caption looks, and neither is a guess:

1. Loudness (per word, via RMS energy pulled straight from the audio) -
   a shouted/emphasized word renders bigger and pops harder. Deliberately
   NOT lyric-content sentiment analysis - that's unreliable on Sheng/
   Swahili slang, which is most of what this app processes. Loudness is
   measurable and language-agnostic.

2. Local speech rate (per word, from Whisper's own timing) - a word said
   slowly enough to read on its own gets its own screen. Consecutive
   words said too fast to read individually get bundled into a "card" of
   up to 4 words that builds up on screen together (each word still pops
   in at the moment it's actually said), sized down to fit, and clears
   together at a natural pause.

A separate THEME controls the palette on top of that: which colours map
to which energy tier, the font, and whether loud words get a comic-style
"impact burst" drawn behind them. Energy dynamics (size/pop/jitter) are
theme-independent - only the paint job changes. See THEMES below.

This is an alternative to render_video.build_ass_subtitles (the
Spotify-style line + karaoke-highlight display) - see caption_style
param on the functions in render_video.py. Works for both short clips
and full-length lyric videos; the grouping logic is driven by local word
timing, not by how long the overall output is.
"""

import math
import random
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import librosa

# Reference tuning against a 1080px tight canvas dimension - same pattern
# render_video.py uses for the line-style captions, so both styles scale
# consistently between vertical (9:16) and landscape (16:9) output.
_REF_TIGHT = 1080
_REF_FONT_BASE = 120           # mid-energy, solo-word size at 1080 tight-dim
_MIN_DISPLAY_SECONDS = 0.15    # floor so a card is never on screen too briefly
_POP_MS = 90                   # scale-in animation duration
_SETTLE_MS = 60                # overshoot -> rest animation duration

# --- Word grouping (speech-rate adaptive) ----------------------------------
# A word only needs company if it's moving too fast to read alone. These
# thresholds decide when that's true and how big a group is allowed to get.
_MIN_SOLO_MS = 220       # a word at least this long can stand by itself
_MAX_CARD_WORDS = 3      # hard cap - never crams more than this together
_MAX_CARD_SPAN_MS = 1400  # hard cap on total card duration even if words keep coming
_PAUSE_GAP_S = 0.35      # a gap this long is a phrase break - always ends a card

# Baseline font shrinks as more words share a card, so a 4-word burst
# still fits without overflowing the canvas.
_GROUP_SIZE_SCALE = {1: 1.0, 2: 0.88, 3: 0.78, 4: 0.68}

# Layout / text-measurement constants. Word width is ESTIMATED from
# character count rather than measured against a real font file - the
# font actually used at render time depends on what's installed on
# whatever machine runs ffmpeg (fontconfig substitutes a lookalike for
# whatever bold sans it has), so measuring against one specific font here
# would be precision theatre. This approximation plus a generous margin
# (_MAX_LINE_WIDTH_FRAC) is what keeps wrapping safe across machines.
_AVG_CHAR_WIDTH_EM = 0.62
_WORD_SPACING_EM = 0.38
_LINE_SPACING_MULT = 1.18
_MAX_LINE_WIDTH_FRAC = 0.86  # max line width as a fraction of canvas width

# The pop-in animation overshoots past 100% scale before settling - great
# for a lone word with empty space around it, dangerous in a multi-word
# card where a neighbour is sitting right next to it: a word mid-bounce
# can visually collide with a cardmate that's already settled. Grouped
# cards get a heavily dampened overshoot; solo cards keep the full punch.
_OVERSHOOT_DAMPING_GROUPED = 0.35

# --- Energy tiers (theme-independent dynamics) -----------------------------
# (upper bound on normalized 0-1 energy, font size multiplier, overshoot
# scale % during the pop, rotation jitter in degrees). Ordered low -> high;
# the last tier's bound is a catch-all. Colour is NOT here - that's the
# theme's job (see THEMES) so a new palette never has to touch physics.
_TIER_DYNAMICS = [
    (0.33, 0.82, 108, 0),   # calm: small pop, no tilt
    (0.66, 1.00, 120, 2),   # mid: normal pop, slight tilt
    (1.01, 1.30, 145, 5),   # hot: big overshoot, more tilt
]


def _tier_index_and_dynamics(energy: float) -> tuple:
    for i, (max_e, size_mult, overshoot, jitter) in enumerate(_TIER_DYNAMICS):
        if energy <= max_e:
            return i, size_mult, overshoot, jitter
    i = len(_TIER_DYNAMICS) - 1
    return i, _TIER_DYNAMICS[i][1], _TIER_DYNAMICS[i][2], _TIER_DYNAMICS[i][3]


# --- Themes -----------------------------------------------------------------
# tier_colours[i] is the primary text colour (ASS &HBBGGRR& override format)
# for energy tier i (calm/mid/hot). "burst" turns on a jagged comic-style
# "impact" shape drawn behind hot-tier words (see _burst_path) - the same
# visual grammar as a comic panel's sound-effect lettering. "hot_shear"
# slants hot-tier text only (a \fax shear), for that angled "impact" look;
# 0.0 keeps text upright.
THEMES = {
    "default": {
        "font": "Arial Black",
        "tier_colours": ["&HFFFFFF&", "&H00FFFF&", "&H0060FF&"],  # white / yellow / orange-red
        "outline_width": 6,
        "shadow_width": 3,
        "hot_shear": 0.0,
        "burst": False,
    },
    "comic": {
        "font": "Impact",
        # Spider-Man palette: web-blue for calm/dialogue-style lines,
        # Spidey-red for mid energy, comic "impact" yellow for the
        # loudest words - paired with a red burst shape behind those so
        # a shouted word reads like a sound-effect panel ("THWIP", "POW"),
        # not just a bigger word.
        "tier_colours": ["&HD97400&", "&H241DED&", "&H00DEFF&"],  # blue / red / impact-yellow
        "outline_width": 7,
        "shadow_width": 4,
        "hot_shear": -0.18,
        "burst": True,
        "burst_colour": "&H241DED&",
        "burst_outline": "&H000000&",
    },
}
_OUTLINE_COLOUR = "&H00000000"  # black, opaque - contrast holds regardless of theme/mood


def _burst_path(cx: float, cy: float, half_w: float, half_h: float, points: int = 10, jag: float = 0.55) -> str:
    """
    A jagged 'impact burst' polygon (alternating long/short spikes) -
    the classic comic sound-effect shape - centered on ABSOLUTE canvas
    point (cx, cy). half_w/half_h let it stretch into an ellipse-ish
    shape matching a word's wide-short bounding box instead of a plain
    circle.

    Coordinates are baked in absolute rather than drawn at local (0,0)
    and positioned via \\pos, because libass does not bounding-box-center
    a \\p vector drawing under \\an5 the way it does text - confirmed by
    rendering a symmetric reference crosshair at the same \\pos as this
    shape and finding it visibly off-center. Emitting absolute
    coordinates with the event pinned at \\an7\\pos(0,0) (see call site)
    sidesteps that alignment ambiguity entirely instead of guessing at a
    correction offset.
    """
    pts = []
    n = points * 2
    for i in range(n):
        angle = math.pi * i / points - math.pi / 2
        r = 1.0 if i % 2 == 0 else jag
        pts.append((round(cx + r * half_w * math.cos(angle)), round(cy + r * half_h * math.sin(angle))))
    body = " ".join(f"l {x} {y}" for x, y in pts[1:])
    return f"m {pts[0][0]} {pts[0][1]} {body} l {pts[0][0]} {pts[0][1]}"


def extract_rms_envelope(audio_path: str, sr: int = 22050, hop_length: int = 512):
    """
    Decode ANY input (audio file OR video container) to mono PCM via
    ffmpeg, then compute a short-time RMS loudness envelope with librosa.

    Returns (rms: np.ndarray, times: np.ndarray) where times[i] is the
    timestamp in seconds (relative to the start of audio_path) of rms[i].
    """
    with tempfile.TemporaryDirectory() as tmp:
        wav_path = f"{tmp}/audio.wav"
        cmd = [
            "ffmpeg", "-y", "-i", audio_path,
            "-vn", "-ac", "1", "-ar", str(sr),
            wav_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg audio extraction failed: {result.stderr}")

        y, _ = librosa.load(wav_path, sr=sr, mono=True)

    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop_length)
    return rms, times


def score_words(words: list, rms: np.ndarray, times: np.ndarray) -> list:
    """
    For each word, average the RMS envelope over its spoken window, then
    normalize across all words passed in to 0..1 (5th-95th percentile
    clipped, so one loud/quiet outlier doesn't flatten everything else
    to 0 or 1).

    Normalization is scoped to the words passed in - i.e. whatever clip
    is being rendered, not the whole song - because that clip's own
    dynamic range is what's actually on screen.
    """
    raw = []
    for w in words:
        mask = (times >= w["start"]) & (times <= max(w["end"], w["start"] + 0.01))
        window = rms[mask]
        raw.append(float(window.mean()) if len(window) else 0.0)

    if not raw:
        return []

    lo, hi = np.percentile(raw, [5, 95])
    if hi - lo < 1e-6:
        return [0.5] * len(raw)  # flat clip (e.g. all spoken at one volume) - treat as mid

    return [float(np.clip((v - lo) / (hi - lo), 0.0, 1.0)) for v in raw]


def _group_into_cards(paired: list) -> list:
    """
    paired: chronological list of (word_dict, energy) tuples.

    Greedily builds "cards" - groups of consecutive words shown together.
    A card grows past one word ONLY while the MOST RECENT word added was
    itself too fast to read solo (< _MIN_SOLO_MS). A pause longer than
    _PAUSE_GAP_S, or hitting _MAX_CARD_WORDS / _MAX_CARD_SPAN_MS, always
    closes it regardless.

    This deliberately does NOT also require the card's cumulative
    duration to clear some minimum before it's allowed to close (an
    earlier version did, via a since-removed _MIN_CARD_MS check) - that
    meant a single comfortably-long word could still get padded with a
    neighbour just because ITS OWN duration didn't clear a separate
    "readable card length" bar, even though it was never too fast to
    read alone in the first place. Tested against a normal singing pace
    (individual words commonly 220-300ms, nothing unusually fast): that
    version produced 2-3 word cards for nearly every word, defeating the
    single-word "pop caption" aesthetic this whole engine exists for, on
    completely ordinary content. Readability of a short-lived card is
    handled at render time instead (_MIN_DISPLAY_SECONDS enforces a floor
    on how long ANY card stays on screen, regardless of how short its
    underlying word timing was) - grouping itself only needs to ask "was
    this word too fast to read", nothing more.

    Returns a list of cards, each a list of (word_dict, energy) tuples.
    """
    cards = []
    i = 0
    n = len(paired)
    while i < n:
        card = [paired[i]]
        i += 1
        while i < n:
            prev_word = card[-1][0]
            next_word = paired[i][0]
            gap = next_word["start"] - prev_word["end"]
            span_if_added_ms = (next_word["end"] - card[0][0]["start"]) * 1000

            if gap > _PAUSE_GAP_S:
                break
            if len(card) >= _MAX_CARD_WORDS:
                break
            if span_if_added_ms > _MAX_CARD_SPAN_MS:
                break

            prev_word_dur_ms = (prev_word["end"] - prev_word["start"]) * 1000
            if prev_word_dur_ms >= _MIN_SOLO_MS:
                break  # the last word added wasn't rushed - card can close here

            card.append(paired[i])
            i += 1
        cards.append(card)
    return cards


def _estimate_width(text: str, font_size: float) -> float:
    return len(text) * font_size * _AVG_CHAR_WIDTH_EM


def _wrap_card(items: list, max_width: float) -> list:
    """
    Greedy line-wrap. items: [(text, font_size, layout_width), ...] in
    card order - layout_width is the space to actually reserve for this
    item (normally its text width, but wider for a word that's about to
    get a comic burst drawn behind it, so the burst has room and doesn't
    encroach on a neighbour - see the burst footprint calculation at the
    call site). Returns lines: [[(text, font_size, layout_width), ...],
    ...], order preserved.
    """
    lines = []
    current = []
    current_width = 0.0
    for text, fs, w in items:
        space = fs * _WORD_SPACING_EM if current else 0.0
        if current and (current_width + space + w) > max_width:
            lines.append(current)
            current = [(text, fs, w)]
            current_width = w
        else:
            current_width += space + w
            current.append((text, fs, w))
    if current:
        lines.append(current)
    return lines


def _position_card(lines: list, cx: float, cy: float) -> list:
    """
    Takes wrapped lines and returns a flat list of (text, font_size, x, y)
    - x,y is the CENTER point for that word (matches ASS \\an5 anchoring),
    with lines centered horizontally and the whole block centered on
    (cx, cy) vertically.
    """
    line_heights = [max(fs for _, fs, _ in line) for line in lines]
    total_height = sum(h * _LINE_SPACING_MULT for h in line_heights)
    y = cy - total_height / 2

    positions = []
    for line, line_h in zip(lines, line_heights):
        line_width = sum(w for _, _, w in line) + sum(
            fs * _WORD_SPACING_EM for _, fs, _ in line[1:]
        )
        x = cx - line_width / 2
        y_center = y + (line_h * _LINE_SPACING_MULT) / 2
        for text, fs, w in line:
            positions.append((text, fs, x + w / 2, y_center))
            x += w + fs * _WORD_SPACING_EM
        y += line_h * _LINE_SPACING_MULT
    return positions


def _fmt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def build_pop_captions_ass(
    words: list,
    clip_start: float,
    clip_end: float,
    output_path: str,
    width: int,
    height: int,
    audio_path: str | None = None,
    uppercase: bool = True,
    theme: str = "default",
) -> str:
    """
    Build an .ass subtitle file in the TikTok pop-caption style: words are
    grouped into "cards" by local speech rate (see _group_into_cards), and
    each card is laid out centered on screen - one big word for a card of
    1, a centered wrapped block for a card of 2-4. Every word still pops
    in at the exact moment it's spoken, sized/coloured by its own audio
    energy; the card just controls how many words share the screen and
    how long they stay up.

    words: flat list of {"word", "start", "end"} in ABSOLUTE (song) time -
    same shape as lyric_lines.flatten_words()'s output. Only words
    overlapping [clip_start, clip_end] are rendered.

    audio_path: what to measure loudness from. Pass the SAME media
    that's actually being rendered for this clip - its time axis is
    assumed to start at clip_start, matching how render_video.py trims
    clips before calling this. If omitted, every word renders at flat
    mid-energy (captions still work, just uniform size/colour, and
    grouping still happens since that's driven by timing not loudness).

    uppercase: render words in caps, matching the bold viral-caption look.

    theme: key into THEMES - controls font, per-tier colour, and whether
    hot-tier words get a comic-style impact burst behind them. See
    THEMES for what each one does.
    """
    if theme not in THEMES:
        raise ValueError(f"Unknown caption theme '{theme}' - choose one of {list(THEMES)}")
    palette = THEMES[theme]

    tight = min(width, height)
    scale = tight / _REF_TIGHT
    base_font = _REF_FONT_BASE * scale
    max_line_width = width * _MAX_LINE_WIDTH_FRAC

    clip_words = [w for w in words if w["end"] >= clip_start and w["start"] <= clip_end]

    if audio_path and clip_words:
        rms, times = extract_rms_envelope(audio_path)
        times = times + clip_start  # rebase to absolute/song time to match word timestamps
        energies = score_words(clip_words, rms, times)
    else:
        energies = [0.5] * len(clip_words)

    paired = list(zip(clip_words, energies))
    cards = _group_into_cards(paired)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Pop,{palette['font']},{round(base_font)},&H00FFFFFF,&H00FFFFFF,{_OUTLINE_COLOUR},&H00000000,1,0,0,0,100,100,0,0,1,{palette['outline_width']},{palette['shadow_width']},5,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    cx, cy = width // 2, round(height * 0.42)  # centre, slightly above true middle
    clip_len = clip_end - clip_start

    events = []
    prev_card_end = 0.0  # enforces non-overlapping cards - see note below
    for card in cards:
        card_start_abs = card[0][0]["start"]
        card_end_abs = card[-1][0]["end"]
        card_rel_start = max(card_start_abs - clip_start, 0.0)
        card_rel_end = min(card_end_abs - clip_start, clip_len)
        card_rel_end = max(card_rel_end, card_rel_start + _MIN_DISPLAY_SECONDS)
        card_rel_end = min(card_rel_end, clip_len)

        # Upstream word timing is not guaranteed to be strictly
        # monotonic/non-overlapping - checkpoint-interpolated or
        # otherwise imprecise alignment can legitimately produce a case
        # where one word's end genuinely lands after the next word's
        # start (found by testing, not assumed - two adjacent single-
        # word cards overlapped by 400ms in a realistic imprecision
        # scenario). Without this clamp, two cards' screen time can
        # overlap and both words render simultaneously, stacked - this
        # forces every card to start no earlier than the previous card's
        # end, so display windows never overlap regardless of what the
        # input timing looks like.
        card_rel_start = max(card_rel_start, prev_card_end)
        card_rel_end = max(card_rel_end, card_rel_start + _MIN_DISPLAY_SECONDS)
        card_rel_end = min(card_rel_end, clip_len)
        if card_rel_end <= card_rel_start:
            continue
        prev_card_end = card_rel_end

        group_scale = _GROUP_SIZE_SCALE.get(len(card), min(_GROUP_SIZE_SCALE.values()))
        max_energy = max(e for _, e in card)
        scatter = max_energy > 0.66  # hot cards get a touch of vertical jitter for energy

        texts_sizes = []
        meta = []  # (word, tier_idx, overshoot, jitter, font_size, text)
        for word, energy in card:
            text = word["word"].strip()
            if not text:
                continue
            if uppercase:
                text = text.upper()
            tier_idx, size_mult, overshoot, jitter = _tier_index_and_dynamics(energy)
            font_size = round(base_font * size_mult * group_scale)
            if len(card) > 1:
                overshoot = 100 + round((overshoot - 100) * _OVERSHOOT_DAMPING_GROUPED)

            text_width = _estimate_width(text, font_size)
            is_hot = tier_idx == len(_TIER_DYNAMICS) - 1
            if is_hot and palette["burst"]:
                # Reserve the burst's actual footprint in the layout, not
                # just the text's - otherwise a wide word's burst can
                # visually encroach on a neighbour sitting right next to
                # it in the same line (found by testing a real multi-word
                # card, not assumed).
                layout_width = text_width / 2 * 1.35 * 2
            else:
                layout_width = text_width
            texts_sizes.append((text, font_size, layout_width))
            meta.append((word, tier_idx, overshoot, jitter, font_size, text))

        if not meta:
            continue

        lines = _wrap_card(texts_sizes, max_line_width)
        positions = _position_card(lines, cx, cy)

        for (word, tier_idx, overshoot, jitter, font_size, text), (_, _, px, py) in zip(meta, positions):
            word_rel_start = max(word["start"] - clip_start, card_rel_start)
            word_rel_end = card_rel_end  # stays visible until the whole card clears
            if word_rel_end <= word_rel_start:
                continue

            y = py + (random.uniform(-0.06, 0.06) * font_size if scatter else 0)
            colour = palette["tier_colours"][tier_idx]
            is_hot = tier_idx == len(_TIER_DYNAMICS) - 1

            # A hot-tier word in a burst-capable theme gets a comic
            # "impact" shape drawn behind it first (Layer 0, so the text
            # on Layer 1 renders on top), sized to roughly match the
            # word's own footprint and popping in on the same beat.
            if is_hot and palette["burst"]:
                half_w = _estimate_width(text, font_size) / 2 * 1.35
                half_h = font_size * 0.75
                path = _burst_path(px, y, half_w, half_h)
                # No scale animation here (see _burst_path's docstring for
                # why) - a fast alpha flash-in instead. This also happens
                # to be a MORE authentic comic effect than a smooth grow:
                # impact lettering in a real panel snaps into existence,
                # it doesn't ease in.
                burst_tags = (
                    f"\\an7\\pos(0,0)\\p1"
                    f"\\c{palette['burst_colour']}\\3c{palette['burst_outline']}\\bord3\\shad0"
                    f"\\alpha&HFF&\\t(0,{_POP_MS},\\alpha&H00&)"
                )
                events.append(
                    f"Dialogue: 0,{_fmt_time(word_rel_start)},{_fmt_time(word_rel_end)},"
                    f"Pop,,0,0,0,,{{{burst_tags}}}{path}"
                )

            rot = f"\\frz{random.uniform(-jitter, jitter):.1f}" if jitter else ""
            shear = f"\\fax{palette['hot_shear']}" if (is_hot and palette["hot_shear"]) else ""

            # Pop animation timing is relative to THIS word's own event
            # start, so within a multi-word card each word still bounces
            # in individually at the moment it's spoken, even though it
            # then sits alongside cardmates already on screen.
            tags = (
                f"\\an5\\pos({round(px)},{round(y)})\\fs{font_size}\\c{colour}{rot}{shear}"
                f"\\fscx60\\fscy60\\alpha&HFF&"
                f"\\t(0,{_POP_MS},\\fscx{overshoot}\\fscy{overshoot}\\alpha&H00&)"
                f"\\t({_POP_MS},{_POP_MS + _SETTLE_MS},\\fscx100\\fscy100)"
            )
            events.append(
                f"Dialogue: 1,{_fmt_time(word_rel_start)},{_fmt_time(word_rel_end)},"
                f"Pop,,0,0,0,,{{{tags}}}{text}"
            )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(header + "\n".join(events), encoding="utf-8")
    return output_path
