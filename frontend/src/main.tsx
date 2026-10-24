import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import SingleTabGate from "./components/SingleTabGate";
import { initTheme } from "./theme";

// Color scheme (dark blue / white & red): resolved from ?theme=, the user's saved
// pick (⚙ → Color scheme), the server's --front-end stamp or VITE_THEME, then
// applied as <html data-theme> before first paint. Palette lives in globals.css.
initTheme();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <SingleTabGate>
      <App />
    </SingleTabGate>
  </React.StrictMode>
);
