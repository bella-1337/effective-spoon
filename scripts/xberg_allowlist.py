"""Render every Xberg allowlist from security/xberg-destinations.toml.

Xberg's SSRF policy, its deny-by-default DNS resolver, and its Squid egress
proxy each need the same destinations in a different format and with
different matching rules. Keeping four hand-edited copies in step failed in
practice, so this module owns them: it validates the single source and
generates the four files, and the doctor contract rejects any drift.

Hostnames end up inside BIND and Squid configuration, so they are validated
strictly before anything is rendered; a quote, semicolon, or newline in a
name would otherwise inject configuration.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

SOURCE = Path("security/xberg-destinations.toml")
SSRF_CONFIG = Path("security/xberg-config.toml")
PROXY_ALLOWLIST = Path("security/xberg-egress-allowlist.txt")
DNS_ZONES = Path("security/xberg-dns-zones.conf")
DNS_ROOT_ZONE = Path("security/xberg-dns-deny-root.zone")
GENERATED_FILES = (SSRF_CONFIG, PROXY_ALLOWLIST, DNS_ZONES, DNS_ROOT_ZONE)

# Services that read the generated files, in restart order: Xberg depends on
# the resolver and the proxy.
SANDBOX_SERVICES = ("xberg-dns", "xberg-egress-proxy", "xberg")

_LABEL = r"(?!-)[a-z0-9-]{1,63}(?<!-)"
# At least two labels and an alphabetic top-level label, so IP literals,
# single-label names, wildcards, and uppercase spellings are all rejected.
_HOST_RE = re.compile(rf"(?=.{{1,253}}\Z)(?:{_LABEL}\.)+(?=[a-z0-9-]*[a-z])(?!-)[a-z0-9-]{{2,63}}(?<!-)")
_DESTINATION_KEYS = {"host", "subdomains", "dns_aliases"}

GENERATED_NOTICE = (
    "GENERATED from security/xberg-destinations.toml by\n"
    "`python3 scripts/maintenance.py xberg-allowlist render`. Do not edit:\n"
    "change the source file and run `xberg-allowlist apply`."
)


class DestinationError(ValueError):
    """The destination source is invalid; the message lists every problem."""


@dataclass(frozen=True)
class Destination:
    host: str
    subdomains: bool
    dns_aliases: tuple[str, ...]


def _covers(zone: str, name: str) -> bool:
    """Whether DNS name `name` is `zone` or lies below it."""
    return name == zone or name.endswith("." + zone)


def parse_destinations(data: Mapping[str, Any]) -> tuple[Destination, ...]:
    problems: list[str] = []
    unknown = set(data) - {"destination"}
    if unknown:
        problems.append("unknown top-level keys: " + ", ".join(sorted(unknown)))
    entries = data.get("destination", [])
    if not isinstance(entries, list) or not entries:
        problems.append("define at least one [[destination]]")
        entries = []

    destinations: list[Destination] = []
    for index, entry in enumerate(entries, start=1):
        label = f"destination {index}"
        if not isinstance(entry, dict):
            problems.append(f"{label} must be a table")
            continue
        extra = set(entry) - _DESTINATION_KEYS
        if extra:
            problems.append(f"{label} has unknown keys: " + ", ".join(sorted(extra)))
        host = entry.get("host")
        if not isinstance(host, str) or not _HOST_RE.fullmatch(host):
            problems.append(
                f"{label} host {host!r} is not a lowercase DNS name "
                "(no scheme, port, path, wildcard, or IP address)"
            )
            continue
        label = f"destination {host}"
        subdomains = entry.get("subdomains", False)
        if not isinstance(subdomains, bool):
            problems.append(f"{label}: subdomains must be true or false")
            subdomains = False
        aliases = entry.get("dns_aliases", [])
        if not isinstance(aliases, list):
            problems.append(f"{label}: dns_aliases must be a list")
            aliases = []
        valid_aliases: list[str] = []
        for alias in aliases:
            if not isinstance(alias, str) or not _HOST_RE.fullmatch(alias):
                problems.append(f"{label}: dns_aliases entry {alias!r} is not a lowercase DNS name")
            elif alias in valid_aliases or alias == host:
                problems.append(f"{label}: dns_aliases repeats {alias}")
            else:
                valid_aliases.append(alias)
        destinations.append(Destination(host, subdomains, tuple(valid_aliases)))

    seen: set[str] = set()
    for destination in destinations:
        if destination.host in seen:
            problems.append(f"destination {destination.host} is listed twice")
        seen.add(destination.host)
    # Squid rejects overlapping dstdomain entries, and an overlap means one
    # entry is redundant or broader than intended.
    for destination in destinations:
        for other in destinations:
            if (
                other is not destination
                and other.subdomains
                and other.host != destination.host
                and _covers(other.host, destination.host)
            ):
                problems.append(
                    f"destination {destination.host} is already covered by "
                    f"{other.host} with subdomains = true"
                )
    if problems:
        raise DestinationError("; ".join(problems))
    return tuple(destinations)


def load_destinations(root: Path) -> tuple[Destination, ...]:
    path = root / SOURCE
    if not path.is_file() or path.is_symlink():
        raise DestinationError(f"{SOURCE} is missing or unsafe")
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise DestinationError(f"{SOURCE} is not valid TOML: {error}") from None
    return parse_destinations(data)


def host_allowed(destinations: tuple[Destination, ...], host: str) -> bool:
    """Whether every layer lets Xberg fetch `host`."""
    return any(
        host == item.host or (item.subdomains and _covers(item.host, host))
        for item in destinations
    )


def _dns_zones(destinations: tuple[Destination, ...]) -> list[tuple[str, str]]:
    """Forward zones as (name, comment), without names a broader zone covers."""
    candidates: list[tuple[str, str]] = []
    for item in destinations:
        candidates.append((item.host, "Allowlisted destination." if not item.subdomains else "Allowlisted destination and its subdomains."))
        candidates.extend((alias, f"CNAME target of {item.host}.") for alias in item.dns_aliases)
    zones: list[tuple[str, str]] = []
    for name, comment in candidates:
        if any(_covers(other, name) and other != name for other, _ in candidates):
            continue
        if name not in {zone for zone, _ in zones}:
            zones.append((name, comment))
    return zones


def _comment(prefix: str, text: str) -> str:
    return "\n".join(f"{prefix} {line}".rstrip() for line in text.splitlines()) + "\n"


def render(destinations: tuple[Destination, ...]) -> dict[Path, str]:
    """Return the content of every generated file, keyed by repo-relative path."""
    matchers = "".join(
        f'  {{ type = "suffix", value = ".{item.host}" }},\n'
        if item.subdomains
        else f'  {{ type = "exact", value = "{item.host}" }},\n'
        for item in destinations
    )
    ssrf = (
        _comment("#", GENERATED_NOTICE)
        + "#\n"
        "# Xberg extracts untrusted, caller-chosen inputs. Local paths and file:// URIs\n"
        "# would expose the container filesystem to any MCP caller or injected prompt,\n"
        "# so only uploaded bytes and allowlisted remote URLs are accepted.\n"
        "#\n"
        "# Xberg permits an allowlisted hostname before its built-in private-range\n"
        "# check, so the Squid proxy's destination-address ACLs are what keep an\n"
        "# allowlisted name from reaching a private address.\n"
        "[url]\n"
        "allow_local_file_inputs = false\n"
        "allow_file_uris = false\n"
        "\n"
        "[url.crawl.ssrf]\n"
        "# exact: only that host. suffix \".host\": the host and its subdomains.\n"
        f"allowlist = [\n{matchers}]\n"
    )

    proxy = (
        _comment("#", GENERATED_NOTICE)
        + "#\n"
        "# Squid dstdomain entries: \"host\" matches only that host; \".host\" also\n"
        "# matches its subdomains.\n"
        + "".join(f"{'.' if item.subdomains else ''}{item.host}\n" for item in destinations)
    )

    zones = _dns_zones(destinations)
    forward = "".join(
        f"\n// {comment}\n"
        f'zone "{name}" {{\n'
        "    type forward;\n"
        "    forward only;\n"
        "    forwarders { 127.0.0.11; };\n"
        "};\n"
        for name, comment in zones
    )
    dns_zones = (
        _comment("//", GENERATED_NOTICE)
        + "//\n"
        "// Xberg sandbox resolver: deny by default.\n"
        "//\n"
        "// The authoritative empty root answers NXDOMAIN locally for every name, so a\n"
        "// lookup for an attacker-controlled hostname never leaves the sandbox. Each\n"
        "// allowlisted name is delegated in xberg-dns-deny-root.zone, without which\n"
        "// the root would still answer NXDOMAIN, and forwarded upstream here.\n"
        'include "/etc/bind/named.conf.options";\n'
        "\n"
        'zone "." {\n'
        "    type primary;\n"
        '    file "/etc/bind/xberg-deny-root.zone";\n'
        "};\n"
        + forward
    )

    width = max(len("localhost."), *(len(name) + 1 for name, _ in zones)) + 2
    root_zone = (
        _comment(";", GENERATED_NOTICE)
        + ";\n"
        "; Empty authoritative root for the Xberg sandbox resolver. Only the delegated\n"
        "; names below are resolved, through the forward zones in xberg-dns-zones.conf.\n"
        "$TTL 300\n"
        f"{'@':<{width}}IN SOA  localhost. nobody.invalid. ( 1 3600 600 86400 300 )\n"
        f"{'@':<{width}}IN NS   localhost.\n"
        f"{'localhost.':<{width}}IN A    127.0.0.1\n"
        "\n"
        + "".join(f"{name + '.':<{width}}IN NS   localhost.\n" for name, _ in zones)
    )

    return {
        SSRF_CONFIG: ssrf,
        PROXY_ALLOWLIST: proxy,
        DNS_ZONES: dns_zones,
        DNS_ROOT_ZONE: root_zone,
    }


def drift_failures(root: Path) -> list[str]:
    """Doctor contract: the source is valid and every generated file matches it."""
    try:
        expected = render(load_destinations(root))
    except DestinationError as error:
        return [f"Xberg destinations are invalid: {error}"]
    stale = [
        str(relative)
        for relative, content in expected.items()
        if not (root / relative).is_file()
        or (root / relative).read_text(encoding="utf-8") != content
    ]
    if stale:
        return [
            "Xberg allowlists differ from security/xberg-destinations.toml "
            "(run `python3 scripts/maintenance.py xberg-allowlist render`): "
            + ", ".join(stale)
        ]
    return []


def write_rendered(root: Path) -> list[Path]:
    """Write the generated files; return the ones whose content changed."""
    changed: list[Path] = []
    for relative, content in render(load_destinations(root)).items():
        path = root / relative
        if path.is_symlink():
            raise DestinationError(f"{relative} is a symlink; refusing to overwrite it")
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            continue
        # Rewrite in place so a running container's single-file bind mount
        # keeps pointing at the same inode.
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        changed.append(relative)
    return changed
