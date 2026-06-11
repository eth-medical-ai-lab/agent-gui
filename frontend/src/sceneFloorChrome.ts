/** Scenes with a light floor/backdrop — chrome labels use dark text. */
export const LIGHT_BACKDROP_SCENES = new Set(["hospital", "lab"]);

export function sceneUsesLightBackdrop(scene?: string | null): boolean {
  return LIGHT_BACKDROP_SCENES.has(scene ?? "default");
}

/** Small overlay UI on the team floor (manager, bed, scene picker, etc.). */
export interface SceneFloorChrome {
  label: string;
  labelDim: string;
  labelHover: string;
  labelAccent: string;
  labelWarn: string;
  controlBg: string;
  controlBorder: string;
  controlColor: string;
  inputBg: string;
  inputBorder: string;
  inputColor: string;
}

export function sceneFloorChrome(scene?: string | null): SceneFloorChrome {
  if (sceneUsesLightBackdrop(scene)) {
    return {
      label: "#1e1e2e",
      labelDim: "#4a4a5e",
      labelHover: "#0f0f18",
      labelAccent: "#087a9a",
      labelWarn: "#b45309",
      controlBg: "rgba(0,0,0,0.06)",
      controlBorder: "rgba(0,0,0,0.16)",
      controlColor: "#4a4a5e",
      inputBg: "rgba(255,255,255,0.75)",
      inputBorder: "rgba(0,0,0,0.2)",
      inputColor: "#1e1e2e",
    };
  }
  return {
    label: "var(--text)",
    labelDim: "var(--text-dim)",
    labelHover: "var(--text)",
    labelAccent: "var(--accent2)",
    labelWarn: "rgba(255,180,80,0.95)",
    controlBg: "rgba(255,255,255,0.06)",
    controlBorder: "rgba(255,255,255,0.12)",
    controlColor: "var(--text-dim)",
    inputBg: "rgba(0,0,0,0.35)",
    inputBorder: "rgba(255,255,255,0.25)",
    inputColor: "var(--text)",
  };
}
