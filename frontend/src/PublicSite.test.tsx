import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import PublicSite from "./PublicSite";

describe("public site", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("keeps the static public boundary, source installation, and documentation", () => {
    render(<PublicSite />);

    expect(screen.getByRole("heading", { name: /from research code to verified environments/i })).toBeInTheDocument();
    expect(screen.getByText(/not published to PyPI yet/i)).toBeInTheDocument();
    expect(screen.getByText(/Docker remains a separate prerequisite/i)).toBeInTheDocument();
    expect(screen.getByText(/static frontend content deployed through GitHub Actions/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /view on github/i })).toHaveAttribute(
      "href",
      "https://github.com/emam26/ReproScout",
    );
    expect(screen.getByRole("link", { name: /Security and Docker/i })).toHaveAttribute(
      "href",
      "https://github.com/emam26/ReproScout/blob/main/docs/DOCKER_SANDBOX.md",
    );
    expect(screen.queryByText(/The practical problem/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Dependency drift/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /switch to (light|dark) mode/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /run preflight/i })).not.toBeInTheDocument();
  });

  it("does not make API requests from the static public site", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<PublicSite />);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("keeps mobile navigation accessible and closes it after section navigation", () => {
    render(<PublicSite />);

    const menu = screen.getByRole("button", { name: /open navigation/i });
    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(menu).toHaveAttribute("aria-controls", "site-navigation");
    fireEvent.click(menu);
    expect(menu).toHaveAttribute("aria-expanded", "true");
    const installation = screen.getByRole("link", { name: "Installation" });
    expect(installation).toHaveAttribute("href", "#installation");
    fireEvent.click(installation);
    expect(menu).toHaveAttribute("aria-expanded", "false");
  });

  it("closes mobile navigation with Escape", () => {
    render(<PublicSite />);

    const menu = screen.getByRole("button", { name: /open navigation/i });
    fireEvent.click(menu);
    fireEvent.keyDown(document, { key: "Escape" });

    expect(menu).toHaveAttribute("aria-expanded", "false");
  });

  it("copies the supported local onboarding commands", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    render(<PublicSite />);

    fireEvent.click(screen.getByRole("button", { name: /copy quick-start commands/i }));

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith(expect.stringContaining("reproscout setup --dry-run"));
      expect(screen.getByRole("button", { name: /copy quick-start commands/i })).toHaveTextContent("Copied");
    });
  });
});
