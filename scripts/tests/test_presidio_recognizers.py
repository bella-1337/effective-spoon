"""Tests for the custom street-address recognizers in presidio/recognizers.yaml.

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


def load_patterns() -> tuple[int, dict[str, re.Pattern[str]]]:
    source = RECOGNIZERS.read_text(encoding="utf-8")
    flags_match = re.search(r"^global_regex_flags:\s*(\d+)\s*$", source, re.MULTILINE)
    assert flags_match, "global_regex_flags is missing"
    flags = int(flags_match.group(1))
    patterns: dict[str, re.Pattern[str]] = {}
    for block in re.split(r"(?=^  - name: )", source, flags=re.MULTILINE):
        name = re.match(r"^  - name: (\S+)", block)
        regex = re.search(r'^\s+regex: (".*")\s*$', block, re.MULTILINE)
        if name and regex and name.group(1) in {"DeStreetAddressRecognizer", "FrStreetAddressRecognizer"}:
            # The YAML scalars are written as JSON strings, which YAML reads the same way.
            patterns[name.group(1)] = re.compile(json.loads(regex.group(1)), flags)
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
}


class AddressRecognizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.flags, cls.patterns = load_patterns()

    def test_both_recognizers_are_defined_for_their_language(self) -> None:
        source = RECOGNIZERS.read_text(encoding="utf-8")
        self.assertEqual(set(self.patterns), set(MATCHES))
        for name, language in (("DeStreetAddressRecognizer", "de"), ("FrStreetAddressRecognizer", "fr")):
            block = source.split(f"  - name: {name}", 1)[1].split("\n  - name: ", 1)[0]
            self.assertIn(f"supported_language: {language}", block)
            self.assertIn("supported_entity: LOCATION", block)
            self.assertIn("type: custom", block)
        # The analyzer matches case-insensitively; the patterns rely on scoped (?-i:).
        self.assertTrue(self.flags & re.IGNORECASE)

    def test_addresses_match_whole(self) -> None:
        for name, cases in MATCHES.items():
            for text, expected in cases:
                with self.subTest(recognizer=name, text=text):
                    self.assertIn(expected, [m.group(0) for m in self.patterns[name].finditer(text)])

    def test_ordinary_text_does_not_match(self) -> None:
        for name, cases in NO_MATCHES.items():
            for text in cases:
                with self.subTest(recognizer=name, text=text):
                    self.assertEqual([m.group(0) for m in self.patterns[name].finditer(text)], [])

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
        ]
        for name, pattern in self.patterns.items():
            for text in attacks:
                started = time.monotonic()
                list(pattern.finditer(text))
                with self.subTest(recognizer=name, attack=text[:12]):
                    # Unbounded repetitions took minutes here; bounded ones under 0.3 s.
                    self.assertLess(time.monotonic() - started, 3.0)


if __name__ == "__main__":
    unittest.main()
