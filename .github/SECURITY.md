# Security policy

## Reporting a vulnerability

Do not open a public issue, pull request, or discussion for a security
problem. Report it privately through
[GitHub private vulnerability reporting](https://github.com/bella-1337/effective-spoon/security/advisories/new)
with:

- the affected file, service, or release tag;
- the steps to reproduce, or a proof of concept that uses placeholder
  credentials only;
- the impact you expect, for example credential exposure, privilege
  escalation, or data leaving the stack.

If a live credential was exposed, rotate it first and say so in the report;
rewriting Git history alone does not revoke it.

## Supported versions

Only the latest release and the default branch (`master`) receive fixes. A fix
ships as a new patch release; published release tags are never moved.

| Version | Supported |
| --- | --- |
| Latest `vMAJOR.MINOR.PATCH` release | Yes |
| `master` | Yes |
| Older releases | No; upgrade with `docs/upgrades-and-rollback.md` |

## Scope

In scope: this repository's Compose files, configuration, scripts, locally
built images, CI/CD workflows, and the published release bundle.

Out of scope: vulnerabilities in upstream images or providers (report them
upstream; this stack tracks them through the image scan in
`docs/image-security.md`), and findings that need an already compromised
Docker host or `.env` file.

## How releases are protected

Every release runs the enforcing supply-chain gate: Gitleaks over the full
history, Trivy and CISA KEV checks of every locally built image, and a
dependency scan. A release with an unexcepted KEV or fixable Critical finding
is not published. Exceptions live in `security/exceptions.toml`, are scoped to
one service and package, and expire.
