import "./styles.css";

const repositoryUrl = "https://github.com/emam26/ReproScout";

export default function PublicSite() {
  return (
    <main className="public-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <header className="public-nav">
        <a className="brand" href="#top" aria-label="ReproScout home">ReproScout</a>
        <nav aria-label="Primary navigation">
          <a href="#problem">Why preflight</a>
          <a href="#how-it-works">How it works</a>
          <a href="#features">Features</a>
          <a href="#installation">Install</a>
          <a href={repositoryUrl}>GitHub</a>
        </nav>
      </header>

      <div id="top" className="public-hero" />
      <section id="main-content" className="public-hero-content" aria-labelledby="hero-title">
        <p className="eyebrow">RESEARCH ENVIRONMENT PREFLIGHT</p>
        <h1 id="hero-title">Know whether a research repository is ready to run.</h1>
        <p className="public-lede">
          ReproScout inspects a pinned GitHub repository, checks its environment in a restricted sandbox, and produces evidence a researcher or coding agent can trust.
        </p>
        <div className="public-actions">
          <a className="public-button primary" href="#installation">Install locally</a>
          <a className="public-button secondary" href={repositoryUrl}>View source</a>
        </div>
        <p className="public-note">Environment preparation is the v1 boundary. ReproScout does not claim that a scientific result was reproduced.</p>
      </section>

      <section id="problem" className="public-section problem-section" aria-labelledby="problem-title">
        <p className="eyebrow">THE PRACTICAL PROBLEM</p>
        <h2 id="problem-title">Published code often arrives without a runnable environment.</h2>
        <div className="problem-grid">
          <p>Python constraints, system tools, package pins, private assets, and undocumented environment variables can block a clean setup before any scientific claim can be assessed.</p>
          <p>ReproScout makes those constraints visible, records what was actually checked, and separates verified facts from unresolved risks.</p>
        </div>
      </section>

      <section id="how-it-works" className="public-section" aria-labelledby="how-title">
        <p className="eyebrow">A TRUSTWORTHY PIPELINE</p>
        <h2 id="how-title">From repository URL to an actionable handoff.</h2>
        <div className="pipeline-grid">
          <PipelineStep number="01" title="Pin" text="Clone the public repository and record the exact source commit." />
          <PipelineStep number="02" title="Inspect" text="Detect Python constraints, packages, tools, assets, and unsafe instructions." />
          <PipelineStep number="03" title="Verify" text="Run only permitted setup and lightweight checks inside Docker." />
          <PipelineStep number="04" title="Handoff" text="Separate verified facts, unresolved problems, and safe next steps." />
        </div>
      </section>

      <section id="features" className="public-section feature-section" aria-labelledby="features-title">
        <div>
          <p className="eyebrow">BUILT FOR RESEARCH SOFTWARE</p>
          <h2 id="features-title">Evidence before optimism.</h2>
        </div>
        <div className="feature-grid">
          <Feature title="Requirement detection" text="Find dependency files, Python constraints, system tools, environment variables, and asset indicators." />
          <Feature title="Restricted checks" text="Use the existing Docker sandbox with non-root execution, resource limits, and network isolation." />
          <Feature title="Honest readiness" text="Distinguish READY from partial, incompatible, missing, failed, unsafe, and unverified states." />
          <Feature title="Agent handoff" text="Give Claude, Codex, or another coding agent a pinned commit and bounded evidence without trusting repository instructions." />
        </div>
      </section>

      <section id="installation" className="public-section install-section" aria-labelledby="install-title">
        <p className="eyebrow">START LOCALLY</p>
        <h2 id="install-title">Run the control surface on your machine.</h2>
        <p>ReproScout uses a local Python CLI, FastAPI control API, and Docker sandbox. The package is not published to PyPI yet.</p>
        <pre><code>{"git clone https://github.com/emam26/ReproScout.git\ncd ReproScout\npython -m pip install -e \".[api]\"\nreproscout doctor\nreproscout preflight https://github.com/user/project"}</code></pre>
        <p className="public-note">Native Windows and macOS verification and public package publication remain pending. See the repository documentation for the current support matrix.</p>
        <a className="text-link" href={`${repositoryUrl}/blob/main/docs/CLI.md`}>Read the CLI guide</a>
      </section>

      <section className="public-section demo-section" aria-labelledby="demo-title">
        <p className="eyebrow">ILLUSTRATIVE OUTPUT</p>
        <h2 id="demo-title">A result that states its limits.</h2>
        <div className="example-card">
          <div><span>Status</span><strong>PARTIAL</strong></div>
          <p>Declared dependencies were inspected, but a required system executable was not verified in the isolated environment.</p>
          <small>This is an illustrative example, not a public benchmark result.</small>
        </div>
      </section>

      <section className="public-section status-section" aria-labelledby="status-title">
        <p className="eyebrow">PROJECT STATUS</p>
        <h2 id="status-title">Local, environment-only, and evidence-led.</h2>
        <div className="feature-grid">
          <Feature title="Current verified path" text="Windows-hosted WSL2 with Docker is the currently verified runtime path." />
          <Feature title="Platform limits" text="Native Windows and macOS validation remain pending." />
          <Feature title="Release status" text="Public package publication and real public-repository evaluation remain pending." />
          <Feature title="Scope boundary" text="Preflight verifies permitted environment checks; it does not reproduce scientific results." />
        </div>
      </section>

      <footer className="public-footer">
        <span>ReproScout v1 · environment preflight first</span>
        <span><a href={`${repositoryUrl}/blob/main/docs/PROJECT_SPEC.md`}>Documentation</a> · <a href={repositoryUrl}>GitHub</a></span>
      </footer>
    </main>
  );
}

function PipelineStep({ number, title, text }: { number: string; title: string; text: string }) {
  return <article className="pipeline-step"><span>{number}</span><h3>{title}</h3><p>{text}</p></article>;
}

function Feature({ title, text }: { title: string; text: string }) {
  return <article className="feature-card"><h3>{title}</h3><p>{text}</p></article>;
}
