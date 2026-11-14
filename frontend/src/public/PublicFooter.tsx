import logo from "../../reproscout-logo.png";

import { repositoryUrl } from "./content";

export function PublicFooter() {
  return (
    <footer className="site-footer">
      <div className="site-footer-brand">
        <img alt="" aria-hidden="true" height="32" src={logo} width="32" />
        <div>
          <strong>ReproScout</strong>
          <span>Verify the environment before reproducing the research.</span>
        </div>
      </div>
      <div className="site-footer-links">
        <a href="#documentation">Documentation</a>
        <a href={`${repositoryUrl}/blob/main/LICENSE`} rel="noreferrer" target="_blank">MIT License</a>
        <a href={repositoryUrl} rel="noreferrer" target="_blank">GitHub</a>
      </div>
    </footer>
  );
}
