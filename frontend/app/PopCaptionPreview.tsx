"use client";

import { useEffect, useRef, useMemo } from "react";
import {
  Word,
  Card,
  ThemeName,
  THEMES,
  REF_TIGHT,
  REF_FONT_BASE,
  POP_MS,
  SETTLE_MS,
  GROUP_SIZE_SCALE,
  groupIntoCards,
  clampCardWindows,
  tierIndexAndDynamics,
  extractRmsEnvelope,
  scoreWords,
  distributeWordsOverSpan,
} from "./lib/popCaptionEngine";

export type PreviewBlock = { text: string; start: number; end: number };

type Props = {
  blocks: PreviewBlock[];
  currentTime: number;
  theme: ThemeName;
  channelData: Float32Array | null; // decoded audio, for energy scoring - null degrades to flat mid-energy
  sampleRate: number;
  backgroundImageUrl?: string;
  width: number;
  height: number;
};

type LaidOutWord = {
  text: string;
  x: number;
  y: number;
  fontSize: number;
  colour: string;
  rotationDeg: number;
  overshoot: number;
  wordStart: number;
  cardEnd: number;
  isHot: boolean;
};

function estimateWidth(ctx: CanvasRenderingContext2D, text: string, fontSize: number, fontFamily: string): number {
  ctx.font = `900 ${fontSize}px ${fontFamily}`;
  return ctx.measureText(text).width;
}

function wrapCard(
  ctx: CanvasRenderingContext2D,
  items: { text: string; fontSize: number }[],
  maxWidth: number,
  fontFamily: string
): { text: string; fontSize: number; width: number }[][] {
  const lines: { text: string; fontSize: number; width: number }[][] = [];
  let current: { text: string; fontSize: number; width: number }[] = [];
  let currentWidth = 0;
  const spaceEm = 0.38;
  for (const { text, fontSize } of items) {
    const w = estimateWidth(ctx, text, fontSize, fontFamily);
    const space = current.length ? fontSize * spaceEm : 0;
    if (current.length && currentWidth + space + w > maxWidth) {
      lines.push(current);
      current = [{ text, fontSize, width: w }];
      currentWidth = w;
    } else {
      currentWidth += space + w;
      current.push({ text, fontSize, width: w });
    }
  }
  if (current.length) lines.push(current);
  return lines;
}

function positionCard(
  lines: { text: string; fontSize: number; width: number }[][],
  cx: number,
  cy: number
): { text: string; fontSize: number; x: number; y: number }[] {
  const lineSpacing = 1.18;
  const spaceEm = 0.38;
  const lineHeights = lines.map((line) => Math.max(...line.map((w) => w.fontSize)));
  const totalHeight = lineHeights.reduce((a, h) => a + h * lineSpacing, 0);
  let y = cy - totalHeight / 2;
  const positions: { text: string; fontSize: number; x: number; y: number }[] = [];
  for (let li = 0; li < lines.length; li++) {
    const line = lines[li];
    const lineHeight = lineHeights[li];
    const lineWidth = line.reduce((a, w) => a + w.width, 0) + line.slice(1).reduce((a, w) => a + w.fontSize * spaceEm, 0);
    let x = cx - lineWidth / 2;
    const yCenter = y + (lineHeight * lineSpacing) / 2;
    for (const w of line) {
      positions.push({ text: w.text, fontSize: w.fontSize, x: x + w.width / 2, y: yCenter });
      x += w.width + w.fontSize * spaceEm;
    }
    y += lineHeight * lineSpacing;
  }
  return positions;
}

/**
 * Draws a jagged "impact burst" star polygon centered on (cx, cy) - the
 * canvas equivalent of pop_captions.py's _burst_path, same point-
 * generation math (10 points, jag ratio 0.55).
 */
