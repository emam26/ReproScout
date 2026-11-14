import { useEffect, useRef, useState } from "react";
import logo from "../../reproscout-logo.png";

import { navigation, repositoryUrl } from "./content";

type PublicHeaderProps = {
  activeSection: string;
  onNavigate: (section: string) => void;
};

export function PublicHeader({ activeSection, onNavigate }: PublicHeaderProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const firstLinkRef = useRef<HTMLAnchorElement>(null);
  const headerRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (menuOpen) firstLinkRef.current?.focus();
  }, [menuOpen]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && menuOpen) {
        setMenuOpen(false);
        buttonRef.current?.focus();
      }
    };
    const handlePointerDown = (event: PointerEvent) => {
      if (menuOpen && event.target instanceof Node && !headerRef.current?.contains(event.target)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("pointerdown", handlePointerDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [menuOpen]);

  const navigate = (section: string) => {
    onNavigate(section);
    setMenuOpen(false);
  };

  return (
    <header className="top" ref={headerRef}>
      <div className="wrap">
        <a aria-label="ReproScout home" className="brand" href="#top" onClick={() => navigate("top")}>
          <img alt="ReproScout compass" className="brand-logo" height="36" src={logo} width="36" />
          ReproScout
        </a>
        <button
          aria-controls="nav"
          aria-expanded={menuOpen}
          aria-label={menuOpen ? "Close menu" : "Open menu"}
          className="menu-btn"
          onClick={() => setMenuOpen((open) => !open)}
          ref={buttonRef}
          type="button"
        >
          <svg aria-hidden="true" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="2" viewBox="0 0 24 24">
            {menuOpen ? <path d="M6 6l12 12M18 6L6 18" /> : <path d="M4 7h16M4 12h16M4 17h16" />}
          </svg>
        </button>
        <nav aria-label="Primary" className={menuOpen ? "open" : undefined} id="nav">
          <ul>
            {navigation.map((item, index) => (
              <li key={item.id}>
                <a
                  aria-current={activeSection === item.id ? "true" : undefined}
                  className="nl"
                  href={`#${item.id}`}
                  onClick={() => navigate(item.id)}
                  ref={index === 0 ? firstLinkRef : undefined}
                >
                  {item.label}
                </a>
              </li>
            ))}
            <li>
              <a className="btn btn-quiet" href={repositoryUrl} onClick={() => setMenuOpen(false)} rel="noreferrer" style={{ minHeight: "2.5rem", padding: ".4rem 1rem" }} target="_blank">
                GitHub
              </a>
            </li>
          </ul>
        </nav>
      </div>
    </header>
  );
}
