"""Tests for the custom recognizers in presidio/recognizers.yaml: street
addresses, URLs, and IP addresses.

The patterns are read from the YAML file itself, so the tests check exactly what
both analyzers load, under the analyzer's global regex flags.
"""

from __future__ import annotations

import json
import re
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECOGNIZERS = ROOT / "presidio" / "recognizers.yaml"
GUARDRAIL = ROOT / "litellm" / "modules" / "guardrails" / "presidio.yaml"
CUSTOM = {"DeStreetAddressRecognizer", "FrStreetAddressRecognizer", "WebUrlRecognizer", "IpAddressRecognizer"}


def recognizer_block(source: str, name: str) -> str:
    return source.split(f"  - name: {name}\n", 1)[1].split("\n  - name: ", 1)[0]


def load_patterns() -> tuple[int, dict[str, list[re.Pattern[str]]]]:
    source = RECOGNIZERS.read_text(encoding="utf-8")
    flags_match = re.search(r"^global_regex_flags:\s*(\d+)\s*$", source, re.MULTILINE)
    assert flags_match, "global_regex_flags is missing"
    flags = int(flags_match.group(1))
    patterns: dict[str, list[re.Pattern[str]]] = {}
    for block in re.split(r"(?=^  - name: )", source, flags=re.MULTILINE):
        name = re.match(r"^  - name: (\S+)", block)
        regexes = re.findall(r'^\s+regex: (".*")\s*$', block, re.MULTILINE)
        if name and regexes and name.group(1) in CUSTOM:
            # The YAML scalars are written as JSON strings, which YAML reads the same way.
            patterns[name.group(1)] = [re.compile(json.loads(regex), flags) for regex in regexes]
    return flags, patterns


MATCHES = {
    "DeStreetAddressRecognizer": [
        ("in der Lindenstraße 12, 80331 München hinterlegt", "Lindenstraße 12, 80331 München"),
        ("Anschrift: Berliner Straße 15a\n10115 Berlin", "Berliner Straße 15a\n10115 Berlin"),
        ("wohnt Hauptstr. 3 in Köln", "Hauptstr. 3"),
        ("Kurfürstendamm 21, 10719 Berlin", "Kurfürstendamm 21, 10719 Berlin"),
        ("Am Brunnenweg 7-9, 60311 Frankfurt am Main", "Brunnenweg 7-9, 60311 Frankfurt am Main"),
        ("Goethestraße 4 / 6, 04109 Leipzig.", "Goethestraße 4 / 6, 04109 Leipzig"),
    ],
    "FrStreetAddressRecognizer": [
        ("enregistré au 5 rue de Rivoli, 75001 Paris.", "5 rue de Rivoli, 75001 Paris"),
        ("habite 12 bis avenue Victor Hugo 69002 Lyon", "12 bis avenue Victor Hugo 69002 Lyon"),
        ("au 3, place de la Comédie\n34000 Montpellier", "3, place de la Comédie\n34000 Montpellier"),
        ("17 boulevard Saint-Michel, 75005 Paris CEDEX 05", "17 boulevard Saint-Michel, 75005 Paris CEDEX 05"),
        ("8 chemin des Vignes, 42000 Saint-Étienne", "8 chemin des Vignes, 42000 Saint-Étienne"),
        ("24 rue d'Alsace", "24 rue d'Alsace"),
        ("1 allée Jean-Jaurès 31000 Toulouse", "1 allée Jean-Jaurès 31000 Toulouse"),
    ],
    "WebUrlRecognizer": [
        ("See https://example.com/u/jane?id=1 today", "https://example.com/u/jane?id=1"),
        ("or www.example.org/profile", "www.example.org/profile"),
        ("blog: http://jane-doe.blog", "http://jane-doe.blog"),
        ("Visit HTTPS://Example.COM now", "HTTPS://Example.COM"),
        ("ftp://files.example.net:21/pub/report.pdf", "ftp://files.example.net:21/pub/report.pdf"),
        ("docs at https://docs.rs/tokio/latest", "https://docs.rs/tokio/latest"),
        ('href="https://example.de/a b"', "https://example.de/a"),
    ],
    "IpAddressRecognizer": [
        ("Server at 192.168.1.20.", "192.168.1.20"),
        ("route 10.0.0.7 via", "10.0.0.7"),
        ("IPv6 2001:db8::1 here", "2001:db8::1"),
        ("fe80::1ff:fe23:4567:890a", "fe80::1ff:fe23:4567:890a"),
        ("2001:0db8:85a3:0000:0000:8a2e:0370:7334", "2001:0db8:85a3:0000:0000:8a2e:0370:7334"),
        ("ip 2001:DB8:0:0:1::1", "2001:DB8:0:0:1::1"),
    ],
}
NO_MATCHES = {
    "DeStreetAddressRecognizer": [
        "Die Rechnung über 12345 Euro wurde bezahlt.",
        "Kapitel 12 der Straßenverkehrsordnung",
        "Wir haben 3 Wege gefunden.",
        "Version 2 des Plans steht im Ring 5 Ordner",
        "Die straße 5 ist gesperrt",
        "Seit 2019 arbeiten 15000 Mitarbeiter in 80 Ländern.",
        "Der Arbeitsmarkt 2024 und der Immobilienmarkt 2025.",
        "Vom Bahnhof 3 Minuten zu Fuß.",
        "Am Alten Markt 2025 gab es ein Fest.",
    ],
    "FrStreetAddressRecognizer": [
        "Le montant de 12345 Euros a été payé.",
        "Il y a 3 rues fermées.",
        "Article 5 de la loi",
        "Nous avons 20 places disponibles.",
        "Le 5 mai, 75000 personnes sont venues.",
    ],
    # File names, paths, code, and hosts that are not personal data.
    "WebUrlRecognizer": [
        "Please fix src/main.rs and lib.rs, then update Cargo.toml.",
        "Open /Users/alex/projects/api/src/handlers/mod.rs and config.py",
        "Edit README.md, docker-compose.yml and scripts/setup.sh",
        "println!(\"{}\", self.name); app.get('/api/users/:id', handler)",
        "ping api.internal.example.org",
        "email john.smith@example.com",
        "curl http://localhost:4000/health/liveliness",
        "http://presidio-analyzer:3000/analyze",
        "http://10.0.0.5/admin",
    ],
    "IpAddressRecognizer": [
        "use std::collections::HashMap; use tokio::sync::Mutex;",
        "fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result",
        "use serde::de::Deserialize; let m = f32::MAX; let x = f64::consts::PI;",
        "let resp = reqwest::get(url).await?; serde_json::from_str(&body)?",
        "loopback ::1 and unspecified ::",
        "at 12:30:45 on 2024-01-02",
        "MAC 00:1a:2b:3c:4d:5e",
        "999.1.1.1 and version 1.2.3",
    ],
}


class CustomRecognizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.flags, cls.patterns = load_patterns()

    def found(self, name: str, text: str) -> list[str]:
        return [m.group(0) for pattern in self.patterns[name] for m in pattern.finditer(text)]

    def test_address_recognizers_are_defined_for_their_language(self) -> None:
        source = RECOGNIZERS.read_text(encoding="utf-8")
        self.assertEqual(set(self.patterns), set(MATCHES))
        for name, language in (("DeStreetAddressRecognizer", "de"), ("FrStreetAddressRecognizer", "fr")):
            block = recognizer_block(source, name)
            self.assertIn(f"supported_language: {language}", block)
            self.assertIn("supported_entity: LOCATION", block)
            self.assertIn("type: custom", block)
        # The analyzer matches case-insensitively; the patterns rely on scoped (?-i:).
        self.assertTrue(self.flags & re.IGNORECASE)

    def test_url_and_ip_recognizers_replace_the_defaults(self) -> None:
        source = RECOGNIZERS.read_text(encoding="utf-8")
        for default, custom, entity in (
            ("UrlRecognizer", "WebUrlRecognizer", "URL"),
            ("IpRecognizer", "IpAddressRecognizer", "IP_ADDRESS"),
        ):
            with self.subTest(recognizer=custom):
                self.assertIn("enabled: false", recognizer_block(source, default))
                block = recognizer_block(source, custom)
                self.assertIn(f"supported_entity: {entity}", block)
                self.assertIn("type: custom", block)
                # No language: the analyzer creates one for each of its languages.
                self.assertNotIn("supported_language", block)

    def test_weak_driver_licence_pattern_is_ignored(self) -> None:
        # Its 0.3 pattern matches Rust types (i64, u128, f32) and compiler codes.
        guardrail = GUARDRAIL.read_text(encoding="utf-8")
        self.assertRegex(guardrail, r"presidio_score_thresholds:\n\s+US_DRIVER_LICENSE: 0\.4\n")

    def test_values_match_whole(self) -> None:
        for name, cases in MATCHES.items():
            for text, expected in cases:
                with self.subTest(recognizer=name, text=text):
                    self.assertIn(expected, self.found(name, text))

    def test_ordinary_text_does_not_match(self) -> None:
        for name, cases in NO_MATCHES.items():
            for text in cases:
                with self.subTest(recognizer=name, text=text):
                    self.assertEqual(self.found(name, text), [])

    def test_matching_stays_linear_on_crafted_input(self) -> None:
        n = 100_000
        attacks = [
            "A" + "b" * n,
            "Aa-" * (n // 3),
            "A." * (n // 2) + " 12",
            "Ab " * (n // 3),
            "1 rue " + "De " * (n // 3),
            "1 rue A" + "-a" * (n // 2),
            "Aweg " * (n // 5),
            "1" * n,
            "https://" + "a." * (n // 2),
            "https://a.bc" + "/" * n,
            "www." + "a-" * (n // 2),
            "www1." + "ab." * (n // 3) + "1",
            "1:" * (n // 2),
            "a:" * (n // 2),
            ":" * n,
            "1." * (n // 2),
            "f32::" * (n // 5),
        ]
        for name, patterns in self.patterns.items():
            for pattern, text in ((p, t) for p in patterns for t in attacks):
                started = time.monotonic()
                list(pattern.finditer(text))
                with self.subTest(recognizer=name, pattern=pattern.pattern[:20], attack=text[:12]):
                    # Unbounded repetitions took minutes here; bounded ones under 0.3 s.
                    self.assertLess(time.monotonic() - started, 3.0)


if __name__ == "__main__":
    unittest.main()
