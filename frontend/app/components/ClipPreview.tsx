"use client";

import { type MutableRefObject } from "react";
import { spreadWords } from "../lib/useClipPlayer";

/**
 * ClipPreview - the clip itself, live, before anything is rendered.
 *
 * This is a browser rebuild of what render_video.py composites: a blurred
 * cover-art bleed filling a 9:16 frame, the sharp art centred slightly above
 * middle, and the lyric burning in word by word underneath. Playing it uses
 * the artist's real audio at the real timestamps, so what they see here is
 * what ffmpeg will produce - minutes before ffmpeg is asked to produce it.
 *
 * A single <video> element backs both cases. For a song it stays invisible
 * and only supplies audio; for a podcast it is the picture.
 */

type Props = {
  mediaRef: MutableRefObject<HTMLVideoElement | null>;
  src: string | null;
  sourceKind: "audio" | "video" | null;
  coverUrl: string | null;
  hue: string;
  text: string;
  start: number;
  end: number;
  position: number | null;
  playing: boolean;
  lyricsOn: boolean;
  onToggle: () => void;
};

export default function ClipPreview({
  mediaRef, src, sourceKind, coverUrl, hue, text, start, end,
  position, playing, lyricsOn, onToggle,
}: Props) {
  const isVideo = sourceKind === "video";
  const words = spreadWords(text, start, end);
  const now = position ?? start;
  const elapsed = Math.max(0, Math.min(end - start, now - start));
  const progress = ((elapsed / Math.max(0.1, end - start)) * 100).toFixed(2);

  return (
    <div className="preview" style={{ ["--hue" as any]: hue }}>
      <div className="preview__frame">
        {/* Blurred bleed, exactly the trick render_video.py uses. */}
        {coverUrl && !isVideo && (
          <div className="preview__bleed" style={{ backgroundImage: `url(${coverUrl})` }} />
        )}
        {!coverUrl && !isVideo && <div className="preview__bleed preview__bleed--none" />}

        {/* One element serves both: hidden for a song, the picture for a podcast. */}
        <video
          ref={mediaRef}
          src={src ?? undefined}
          playsInline
          className={isVideo ? "preview__video" : "preview__audio"}
        />

        {!isVideo && (
          <div className="preview__stack">
            <div className="preview__art">
              {coverUrl
                ? <img src={coverUrl} alt="" />
                : <div className="preview__art-fallback"><Disc /></div>}
            </div>

            {lyricsOn && (
              <p className="preview__lyric">
                {words.map((w, i) => (
                  <span key={i} data-sung={now >= w.start} data-active={now >= w.start && now < w.end}>
                    {w.word}{" "}
                  </span>
                ))}
              </p>
            )}
          </div>
        )}

        <button className="preview__tap" onClick={onToggle}
          aria-label={playing ? "Pause preview" : "Play preview"}>
          {!playing && (
            <span className="preview__play"><PlayGlyph /></span>
          )}
        </button>

        <div className="preview__bar">
          <span style={{ width: `${progress}%` }} />
        </div>

        <span className="preview__badge mono">
          {fmt(elapsed)} / {fmt(end - start)}
        </span>
      </div>

      <p className="preview__caption">
        {src
          ? isVideo
            ? "Your video, at this cut"
            : `Live preview${lyricsOn ? " — lyric timing is approximate here" : ""}`
          : "Drop a real file to preview the clip"}
      </p>
    </div>
  );
}

function fmt(s: number): string {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
}

function PlayGlyph() {
  return (
    <svg width="26" height="26" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M4 2.6v10.8a.6.6 0 0 0 .92.5l8.4-5.4a.6.6 0 0 0 0-1l-8.4-5.4a.6.6 0 0 0-.92.5z" />
    </svg>
  );
}

function Disc() {
  return (
    <svg width="54" height="54" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="9.2" stroke="currentColor" strokeWidth="1.3" opacity="0.5" />
      <circle cx="12" cy="12" r="3.4" stroke="currentColor" strokeWidth="1.3" opacity="0.8" />
      <circle cx="12" cy="12" r="1" fill="currentColor" />
    </svg>
  );
}
