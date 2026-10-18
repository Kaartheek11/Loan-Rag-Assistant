import "./styles/index.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";

// UI-T6: never restore a previous conversation from the back-forward cache. A page shown
// from bfcache is reloaded, so the app re-checks the session (and finds none after sign-out).
window.addEventListener("pageshow", (event) => {
  if (event.persisted) window.location.reload();
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
