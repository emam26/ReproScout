import { CodeBlock } from "./CodeBlock";
import {
  artifacts,
  dashboardStart,
  documentationLinks,
  features,
  quickStart,
  repositoryUrl,
  sourceInstall,
  workflow,
} from "./content";

export function HeroSection() {
  return (
    <section aria-labelledby="hero-title" className="site-hero" id="overview">
      <div className="site-hero-copy">
        <p className="site-kicker">Research environment preflight</p>
        <h1 id="hero-title">From research code to verified environments.</h1>
        <p className="site-lede">
          Inspect research repositories, prepare isolated environments, and generate
          evidence-backed reports before you invest in an experiment.
        </p>
        <div className="site-actions">
          <a className="site-button site-button-primary" href="#installation">
            Get started <span aria-hidden="true">{"\u2192"}</span>
          </a>
          <a className="site-button site-button-secondary" href={repositoryUrl} rel="noreferrer" target="_blank">
            View on GitHub <span aria-hidden="true">{"\u2197"}</span>
          </a>
        </div>
        <p className="site-boundary">
          Environment readiness is not scientific reproducibility. ReproScout does
          not automatically run target experiments, training, benchmarks, or tests.
        </p>
      </div>
      <div aria-label="Representative ReproScout command flow" className="site-terminal">
        <div className="site-terminal-chrome">
          <span>local terminal</span>
          <span aria-hidden="true">{"\u25cf \u25cf \u25cf"}</span>
        </div>
        <pre><code><span className="site-terminal-prompt">$</span> reproscout doctor{`\n`}<span className="site-terminal-note"># inspect local prerequisites without changing state</span>{`\n\n`}<span className="site-terminal-prompt">$</span> reproscout setup --dry-run{`\n`}<span className="site-terminal-note"># preview supported onboarding without installing anything</span>{`\n\n`}<span className="site-terminal-prompt">$</span> reproscout preflight https://github.com/OWNER/REPOSITORY</code></pre>
        <p>Representative supported commands. This static site does not run them.</p>
      </div>
    </section>
  );
}

