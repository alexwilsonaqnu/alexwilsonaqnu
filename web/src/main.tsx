import React from "react";
import { createRoot } from "react-dom/client";
import { AdsThemeProvider } from "@ads/react";
import registerIcons from "@ads/icons";
import iconSprite from "@ads/icons/images/icon-sprite.svg";
import App from "./App";
import "./index.scss";

// Register the ADS icon sprite as the <svg-icon> web component (per ADS install).
registerIcons("svg-icon", iconSprite)();

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AdsThemeProvider>
      <App />
    </AdsThemeProvider>
  </React.StrictMode>,
);
