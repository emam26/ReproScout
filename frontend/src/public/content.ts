export const repositoryUrl = "https://github.com/emam26/ReproScout";

export const navigation = [
  { id: "overview", label: "Overview" },
  { id: "features", label: "Features" },
  { id: "installation", label: "Installation" },
  { id: "documentation", label: "Documentation" },
] as const;

export const sourceInstall = [
  "git clone https://github.com/emam26/ReproScout.git",
  "cd ReproScout",
  "uv tool install --editable .",
].join("\n");

export const quickStart = [
  "reproscout doctor",
  "reproscout setup --dry-run",
  "reproscout setup",
  "reproscout preflight https://github.com/OWNER/REPOSITORY",
].join("\n");

export const dashboardStart = [
  'uv tool install --reinstall --editable ".[api]"',
  "reproscout serve",
  "# In a second terminal: cd frontend && npm run dev",
].join("\n");

export const workflow = [
  {
    number: "01",
    title: "Inspect",
    text: "Pin the public revision, then read declared Python, package, system-tool, and setup requirements.",
  },
  {
    number: "02",
    title: "Prepare",
    text: "Build a bounded setup-only plan. Repository instructions never authorize arbitrary execution.",
  },
  {
    number: "03",
    title: "Verify",
    text: "Run permitted checks in Docker and record only observed environment facts and unresolved risks.",
  },
] as const;

export const features = [
  {
    number: "01",
    title: "Intelligent repository inspection",
    text: "Discover dependency declarations, runtime constraints, prerequisite tools, environment variables, and deferred assets from a pinned checkout.",
    kind: "wide",
  },
  {
    number: "02",
    title: "Isolated environment preparation",
    text: "Prepare permitted setup steps in the Docker sandbox with non-root execution and resource bounds.",
    kind: "standard",
  },
  {
    number: "03",
    title: "Controlled diagnosis and repair",
    text: "The separate, explicit audit path can diagnose failures and evaluate bounded reversible repairs. Default preflight does neither.",
    kind: "standard",
  },
  {
    number: "04",
    title: "Evidence-backed reports",
    text: "Hand researchers and coding agents verified facts, executed commands, unresolved problems, and safe next steps.",
    kind: "standard",
  },
] as const;

export const artifacts = [
  {
    name: "environment-report.md",
    label: "Researcher brief",
    text: "A concise explanation of observed readiness, blockers, and safe next steps.",
  },
  {
    name: "environment-evidence.json",
    label: "Structured record",
    text: "Machine-readable commands, outcomes, checks, configured limits, and unresolved findings.",
  },
  {
    name: "agent-handoff.md",
    label: "Agent context",
    text: "The pinned revision, verified environment facts, unresolved issues, and bounded follow-up work.",
  },
] as const;

export const documentationLinks = [
  {
    href: `${repositoryUrl}/blob/main/docs/CLI.md`,
    label: "CLI reference",
    text: "Commands, options, profiles, and consent-aware onboarding.",
  },
  {
    href: `${repositoryUrl}/blob/main/docs/ARCHITECTURE.md`,
    label: "Architecture",
    text: "The control plane, trust boundaries, and verifier-led workflow.",
  },
  {
    href: `${repositoryUrl}/blob/main/docs/DOCKER_SANDBOX.md`,
    label: "Security and Docker",
    text: "Sandbox constraints, observed readiness, and security limitations.",
  },
  {
    href: `${repositoryUrl}/blob/main/docs/EVALUATION_SET.md`,
    label: "Evaluation and testing",
    text: "Controlled fixture coverage and the evaluation contract.",
  },
  {
    href: `${repositoryUrl}/blob/main/CONTRIBUTING.md`,
    label: "Contributing guide",
    text: "How to contribute within the project’s bounded safety model.",
  },
] as const;