export function WorkflowSection() {
  return (
    <section aria-labelledby="workflow-title" className="site-section" id="how-it-works">
      <SectionHeading
        eyebrow="How it works"
        id="workflow-title"
        title="A deliberate path from repository URL to evidence."
        description="Repository instructions are untrusted input. Deterministic checks and observed command outcomes determine the result."
      />
      <ol className="site-workflow">
        {workflow.map((step) => (
          <li key={step.number}>
            <span>{step.number}</span>
            <h3>{step.title}</h3>
            <p>{step.text}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function FeaturesSection() {
  return (
    <section aria-labelledby="features-title" className="site-section" id="features">
      <SectionHeading
        eyebrow="Capabilities"
        id="features-title"
        title="A sober control plane for research software."
        description="The default workflow stays focused on environment readiness. Experiment-capable auditing remains an explicit, separately controlled path."
      />
      <div className="site-feature-layout">
        {features.map((feature) => (
          <article className={`site-feature-card site-feature-${feature.kind}`} key={feature.number}>
            <div className="site-feature-topline">
              <span>{feature.number}</span>
              <span aria-hidden="true" className="site-feature-glyph">{"\u2197"}</span>
            </div>
            <h3>{feature.title}</h3>
            <p>{feature.text}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

export function InstallationSection() {
  return (
    <section aria-labelledby="installation-title" className="site-section" id="installation">
      <SectionHeading
        eyebrow="Installation and quick start"
        id="installation-title"
        title="Start local. Keep execution under your control."
        description="ReproScout is not published to PyPI yet. Install the CLI from a source checkout; Docker remains a separate prerequisite for sandboxed preflight."
      />
      <div className="site-install-grid">
        <CodeBlock command={sourceInstall} copyLabel="Copy source installation commands" eyebrow="Step 1" title="Install the Python CLI" />
        <article className="site-install-card">
          <p className="site-card-eyebrow">Step 2</p>
          <h3>Check the host before changing it.</h3>
          <p><code>reproscout doctor</code> is read-only. It reports local prerequisites without installing software or running a container.</p>
          <div className="site-install-detail"><strong>Python 3.11+</strong><span>Required for the CLI and current supported source installation.</span></div>
        </article>
        <article className="site-install-card">
          <p className="site-card-eyebrow">Step 3</p>
          <h3>Use guided setup only with consent.</h3>
          <p><code>reproscout setup --dry-run</code> previews onboarding. <code>reproscout setup</code> requests consent before any permitted action.</p>
          <div className="site-install-detail"><strong>Docker is separate</strong><span>Installing ReproScout does not install Docker; sandboxed execution needs a suitable local Docker runtime.</span></div>
        </article>
      </div>
      <div className="site-quick-start" id="quick-start">
        <div>
          <p className="site-kicker">Step 4</p>
          <h3>Run an environment preflight.</h3>
          <p>The default path observes permitted setup facts in Docker and writes the evidence artifacts. It does not claim a research result has been reproduced.</p>
        </div>
        <CodeBlock command={quickStart} copyLabel="Copy quick-start commands" title="Local onboarding path" />
      </div>
      <aside className="site-dashboard-note">
        <div>
          <p className="site-card-eyebrow">Optional local dashboard</p>
          <h3>Keep the API and dashboard on loopback.</h3>
          <p>Install the <code>[api]</code> extra for the local FastAPI control service and React dashboard. Vite serves the dashboard at <code>127.0.0.1:5173</code> and proxies to the local API at <code>127.0.0.1:8000</code>.</p>
        </div>
        <div className="site-dashboard-command"><code>{dashboardStart}</code></div>
        <p className="site-dashboard-health"><code>GET /api/v1/health</code> confirms only the local API process; it does not prove Docker or sandbox readiness.</p>
        <a href={`${repositoryUrl}/blob/main/docs/DASHBOARD.md`} rel="noreferrer" target="_blank">Read dashboard setup <span aria-hidden="true">{"\u2197"}</span></a>
      </aside>
    </section>
  );
}

export function EvidenceSection() {
  return (
    <section aria-labelledby="evidence-title" className="site-section" id="evidence">
      <SectionHeading
        eyebrow="Evidence and reports"
        id="evidence-title"
        title="A handoff that states what it knows — and what it does not."
        description="Every preflight writes three portable artifacts for a researcher or coding agent. The example at right is illustrative, not an audited repository."
      />
      <div className="site-evidence-layout">
        <article className="site-report-preview" aria-label="Illustrative environment report preview">
          <div className="site-report-preview-chrome">
            <span>environment-report.md</span>
            <span>illustrative</span>
          </div>
          <div className="site-report-preview-body">
            <p className="site-preview-status">Environment facts</p>
            <dl>
              <div><dt>Repository revision</dt><dd>Pinned in the report</dd></div>
              <div><dt>Permitted checks</dt><dd>Recorded with exit outcomes</dd></div>
              <div><dt>Unresolved setup</dt><dd>Preserved for follow-up</dd></div>
            </dl>
            <p className="site-preview-boundary">Not verified: model training, benchmarks, experiments, or scientific claims.</p>
          </div>
        </article>
        <div className="site-artifact-list">
          {artifacts.map((artifact) => (
            <article className="site-artifact-card" key={artifact.name}>
              <span>{artifact.label}</span>
              <h3>{artifact.name}</h3>
              <p>{artifact.text}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

export function DocumentationSection() {
  return (
    <section aria-labelledby="documentation-title" className="site-section" id="documentation">
      <SectionHeading
        eyebrow="Documentation"
        id="documentation-title"
        title="Read the contracts. Keep control local."
        description="The GitHub Pages site is static frontend content deployed through GitHub Actions. It has no hosted API, Docker daemon, or public audit execution."
      />
      <div className="site-doc-grid">
        {documentationLinks.map((link) => (
          <a className="site-doc-link" href={link.href} key={link.label} rel="noreferrer" target="_blank">
            <span>{link.label}</span>
            <p>{link.text}</p>
            <strong>Read on GitHub <b aria-hidden="true">{"\u2197"}</b></strong>
          </a>
        ))}
      </div>
      <div className="site-trust-grid">
        <div><strong>Untrusted by default</strong><span>Repository files and setup instructions never authorize arbitrary host execution.</span></div>
        <div><strong>Evidence over configuration</strong><span>Installed packages and configured restrictions alone do not prove readiness.</span></div>
        <div><strong>Static by design</strong><span>Run audits and Docker commands only from a locally installed ReproScout application.</span></div>
      </div>
    </section>
  );
}

export function OpenSourceSection() {
  return (
    <section aria-labelledby="open-source-title" className="site-open-source">
      <div>
        <p className="site-kicker">Open source</p>
        <h2 id="open-source-title">Help make environment verification more trustworthy.</h2>
        <p>Contributions are welcome within ReproScout’s documented scope and safety model.</p>
      </div>
      <div className="site-open-source-actions">
        <a className="site-button site-button-primary" href={`${repositoryUrl}/blob/main/CONTRIBUTING.md`} rel="noreferrer" target="_blank">Contribution guide <span aria-hidden="true">{"\u2197"}</span></a>
        <a className="site-button site-button-secondary" href={`${repositoryUrl}/issues`} rel="noreferrer" target="_blank">Open an issue <span aria-hidden="true">{"\u2197"}</span></a>
      </div>
    </section>
  );
}

type SectionHeadingProps = {
  description: string;
  eyebrow: string;
  id: string;
  title: string;
};

function SectionHeading({ description, eyebrow, id, title }: SectionHeadingProps) {
  return (
    <div className="site-section-heading">
      <p className="site-kicker">{eyebrow}</p>
      <h2 id={id}>{title}</h2>
      <p>{description}</p>
    </div>
  );
}
