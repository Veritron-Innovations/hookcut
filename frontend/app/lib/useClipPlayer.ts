"use client";

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

/**
 * Drives a media element through one exact range and stops at its out-point,
 * so a cut can be heard - and watched - before it is rendered.
 *
 * The element is owned by whoever renders it (the preview frame), because a
 * single <video> handles both cases: invisible for a song, on screen for a
 * podcast. The playhead runs on requestAnimationFrame rather than the
 * element's timeupdate event, which fires about four times a second and makes
 * both the marker and the lyric highlight crawl.
 */
export function useClipPlayer(
  mediaRef: RefObject<HTMLMediaElement | null>,
  src: string | null,
) {
  const stopAtRef = useRef<number | null>(null);
  const rafRef = useRef<number | null>(null);

  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState<number | null>(null);

  const cancelFrame = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  }, []);

  const stop = useCallback(() => {
    mediaRef.current?.pause();
    stopAtRef.current = null;
    cancelFrame();
    setPlaying(false);
  }, [cancelFrame, mediaRef]);

  // Reset whenever the source changes or the component goes away.
  useEffect(() => {
    return () => {
      cancelFrame();
      stopAtRef.current = null;
    };
  }, [src, cancelFrame]);

  const playRange = useCallback((start: number, end: number) => {
    const media = mediaRef.current;
    if (!media) return;

    stopAtRef.current = end;
    try {
      media.currentTime = start;
    } catch {
      /* Not seekable yet - play from wherever it is rather than failing. */
    }
    setPosition(start);

    const follow = () => {
      const m = mediaRef.current;
      if (!m) return;
      setPosition(m.currentTime);
      if (stopAtRef.current !== null && m.currentTime >= stopAtRef.current) {
        m.pause();
        setPlaying(false);
        cancelFrame();
        return;
      }
      rafRef.current = requestAnimationFrame(follow);
    };

    void media.play()
      .then(() => {
        setPlaying(true);
        cancelFrame();
        rafRef.current = requestAnimationFrame(follow);
      })
      .catch(() => {
        // Autoplay blocked, or this source can't be decoded for playback.
        setPlaying(false);
      });
  }, [cancelFrame, mediaRef]);

  return { available: Boolean(src), playing, position, playRange, stop };
}

/**
 * Spread a lyric line across a clip's duration so it can be highlighted word
 * by word. Weighted by word length, which tracks sung timing closely enough
 * for a preview - the rendered clip uses Whisper's real per-word timestamps.
 */
export type TimedWord = { word: string; start: number; end: number };

export function spreadWords(text: string, start: number, end: number): TimedWord[] {
  const words = text.split(/\s+/).filter(Boolean);
  if (words.length === 0) return [];

  const weights = words.map((w) => Math.max(2, w.replace(/[^\w']/g, "").length));
  const total = weights.reduce((a, b) => a + b, 0) || 1;
  const span = Math.max(0.1, end - start);

  let t = start;
  return words.map((word, i) => {
    const d = (weights[i] / total) * span;
    const timed = { word, start: t, end: t + d };
    t += d;
    return timed;
  });
}
