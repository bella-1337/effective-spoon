# Documentation site

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

The guides in this repository are also published as a searchable, versioned
documentation site built with [Zensical](https://zensical.org/), the successor
of Material for MkDocs by the same team. The site is built from the same
Markdown files you read on GitHub or GitLab, where they already live; there is
no second copy to keep in step and no build plugin.

The repository is private, and so is its documentation: it describes internal
hostnames, network boundaries, security findings, and residual risks. Publish
it only to a Pages site that is restricted to repository members. Both
pipelines refuse to publish a site that they can see is public; see
[Keep the site private](#keep-the-site-private).

## Preview and build locally

You need [`uv`](maintenance-runtime.md), which the maintenance scripts already
require. The first run installs the hash-locked toolchain from
`docs-site/requirements.txt` into `.docs-cache/`.

```sh
./scripts/build-docs.py serve      # live preview on http://127.0.0.1:8000
./scripts/build-docs.py build      # strict build of the working tree into site/
```

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
site existed are skipped.

The selector keeps you on the same page when it exists in the other version.
Each page's edit button opens the file on the default branch for `dev`, or the
file at the release tag for a release.

## How the site is assembled

| Path | Purpose |
| --- | --- |
| `mkdocs.yml` | Site configuration, theme, navigation, and the rules for which files are published |
| `docs-site/overrides/main.html` | The home page banner, the dev, local, and older-release banners, and the `noindex` header |
| `docs-site/overrides/partials/` | The build footer, a repository link without the GitHub API call, and a fix for the older-release banner script |
| `docs-site/assets/` | Logo, favicon, banner illustration, and stylesheet |
| `docs-site/requirements.in`, `requirements.txt` | The toolchain and its hash-locked pins |
| `scripts/build-docs.py` | Installs the toolchain, previews, builds, and assembles the versioned site |
| `scripts/ci/docs.sh` | The single build entry point shared by GitHub Actions and GitLab CI |

Zensical reads `mkdocs.yml` with the repository root as its `docs_dir`, so
every page keeps its repository path and the relative links between guides
work unchanged. `README.md` is the home page, and each other `README.md` is the
index page of its directory. `exclude_docs` first excludes everything, then
re-includes only Markdown, the rendered diagrams and their Mermaid sources,
images under `docs/`, and the site assets; hidden directories, build output,
and runtime state stay excluded. Compose files, scripts, and other source
files are not published, so a guide links to them only as code (for example
`searxng/settings.yml`) or with an absolute link to the repository.

The site loads no fonts, scripts, or API data from third parties and shows no
repository statistics, so it works on an isolated network and does not reveal
page views outside the organisation.

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

## Publishing

Both platforms run the same `scripts/ci/docs.sh` in the same digest-pinned
image (`DOCS_IMAGE`: uv, Python 3.13, and Git), with the full Git history so
that every release tag and the default branch are available.

| Event | GitHub Actions (`docs.yml`) | GitLab CI (`docs.yml`) |
| --- | --- | --- |
| Pull or merge request | `build` job: `docs.sh check`, with `dev` taken from the change | `docs` job, same command |
| Default-branch push or release tag, publishing enabled | `build`, then `deploy` to GitHub Pages | `pages` job deploys to GitLab Pages |
| Default-branch push or release tag, publishing disabled | `build` only | `docs` only |

Publishing is on by default: `DOCS_PAGES_ENABLED` is `true` in
`.github/workflows/docs.yml` and `.gitlab-ci.yml`. To only build the site, for
example in a fork, set a repository (GitHub) or project CI/CD (GitLab)
variable `DOCS_PAGES_ENABLED` to `false`; it overrides the default. A release
tag pipeline
rebuilds the whole site, so the new version appears in the selector as soon as
the tag is pushed.

### GitHub Pages

1. Confirm that your plan can restrict Pages visibility. Private Pages
   visibility requires GitHub Enterprise Cloud. On GitHub Free, Pro, or Team,
   Pages of a private repository are **public on the Internet**, and the
   workflow refuses to publish them.
2. Under **Settings > Pages**, set **Source** to **GitHub Actions** and
   **Visibility** to **Private**.
3. Under **Settings > Environments > github-pages > Deployment branches and
   tags**, keep the default branch and add the tag rule `v*.*.*`; otherwise
   release tag deployments are rejected.
4. Push to the default branch, or run the **Documentation** workflow on it.

Before building, the workflow reads the Pages settings with the job token. It
stops when Pages is not enabled, when the source is not GitHub Actions, or when
the repository is private and GitHub does not report the site as private.

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

### Keep the site private

- Do not set `DOCS_ALLOW_PUBLIC_PAGES=true` for this repository. It exists only
  for a deliberately public copy, and it turns both visibility checks into
  warnings.
- Every page carries `noindex, nofollow, noarchive`, and the root `robots.txt`
  disallows crawling, as a second line of defence if a site is ever exposed by
  mistake.
- Share links to the site only with people who can read the repository.

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
- **Why Zensical.** Material for MkDocs receives only security fixes until
  5 May 2027, MkDocs 1.x has had no release since August 2024, and MkDocs 2.0
  supports neither Material nor plugins. Zensical reads the same `mkdocs.yml`
  and theme overrides. It does not run MkDocs plugins or hooks, so the site
  uses none.
- **Diagrams.** Re-render the diagrams with `./scripts/render-diagrams.py`
  after changing a Mermaid source, as the
  [diagram guide](../diagrams/README.md#updating-diagrams) describes; the site
  publishes the rendered HTML.
