from __future__ import annotations

import re
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import xberg_allowlist as allowlist  # noqa: E402

REPO = SCRIPTS.parent


def destinations(*entries: dict) -> tuple[allowlist.Destination, ...]:
    return allowlist.parse_destinations({"destination": list(entries)})


class XbergAllowlistTests(unittest.TestCase):
    def test_committed_files_match_the_source(self) -> None:
        self.assertEqual(allowlist.drift_failures(REPO), [])

    def test_every_layer_gets_the_same_semantics(self) -> None:
        rendered = allowlist.render(
            destinations(
                {"host": "docs.example.org"},
                {"host": "example.net", "subdomains": True, "dns_aliases": ["cdn.example-cdn.com"]},
            )
        )
        ssrf = tomllib.loads(rendered[allowlist.SSRF_CONFIG])
        self.assertIs(ssrf["url"]["allow_local_file_inputs"], False)
        self.assertIs(ssrf["url"]["allow_file_uris"], False)
        self.assertEqual(
            ssrf["url"]["crawl"]["ssrf"]["allowlist"],
            [
                {"type": "exact", "value": "docs.example.org"},
                {"type": "suffix", "value": ".example.net"},
            ],
        )
        proxy = [
            line
            for line in rendered[allowlist.PROXY_ALLOWLIST].splitlines()
            if line and not line.startswith("#")
        ]
        self.assertEqual(proxy, ["docs.example.org", ".example.net"])

        zones = re.findall(r'zone "([^"]+)" \{\s*type forward;', rendered[allowlist.DNS_ZONES])
        delegated = re.findall(r"^(\S+)\.\s+IN NS", rendered[allowlist.DNS_ROOT_ZONE], re.MULTILINE)
        # A forward zone without its root delegation still answers NXDOMAIN.
        self.assertEqual(zones, ["docs.example.org", "example.net", "cdn.example-cdn.com"])
        self.assertEqual(delegated, zones)

    def test_dns_zones_skip_names_a_broader_zone_covers(self) -> None:
        rendered = allowlist.render(
            destinations(
                {"host": "example.net", "subdomains": True},
                {"host": "api.example.org"},
                {"host": "example.org"},
            )
        )
        zones = re.findall(r'zone "([^"]+)" \{\s*type forward;', rendered[allowlist.DNS_ZONES])
        self.assertEqual(zones, ["example.net", "example.org"])

    def test_rejects_names_that_could_inject_configuration(self) -> None:
        for host in (
            'evil.example"; };\nzone "." {',
            "example.com\nevil.test",
            "https://example.com",
            "example.com:443",
            "example.com/path",
            "*.example.com",
            ".example.com",
            "Example.com",
            "localhost",
            "192.168.0.1",
            "example.com.",
            "-bad.example.com",
            "",
        ):
            with self.subTest(host=host), self.assertRaises(allowlist.DestinationError):
                destinations({"host": host})
        with self.assertRaises(allowlist.DestinationError):
            destinations({"host": "example.com", "dns_aliases": ['x.test"; };']})

    def test_rejects_typos_duplicates_and_overlaps(self) -> None:
        cases = (
            ({"destination": [{"host": "example.com", "subdomain": True}]}, "unknown keys"),
            ({"destination": [{"host": "example.com", "subdomains": "yes"}]}, "true or false"),
            ({"destination": [{"host": "example.com"}, {"host": "example.com"}]}, "listed twice"),
            (
                {"destination": [{"host": "example.com", "subdomains": True}, {"host": "api.example.com"}]},
                "already covered",
            ),
            ({"destination": []}, "at least one"),
            ({"destinations": [{"host": "example.com"}]}, "unknown top-level"),
        )
        for data, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(allowlist.DestinationError, message):
                    allowlist.parse_destinations(data)

    def test_host_allowed_matches_squid_and_xberg(self) -> None:
        items = destinations({"host": "docs.example.org"}, {"host": "example.net", "subdomains": True})
        self.assertTrue(allowlist.host_allowed(items, "docs.example.org"))
        self.assertFalse(allowlist.host_allowed(items, "sub.docs.example.org"))
        self.assertTrue(allowlist.host_allowed(items, "example.net"))
        self.assertTrue(allowlist.host_allowed(items, "a.b.example.net"))
        self.assertFalse(allowlist.host_allowed(items, "badexample.net"))

    def test_drift_and_render_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "security").mkdir()
            (root / allowlist.SOURCE).write_text(
                '[[destination]]\nhost = "example.org"\n', encoding="utf-8"
            )
            self.assertIn("differ from", allowlist.drift_failures(root)[0])
            self.assertEqual(len(allowlist.write_rendered(root)), 4)
            self.assertEqual(allowlist.drift_failures(root), [])
            self.assertEqual(allowlist.write_rendered(root), [])

            (root / allowlist.SOURCE).write_text(
                '[[destination]]\nhost = "example.org"\nsubdomains = true\n', encoding="utf-8"
            )
            self.assertTrue(allowlist.drift_failures(root))

            (root / allowlist.SOURCE).write_text("not toml [", encoding="utf-8")
            self.assertIn("not valid TOML", allowlist.drift_failures(root)[0])


if __name__ == "__main__":
    unittest.main()
