from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import maintenance_common  # noqa: E402

SPEC = importlib.util.spec_from_file_location("build_docs", SCRIPTS / "build-docs.py")
assert SPEC is not None and SPEC.loader is not None
build_docs = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = build_docs
SPEC.loader.exec_module(build_docs)


def git(cwd: Path, *args: str) -> str:
    environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(cwd),
    }
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True, env=environment
    )
    return result.stdout.strip()


class VersionPlanTests(unittest.TestCase):
    def version(self, name: str, kind: str) -> object:
        return build_docs.Version(name=name, ref=name, commit="0" * 40, kind=kind, source_ref=name)

    def test_manifest_lists_dev_first_and_aliases_newest_release_as_latest(self) -> None:
        versions = [
            self.version("dev", "dev"),
            self.version("v1.10.0", "release"),
            self.version("v1.9.2", "release"),
        ]
        self.assertEqual(
            build_docs.versions_manifest(versions),
            [
                {"version": "dev", "title": "dev (unreleased)", "aliases": []},
                {"version": "v1.10.0", "title": "v1.10.0 (latest)", "aliases": ["latest"]},
                {"version": "v1.9.2", "title": "v1.9.2", "aliases": []},
            ],
        )

    def test_manifest_without_releases_has_no_latest_alias(self) -> None:
        manifest = build_docs.versions_manifest([self.version("dev", "dev")])
        self.assertEqual(manifest, [{"version": "dev", "title": "dev (unreleased)", "aliases": []}])

    def test_release_tags_sort_numerically_and_ignore_other_tags(self) -> None:
        tags = "v1.9.0\nv1.10.0\nv1.2.3-rc1\nvnext\nv0.1.0\n"
        with mock.patch.object(build_docs, "git", return_value=tags):
            self.assertEqual(build_docs.release_tags(), ["v1.10.0", "v1.9.0", "v0.1.0"])

    def test_plan_skips_tags_without_the_documentation_site(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            git(repo, "init", "--quiet", "--initial-branch=master")
            (repo / "README.md").write_text("# Old\n", encoding="utf-8")
            git(repo, "add", "-A")
            git(repo, "commit", "--quiet", "-m", "Before the site")
            git(repo, "tag", "v0.2.0")
            (repo / "docs-site").mkdir()
            (repo / "mkdocs.yml").write_text("site_name: Test\n", encoding="utf-8")
            (repo / "docs-site" / "requirements.txt").write_text("\n", encoding="utf-8")
            git(repo, "add", "-A")
            git(repo, "commit", "--quiet", "-m", "Add the site")
            git(repo, "tag", "-a", "v0.3.0", "-m", "v0.3.0")
            with mock.patch.object(build_docs, "ROOT", repo), mock.patch.object(
                maintenance_common, "ROOT", repo
            ), mock.patch.object(build_docs, "capture", lambda args: git(repo, *args[1:]) + "\n"):
                versions = build_docs.plan_versions("refs/heads/master", "master")
        self.assertEqual([(v.name, v.kind, v.source_ref) for v in versions], [
            ("dev", "dev", "master"),
            ("v0.3.0", "release", "v0.3.0"),
        ])

    def test_plan_skips_releases_without_the_warranty_and_licence_notice(self) -> None:
        tags = ["v0.1.1", "v0.1.0", "v0.0.9"]
        with mock.patch.object(build_docs, "release_tags", return_value=tags), \
                mock.patch.object(build_docs, "git", return_value="0" * 40), \
                mock.patch.object(build_docs, "has_docs", return_value=True):
            versions = build_docs.plan_versions("refs/heads/master", "master")
        self.assertEqual([v.name for v in versions], ["dev", "v0.1.1"])


class OutputSafetyTests(unittest.TestCase):
    def test_refuses_the_repository_root_and_its_parents(self) -> None:
        for path in (str(build_docs.ROOT), str(build_docs.ROOT.parent)):
            with self.assertRaises(maintenance_common.ScriptError):
                build_docs.prepare_output(path)

    def test_refuses_a_foreign_non_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "keep.txt").write_text("data", encoding="utf-8")
            with self.assertRaises(maintenance_common.ScriptError):
                build_docs.prepare_output(directory)
            self.assertTrue((Path(directory) / "keep.txt").exists())

    def test_reuses_and_empties_its_own_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "public"
            build_docs.prepare_output(str(output))
            (output / "stale.html").write_text("old", encoding="utf-8")
            build_docs.prepare_output(str(output))
            self.assertEqual(sorted(p.name for p in output.iterdir()), [build_docs.OUTPUT_MARKER])

    def test_root_files_redirect_to_latest_and_block_crawlers(self) -> None:
        versions = [
            build_docs.Version("dev", "dev", "a" * 40, "dev", "master"),
            build_docs.Version("v1.0.0", "v1.0.0", "b" * 40, "release", "v1.0.0"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            build_docs.write_root_files(output, versions, "https://docs.example.internal/stack")
            self.assertIn('url=latest/"', (output / "index.html").read_text(encoding="utf-8"))
            self.assertIn("Disallow: /", (output / "robots.txt").read_text(encoding="utf-8"))
            self.assertIn(
                'href="https://docs.example.internal/stack/"',
                (output / "404.html").read_text(encoding="utf-8"),
            )
            self.assertEqual(json.loads((output / "versions.json").read_text())[1]["aliases"], ["latest"])

    def test_root_redirects_to_dev_before_the_first_release(self) -> None:
        versions = [build_docs.Version("dev", "dev", "a" * 40, "dev", "master")]
        with tempfile.TemporaryDirectory() as directory:
            build_docs.write_root_files(Path(directory), versions, "")
            self.assertIn('url=dev/"', (Path(directory) / "index.html").read_text(encoding="utf-8"))


class ContextTests(unittest.TestCase):
    def arguments(self, **values: str | None) -> object:
        return types.SimpleNamespace(
            repo_url=values.get("repo_url"),
            repo_kind=values.get("repo_kind"),
            default_branch=values.get("default_branch"),
        )

    def test_gitlab_ci_supplies_repository_and_default_branch(self) -> None:
        environment = {
            "GITLAB_CI": "true",
            "CI_PROJECT_URL": "https://gitlab.example.internal/platform/litellm-stack",
            "CI_DEFAULT_BRANCH": "main",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            context = build_docs.detect_context(self.arguments())
        self.assertEqual(
            context,
            build_docs.Context(
                "https://gitlab.example.internal/platform/litellm-stack", "gitlab", "main"
            ),
        )

    def test_github_actions_supplies_repository(self) -> None:
        environment = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "example/stack",
            "DOCS_DEFAULT_BRANCH": "master",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            context = build_docs.detect_context(self.arguments())
        self.assertEqual(context, build_docs.Context("https://github.com/example/stack", "github", "master"))

    def test_rejects_a_plain_http_repository_url(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(
            maintenance_common.ScriptError
        ):
            build_docs.detect_context(
                self.arguments(repo_url="http://git.example.internal/x", default_branch="master")
            )


class VisibilityTests(unittest.TestCase):
    def check(self, status: int, allow_public: str = "") -> None:
        arguments = types.SimpleNamespace(url="https://docs.example.internal/")
        with mock.patch.object(build_docs, "anonymous_status", return_value=status), mock.patch.dict(
            os.environ, {"DOCS_ALLOW_PUBLIC_PAGES": allow_public}
        ):
            build_docs.verify_private(arguments)

    def test_public_site_is_refused(self) -> None:
        with self.assertRaises(maintenance_common.ScriptError):
            self.check(200)

    def test_public_site_passes_only_with_explicit_override(self) -> None:
        self.check(200, allow_public="true")

    def test_sign_in_redirect_and_denials_pass(self) -> None:
        for status in (302, 401, 403):
            self.check(status)

    def test_first_deployment_is_allowed_with_a_warning(self) -> None:
        self.check(404)

    def test_unexpected_status_fails_closed(self) -> None:
        with self.assertRaises(maintenance_common.ScriptError):
            self.check(500)

    def test_non_https_and_empty_urls_are_refused(self) -> None:
        for url in ("http://docs.example.internal/", ""):
            with self.assertRaises(maintenance_common.ScriptError):
                build_docs.verify_private(types.SimpleNamespace(url=url))


class RepositoryContractTests(unittest.TestCase):
    def test_docs_image_is_identical_on_both_platforms(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "docs.yml").read_text(encoding="utf-8")
        images = (ROOT / ".gitlab" / "ci" / "images.yml").read_text(encoding="utf-8")
        github = next(
            line.split(":", 1)[1].strip()
            for line in workflow.splitlines()
            if line.strip().startswith("DOCS_IMAGE:")
        )
        gitlab = next(
            line.split(":", 1)[1].strip().strip('"')
            for line in images.splitlines()
            if line.strip().startswith("DOCS_IMAGE:")
        )
        self.assertEqual(github, gitlab)

    def test_every_markdown_file_is_in_the_navigation(self) -> None:
        # Zensical publishes every Markdown file outside the excluded
        # directories; one missing from nav is reachable only by search.
        config = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
        navigation = config[config.index("\nnav:") :]
        excluded = {"site", "public", "dist", "logs", "backups", "node_modules", "upgrade-state"}
        for directory, subdirectories, files in os.walk(ROOT):
            subdirectories[:] = [
                name for name in subdirectories if not name.startswith(".") and name not in excluded
            ]
            if Path(directory).relative_to(ROOT).as_posix() == "docs-site":
                # Reader versions replace their page at build time.
                subdirectories[:] = [name for name in subdirectories if name != "human"]
            for name in files:
                if name.endswith(".md"):
                    path = Path(directory, name).relative_to(ROOT).as_posix()
                    self.assertIn(f": {path}\n", navigation, f"{path} is missing from mkdocs.yml nav")

    def test_site_publishes_documentation_files_only(self) -> None:
        config = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
        rules = config[config.index("exclude_docs: |") :].split("\n\n", 1)[0].splitlines()[1:]
        rules = [rule.strip() for rule in rules]
        self.assertEqual(rules[0], "*", "exclude_docs must start by excluding everything")
        # Zensical honours a directory exclusion only in the /** form.
        for hidden in ("**/.*/**", "/site/**", "/public/**"):
            self.assertIn(hidden, rules)
        # The Markdown for AI models is published; the reader versions are
        # never published as pages of their own.
        self.assertIn("!/llm/**", rules)
        self.assertIn("/docs-site/human/**", rules)
        self.assertGreater(rules.index("/docs-site/human/**"), rules.index("!*.md"))

    def test_every_reader_version_matches_the_current_page(self) -> None:
        # A page changed without its reader version: the site would fall back
        # to the page, so refresh docs-site/human/<page> and stamp it.
        pages = set(build_docs.published_pages(ROOT))
        for page in build_docs.human_pages(ROOT):
            with self.subTest(page=page):
                self.assertIn(page, pages, f"docs-site/human/{page} has no page")
                state, body = build_docs.human_state(ROOT, page)
                self.assertEqual(
                    state,
                    "fresh",
                    f"{page} changed after its reader version was written; update "
                    f"docs-site/human/{page}, then run scripts/build-docs.py human-stamp {page}",
                )
                self.assertTrue(body.strip())

    def test_every_guide_has_a_reader_version(self) -> None:
        # New guides ship with their reader version; only the pages listed in
        # WITHOUT_READER_VERSION are shown as written.
        human = set(build_docs.human_pages(ROOT))
        missing = [
            page
            for page in build_docs.published_pages(ROOT)
            if build_docs.needs_reader_version(page) and page not in human
        ]
        self.assertEqual(
            missing,
            [],
            "write docs-site/human/<page> for each page, then run "
            "scripts/build-docs.py human-stamp <page> (see docs/documentation-site.md)",
        )

    def test_exempt_patterns_match_existing_pages(self) -> None:
        pages = build_docs.published_pages(ROOT)
        for pattern in build_docs.WITHOUT_READER_VERSION:
            with self.subTest(pattern=pattern):
                self.assertTrue(any(build_docs.fnmatch.fnmatchcase(page, pattern) for page in pages))

    def test_reader_versions_keep_every_heading_of_their_page(self) -> None:
        # Other pages link to headings by anchor; the strict build checks the
        # links of the reader versions only, so check the anchors here.
        def headings(text: str) -> list[str]:
            found, fenced = [], False
            for line in text.splitlines():
                if line.startswith(("```", "~~~")):
                    fenced = not fenced
                elif not fenced and line.startswith("#"):
                    found.append(line.rstrip())
            return found

        for page in build_docs.human_pages(ROOT):
            with self.subTest(page=page):
                original = (ROOT / page).read_text(encoding="utf-8")
                body = build_docs.human_state(ROOT, page)[1]
                missing = [h for h in headings(original) if h not in headings(body)]
                self.assertEqual(missing, [], f"docs-site/human/{page} drops headings")

    def test_docs_image_build_context_includes_the_staging_script(self) -> None:
        ignore = (ROOT / "docs-site" / "Dockerfile.dockerignore").read_text(encoding="utf-8")
        self.assertIn("!scripts/build-docs.py\n", ignore)
        self.assertIn("build-docs.py stage --output /stage", (ROOT / "docs-site" / "Dockerfile").read_text())

    def test_build_refuses_a_site_with_hidden_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            (site / "index.html").write_text("ok", encoding="utf-8")
            build_docs.check_published(site)
            leaked = site / ".docs-cache" / "venvs" / "LICENSE"
            leaked.mkdir(parents=True)
            with self.assertRaises(maintenance_common.ScriptError):
                build_docs.check_published(site)


class StagingTests(unittest.TestCase):
    PAGE = "docs/guide.md"
    ORIGINAL = "# Guide\n\nThe full reference text.\n"

    def tree(self, root: Path) -> Path:
        for path, text in {
            "README.md": "# Home\n",
            self.PAGE: self.ORIGINAL,
            "mkdocs.yml": "site_name: Test\n",
            "diagrams/README.md": "# Diagrams\n",
            "diagrams/01-a.html": "<svg/>",
            "diagrams/assets/mermaid.min.js": "//",
            "docs-site/overrides/main.html": "",
            "litellm/provider-secrets/vertex.json": "{}",
            "litellm/config.prod.yaml": "model_list: []\n",
            ".env": "SECRET=1\n",
            "site/old.md": "# Old build\n",
        }.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(text, encoding="utf-8")
        return root

    def human(self, root: Path, body: str, original: str | None = None) -> None:
        human = root / "docs-site" / "human" / self.PAGE
        human.parent.mkdir(parents=True, exist_ok=True)
        stamp = build_docs.human_stamp(self.PAGE, (original or self.ORIGINAL).encode())
        human.write_text(stamp + "\n" + body, encoding="utf-8")

    def test_page_urls_follow_directory_urls(self) -> None:
        self.assertEqual(build_docs.page_url("README.md"), "")
        self.assertEqual(build_docs.page_url("diagrams/README.md"), "diagrams/")
        self.assertEqual(build_docs.page_url("docs/getting-started.md"), "docs/getting-started/")
        self.assertEqual(build_docs.llm_copy("README.md"), "llm/index.md.txt")
        self.assertEqual(
            build_docs.llm_copy("docs/getting-started.md"), "llm/docs/getting-started/index.md.txt"
        )

    def test_exemptions_match_whole_paths(self) -> None:
        self.assertFalse(build_docs.needs_reader_version("AGENTS.md"))
        self.assertFalse(build_docs.needs_reader_version("docs/prompts/create-dev-to-prod-delta.md"))
        self.assertTrue(build_docs.needs_reader_version("docs/AGENTS.md"))
        self.assertTrue(build_docs.needs_reader_version("docs/dns-entries.md"))

    def test_published_pages_skip_hidden_build_and_reader_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.tree(Path(directory))
            self.human(root, "# Guide\n")
            self.assertEqual(
                build_docs.published_pages(root), ["README.md", "diagrams/README.md", self.PAGE]
            )
            self.assertEqual(build_docs.human_pages(root), [self.PAGE])

    def test_fresh_reader_version_is_shown_and_the_original_is_published(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.tree(Path(directory))
            self.human(root, "# Guide\n\nIn plain words.\n")
            build_docs.stage_pages(root, root)
            self.assertEqual((root / self.PAGE).read_text(), "# Guide\n\nIn plain words.\n")
            self.assertEqual(
                (root / "llm/docs/guide/index.md.txt").read_text(), self.ORIGINAL
            )
            self.assertEqual((root / "llm/index.md.txt").read_text(), "# Home\n")
            self.assertFalse((root / "docs-site" / "human").exists())

    def test_stale_reader_version_falls_back_to_the_page(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.tree(Path(directory))
            self.human(root, "# Guide\n\nOutdated.\n", original="# Guide\n\nOlder text.\n")
            self.assertEqual(build_docs.human_state(root, self.PAGE)[0], "stale")
            build_docs.stage_pages(root, root)
            self.assertEqual((root / self.PAGE).read_text(), self.ORIGINAL)

    def test_unstamped_or_orphaned_reader_versions_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.tree(Path(directory))
            human = root / "docs-site" / "human" / self.PAGE
            human.parent.mkdir(parents=True)
            human.write_text("# Guide\n", encoding="utf-8")
            with self.assertRaisesRegex(maintenance_common.ScriptError, "stamp"):
                build_docs.stage_pages(root, root)
        with tempfile.TemporaryDirectory() as directory:
            root = self.tree(Path(directory))
            orphan = root / "docs-site" / "human" / "docs" / "gone.md"
            orphan.parent.mkdir(parents=True)
            orphan.write_text("# Gone\n", encoding="utf-8")
            with self.assertRaisesRegex(maintenance_common.ScriptError, "without a page"):
                build_docs.stage_pages(root, root)

    def test_staging_copies_only_published_files(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as stage_dir:
            source = self.tree(Path(source_dir))
            self.human(source, "# Guide\n\nIn plain words.\n")
            stage = Path(stage_dir)
            with mock.patch.object(build_docs, "ROOT", source):
                build_docs.stage_working_tree(stage)
            staged = sorted(
                path.relative_to(stage).as_posix() for path in stage.rglob("*") if path.is_file()
            )
            self.assertEqual(staged, [
                "README.md",
                "diagrams/01-a.html",
                "diagrams/README.md",
                "diagrams/assets/mermaid.min.js",
                "docs-site/overrides/main.html",
                "docs/guide.md",
                "llm/diagrams/index.md.txt",
                "llm/docs/guide/index.md.txt",
                "llm/index.md.txt",
                "mkdocs.yml",
            ])
            self.assertIn("In plain words.", (stage / self.PAGE).read_text())
            self.assertEqual((source / self.PAGE).read_text(), self.ORIGINAL)

    def test_staging_refuses_the_repository_itself(self) -> None:
        with self.assertRaises(maintenance_common.ScriptError):
            build_docs.stage_pages(build_docs.ROOT, build_docs.ROOT)

    def test_stamp_refuses_paths_outside_the_published_pages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.tree(Path(directory))
            self.human(root, "# Guide\n")
            with mock.patch.object(build_docs, "ROOT", root):
                for page in ("../outside.md", "litellm/config.prod.yaml", "docs/missing.md"):
                    with self.assertRaises(maintenance_common.ScriptError):
                        build_docs.human_stamp_command(types.SimpleNamespace(pages=[page]))

    def test_symlinked_reader_version_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.tree(Path(directory))
            human = root / "docs-site" / "human" / self.PAGE
            human.parent.mkdir(parents=True)
            human.symlink_to(root / "README.md")
            with self.assertRaisesRegex(maintenance_common.ScriptError, "symlink"):
                build_docs.stage_pages(root, root)

    def test_stamp_records_the_current_page(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.tree(Path(directory))
            self.human(root, "# Guide\n", original="# Guide\n\nOlder text.\n")
            with mock.patch.object(build_docs, "ROOT", root):
                build_docs.human_stamp_command(types.SimpleNamespace(pages=[self.PAGE]))
                self.assertEqual(build_docs.human_state(root, self.PAGE), ("fresh", "# Guide\n"))


class DiagramRenderingTests(unittest.TestCase):
    def tree(self, root: Path, *, html: bool) -> Path:
        (root / "diagrams").mkdir()
        (root / "scripts").mkdir()
        (root / "scripts" / "render-diagrams.py").write_text("", encoding="utf-8")
        (root / "diagrams" / "01-sample.mmd").write_text("flowchart TD\n", encoding="utf-8")
        if html:
            (root / "diagrams" / "01-sample.html").write_text("<svg/>", encoding="utf-8")
        return root

    def test_renders_with_the_renderer_of_the_same_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = self.tree(Path(temporary), html=False)
            with mock.patch.object(build_docs.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
                build_docs.render_diagrams(source)
            run.assert_called_once_with(
                [sys.executable, str(source / "scripts" / "render-diagrams.py")], cwd=source, check=False
            )

    def test_release_with_committed_html_is_published_as_tagged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = self.tree(Path(temporary), html=True)
            with mock.patch.object(build_docs.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
                build_docs.render_diagrams(source)
                run.assert_not_called()
                build_docs.render_diagrams(source, always=True)
                run.assert_called_once()

    def test_failed_rendering_fails_the_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = self.tree(Path(temporary), html=False)
            with mock.patch.object(build_docs.subprocess, "run", return_value=mock.Mock(returncode=1)):
                with self.assertRaisesRegex(maintenance_common.ScriptError, "could not render"):
                    build_docs.render_diagrams(source)


class EditLinkTests(unittest.TestCase):
    def test_dev_and_local_builds_edit_on_the_default_branch(self) -> None:
        github = build_docs.Context("https://github.com/example/stack", "github", "main")
        gitlab = build_docs.Context("https://gitlab.example.internal/x/stack", "gitlab", "main")
        self.assertEqual(build_docs.edit_uri(github, "dev", "main"), "edit/main/")
        self.assertEqual(build_docs.edit_uri(gitlab, "local", "main"), "-/edit/main/")

    def test_releases_link_to_the_file_at_their_tag(self) -> None:
        github = build_docs.Context("https://github.com/example/stack", "github", "main")
        gitlab = build_docs.Context("https://gitlab.example.internal/x/stack", "gitlab", "main")
        self.assertEqual(build_docs.edit_uri(github, "release", "v1.2.3"), "blob/v1.2.3/")
        self.assertEqual(build_docs.edit_uri(gitlab, "release", "v1.2.3"), "-/blob/v1.2.3/")

    def test_build_date_is_not_parsed_as_a_yaml_date(self) -> None:
        # !ENV values are parsed as YAML, and Zensical rejects date objects.
        context = build_docs.Context("", "github", "main")
        env = build_docs.docs_environment(context, version="dev", kind="dev", source_ref="main", commit="")
        self.assertRegex(env["DOCS_BUILD_DATE"], r"^\d{4}-\d{2}-\d{2} UTC$")

if __name__ == "__main__":
    unittest.main()
