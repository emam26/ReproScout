import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import PublicSite from "./PublicSite";

const visibleInstallationCommands = () => [...document.querySelectorAll<HTMLElement>(".steps .code")]
  .filter((panel) => !panel.hidden)
  .map((panel) => panel.querySelector("code")?.textContent ?? "");

describe("public site", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("matches the reference structure and preserves the public boundary", () => {
    render(<PublicSite />);

    expect(screen.getByRole("heading", { name: "Check the environment before you chase the result." })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Install from a source checkout." })).toBeInTheDocument();
    expect(screen.getByRole("figure", { name: "Illustrative evidence ledger" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "ReproScout compass" })).toHaveAttribute("alt", "ReproScout compass");
    expect(screen.queryByText(/Open-source Python CLI, MIT licensed/i)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "How it works" })).toHaveAttribute("href", "#how");
    expect(screen.getByRole("link", { name: "Reports" })).toHaveAttribute("href", "#outputs");
    expect(screen.getByRole("link", { name: "View on GitHub" })).toHaveAttribute(
      "href",
      "https://github.com/emam26/ReproScout",
    );
    expect(screen.getByRole("link", { name: /Docker sandboxRead before sensitive use/i })).toHaveAttribute(
      "href",
      "https://github.com/emam26/ReproScout/blob/main/docs/DOCKER_SANDBOX.md",
    );
    expect(screen.queryByRole("button", { name: /switch to (light|dark) mode/i })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/dashboard|fastapi|127\.0\.0\.1|health endpoint|private testing/i);
  });

  it("does not make runtime requests from the static public site", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<PublicSite />);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("keeps mobile navigation accessible and closes it after navigation or Escape", () => {
    render(<PublicSite />);

    const menu = screen.getByRole("button", { name: "Open menu" });
    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(menu).toHaveAttribute("aria-controls", "nav");
    fireEvent.click(menu);
    expect(menu).toHaveAttribute("aria-expanded", "true");
    expect(document.activeElement).toBe(screen.getByRole("link", { name: "How it works" }));

    fireEvent.click(screen.getByRole("link", { name: "Install" }));
    expect(menu).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(menu);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(document.activeElement).toBe(menu);
  });

  it("supports keyboard navigation for the workflow stages", () => {
    render(<PublicSite />);

    const inspect = screen.getByRole("tab", { name: /Inspect/ });
    const prepare = screen.getByRole("tab", { name: /Prepare/ });
    expect(inspect).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(inspect, { key: "ArrowRight" });

    expect(prepare).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel", { name: /Prepare/ })).toHaveTextContent(/build a bounded plan/i);
    expect(document.activeElement).toBe(prepare);
  });

  it("keeps all four installation steps visible and the default uv panel non-empty", () => {
    render(<PublicSite />);

    expect(visibleInstallationCommands()).toEqual([
      "git clone https://github.com/emam26/ReproScout.git\ncd ReproScout",
      "uv tool install --editable .",
      "reproscout doctor\nreproscout setup --dry-run\nreproscout setup",
      "reproscout preflight https://github.com/OWNER/REPOSITORY",
    ]);
    expect(document.getElementById("p-uv")).not.toBeNull();
    expect(document.getElementById("p-uv")).not.toHaveAttribute("hidden");
    expect(document.getElementById("p-uv")?.textContent).toContain("uv tool install --editable .");
  });

  it("switches installation methods, restores uv, and copies the displayed command", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    render(<PublicSite />);

    const install = screen.getByRole("region", { name: "Install from a source checkout." });
    const uvTab = within(install).getByRole("tab", { name: "uv" });
    fireEvent.keyDown(uvTab, { key: "ArrowRight" });
    expect(within(install).getByRole("tab", { name: "venv + pip" })).toHaveAttribute("aria-selected", "true");
    expect(document.getElementById("p-uv")).toHaveAttribute("hidden");
    expect(document.getElementById("p-venv")).not.toHaveAttribute("hidden");
    expect(document.getElementById("p-venv")?.textContent).toContain("python -m venv .venv");
    expect(visibleInstallationCommands()).toContain("python -m venv .venv\n# Activate .venv for your shell, then:\npython -m pip install -e .");

    fireEvent.click(within(install).getByRole("tab", { name: "uv" }));
    expect(document.getElementById("p-uv")).not.toHaveAttribute("hidden");
    expect(document.getElementById("p-uv")?.textContent).toContain("uv tool install --editable .");
    fireEvent.click(within(document.getElementById("p-uv")!).getByRole("button", { name: /copy uv/i }));

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith("uv tool install --editable .");
      expect(within(document.getElementById("p-uv")!).getByRole("button", { name: /copy uv/i })).toHaveTextContent("Copied");
    });
  });

  it("switches report tabs without leaving the page", () => {
    render(<PublicSite />);

    fireEvent.click(screen.getByRole("tab", { name: /environment-evidence\.json/i }));

    expect(screen.getByRole("tabpanel", { name: /environment-evidence\.json/i })).toHaveTextContent(/structured evidence/i);
    expect(screen.getByRole("tab", { name: /environment-evidence\.json/i })).toHaveAttribute("aria-selected", "true");
  });

  it("keeps the page content visible when IntersectionObserver is unavailable", () => {
    Reflect.deleteProperty(window, "IntersectionObserver");
    render(<PublicSite />);

    expect(screen.getByRole("heading", { name: "Three stages, one evidence trail." })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Three files you can hand to a person or an agent." })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Install from a source checkout." })).toBeVisible();
  });
});
