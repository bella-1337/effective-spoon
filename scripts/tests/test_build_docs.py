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
            git(repo, "tag", "v0.1.0")
            (repo / "docs-site").mkdir()
            (repo / "mkdocs.yml").write_text("site_name: Test\n", encoding="utf-8")
            (repo / "docs-site" / "requirements.txt").write_text("\n", encoding="utf-8")
            git(repo, "add", "-A")
            git(repo, "commit", "--quiet", "-m", "Add the site")
            git(repo, "tag", "-a", "v0.2.0", "-m", "v0.2.0")
            with mock.patch.object(build_docs, "ROOT", repo), mock.patch.object(
                maintenance_common, "ROOT", repo
            ), mock.patch.object(build_docs, "capture", lambda args: git(repo, *args[1:]) + "\n"):
                versions = build_docs.plan_versions("refs/heads/master", "master")
        self.assertEqual([(v.name, v.kind, v.source_ref) for v in versions], [
            ("dev", "dev", "master"),
            ("v0.2.0", "release", "v0.2.0"),
        ])


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

    def test_build_refuses_a_site_with_hidden_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            (site / "index.html").write_text("ok", encoding="utf-8")
            build_docs.check_published(site)
            leaked = site / ".docs-cache" / "venvs" / "LICENSE"
            leaked.mkdir(parents=True)
            with self.assertRaises(maintenance_common.ScriptError):
                build_docs.check_published(site)


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
