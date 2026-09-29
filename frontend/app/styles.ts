import type { CSSProperties } from "react";

// Shared style constants used across page.tsx and other components
// (e.g. CaptionTimelineEditor.tsx). Deliberately NOT exported from
// page.tsx itself - Next.js's app-router type-checking rejects arbitrary
// named exports from a route file (only a specific allowlist like
// `metadata` is permitted), so a shared constant needs its own neutral
// module regardless of which components use it.

export const inputStyle: CSSProperties = {
  display: "block",
  width: "100%",
  padding: "10px 12px",
  marginTop: 6,
  background: "var(--surface-2)",
  border: "1px solid var(--border)",
  borderRadius: 10,
  color: "var(--paper)",
  fontSize: 14,
  boxSizing: "border-box",
};

export const buttonStyle: CSSProperties = {
  padding: "12px 20px",
  background: "var(--gradient-signature)",
  border: "none",
  borderRadius: 10,
  color: "#0c0b12",
  fontWeight: 700,
  fontFamily: "var(--font-display)",
  cursor: "pointer",
  fontSize: 15,
};

export const secondaryButtonStyle: CSSProperties = {
  padding: "12px 20px",
  background: "transparent",
  border: "1px solid var(--border)",
  borderRadius: 10,
  color: "var(--paper)",
  fontWeight: 600,
  fontFamily: "var(--font-body)",
  cursor: "pointer",
  fontSize: 15,
};

export const labelStyle: CSSProperties = {
  fontSize: 11,
  textTransform: "uppercase",
  letterSpacing: 0.6,
  color: "var(--muted)",
  marginBottom: 4,
  fontWeight: 600,
};
