import { useEffect, useState } from "react";

import { PublicFooter } from "./public/PublicFooter";
import { PublicHeader } from "./public/PublicHeader";
import {
  DocumentationSection,
  EvidenceSection,
  FeaturesSection,
  HeroSection,
  InstallationSection,
  OpenSourceSection,
  WorkflowSection,
} from "./public/PublicSections";
import "./public/public.css";

export default function PublicSite() {
  const [activeSection, setActiveSection] = useState("overview");

  useEffect(() => {
    if (!("IntersectionObserver" in window)) return undefined;
    const sections = ["overview", "features", "installation", "documentation"]
      .map((id) => document.getElementById(id))
      .filter((section): section is HTMLElement => section !== null);
    const observer = new IntersectionObserver(
      (entries) => {
        const activeEntry = entries.find((entry) => entry.isIntersecting);
        if (activeEntry?.target.id) setActiveSection(activeEntry.target.id);
      },
      { rootMargin: "-25% 0px -65%", threshold: 0.01 },
    );
    sections.forEach((section) => observer.observe(section));
    return () => observer.disconnect();
  }, []);

  return (
    <div className="site-shell">
      <a className="site-skip-link" href="#main-content">Skip to content</a>
      <PublicHeader activeSection={activeSection} onNavigate={setActiveSection} />
      <main id="main-content">
        <HeroSection />
        <WorkflowSection />
        <FeaturesSection />
        <InstallationSection />
        <EvidenceSection />
        <DocumentationSection />
        <OpenSourceSection />
      </main>
      <PublicFooter />
    </div>
  );
}
