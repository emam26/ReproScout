# Security policy

ReproScout executes untrusted repository workflows. It is designed with Docker
as defense in depth: target code is not executed on the host, credentials and
the Docker socket are not mounted, containers are non-privileged with dropped
capabilities and `no-new-privileges`, workspaces and resources are bounded, and
network access is denied by default unless the bounded plan requires it.

These controls are not a perfect hostile-code boundary. Do not expose the
Docker daemon socket, run the local API on the public Internet, or use the tool
with highly sensitive host data. The API is a local development/control API and
is not a hardened multi-tenant service.

## Secrets

Provider credentials belong only in environment variables. Never commit `.env`,
API keys, Git credentials, SSH keys, cookies, or private datasets. ReproScout
redacts bounded command/log evidence and rejects sensitive persisted mappings,
but users should still review run artifacts before sharing them.

## Reporting a vulnerability

Do not include exploitable details or credentials in a public issue. Until a
dedicated private security contact is documented, contact the repository
maintainers through a private channel associated with the GitHub repository and
include a concise impact description, affected commit, safe reproduction, and
whether secrets or target data were exposed. Do not test against unrelated
Docker resources or public infrastructure.
