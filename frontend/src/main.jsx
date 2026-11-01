import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import { I18nProvider } from "./i18n/index.jsx";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <I18nProvider>
      <App />
    </I18nProvider>
  </React.StrictMode>
);

// Installable app (PWA): the service worker caches the app shell and shows an offline message for the API.
if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
