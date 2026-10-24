import { describe, it, expect, beforeEach } from "vitest";
import { applyTheme, normalizeTheme, resolveInitialTheme, selectTheme, THEME_STORAGE_KEY } from "./theme";

describe("normalizeTheme", () => {
  it("accepts the two schemes and the server's 'light' spelling", () => {
    expect(normalizeTheme("dark")).toBe("dark");
    expect(normalizeTheme("white")).toBe("white");
    expect(normalizeTheme("light")).toBe("white");
  });
  it("rejects anything else", () => {
    expect(normalizeTheme("")).toBeNull();
    expect(normalizeTheme("blue")).toBeNull();
    expect(normalizeTheme(null)).toBeNull();
    expect(normalizeTheme(undefined)).toBeNull();
  });
});

describe("resolveInitialTheme precedence", () => {
  it("defaults to dark", () => {
    expect(resolveInitialTheme({ search: "", stored: null, htmlAttr: null })).toBe("dark");
  });
  it("URL param beats everything", () => {
    expect(resolveInitialTheme({ search: "?theme=white", stored: "dark", htmlAttr: "dark" })).toBe("white");
    expect(resolveInitialTheme({ search: "?x=1&theme=dark", stored: "white", htmlAttr: "white" })).toBe("dark");
  });
  it("stored user choice beats the server stamp and build default", () => {
    expect(resolveInitialTheme({ search: "", stored: "dark", htmlAttr: "white", buildDefault: "white" })).toBe("dark");
    expect(resolveInitialTheme({ search: "", stored: "white", htmlAttr: null })).toBe("white");
  });
  it("server-stamped data-theme beats the build default", () => {
    expect(resolveInitialTheme({ search: "", stored: null, htmlAttr: "white", buildDefault: "dark" })).toBe("white");
    expect(resolveInitialTheme({ search: "", stored: null, htmlAttr: null, buildDefault: "white" })).toBe("white");
  });
  it("ignores garbage at every level", () => {
    expect(resolveInitialTheme({ search: "?theme=neon", stored: "nope", htmlAttr: "x", buildDefault: "y" })).toBe("dark");
  });
});

describe("applyTheme / selectTheme (DOM)", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
    window.history.replaceState(null, "", "/");
  });
  it("applyTheme sets data-theme without persisting", () => {
    applyTheme("white");
    expect(document.documentElement.getAttribute("data-theme")).toBe("white");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
  });
  it("selectTheme applies, persists and strips a stale ?theme= param", () => {
    window.history.replaceState(null, "", "/?theme=white&keep=1");
    selectTheme("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(window.location.search).toBe("?keep=1");
  });
});
