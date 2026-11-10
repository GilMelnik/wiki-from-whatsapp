import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "@shell";
import { TOOLS } from "./tools/index.js";
import "./index.css";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App tools={TOOLS} />
  </StrictMode>
);