function drawBurst(ctx: CanvasRenderingContext2D, cx: number, cy: number, halfW: number, halfH: number, fillColour: string, outlineColour: string) {
  const points = 10;
  const jag = 0.55;
  ctx.beginPath();
  for (let i = 0; i < points * 2; i++) {
    const angle = (Math.PI * i) / points - Math.PI / 2;
    const r = i % 2 === 0 ? 1.0 : jag;
    const x = cx + r * halfW * Math.cos(angle);
    const y = cy + r * halfH * Math.sin(angle);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.closePath();
  ctx.fillStyle = fillColour;
  ctx.fill();
  ctx.lineWidth = 3;
  ctx.strokeStyle = outlineColour;
  ctx.stroke();
}

/**
 * Live canvas preview of the pop-caption export, synced to currentTime -
 * see lib/popCaptionEngine.ts for what this does and does not replicate
 * exactly from the real Python/ffmpeg export.
 */
export default function PopCaptionPreview({ blocks, currentTime, theme, channelData, sampleRate, backgroundImageUrl, width, height }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const bgImageRef = useRef<HTMLImageElement | null>(null);

  useEffect(() => {
    if (!backgroundImageUrl) {
      bgImageRef.current = null;
      return;
    }
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.src = backgroundImageUrl;
    bgImageRef.current = img;
  }, [backgroundImageUrl]);

  // Rebuild cards + energy whenever the block list or audio changes -
  // not on every currentTime tick, since neither depends on playhead
  // position.
  const { cards, windows, energies } = useMemo(() => {
    const words: Word[] = blocks
      .slice()
      .sort((a, b) => a.start - b.start)
      .flatMap((b) => distributeWordsOverSpan(b.text, b.start, b.end));

    const builtCards = groupIntoCards(words);
    const builtWindows = clampCardWindows(builtCards);

    let energyByWord: number[];
    if (channelData && words.length > 0) {
      const { rms, times } = extractRmsEnvelope(channelData, sampleRate);
      energyByWord = scoreWords(words, rms, times);
    } else {
      energyByWord = words.map(() => 0.5);
    }
    // Re-associate per-word energies back onto each card by matching
    // array position (groupIntoCards preserves input order/identity).
    let cursor = 0;
    const cardEnergies: number[][] = builtCards.map((card) => {
      const es = card.map(() => energyByWord[cursor++]);
      return es;
    });

    return { cards: builtCards, windows: builtWindows, energies: cardEnergies };
  }, [blocks, channelData, sampleRate]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    canvas.width = width;
    canvas.height = height;
    ctx.clearRect(0, 0, width, height);

    // Background: cover art if available, else a plain dark fill -
    // matches the export's fallback when no cover is given.
    if (bgImageRef.current && bgImageRef.current.complete && bgImageRef.current.naturalWidth > 0) {
      const img = bgImageRef.current;
      const scale = Math.max(width / img.naturalWidth, height / img.naturalHeight);
      const dw = img.naturalWidth * scale;
      const dh = img.naturalHeight * scale;
      ctx.drawImage(img, (width - dw) / 2, (height - dh) / 2, dw, dh);
      ctx.fillStyle = "rgba(0,0,0,0.15)";
      ctx.fillRect(0, 0, width, height);
    } else {
      ctx.fillStyle = "#121016";
      ctx.fillRect(0, 0, width, height);
    }

    // Find the active card for currentTime, if any.
    const cardIdx = windows.findIndex(([s, e]) => currentTime >= s && currentTime < e);
    if (cardIdx === -1) return;

    const card = cards[cardIdx];
    const cardEnergyList = energies[cardIdx];
    const palette = THEMES[theme];
    const tight = Math.min(width, height);
    const scale = tight / REF_TIGHT;
    const baseFont = REF_FONT_BASE * scale;
    const maxLineWidth = width * 0.86;
    const groupScale = GROUP_SIZE_SCALE[card.length] ?? Math.min(...Object.values(GROUP_SIZE_SCALE));

    const items: { text: string; fontSize: number }[] = [];
    const meta: { overshoot: number; jitter: number; isHot: boolean; wordStart: number }[] = [];
    for (let i = 0; i < card.length; i++) {
      const word = card[i];
      const energy = cardEnergyList[i] ?? 0.5;
      const { idx, sizeMult, overshoot, jitter } = tierIndexAndDynamics(energy);
      const fontSize = Math.round(baseFont * sizeMult * groupScale);
      items.push({ text: word.word.toUpperCase(), fontSize });
      meta.push({ overshoot: card.length > 1 ? 100 + Math.round((overshoot - 100) * 0.35) : overshoot, jitter, isHot: idx === 2, wordStart: word.start });
    }

    const lines = wrapCard(ctx, items, maxLineWidth, palette.font);
    const cx = width / 2;
    const cy = height * 0.42;
    const positions = positionCard(lines, cx, cy);

    positions.forEach((pos, i) => {
      const { overshoot, jitter, isHot, wordStart } = meta[i];
      const colour = palette.tierColours[isHot ? 2 : cardEnergyList[i] > 0.66 ? 2 : cardEnergyList[i] > 0.33 ? 1 : 0];
      const elapsedMs = (currentTime - wordStart) * 1000;
      if (elapsedMs < 0) return; // word hasn't started yet within this card

      let scaleFactor: number;
      if (elapsedMs < POP_MS) {
        scaleFactor = 0.6 + (overshoot / 100 - 0.6) * (elapsedMs / POP_MS);
      } else if (elapsedMs < POP_MS + SETTLE_MS) {
        const t = (elapsedMs - POP_MS) / SETTLE_MS;
        scaleFactor = overshoot / 100 + (1.0 - overshoot / 100) * t;
      } else {
        scaleFactor = 1.0;
      }

      ctx.save();
      ctx.translate(pos.x, pos.y);
      ctx.rotate((jitter ? (Math.random() * 2 - 1) * jitter : 0) * (Math.PI / 180));
      ctx.scale(scaleFactor, scaleFactor);
      if (isHot && palette.hotShear) {
        ctx.transform(1, 0, palette.hotShear, 1, 0, 0);
      }

      if (isHot && palette.burst && palette.burstColour && palette.burstOutline) {
        const halfW = (estimateWidth(ctx, pos.text, pos.fontSize, palette.font) / 2) * 1.35;
        const halfH = pos.fontSize * 0.75;
        drawBurst(ctx, 0, 0, halfW, halfH, palette.burstColour, palette.burstOutline);
      }

      ctx.font = `900 ${pos.fontSize}px ${palette.font}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.lineWidth = palette.outlineWidth;
      ctx.strokeStyle = "#000000";
      ctx.strokeText(pos.text, 0, 0);
      ctx.fillStyle = colour;
      ctx.fillText(pos.text, 0, 0);
      ctx.restore();
    });
  }, [cards, windows, energies, currentTime, theme, width, height, backgroundImageUrl]);

  return <canvas ref={canvasRef} style={{ width: "100%", height: "auto", display: "block", borderRadius: 10, background: "#121016" }} />;
}
