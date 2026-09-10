"use client";

import { useEffect, useRef, useState, useCallback } from "react";

const GRID_SIZE = 18;
const CELL_PX = 16;
const CANVAS_PX = GRID_SIZE * CELL_PX;
const TICK_MS = 130;

type Point = { x: number; y: number };
type Direction = "UP" | "DOWN" | "LEFT" | "RIGHT";

const OPPOSITE: Record<Direction, Direction> = {
  UP: "DOWN",
  DOWN: "UP",
  LEFT: "RIGHT",
  RIGHT: "LEFT",
};

function randomCell(exclude: Point[]): Point {
  let cell: Point;
  do {
    cell = { x: Math.floor(Math.random() * GRID_SIZE), y: Math.floor(Math.random() * GRID_SIZE) };
  } while (exclude.some((p) => p.x === cell.x && p.y === cell.y));
  return cell;
}

export default function SnakeGame() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const snakeRef = useRef<Point[]>([{ x: 8, y: 9 }, { x: 7, y: 9 }, { x: 6, y: 9 }]);
  const dirRef = useRef<Direction>("RIGHT");
  const nextDirRef = useRef<Direction>("RIGHT");
  const foodRef = useRef<Point>(randomCell(snakeRef.current));
  const [score, setScore] = useState(0);
  const [best, setBest] = useState(0);
  const [gameOver, setGameOver] = useState(false);
  const [started, setStarted] = useState(false);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.fillStyle = "#17151f";
    ctx.fillRect(0, 0, CANVAS_PX, CANVAS_PX);

    // food
    ctx.fillStyle = "#ff3d7f";
    ctx.beginPath();
    ctx.arc(
      foodRef.current.x * CELL_PX + CELL_PX / 2,
      foodRef.current.y * CELL_PX + CELL_PX / 2,
      CELL_PX / 2.6,
      0,
      Math.PI * 2
    );
    ctx.fill();

    // snake
    snakeRef.current.forEach((seg, i) => {
      ctx.fillStyle = i === 0 ? "#ffc53d" : "#e8b23d";
      ctx.fillRect(seg.x * CELL_PX + 1, seg.y * CELL_PX + 1, CELL_PX - 2, CELL_PX - 2);
    });
  }, []);

  const resetGame = useCallback(() => {
    snakeRef.current = [{ x: 8, y: 9 }, { x: 7, y: 9 }, { x: 6, y: 9 }];
    dirRef.current = "RIGHT";
    nextDirRef.current = "RIGHT";
    foodRef.current = randomCell(snakeRef.current);
    setScore(0);
    setGameOver(false);
    setStarted(true);
    draw();
  }, [draw]);

  useEffect(() => {
    draw();
  }, [draw]);

  useEffect(() => {
    if (!started || gameOver) return;

    const interval = setInterval(() => {
      dirRef.current = nextDirRef.current;
      const head = snakeRef.current[0];
      let newHead: Point;
      switch (dirRef.current) {
        case "UP": newHead = { x: head.x, y: head.y - 1 }; break;
        case "DOWN": newHead = { x: head.x, y: head.y + 1 }; break;
        case "LEFT": newHead = { x: head.x - 1, y: head.y }; break;
        case "RIGHT": newHead = { x: head.x + 1, y: head.y }; break;
      }

      const hitWall = newHead.x < 0 || newHead.x >= GRID_SIZE || newHead.y < 0 || newHead.y >= GRID_SIZE;
      const hitSelf = snakeRef.current.some((seg) => seg.x === newHead.x && seg.y === newHead.y);

      if (hitWall || hitSelf) {
        setGameOver(true);
        setBest((b) => Math.max(b, score));
        return;
      }

      const ateFood = newHead.x === foodRef.current.x && newHead.y === foodRef.current.y;
      const newSnake = [newHead, ...snakeRef.current];
      if (ateFood) {
        setScore((s) => s + 1);
        foodRef.current = randomCell(newSnake);
      } else {
        newSnake.pop();
      }
      snakeRef.current = newSnake;
      draw();
    }, TICK_MS);

    return () => clearInterval(interval);
  }, [started, gameOver, score, draw]);

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      const keyMap: Record<string, Direction> = {
        ArrowUp: "UP", w: "UP", W: "UP",
        ArrowDown: "DOWN", s: "DOWN", S: "DOWN",
        ArrowLeft: "LEFT", a: "LEFT", A: "LEFT",
        ArrowRight: "RIGHT", d: "RIGHT", D: "RIGHT",
      };
      const next = keyMap[e.key];
      if (!next) return;
      e.preventDefault();
      if (!started || gameOver) {
        resetGame();
        return;
      }
      if (next !== OPPOSITE[dirRef.current]) {
        nextDirRef.current = next;
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [started, gameOver, resetGame]);

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", width: CANVAS_PX, fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--muted)" }}>
        <span>SCORE {score}</span>
        <span>BEST {best}</span>
      </div>
      <div style={{ position: "relative" }}>
        <canvas
          ref={canvasRef}
          width={CANVAS_PX}
          height={CANVAS_PX}
          style={{ borderRadius: 12, border: "1px solid var(--border)", display: "block" }}
        />
        {(!started || gameOver) && (
          <div
            onClick={resetGame}
            style={{
              position: "absolute", inset: 0, display: "flex", flexDirection: "column",
              alignItems: "center", justifyContent: "center", gap: 6, cursor: "pointer",
              background: "rgba(12,11,18,0.82)", borderRadius: 12,
            }}
          >
            <span style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 15, color: "var(--paper)" }}>
              {gameOver ? "Game over" : "Bored yet?"}
            </span>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>
              {gameOver ? `Scored ${score} — click or press a key` : "Arrow keys or WASD to play"}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
