# Documentation site

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

The guides in this repository are also published as a searchable site built
with [Zensical](https://zensical.org/), the successor of Material for MkDocs by
the same team: <https://bella-1337.github.io/symmetrical-engine/>. The site is
built from the same Markdown files you read on GitHub. There is one version of
it, built from the default branch, and no version selector.

Like the repository, the site is published as is, without warranty of any kind.
Its footer says so on every page and states the licence.

## Preview and build locally

You need [`uv`](https://docs.astral.sh/uv/). The first run installs the
hash-locked toolchain from `docs-site/requirements.txt` into `.docs-cache/`
(Python 3.13, downloaded by uv if missing) and reuses it until that file
changes.

```sh
./scripts/build-docs.py serve      # live preview on http://127.0.0.1:8000
./scripts/build-docs.py build      # strict build of the working tree into site/
```

`serve` takes `--dev-addr HOST:PORT`. `build` takes `--site-url URL` (or the
`DOCS_SITE_URL` environment variable) and `--output DIR`, which copies the
finished site to `DIR` and replaces what is there.

`build` and CI run Zensical in strict mode, so they fail on a link to a Markdown
page or heading that does not exist and on any other warning. The build also
fails if it would publish a hidden path such as `.docs-cache/`. Only a preview
shows the "Local preview" banner.

CI calls the build through `scripts/ci/docs.sh`, which you can run too. Both
modes leave the strict build in `site/`; `publish` also writes `public/`, the
Pages artifact directory:

```sh
scripts/ci/docs.sh check
scripts/ci/docs.sh publish https://example.test/
python3 -m http.server --directory public 8000
```

## How the site is assembled

| Path | Purpose |
| --- | --- |
| `mkdocs.yml` | Site configuration, theme, navigation, and the rules for which files are published |
| `docs-site/overrides/` | The `noindex` header, the local-preview banner, the footer with the warranty disclaimer and licence notice, and a repository link without the GitHub API call |
| `docs-site/assets/` | Logo, favicon, and stylesheet |
| `docs-site/requirements.in`, `requirements.txt` | The toolchain and its hash-locked pins |
| `scripts/build-docs.py` | Installs the toolchain, previews, and builds |
| `scripts/ci/docs.sh` | The build entry point CI runs |
| `.github/workflows/docs.yml` | The Documentation workflow |

Zensical reads `mkdocs.yml` with the repository root as its `docs_dir`, so every
page keeps its repository path and the relative links between guides work
unchanged. `README.md` is the home page and each other `README.md` is the index
page of its directory. `exclude_docs` first excludes everything, then
re-includes only Markdown and the site assets; hidden directories, build output,
and the toolchain cache stay excluded. The chart's templates, bundled files, and
the scripts are not published.

The site loads no fonts, scripts, or API data from third parties and shows no
repository statistics, so it works on an isolated network and does not reveal
page views to third parties.

### Add or move a page

1. Write the guide as Markdown anywhere in the repository, usually under
   `deploy/docs/`, and link it from the guides tables in the
   [chart README](../README.md#documentation) and the
   [repository README](../../README.md#documentation).
2. Add it to `nav:` in `mkdocs.yml`. `scripts/tests/test_build_docs.py` fails,
   in the Validate workflow too, when a published Markdown file is missing from
   `nav:`. A Zensical build alone does not notice it.
3. Run `./scripts/build-docs.py build`.

Use relative links between Markdown files, and link to a heading with its
GitHub-style anchor; the site uses the same anchors.

A relative link to a file that is not Markdown (a script, a values file, the
chart) is not checked and its target is not on the site, so it becomes a dead
link. A local strict build of a relative link to `scripts/ci/chart-checks.sh`
passed without a warning. Link to such files with an absolute URL, for example
`https://github.com/bella-1337/symmetrical-engine/blob/main/scripts/ci/chart-checks.sh`,
or mention them as code.

## Publishing

`.github/workflows/docs.yml` (name **Documentation**) runs on every pull
request, on every push to `main`, and on demand (**Actions > Documentation >
Run workflow**). Each run builds the site inside the digest-pinned image
`DOCS_IMAGE` (uv 0.9.30 with Python 3.13 on Debian bookworm), started with
`--user "$(id -u):$(id -g)"` and `HOME=/tmp`, so the build needs no root.

| Event | What runs |
| --- | --- |
| Pull request | `build` job: `scripts/ci/docs.sh check`, a strict build; nothing is published |
| Push to `main`, or a manual run on `main` | `build` job: `scripts/ci/docs.sh publish SITE_URL` and upload of `public/` as the Pages artifact, then the `deploy` job |
| Manual run on another branch | `check`; nothing is published |
| `DOCS_PAGES_ENABLED` is `false` | `check` on every event |

The workflow's default permission is `contents: read`; the `build` job adds
`pages: read`. The `deploy` job holds `pages: write` and `id-token: write`,
uses the `github-pages` environment, and has its own `pages` concurrency group
that never cancels a running deployment. No secret and no `pull_request_target`
trigger is used.

### GitHub Pages

Do this once, as a repository administrator:

1. Under **Settings > Pages**, set **Source** to **GitHub Actions**.
2. Push to `main`, or run the **Documentation** workflow on `main`.

The `github-pages` environment is created by the first deployment. If you
restrict it under **Settings > Environments > github-pages > Deployment
branches and tags**, allow `main`.

Before building, a publishing run reads the Pages settings with
`gh api repos/$GITHUB_REPOSITORY/pages` and the job token. It stops with one of
these messages:

```text
GitHub Pages is not enabled. Set Settings > Pages > Source to GitHub Actions.
GitHub Pages source is '<build_type>'. Set Settings > Pages > Source to GitHub Actions.
Could not read the GitHub Pages settings; see the API error above.
```

The first is printed when the API answers 404, the second when `build_type` is
anything but `workflow` (GitHub's REST documentation gives `legacy` for
**Deploy from a branch**), the third for any other API error. Pull request runs never read the settings, so they pass before
Pages is set up.

### Turn publishing off

Set the repository variable `DOCS_PAGES_ENABLED` to `false` (**Settings >
Secrets and variables > Actions > Variables**). The workflow then only checks
that the site builds, on every event, and never reads or changes the Pages
settings. An unset variable counts as `true`. A fork that does not want a site
can keep the check this way.

### Site URL

A publishing run takes the URL from the Pages API (`html_url`) and passes it to
`scripts/ci/docs.sh publish`, which hands it to
`scripts/build-docs.py build --site-url`. It becomes `site_url` of the build, so
canonical links and the sitemap use it. For this repository that is
`https://bella-1337.github.io/symmetrical-engine/`. A check build has no site
URL. `build-docs.py` accepts only an `https://` URL without query or fragment.

### Search engines

The site asks not to be indexed. Every page carries
`<meta name="robots" content="noindex, nofollow, noarchive">` from
`docs-site/overrides/main.html`, and `docs.sh publish` writes a `robots.txt`
with `Disallow: /`. Neither hides the pages from anyone who has the URL: the
repository is public and so is the site.

To allow indexing, delete the `robots` meta line in
`docs-site/overrides/main.html` and the `robots.txt` line in the `publish`
branch of `scripts/ci/docs.sh`, then publish again. Search engines can take
weeks to drop a `noindex` they have already seen.

`docs.sh publish` also writes an empty `public/.nojekyll`. The Pages artifact
upload skips hidden files unless asked, so the workflow sets
`include-hidden-files: true`; without it `.nojekyll` would be dropped.

## Maintenance

- **Toolchain updates.** Zensical is pre-1.0 and changes quickly, so it is
  pinned to an exact version in `docs-site/requirements.in`. Dependabot
  proposes updates of `docs-site/requirements.txt` weekly, after a seven-day
  cooldown. Before merging one, read the
  [Zensical release notes](https://github.com/zensical/zensical/releases) and
  check the preview. To change the pin by hand, edit
  `docs-site/requirements.in` and regenerate the lock:

  ```sh
  uv pip compile docs-site/requirements.in --generate-hashes --universal \
    --python-version 3.13 -o docs-site/requirements.txt
  ```

  The next `build-docs.py` run sees that the file changed and reinstalls the
  toolchain with `--require-hashes`.
- **Build image.** `DOCS_IMAGE` is set once, in `env:` of
  `.github/workflows/docs.yml`, and Dependabot does not track it. To move it,
  replace tag and digest together. A pull request build then shows whether
  `scripts/ci/docs.sh` still runs in it as a non-root user.
- **Actions.** Every action is pinned to a commit SHA with a version comment.
  Dependabot proposes updates weekly after a seven-day cooldown. The
  `workflows` job of the Validate workflow runs actionlint and zizmor on every
  change; `.github/zizmor.yml` requires hash pins.

## What has not been exercised in GitHub Actions

Nothing has run in GitHub Actions yet. These were checked on a workstation:

- `scripts/ci/docs.sh publish` produced `public/` with `.nojekyll`,
  `robots.txt`, and the site URL in the canonical link, with no other hidden
  path. It ran with the workstation's own uv and Python, not inside `DOCS_IMAGE`
  as a non-root user.
- The `docker run` argument list of the build step was printed for both modes,
  not executed.
- The publish decision and the Pages-settings step ran against a stubbed `gh`
  for a missing site, a `legacy` source, an API error, and a `workflow` source.
  The real API call and the `pages: read` permission were not tried.
- actionlint and zizmor report nothing on both workflows.
- The `DOCS_IMAGE` digest resolves on `ghcr.io` to an OCI index with
  `linux/amd64` and `linux/arm64` manifests.

Not tried: the first deployment (the `github-pages` environment, the
`id-token` exchange, `actions/deploy-pages`), the artifact as Pages serves it,
and the pinned image on a GitHub-hosted runner. Expect the first publishing run
to show something that needs fixing.
