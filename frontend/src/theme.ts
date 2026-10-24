/**
 * Color scheme ("theme") for the whole office — dark blue (default) or the
 * white/red demo scheme. The palette itself is pure CSS: globals.css defines
 * the dark tokens on :root and overrides them under :root[data-theme="white"],
 * so switching is just flipping the data-theme attribute on <html>.
 *
 * Resolution order on load (first hit wins):
 *   1. ?theme=dark|white URL param        — explicit per-load override
 *   2. localStorage "hermes-theme"        — what the user last picked in the GUI
 *   3. <html data-theme> already present  — stamped by the server (--front-end light)
 *   4. VITE_THEME build-time default      — `npm run dev:white`
 *   5. dark
 */
import { useCallback, useEffect, useState } from "react";

export type ThemeId = "dark" | "white";

export const THEMES: { id: ThemeId; name: string; icon: string; hint: string }[] = [
  { id: "dark",  name: "Dark blue",   icon: "🌙", hint: "Navy office, teal/pink accents." },
  { id: "white", name: "White & red", icon: "☀️", hint: "White office, brick-red accents." },
];

export const THEME_STORAGE_KEY = "hermes-theme";
const CHANGE_EVENT = "hermes-theme-change";

export function normalizeTheme(value: unknown): ThemeId | null {
  if (value === "dark" || value === "white") return value;
  if (value === "light") return "white"; // the server flag is spelled --front-end light
  return null;
}

export function resolveInitialTheme(src: {
  search: string;
  stored: string | null;
  htmlAttr: string | null;
  buildDefault?: string | null;
}): ThemeId {
  return (
    normalizeTheme(new URLSearchParams(src.search).get("theme")) ??
    normalizeTheme(src.stored) ??
    normalizeTheme(src.htmlAttr) ??
    normalizeTheme(src.buildDefault) ??
    "dark"
  );
}

export function readStoredTheme(): string | null {
  try { return localStorage.getItem(THEME_STORAGE_KEY); } catch { return null; }
}

export function currentTheme(): ThemeId {
  if (typeof document === "undefined") return "dark";
  return normalizeTheme(document.documentElement.dataset.theme) ?? "dark";
}

/** Flip the <html data-theme> attribute (what the CSS keys on) and notify hooks. */
export function applyTheme(theme: ThemeId): void {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("data-theme", theme);
  try { window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: theme })); } catch { /* non-DOM */ }
}

/** Boot-time entry point: resolve from URL/storage/server/build and apply. */
export function initTheme(): ThemeId {
  const theme = resolveInitialTheme({
    search: typeof window !== "undefined" ? window.location.search : "",
    stored: readStoredTheme(),
    htmlAttr: typeof document !== "undefined" ? (document.documentElement.dataset.theme ?? null) : null,
    buildDefault: import.meta.env.VITE_THEME ?? null,
  });
  applyTheme(theme);
  return theme;
}

/** User picked a scheme in the GUI: apply, persist, and drop a stale ?theme= so a
 *  reload doesn't silently revert the choice. */
export function selectTheme(theme: ThemeId): void {
  applyTheme(theme);
  try { localStorage.setItem(THEME_STORAGE_KEY, theme); } catch { /* private mode */ }
  try {
    const url = new URL(window.location.href);
    if (url.searchParams.has("theme")) {
      url.searchParams.delete("theme");
      window.history.replaceState(window.history.state, "", url.toString());
    }
  } catch { /* non-DOM */ }
}

/** React binding: the current theme + a setter that persists the user's pick. */
export function useTheme(): [ThemeId, (t: ThemeId) => void] {
  const [theme, setThemeState] = useState<ThemeId>(() => currentTheme());
  useEffect(() => {
    const onChange = (e: Event) => {
      const t = normalizeTheme((e as CustomEvent).detail);
      setThemeState(t ?? currentTheme());
    };
    window.addEventListener(CHANGE_EVENT, onChange);
    return () => window.removeEventListener(CHANGE_EVENT, onChange);
  }, []);
  const setTheme = useCallback((t: ThemeId) => { selectTheme(t); }, []);
  return [theme, setTheme];
}
