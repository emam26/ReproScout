import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

const application = import.meta.env.MODE === "public"
  ? import("./PublicSite")
  : import("./App");

void application.then(({ default: Application }) => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <Application />
    </StrictMode>,
  );
});
