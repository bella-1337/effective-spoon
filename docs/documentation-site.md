# Documentation site

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

The guides in this repository are also published as a searchable, versioned
documentation site with [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/).
The site is built from the same Markdown files you read on GitHub or GitLab;
there is no second copy to keep in step.

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

`build` and the CI builds run MkDocs in strict mode, so they fail on a broken
link or anchor, on a Markdown file that is missing from the navigation, and on
any other MkDocs warning.

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
the Material version selector reads, the root redirect, a `404.html`, and a
`robots.txt` that disallows crawling. Tags created before the documentation
site existed are skipped.

The selector keeps you on the same page when it exists in the other version.
Each page's edit button opens the file on the default branch for `dev`, or the
file at the release tag for a release.

## How the site is assembled

| Path | Purpose |
| --- | --- |
| `mkdocs.yml` | Site configuration, theme, Markdown extensions, and the navigation |
| `docs-site/hooks.py` | Adds the repository's Markdown files to the site and generates pages |
| `docs-site/content/` | Site-only content: the home page and the theme assets |
| `docs-site/overrides/` | Theme overrides: the dev and older-release banners, the build footer, the `noindex` header |
| `docs-site/requirements.in`, `requirements.txt` | The toolchain and its hash-locked pins |
| `scripts/build-docs.py` | Installs the toolchain, previews, builds, and assembles the versioned site |
| `scripts/ci/docs.sh` | The single build entry point shared by GitHub Actions and GitLab CI |

The hook publishes every `*.md` file of the repository under its repository
path, outside hidden directories and runtime output, so the relative links
between guides work unchanged. `README.md` becomes the **Overview** page and
each other `README.md` the index page of its directory. The hook also:

- publishes the animated diagrams `diagrams/*.html` and any image under
  `docs/`;
- rewrites links to repository files that are not pages, such as
  `docker-compose.yml`, scripts, or Mermaid sources, to the GitHub or GitLab
  source browser at the built revision, and does the same for line anchors
  such as `README.md#L12`. A report named `*-COMMIT-<sha>.md` links to the
  files as they were at that commit;
- generates the **Diagram gallery** page, which embeds every rendered diagram
  listed in `diagrams/`, with titles from the Mermaid sources;
- generates the **Changelog** page from the first-parent history of the
  default branch: every merged pull or merge request and the commits it brought
  in, grouped by release tag. Commit messages are escaped, so they cannot
  inject markup into the site.

The site loads no fonts, scripts, or API data from third parties and shows no
repository statistics, so it works on an isolated network and does not reveal
page views outside the organisation.

### Add or move a page

1. Write the guide as Markdown anywhere in the repository, usually under
   `docs/`, and link it from the [README](../README.md#documentation) table.
2. Add it to `nav:` in `mkdocs.yml`. The strict build fails on a Markdown file
   that is not in the navigation.
3. Run `./scripts/build-docs.py build`.

Use relative links between Markdown files, as GitHub and GitLab expect, and
link to a heading with its GitHub-style anchor; the site uses the same
anchors.

## Publishing

Both platforms run the same `scripts/ci/docs.sh` in the same digest-pinned
image (`DOCS_IMAGE`: uv, Python 3.13, and Git), with the full Git history so
that every release tag and the default branch are available.

| Event | GitHub Actions (`docs.yml`) | GitLab CI (`docs.yml`) |
| --- | --- | --- |
| Pull or merge request | `build` job: `docs.sh check`, with `dev` taken from the change | `docs` job, same command |
| Default-branch push or release tag, publishing enabled | `build`, then `deploy` to GitHub Pages | `pages` job deploys to GitLab Pages |
| Default-branch push or release tag, publishing disabled | `build` only | `docs` only |

Publishing is off until you enable it, so a fork or a repository without a
Pages configuration only checks that the site builds. A release tag pipeline
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
4. Under **Settings > Secrets and variables > Actions > Variables**, create the
   repository variable `DOCS_PAGES_ENABLED` with the value `true`.
5. Push to the default branch, or run the **Documentation** workflow on it.

Before building, the workflow reads the Pages settings with the job token. It
stops when Pages is not enabled, when the source is not GitHub Actions, or when
the repository is private and GitHub does not report the site as private.

### GitLab Pages

1. Confirm that Pages access control is available. It is on GitLab.com; on a
   self-managed instance an administrator enables it in the Pages settings.
2. Under **Settings > General > Visibility, project features, permissions**,
   set **Pages** to **Only Project Members**.
3. Under **Settings > CI/CD > Variables**, add `DOCS_PAGES_ENABLED` with the
   value `true`.
4. Run a pipeline on the default branch.

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

- **Toolchain updates.** Dependabot proposes updates of
  `docs-site/requirements.txt`. To change a pin by hand, edit
  `docs-site/requirements.in` and regenerate the lock with the command in its
  header. Record new packages in the
  [licence register](third-party-licences.md#documentation-toolchain).
- **Image updates.** `DOCS_IMAGE` appears in `.github/workflows/docs.yml` and
  `.gitlab/ci/images.yml`; update both digests together.
- **Material for MkDocs end of life.** Its maintainers provide critical fixes
  and security updates until May 2027 and recommend their successor, Zensical,
  which reads the same `mkdocs.yml`. Plan the move before then; the hook in
  `docs-site/hooks.py` is the part to port.
- **Diagrams.** The gallery embeds the rendered HTML, so re-render the diagrams
  with `./scripts/render-diagrams.py` after changing a Mermaid source, as the
  [diagram guide](../diagrams/README.md#updating-diagrams) describes.
