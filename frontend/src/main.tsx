import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import PublicSite from "./PublicSite";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {import.meta.env.MODE === "public" ? <PublicSite /> : <App />}
  </StrictMode>,
);
