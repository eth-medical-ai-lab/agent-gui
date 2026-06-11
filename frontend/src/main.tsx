import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import SingleTabGate from "./components/SingleTabGate";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <SingleTabGate>
      <App />
    </SingleTabGate>
  </React.StrictMode>
);
