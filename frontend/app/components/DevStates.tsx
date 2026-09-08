"use client";

import { useEffect, useRef, useState } from "react";
import { MOCK_SEEDS } from "../lib/api";

/**
 * DevStates - the mock-data state switcher.
 *
 * Deliberately styled as a tool, not as product: mono type, no gradient, and
 * floated out of the layout entirely so it costs no vertical space in a
 * screen that never scrolls. Collapsed to a single chip until asked for.
 *
 * Backtick toggles it. Escape closes it.
 */
export default function DevStates() {
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  // Read after mount so server and client render identical markup.
  useEffect(() => {
    setCurrent(new URLSearchParams(window.location.search).get("state"));
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (e.key === "`") { e.preventDefault(); setOpen((o) => !o); }
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [open]);

  return (
    <div className="devstates" ref={ref}>
      {open && (
        <div className="devstates__menu" role="menu">
          <p className="devstates__heading">Mock state</p>
          {MOCK_SEEDS.map((seed) => (
            <a key={seed} href={`?state=${seed}`} role="menuitem"
              className="devstates__item" data-on={current === seed}>
              {seed}
            </a>
          ))}
          <a href="/" className="devstates__item devstates__item--reset">clear</a>
        </div>
      )}

      <button className="devstates__toggle" onClick={() => setOpen((o) => !o)}
        aria-expanded={open} title="Mock state switcher (press `)">
        <span className="devstates__pip" aria-hidden="true" />
        {current ?? "mock"}
      </button>
    </div>
  );
}
