/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Color scheme override. "white" => white/red demo theme (see globals.css). */
  readonly VITE_THEME?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
