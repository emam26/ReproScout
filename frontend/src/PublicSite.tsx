import { useEffect, useState } from "react";
import { PublicFooter } from "./public/PublicFooter";
import { PublicHeader } from "./public/PublicHeader";
import {
  DocumentationSection,
  HeroSection,
  InstallationSection,
  ReportsSection,
  ScopeSection,
  WorkflowSection,
} from "./public/PublicSections";
import "./public/public.css";

export default function PublicSite() {
  const [activeSection, setActiveSection] = useState("top");

  useEffect(() => {
    if (!("IntersectionObserver" in window)) return undefined;
    const sections = ["top", "how", "install", "outputs", "scope", "docs"]
      .map((id) => document.getElementById(id))
      .filter((section): section is HTMLElement => section !== null);
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) setActiveSection(entry.target.id);
        });
      },
      { rootMargin: "-30% 0px -60%" },
    );
    sections.forEach((section) => observer.observe(section));
    return () => observer.disconnect();
  }, []);

  return (
    <div className="site-shell">
      <a className="skip" href="#main">Skip to content</a>
      <PublicHeader activeSection={activeSection} onNavigate={setActiveSection} />
      <main id="main">
        <HeroSection />
        <WorkflowSection />
        <InstallationSection />
        <ReportsSection />
        <ScopeSection />
        <DocumentationSection />
      </main>
      <PublicFooter />
    </div>
  );
}
