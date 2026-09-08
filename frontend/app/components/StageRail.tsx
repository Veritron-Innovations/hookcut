"use client";

/**
 * StageRail - a transport control.
 *
 * A row of segments with one gradient pill that slides to whichever is
 * active, the way a fader travels its groove. Used twice:
 *
 *   - as the live pipeline indicator, so the artist can always see which of
 *     the five steps their job is on
 *   - as the mock-data jump bar in development
 *
 * The sliding pill is pure CSS: every segment is an equal grid column, so
 * the pill is 1/n wide and translates by (index * 100%) of its own width.
 */

export type RailSegment = {
  key: string;
  label: string;
  /** Renders an anchor instead of a button. */
  href?: string;
};

type Props = {
  segments: RailSegment[];
  /** Which segment the pill sits on. Clamped into range. */
  activeIndex: number;
  onSelect?: (key: string, index: number) => void;
  /** warm = something is happening, cool = where you are. */
  tone?: "cool" | "warm";
  /** Small caps tag rendered before the track. */
  tag?: string;
  /** Pulses the pill - use while the step is actually running. */
  live?: boolean;
  compact?: boolean;
};

export default function StageRail({
  segments, activeIndex, onSelect, tone = "cool", tag, live = false, compact = false,
}: Props) {
  if (segments.length === 0) return null;
  const i = Math.min(Math.max(activeIndex, 0), segments.length - 1);
  const interactive = Boolean(onSelect) || segments.some((s) => s.href);

  return (
    <div className={`transport${compact ? " transport--compact" : ""}`}>
      {tag && <span className="transport__tag">{tag}</span>}

      <div
        className="transport__track"
        role={interactive ? undefined : "progressbar"}
        aria-valuemin={interactive ? undefined : 1}
        aria-valuemax={interactive ? undefined : segments.length}
        aria-valuenow={interactive ? undefined : i + 1}
        aria-valuetext={interactive ? undefined : segments[i].label}
        style={{ ["--n" as any]: segments.length, ["--i" as any]: i }}
      >
        <span className={`transport__pill transport__pill--${tone}${live ? " is-live" : ""}`}
          aria-hidden="true" />

        {segments.map((seg, index) => {
          const on = index === i;
          const content = (
            <>
              <span className="transport__dot" aria-hidden="true" />
              {seg.label}
            </>
          );

          if (seg.href) {
            return (
              <a key={seg.key} className="transport__seg" data-on={on} href={seg.href}>
                {content}
              </a>
            );
          }
          if (onSelect) {
            return (
              <button key={seg.key} type="button" className="transport__seg" data-on={on}
                aria-current={on ? "step" : undefined}
                onClick={() => onSelect(seg.key, index)}>
                {content}
              </button>
            );
          }
          return (
            <span key={seg.key} className="transport__seg" data-on={on}
              aria-current={on ? "step" : undefined}>
              {content}
            </span>
          );
        })}
      </div>
    </div>
  );
}

/* ------------------------------------------------- the real pipeline --- */

/** The five steps an artist actually moves through. */
export const PIPELINE: RailSegment[] = [
  { key: "listen", label: "Listen" },
  { key: "find", label: "Find" },
  { key: "choose", label: "Choose" },
  { key: "render", label: "Render" },
  { key: "done", label: "Done" },
];

/** Where a backend stage sits on that five-step rail. */
export function pipelineIndex(stage: string | undefined): number {
  switch (stage) {
    case "queued":
    case "transcribing": return 0;
    case "analyzing":
    case "resolving_cover_art": return 1;
    case "awaiting_review": return 2;
    case "cutting_clips": return 3;
    case "done": return 4;
    default: return 0;
  }
}
