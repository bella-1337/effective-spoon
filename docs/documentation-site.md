# Documentation site

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

The guides in this repository are also published as a searchable, versioned
documentation site built with [Zensical](https://zensical.org/), the successor
of Material for MkDocs by the same team. The site is built from the same
Markdown files you read on GitHub or GitLab, where they already live, without a
build plugin. Those files are the full reference text, which agents and AI
models read. Where `docs-site/human/` holds a shorter
[reader version](#reader-versions-and-copy-for-llm) of a page, written for its
current text, the site shows that version instead, and every page has a
**Copy for LLM** button that copies the page's own Markdown.

Like the repository, the site is published as is, without warranty of any kind.
Its footer says so on every page and states the licences: the stack's own
container images are MIT-licensed, and every third-party tool keeps its own
licence, as the [licence register](third-party-licences.md) records.

## Preview and build locally

You need [`uv`](maintenance-runtime.md), which the maintenance scripts already
require. The first run installs the hash-locked toolchain from
`docs-site/requirements.txt` into `.docs-cache/`.

```sh
./scripts/build-docs.py serve      # live preview on http://127.0.0.1:8000
./scripts/build-docs.py build      # strict build of the working tree into site/
./scripts/build-docs.py human-status   # which pages have a current reader version
```

Both build a staged copy of the published files in a temporary directory, with
the reader versions swapped in; `serve` refreshes that copy within a second of
each change, so live reload still works.

`build` and the CI builds run Zensical in strict mode, so they fail on a broken
link to a page or heading and on any other warning. A unit test fails when a
Markdown file is missing from the navigation, and the build fails if it ever
publishes a hidden path such as the toolchain cache.

To build the full versioned site the way CI does, from committed revisions
only:

```sh
./scripts/build-docs.py versions                 # the versions it would build
./scripts/build-docs.py site --dev-ref HEAD      # into public/
python3 -m http.server --directory public 8000   # browse it
```

## Versions and the dev switch

The version selector next to the site title switches between:

| Version | Built from | Notes |
| --- | --- | --- |
| `dev` | The default branch (`master`) | Unreleased changes. Every page shows a banner with the commit and a link to the latest release. |
| `vMAJOR.MINOR.PATCH` | Each release tag that contains `mkdocs.yml` | Older releases show a banner that links to the latest release. |
| `latest` | A copy of the newest release | The site root redirects here, or to `dev` until the first release exists. |

`scripts/build-docs.py site` builds each version from its own committed tree
(`git archive`) with the toolchain pinned in that revision, so an old release
renders as it did when it was tagged. It then writes `versions.json`, which
the theme's version selector reads, the root redirect, a `404.html`, and a
`robots.txt` that disallows crawling. Tags created before the documentation
site existed are skipped, and so is `v0.1.0` and every earlier release, whose
pages carry no warranty disclaimer or licence notice. Until a newer release
exists, the site root redirects to `dev`.

The selector keeps you on the same page when it exists in the other version.
Each page's edit button opens the file on the default branch for `dev`, or the
file at the release tag for a release.

## How the site is assembled

| Path | Purpose |
| --- | --- |
| `mkdocs.yml` | Site configuration, theme, navigation, and the rules for which files are published |
| `docs-site/overrides/main.html` | The home page banner, the dev, local, deployment, and older-release banners, and the `noindex` header |
| `docs-site/overrides/partials/` | The footer with the warranty disclaimer, licence notice, and build information, a repository link without the GitHub API call, a fix for the older-release banner script, page content without the README's status badges, which would load third-party images, and the **Copy for LLM** button |
| `docs-site/human/` | [Reader versions](#reader-versions-and-copy-for-llm) of pages, at the same paths as the pages they replace on the site |
| `docs-site/assets/` | Logo, favicon, banner illustration, and stylesheet |
| `docs-site/requirements.in`, `requirements.txt` | The toolchain and its hash-locked pins |
| `scripts/build-docs.py` | Installs the toolchain, stages the pages with their reader versions, previews, builds, and assembles the versioned site |
| `scripts/ci/docs.sh` | The single build entry point shared by GitHub Actions and GitLab CI |
| `docs-site/Dockerfile`, `Dockerfile.dockerignore` | The image of the optional [`docs` profile](#serve-the-site-from-the-stack), which runs `zensical serve` |

Zensical reads `mkdocs.yml` with the repository root as its `docs_dir`, so
every page keeps its repository path and the relative links between guides
work unchanged. `scripts/build-docs.py` runs it on a staged copy of the
published files, so always build through that script. `README.md` is the home page, and each other `README.md` is the
index page of its directory. `exclude_docs` first excludes everything, then
re-includes only Markdown, the rendered diagrams, their Mermaid sources and
viewer pages, the self-hosted Mermaid.js in `diagrams/assets/`, images under
`docs/`, the site assets, and the Markdown for AI models in `llm/`; hidden
directories, build output, runtime state, and `docs-site/human/` stay
excluded. Compose files, scripts, and other source
files are not published, so a guide links to them only as code (for example
`searxng/settings.yml`) or with an absolute link to the repository.

The site loads no fonts, scripts, or API data from third parties and shows no
repository statistics, so it works on an isolated network and does not reveal
page views to third parties.

### Add or move a page

1. Write the guide as Markdown anywhere in the repository, usually under
   `docs/`, and link it from the [README](../README.md#documentation) table.
2. Add it to `nav:` in `mkdocs.yml`; the unit tests fail on a Markdown file
   that is not in the navigation.
3. Run `./scripts/build-docs.py build`.

Use relative links between Markdown files, as GitHub and GitLab expect, and
link to a heading with its GitHub-style anchor; the site uses the same
anchors. Do not link to a line number (`#L12`) of a page or to a source file
with a relative link: neither exists on the site.

## Reader versions and Copy for LLM

Each guide is written as complete reference text: every rule, default, and
exception, in the order an operator or an AI model needs them. That makes a
good source for agents, and a slow read for a person. So a guide can also
have a reader version, a rewrite in plainer and shorter language with the
same facts, which the site shows in its place.

- **The guide stays the source of truth.** Agents, GitHub, and GitLab read the
  guide itself. Change the guide first, then its reader version.
- **The reader version lives at the same path** under `docs-site/human/`, for
  example `docs-site/human/docs/getting-started.md` for
  `docs/getting-started.md`. Pages without one, such as the agent
  instructions, the changelog, and the reports, are shown as they are.
- **Copy for LLM** on every page copies the guide's own Markdown, not the
  reader text. The build publishes it at `llm/<page>/index.md.txt` (for
  example `llm/docs/getting-started/index.md.txt`), and each page links it in
  its head as `text/markdown`. The theme's own clipboard handler copies it, so
  the site loads no extra script.
- **A stamp keeps them in step.** The first line of a reader version records
  the guide it was written for and the SHA-256 of that guide's text. When the
  guide changes, the stamp no longer matches: the build shows the guide
  instead, with a warning, so the site never shows outdated reader text, and
  the unit tests fail until the reader version is updated.

After changing a guide that has a reader version:

1. Update `docs-site/human/<same path>` to match the new text.
2. Run `./scripts/build-docs.py human-stamp <path>`, for example
   `./scripts/build-docs.py human-stamp docs/getting-started.md`.
3. Run `./scripts/build-docs.py build`.

Rules for a reader version:

- Keep every heading of the guide, word for word, so links to its anchors from
  other pages keep working; a unit test checks this. You may add headings.
- Keep every fact, command, value, file path, and link. Copy code blocks
  unchanged.
- Start with what the page is for and when to read it. Prefer short sentences,
  plain words, and lists over long paragraphs.
- Avoid the habits of machine-written text: inflated or promotional words,
  filler such as "it is important to note", lists padded to three items, and
  chains of dashes.
- Relative links resolve as they do from the guide, because the reader version
  replaces the guide at its path.

## Serve the site from the stack

The optional `docs` profile serves this documentation from the stack itself,
at `https://$DOCS_HOST`. `DOCS_HOST` is set in `.env.optional` and defaults to
`gateway-deployment-docs.${STACK_DOMAIN}`. The container runs `zensical serve`
on the checked-out revision the stack was built from, and Traefik routes
`DOCS_HOST` to it. It serves that one version, without the version selector;
the versioned site with `dev`, the releases, and `latest` stays on Pages. Every
page's banner names the commit, and its edit buttons open the file at that
commit.

```sh
cp .env.optional.example .env.optional   # if it does not exist yet; keep DOCS_HOST
export STACK_REPOSITORY_REVISION="$(git rev-parse HEAD)"
docker compose --env-file .env --env-file .env.optional \
  --profile docs up -d --build --wait docs
curl --fail --silent --output /dev/null "https://$DOCS_HOST/"
```

Add `DOCS_HOST` to the TLS certificate and to host resolution, as in
[Getting started](getting-started.md#2-deliver-tls-certificates). Rebuild the
image after each upgrade (`up -d --build docs`), so the site keeps matching the
deployment. Without `STACK_REPOSITORY_REVISION`, the banner says that the
revision is unknown; a value that is not a commit SHA fails the build.

How it works:

- `docs-site/Dockerfile` renders the diagrams, stages the published files with
  their reader versions (`scripts/build-docs.py stage`), installs the
  hash-locked toolchain from `docs-site/requirements.txt` in a build stage,
  outside the docs directory, and runs the same strict build as `build`, because
  `zensical serve` does not support strict mode. A broken link therefore fails
  the image build. Graphviz stays in the build stage. The build needs network
  access to install the toolchain and to download the pinned diagram renderer
  and Mermaid.js, as `build` does.
- `docs-site/Dockerfile.dockerignore` limits the build context to the
  published files and the build scripts, so `.env`, certificates, and runtime
  state never reach the image.
- The container builds the site once when it starts, in a few seconds, and
  then serves it. It runs as a non-root user with a read-only root filesystem
  and no capabilities; only Zensical's output (`/src/site`) and cache
  (`/src/.cache`) are small `tmpfs` mounts. It joins only the `edge` network,
  so it reaches no other service, and it mounts no volumes.
- Traefik answers only `GET` and `HEAD` (the router rule) and adds a
  same-origin Content Security Policy, `X-Frame-Options: SAMEORIGIN`,
  `X-Robots-Tag: noindex`, and a `Permissions-Policy` through the `docs-headers`
  middleware in the service's labels, on top of the HSTS, `nosniff`, referrer
  policy, and rate limits it applies to every route.
- `zensical serve` is Zensical's preview server. Every page keeps a live-reload
  WebSocket to the same host, which Traefik passes through; the files never
  change inside the container, so it only reloads the page after a container
  restart. The Mermaid viewer pages carry their own stricter policy, which
  blocks only that live-reload script.

The site has **no sign-in**. Anyone who can reach Traefik can read it. The
pages contain no secrets, but they do describe the stack's hostnames,
networks, and security controls, so expose `DOCS_HOST` only on the private
network that already reaches the other stack hostnames. The maintenance
contracts fail if the `docs` service leaves its profile, joins another
network, publishes a port, mounts a volume, or becomes a dependency of another
service.

## Publishing

Both platforms run the same `scripts/ci/docs.sh` in the same digest-pinned
image (`DOCS_IMAGE`: uv, Python 3.13, and Git), with the full Git history so
that every release tag and the default branch are available.

| Event | GitHub Actions (`docs.yml`) | GitLab CI (`docs.yml`) |
| --- | --- | --- |
| Pull or merge request | `build` job: `docs.sh check`, with `dev` taken from the change | `docs` job, same command |
| Default-branch push, publishing enabled | `build`, then `deploy` to GitHub Pages | `pages` job deploys to GitLab Pages |
| Release tag, publishing enabled | `publish-release` reruns the workflow on the default branch, which builds and deploys | `pages` job deploys to GitLab Pages |
| Default-branch push or release tag, publishing disabled | `build` only | `docs` only |

Publishing is on by default: `DOCS_PAGES_ENABLED` is `true` in
`.github/workflows/docs.yml` and `.gitlab-ci.yml`. To only build the site, for
example in a fork, set a repository (GitHub) or project CI/CD (GitLab)
variable `DOCS_PAGES_ENABLED` to `false`; it overrides the default. A release
tag rebuilds the whole site, so the new version appears in the selector as soon
as the tag is pushed. On GitHub the rebuild runs on the default branch, so the
`github-pages` environment only has to admit that branch, and it cancels a
still-running default-branch build of the same commit instead of deploying
twice.

### GitHub Pages

1. Under **Settings > Pages**, set **Source** to **GitHub Actions**.
2. Under **Settings > Environments > github-pages > Deployment branches and
   tags**, keep the default branch. No tag rule is needed: release tags
   publish through a default-branch run.
3. Push to the default branch, or run the **Documentation** workflow on it.

Before building, the workflow reads the Pages settings with the job token. It
stops when Pages is not enabled or when the source is not GitHub Actions. A
public repository publishes a public site. A private repository publishes only
when GitHub reports its site as private, which requires GitHub Enterprise
Cloud, or when `DOCS_ALLOW_PUBLIC_PAGES` is `true`; on GitHub Free, Pro, or
Team, Pages of a private repository are public on the Internet.

### GitLab Pages

1. Confirm that Pages access control is available. It is on GitLab.com; on a
   self-managed instance an administrator enables it in the Pages settings.
2. Under **Settings > General > Visibility, project features, permissions**,
   set **Pages** to **Only Project Members**.
3. Run a pipeline on the default branch.

The `pages` job first requests `CI_PAGES_URL` without credentials. It stops
when the current site answers with content instead of a sign-in redirect, so a
site that was opened to everyone is not updated. Before the first deployment
the site does not exist yet (HTTP 404), so the check cannot decide; after it,
open the site in a private browser window and confirm that GitLab asks you to
sign in. When the runner cannot reach the Pages host, the job fails rather than
publish unchecked.

### Visibility checks

- `DOCS_ALLOW_PUBLIC_PAGES=true` turns both visibility checks into warnings,
  so a private repository or project can publish a public site on purpose.
- Every page carries `noindex, nofollow, noarchive`, and the root `robots.txt`
  disallows crawling, so search engines do not index the site.

## Maintenance

- **Toolchain updates.** Zensical is pre-1.0 and changes quickly, so it is
  pinned to an exact version. Dependabot proposes updates of
  `docs-site/requirements.txt`; before merging one, read the
  [Zensical release notes](https://github.com/zensical/zensical/releases) and
  check the preview, especially the banners and the version selector. To
  change a pin by hand, edit `docs-site/requirements.in` and regenerate the
  lock with the command in its header. Record new packages in the
  [licence register](third-party-licences.md#documentation-toolchain).
- **Image updates.** `DOCS_IMAGE` appears in `.github/workflows/docs.yml` and
  `.gitlab/ci/images.yml`; update both digests together.
  The `docs` profile's `docs-site/Dockerfile` pins the same uv image as the
  other local builds; Dependabot proposes its updates.
- **Why Zensical.** Material for MkDocs receives only security fixes until
  5 May 2027, MkDocs 1.x has had no release since August 2024, and MkDocs 2.0
  supports neither Material nor plugins. Zensical reads the same `mkdocs.yml`
  and theme overrides. It does not run MkDocs plugins or hooks, so the site
  uses none.
- **Diagrams.** The build renders `diagrams/*.mmd` into animated HTML with
  `scripts/render-diagrams.py` before Zensical runs, so the HTML is never
  committed. Rendering needs Graphviz (`dot`): `scripts/ci/docs.sh` installs
  it in the CI image, and a local `build` or `serve` needs it on the
  workstation. The same script writes a viewer page for each source,
  `NN-name.mmd.html`, which draws it with Mermaid.js served from the site,
  because GitHub Pages serves a bare `.mmd` file as a download. Mermaid.js is
  downloaded once at a pinned version, checked against its SHA-256 digests,
  and copied into the ignored `diagrams/assets/` directory. Every diagram
  page, animated or Mermaid, has zoom controls and SVG, PNG, and source
  downloads.
  Releases that committed their HTML are published as tagged. See the
  [diagram guide](../diagrams/README.md#updating-diagrams).
