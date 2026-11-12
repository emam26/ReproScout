import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import PublicSite from "./PublicSite";

describe("public site", () => {
  it("explains the local-only product boundary and source installation", () => {
    render(<PublicSite />);

    expect(screen.getByRole("heading", { name: /ready to run/i })).toBeInTheDocument();
    expect(screen.getByText(/not published to PyPI yet/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /view source/i })).toHaveAttribute(
      "href",
      "https://github.com/emam26/ReproScout",
    );
    expect(screen.queryByRole("button", { name: /run preflight/i })).not.toBeInTheDocument();
  });
});
