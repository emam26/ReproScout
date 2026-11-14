import { useEffect, useState } from "react";

import logo from "../../reproscout-logo.png";

import { navigation, repositoryUrl } from "./content";

type PublicHeaderProps = {
  activeSection: string;
  onNavigate: (section: string) => void;
};

export function PublicHeader({ activeSection, onNavigate }: PublicHeaderProps) {
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, []);

  const navigate = (section: string) => {
    onNavigate(section);
    setMenuOpen(false);
  };

  return (
    <header className="site-header">
      <div className="site-nav">
        <a aria-label="ReproScout home" className="site-brand" href="#overview" onClick={() => navigate("overview")}>
          <img alt="ReproScout compass" height="36" src={logo} width="36" />
          <span>ReproScout</span>
        </a>
        <button
          aria-controls="site-navigation"
          aria-expanded={menuOpen}
          className="site-menu-toggle"
          onClick={() => setMenuOpen((open) => !open)}
          type="button"
        >
          <span aria-hidden="true">{menuOpen ? "\u00d7" : "\u2630"}</span>
          <span className="site-sr-only">{menuOpen ? "Close navigation" : "Open navigation"}</span>
        </button>
        <nav aria-label="Primary navigation" className={`site-nav-links ${menuOpen ? "is-open" : ""}`} id="site-navigation">
          {navigation.map((item) => (
            <a
              aria-current={activeSection === item.id ? "location" : undefined}
              href={`#${item.id}`}
              key={item.id}
              onClick={() => navigate(item.id)}
            >
              {item.label}
            </a>
          ))}
          <a href={repositoryUrl} onClick={() => setMenuOpen(false)} rel="noreferrer" target="_blank">
            GitHub <span aria-hidden="true">{"\u2197"}</span>
          </a>
        </nav>
      </div>
    </header>
  );
}
