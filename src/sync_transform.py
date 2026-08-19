#!/usr/bin/env python3
"""Fetch upstream content and write transformed records."""

from __future__ import annotations

import argparse
import csv
import io
import ipaddress
import json
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?$", re.IGNORECASE)
CLASH_DOMAIN_RULES = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-FULL"}
CLASH_IP_RULES = {"IP", "IP-CIDR", "IP-CIDR6"}
V2RAY_DOMAIN_PREFIXES = {"domain", "full"}
V2RAY_IP_PREFIXES = {"ip", "cidr", "ip-cidr", "ip-cidr6"}
V2RAY_VALUE_PREFIXES = V2RAY_DOMAIN_PREFIXES | V2RAY_IP_PREFIXES | {"regexp"}


class SyncError(Exception):
    """An expected configuration, network, parsing, or output error."""


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SyncError(f"Config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SyncError(f"Invalid JSON in config {path}: {exc}") from exc

    if not isinstance(config, dict) or not isinstance(config.get("sources", []), list):
        raise SyncError("Config must be an object with a 'sources' array")
    return config


def fetch(url: str, timeout: int, headers: dict[str, str]) -> bytes:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise SyncError(f"HTTP {exc.code} while fetching {url}") from exc
    except urllib.error.URLError as exc:
        raise SyncError(f"Network error while fetching {url}: {exc.reason}") from exc


def fetch_json(url: str, timeout: int, headers: dict[str, str]) -> Any:
    try:
        return json.loads(fetch(url, timeout, headers).decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SyncError(f"Unable to parse JSON response from {url}: {exc}") from exc


def normalize_domain(value: str) -> str | None:
    """Return a normalized domain, or None for IPs, wildcards, and invalid values."""
    domain = value.strip().strip("\"'").lower()
    if domain.startswith("||"):
        domain = domain[2:]
    if "^" in domain:
        domain = domain.split("^", 1)[0]
    domain = domain.strip().strip(".")
    if not domain or "*" in domain or "/" in domain or ":" in domain:
        return None
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        return None
    if len(domain) > 253 or any(not DOMAIN_LABEL_RE.fullmatch(label) for label in domain.split(".")):
        return None
    return domain


def normalize_ip_cidr(value: str) -> str | None:
    """Return a canonical IP or CIDR value, or None for invalid values."""
    candidate = value.strip().strip("\"'")
    try:
        if "/" in candidate:
            return str(ipaddress.ip_network(candidate, strict=False))
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def _clean_rule_line(raw_line: str) -> str:
    line = raw_line.strip().lstrip("\ufeff")
    if line.startswith("- "):
        line = line[2:].strip()
    return line


def extract_clash_domains(
    content: str,
    include_plain_lines: bool = False,
    rule_types: set[str] | None = None,
) -> tuple[list[str], dict[str, int]]:
    """Extract one normalized domain per supported Clash rule line."""
    supported = rule_types or CLASH_DOMAIN_RULES
    domains: set[str] = set()
    stats = {"lines": 0, "accepted": 0, "skipped": 0, "duplicates": 0}
    for raw_line in content.splitlines():
        stats["lines"] += 1
        line = _clean_rule_line(raw_line)
        if not line or line.startswith("#"):
            stats["skipped"] += 1
            continue
        line = line.split("#", 1)[0].strip()
        parts = [part.strip() for part in line.split(",")]
        rule_type = parts[0].upper()
        candidate: str | None = None
        if rule_type in supported and len(parts) >= 2:
            candidate = parts[1]
        elif include_plain_lines and len(parts) == 1:
            candidate = parts[0]
        domain = normalize_domain(candidate) if candidate else None
        if not domain:
            stats["skipped"] += 1
            continue
        if domain in domains:
            stats["duplicates"] += 1
            continue
        domains.add(domain)
        stats["accepted"] += 1
    return sorted(domains), stats


def extract_clash_ips(
    content: str,
    rule_types: set[str] | None = None,
) -> tuple[list[str], dict[str, int]]:
    """Extract one canonical IP or CIDR per supported Clash rule line."""
    supported = rule_types or CLASH_IP_RULES
    values: set[str] = set()
    stats = {"lines": 0, "accepted": 0, "skipped": 0, "duplicates": 0}
    for raw_line in content.splitlines():
        stats["lines"] += 1
        line = _clean_rule_line(raw_line)
        if not line or line.startswith("#"):
            stats["skipped"] += 1
            continue
        line = line.split("#", 1)[0].strip()
        parts = [part.strip() for part in line.split(",")]
        if parts[0].upper() not in supported or len(parts) < 2:
            stats["skipped"] += 1
            continue
        value = normalize_ip_cidr(parts[1])
        if not value:
            stats["skipped"] += 1
            continue
        if value in values:
            stats["duplicates"] += 1
            continue
        values.add(value)
        stats["accepted"] += 1
    return sorted(values, key=lambda value: (":" in value, value)), stats


def extract_v2ray_rule_list(content: str) -> tuple[list[str], list[str], list[str], dict[str, int]]:
    """Split a v2ray-rules-dat text list into domains, IP/CIDR, and regex rules."""
    domains: set[str] = set()
    ips: set[str] = set()
    regexes: set[str] = set()
    stats = {"lines": 0, "domains": 0, "ips": 0, "regexes": 0, "skipped": 0, "duplicates": 0}

    for raw_line in content.splitlines():
        stats["lines"] += 1
        line = _clean_rule_line(raw_line)
        if not line or line.startswith("#"):
            stats["skipped"] += 1
            continue

        prefix, separator, value = line.partition(":")
        prefix = prefix.lower() if separator else ""
        value = value.strip() if separator else line
        if prefix == "regexp":
            if value in regexes:
                stats["duplicates"] += 1
            elif value:
                regexes.add(value)
                stats["regexes"] += 1
            else:
                stats["skipped"] += 1
            continue

        if prefix in V2RAY_IP_PREFIXES:
            candidate = normalize_ip_cidr(value)
            if candidate:
                if candidate in ips:
                    stats["duplicates"] += 1
                else:
                    ips.add(candidate)
                    stats["ips"] += 1
            else:
                stats["skipped"] += 1
            continue

        if prefix and prefix not in V2RAY_DOMAIN_PREFIXES:
            stats["skipped"] += 1
            continue

        candidate = normalize_domain(value)
        if not candidate:
            candidate = normalize_ip_cidr(value)
            if candidate:
                if candidate in ips:
                    stats["duplicates"] += 1
                else:
                    ips.add(candidate)
                    stats["ips"] += 1
                continue
            stats["skipped"] += 1
            continue
        if candidate in domains:
            stats["duplicates"] += 1
        else:
            domains.add(candidate)
            stats["domains"] += 1

    return (
        sorted(domains),
        sorted(ips, key=lambda value: (":" in value, value)),
        sorted(regexes),
        stats,
    )


def normalize_for_domain_output(value: str, preserve_case: bool = False) -> str | None:
    """Keep any usable rule value in one line-oriented domain output list."""
    value = value.strip().strip("\"'")
    if not value or any(char in value for char in "\r\n"):
        return None
    return value if preserve_case else value.lower()


def normalize_ikuai_isp_value(value: str) -> str | None:
    """Return an iKuai-compatible IPv4 or IPv4/CIDR value."""
    candidate = normalize_ip_cidr(value)
    if not candidate:
        return None
    try:
        version = ipaddress.ip_network(candidate, strict=False).version if "/" in candidate else ipaddress.ip_address(candidate).version
    except ValueError:
        return None
    return candidate if version == 4 else None


def _classify_rule_value(
    candidate: str,
    domains: set[str],
    ips: set[str],
    stats: dict[str, int],
    preserve_case: bool = False,
) -> None:
    """Put one rule payload into the domain or IPv4/CIDR output set."""
    isp_value = normalize_ikuai_isp_value(candidate)
    if isp_value:
        if isp_value in ips:
            stats["duplicates"] += 1
        else:
            ips.add(isp_value)
            stats["ips"] += 1
            stats["accepted"] += 1
        return

    # IPv6 is not written to the iKuai ISP output file.
    if normalize_ip_cidr(candidate):
        stats["skipped"] += 1
        return

    value = normalize_for_domain_output(candidate, preserve_case=preserve_case)
    if not value:
        stats["skipped"] += 1
        return
    if value in domains:
        stats["duplicates"] += 1
    else:
        domains.add(value)
        stats["domains"] += 1
        stats["accepted"] += 1


def extract_clash_ikuai_values(
    content: str,
) -> tuple[list[str], list[str], dict[str, int]]:
    """Extract Clash content into domain and IPv4/CIDR lists."""
    domains: set[str] = set()
    ips: set[str] = set()
    stats = {"lines": 0, "accepted": 0, "domains": 0, "ips": 0, "skipped": 0, "duplicates": 0}
    for raw_line in content.splitlines():
        stats["lines"] += 1
        line = _clean_rule_line(raw_line)
        if not line or line.startswith("#"):
            stats["skipped"] += 1
            continue
        line = line.split("#", 1)[0].strip()
        parts = [part.strip() for part in line.split(",")]
        if len(parts) == 1 and line.endswith(":"):
            stats["skipped"] += 1
            continue
        candidate = parts[1] if len(parts) >= 2 else parts[0]
        _classify_rule_value(candidate, domains, ips, stats)
    return sorted(domains), sorted(ips, key=lambda value: (":" in value, value)), stats


def extract_v2ray_ikuai_values(
    content: str,
) -> tuple[list[str], list[str], dict[str, int]]:
    """Extract v2ray-rules-dat content into domain and IPv4/CIDR lists."""
    domains: set[str] = set()
    ips: set[str] = set()
    stats = {"lines": 0, "accepted": 0, "domains": 0, "ips": 0, "skipped": 0, "duplicates": 0}
    for raw_line in content.splitlines():
        stats["lines"] += 1
        line = _clean_rule_line(raw_line)
        if not line or line.startswith("#"):
            stats["skipped"] += 1
            continue
        line = line.split("#", 1)[0].strip()
        prefix, separator, payload = line.partition(":")
        prefix = prefix.strip().lower() if separator else ""
        candidate = payload.strip() if separator else line
        _classify_rule_value(candidate, domains, ips, stats, preserve_case=prefix == "regexp")
    return sorted(domains), sorted(ips, key=lambda value: (":" in value, value)), stats


# Backwards-compatible helpers for callers of the earlier API.
def extract_clash_all_values(content: str) -> tuple[list[str], dict[str, int]]:
    domains, ips, stats = extract_clash_ikuai_values(content)
    return sorted(domains + ips), stats


def extract_v2ray_all_values(content: str) -> tuple[list[str], dict[str, int]]:
    domains, ips, stats = extract_v2ray_ikuai_values(content)
    return sorted(domains + ips), stats


def safe_relative_path(project_root: Path, configured: str, label: str) -> Path:
    path = Path(configured)
    if path.is_absolute() or ".." in path.parts:
        raise SyncError(f"{label} must be a relative path inside the project: {configured}")
    return project_root / path


def write_text_if_changed(path: Path, content: str, dry_run: bool) -> bool:
    return write_if_changed(path, content, dry_run)


def sync_github_directory(source: dict[str, Any], project_root: Path, dry_run: bool) -> bool:
    """Sync GitHub directory files into one combined value list per source file."""
    api_url = str(source.get("api_url", ""))
    if not api_url:
        raise SyncError("github_directory sources require 'api_url'")
    output_dir = safe_relative_path(project_root, str(source.get("output_dir", "")), "output_dir")
    timeout = int(source.get("timeout_seconds", 30))
    headers = {"User-Agent": "upstream-sync-transformer/0.1"}
    headers.update({str(k): str(v) for k, v in source.get("headers", {}).items()})
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token and "Authorization" not in headers:
        headers["Authorization"] = f"Bearer {token}"
    extensions = {str(value).lower() for value in source.get("include_extensions", [".list"])}
    recursive = bool(source.get("recursive", False))
    directory_files = list_github_files(api_url, timeout, headers, recursive)
    filtered_files: list[dict[str, Any]] = []
    exclude_prefixes = tuple(str(value).rstrip("/") + "/" for value in source.get("exclude_prefixes", []))
    exclude_names = {str(value) for value in source.get("exclude_names", [])}
    for item in directory_files:
        if not isinstance(item, dict) or item.get("type") != "file":
            continue
        name = str(item.get("name", ""))
        path = str(item.get("path", ""))
        if Path(name).suffix.lower() not in extensions:
            continue
        if name in exclude_names or any(path.startswith(prefix) for prefix in exclude_prefixes):
            continue
        if not item.get("download_url"):
            continue
        filtered_files.append(item)
    directory_files = sorted(filtered_files, key=lambda item: str(item.get("path", "")))

    generated_files: list[str] = []
    all_domains: set[str] = set()
    all_ips: set[str] = set()
    manifest_files: list[dict[str, Any]] = []
    changed = False
    path_prefix = str(source.get("path_prefix", "")).rstrip("/")

    for item in directory_files:
        item_name = str(item["name"])
        source_path = str(item.get("path", item_name))
        source_relative = source_path.removeprefix(path_prefix + "/") if path_prefix else source_path
        source_relative_path = Path(source_relative)
        relative_parent = source_relative_path.parent
        output_stem = source_relative_path.stem
        domain_path = output_dir / relative_parent / f"{output_stem}_domain.txt"
        isp_path = output_dir / relative_parent / f"{output_stem}_isp.txt"
        domain_relative = domain_path.relative_to(project_root).as_posix()
        isp_relative = isp_path.relative_to(project_root).as_posix()
        content = fetch(str(item["download_url"]), timeout, headers).decode("utf-8-sig")
        domains, ips, value_stats = extract_clash_ikuai_values(content)
        generated_files.append(domain_relative)
        if ips:
            generated_files.append(isp_relative)
        all_domains.update(domains)
        all_ips.update(ips)
        manifest_files.append({
            "source": source_path,
            "domain_output": domain_relative,
            "isp_output": isp_relative if ips else None,
            "domains": len(domains),
            "ips": len(ips),
            "skipped": value_stats["skipped"],
        })
        changed = write_text_if_changed(domain_path, "\n".join(domains) + ("\n" if domains else ""), dry_run) or changed
        if ips:
            changed = write_text_if_changed(isp_path, "\n".join(ips) + "\n", dry_run) or changed
        status = "would update" if dry_run else "updated"
        print(f"[{source['name']}] {status}: {domain_relative} ({len(domains)} domains)")
        if ips:
            print(f"[{source['name']}] {status}: {isp_relative} ({len(ips)} IPv4/CIDR values)")

    merged_domain_output = safe_relative_path(
        project_root,
        str(source.get("merged_domain_output", str(output_dir.relative_to(project_root) / "all_domain.txt"))),
        "merged_domain_output",
    )
    merged_domain_relative = merged_domain_output.relative_to(project_root).as_posix()
    merged_isp_output = safe_relative_path(
        project_root,
        str(source.get("merged_isp_output", str(output_dir.relative_to(project_root) / "all_isp.txt"))),
        "merged_isp_output",
    )
    merged_isp_relative = merged_isp_output.relative_to(project_root).as_posix()
    generated_files.append(merged_domain_relative)
    if all_ips:
        generated_files.append(merged_isp_relative)
    merged_domains = sorted(all_domains)
    merged_ips = sorted(all_ips, key=lambda value: (":" in value, value))
    changed = write_text_if_changed(merged_domain_output, "\n".join(merged_domains) + ("\n" if merged_domains else ""), dry_run) or changed
    if merged_ips:
        changed = write_text_if_changed(merged_isp_output, "\n".join(merged_ips) + "\n", dry_run) or changed

    manifest_path = output_dir / ".sync-manifest.json"
    generated_files.append(manifest_path.relative_to(project_root).as_posix())
    old_manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                old_manifest = loaded
        except json.JSONDecodeError:
            old_manifest = {}
    old_files = set(old_manifest.get("generated_files", []))
    for stale in old_files - set(generated_files):
        stale_path = safe_relative_path(project_root, stale, "managed output")
        if stale_path.exists() and not dry_run:
            stale_path.unlink()
            changed = True

    manifest = {
        "source": str(source["name"]),
        "generated_files": sorted(generated_files),
        "files": manifest_files,
        "merged_domains": len(merged_domains),
        "merged_ips": len(merged_ips),
    }
    manifest_content = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    changed = write_text_if_changed(manifest_path, manifest_content, dry_run) or changed
    status = "would update" if dry_run else "updated"
    print(f"[{source['name']}] {status}: {merged_domain_relative} ({len(merged_domains)} unique domains total)")
    print(f"[{source['name']}] {status}: {merged_isp_relative} ({len(merged_ips)} unique IPv4/CIDR values total)")
    return changed


def sync_rule_list(source: dict[str, Any], project_root: Path, dry_run: bool) -> bool:
    """Sync a single text rule list into iKuai domain and ISP output files."""
    for required in ("name", "url", "output_dir"):
        if not source.get(required):
            raise SyncError(f"rule_list sources require '{required}'")

    timeout = int(source.get("timeout_seconds", 30))
    headers = {"User-Agent": "upstream-sync-transformer/0.1"}
    headers.update({str(k): str(v) for k, v in source.get("headers", {}).items()})
    content = fetch(str(source["url"]), timeout, headers).decode("utf-8-sig")
    domains, ips, stats = extract_v2ray_ikuai_values(content)
    output_dir = safe_relative_path(project_root, str(source["output_dir"]), "output_dir")
    domain_path = output_dir / f"{source['name']}_domain.txt"
    isp_path = output_dir / f"{source['name']}_isp.txt"
    generated_files = [domain_path.relative_to(project_root).as_posix()]
    isp_relative = isp_path.relative_to(project_root).as_posix()
    if ips:
        generated_files.append(isp_relative)
    manifest_name = str(source.get("manifest_name", f".sync-manifest-{source['name']}.json"))
    old_manifest_path = output_dir / manifest_name
    old_manifest: dict[str, Any] = {}
    if old_manifest_path.exists():
        try:
            loaded = json.loads(old_manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                old_manifest = loaded
        except json.JSONDecodeError:
            pass

    changed = False
    changed = write_text_if_changed(domain_path, "\n".join(domains) + ("\n" if domains else ""), dry_run) or changed
    if ips:
        changed = write_text_if_changed(isp_path, "\n".join(ips) + "\n", dry_run) or changed

    # Remove files produced by the previous split-output mode.
    for legacy_name in (f"{source['name']}_ip.txt", f"{source['name']}_regexp.txt"):
        legacy_path = output_dir / legacy_name
        if legacy_path.exists() and not dry_run:
            legacy_path.unlink()
            changed = True

    for stale in set(old_manifest.get("generated_files", [])) - set(generated_files):
        stale_path = safe_relative_path(project_root, stale, "managed output")
        if stale_path.exists() and not dry_run:
            stale_path.unlink()
            changed = True

    manifest = {
        "source": str(source["name"]),
        "url": str(source["url"]),
        "generated_files": sorted(generated_files + [old_manifest_path.relative_to(project_root).as_posix()]),
        "domains": len(domains),
        "ips": len(ips),
        "skipped": stats["skipped"],
    }
    changed = write_text_if_changed(old_manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", dry_run) or changed
    status = "would update" if dry_run else ("updated" if changed else "unchanged")
    print(f"[{source['name']}] {status}: {generated_files[0]} ({len(domains)} domains; {stats['skipped']} skipped)")
    if ips:
        print(f"[{source['name']}] {status}: {isp_relative} ({len(ips)} IPv4/CIDR values)")
    return changed


def list_github_files(
    api_url: str,
    timeout: int,
    headers: dict[str, str],
    recursive: bool,
) -> list[dict[str, Any]]:
    listing = fetch_json(api_url, timeout, headers)
    if not isinstance(listing, list):
        raise SyncError("GitHub directory API did not return a file listing")
    files: list[dict[str, Any]] = []
    for item in listing:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "file":
            files.append(item)
        elif recursive and item_type == "dir" and item.get("url"):
            files.extend(list_github_files(str(item["url"]), timeout, headers, True))
    return files


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_payload(payload: bytes, source_format: str) -> Any:
    source_format = source_format.lower()
    if source_format == "json":
        try:
            return json.loads(payload.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SyncError(f"Unable to parse JSON upstream content: {exc}") from exc

    if source_format in {"rss", "atom", "xml"}:
        try:
            return parse_xml_records(payload)
        except (ET.ParseError, UnicodeDecodeError) as exc:
            raise SyncError(f"Unable to parse XML upstream content: {exc}") from exc

    if source_format == "text":
        return payload.decode("utf-8-sig").splitlines()

    raise SyncError(f"Unsupported source format: {source_format}")


def parse_xml_records(payload: bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(payload)
    candidates = [element for element in root.iter() if _local_name(element.tag) in {"item", "entry"}]
    records: list[dict[str, Any]] = []
    for element in candidates:
        record: dict[str, Any] = {}
        for child in element:
            key = _local_name(child.tag)
            value = (child.text or "").strip()
            if key == "link" and not value:
                value = child.attrib.get("href", "")
            record[key] = value
        records.append(record)
    return records


def get_path(value: Any, path: str | None, default: Any = None) -> Any:
    if not path:
        return value
    current = value
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
            current = current[int(segment)]
        else:
            return default
    return current


def as_records(payload: Any, root_path: str | None) -> list[Any]:
    selected = get_path(payload, root_path, payload)
    if selected is None:
        return []
    if isinstance(selected, list):
        return selected
    if isinstance(selected, dict):
        return [selected]
    if isinstance(selected, str):
        return [{"value": selected}]
    raise SyncError("The configured transform root must resolve to an object, array, or string")


def transform_records(payload: Any, transform: dict[str, Any]) -> list[dict[str, Any]]:
    fields = transform.get("fields", {})
    defaults = transform.get("defaults", {})
    if not isinstance(fields, dict):
        raise SyncError("transform.fields must be an object")
    if not isinstance(defaults, dict):
        raise SyncError("transform.defaults must be an object")

    records: list[dict[str, Any]] = []
    for item in as_records(payload, transform.get("root")):
        result: dict[str, Any] = {}
        for target, source_path in fields.items():
            value = get_path(item, source_path, defaults.get(target))
            result[str(target)] = value
        for key, value in defaults.items():
            result.setdefault(str(key), value)
        records.append(result)

    sort_by = transform.get("sort_by")
    if sort_by:
        reverse = bool(transform.get("reverse", False))
        records.sort(key=lambda row: str(row.get(sort_by, "")), reverse=reverse)
    return records


def format_json(records: list[dict[str, Any]], source_name: str, generated_at: str) -> str:
    return json.dumps(
        {"source": source_name, "generated_at": generated_at, "items": records},
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def format_jsonl(records: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records)


def format_csv(records: list[dict[str, Any]]) -> str:
    fieldnames: list[str] = []
    for row in records:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    if fieldnames:
        writer.writeheader()
        writer.writerows(records)
    return output.getvalue()


class SafeFormatDict(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def format_markdown(records: list[dict[str, Any]], transform: dict[str, Any]) -> str:
    template = transform.get("markdown_template", "- {title}\n")
    lines: list[str] = []
    for row in records:
        values = SafeFormatDict(row)
        if "url" in row and row.get("url"):
            values.setdefault("link", row["url"])
        if row.get("published_at"):
            values.setdefault("published_suffix", f" ({row['published_at']})")
        else:
            values.setdefault("published_suffix", "")
        lines.append(template.format_map(values).rstrip())
    return "\n\n".join(lines) + ("\n" if lines else "")


def render(records: list[dict[str, Any]], source: dict[str, Any], generated_at: str) -> str:
    output_format = str(source.get("output_format", "json")).lower()
    transform = source.get("transform", {})
    if output_format == "json":
        return format_json(records, str(source["name"]), generated_at)
    if output_format == "jsonl":
        return format_jsonl(records)
    if output_format == "csv":
        return format_csv(records)
    if output_format in {"md", "markdown"}:
        return format_markdown(records, transform)
    raise SyncError(f"Unsupported output format: {output_format}")


def write_if_changed(path: Path, content: str, dry_run: bool) -> bool:
    if dry_run:
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old == content:
        return False
    path.write_text(content, encoding="utf-8", newline="\n")
    return True


def sync_source(source: dict[str, Any], project_root: Path, dry_run: bool) -> bool:
    if source.get("type") == "github_directory":
        return sync_github_directory(source, project_root, dry_run)
    if source.get("type") == "rule_list":
        return sync_rule_list(source, project_root, dry_run)
    for required in ("name", "url", "output"):
        if not source.get(required):
            raise SyncError(f"Each source requires '{required}'")
    payload = fetch(
        str(source["url"]),
        int(source.get("timeout_seconds", 30)),
        {str(k): str(v) for k, v in source.get("headers", {}).items()},
    )
    parsed = parse_payload(payload, str(source.get("format", "json")))
    records = transform_records(parsed, source.get("transform", {}))
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    content = render(records, source, generated_at)
    output = safe_relative_path(project_root, str(source["output"]), "output")
    changed = write_if_changed(output, content, dry_run)
    status = "would update" if dry_run else ("updated" if changed else "unchanged")
    print(f"[{source['name']}] {status}: {output} ({len(records)} records)")
    return changed


def run(config_path: Path, project_root: Path, only: set[str] | None, dry_run: bool) -> int:
    config = load_config(config_path)
    sources = config.get("sources", [])
    invalid_sources = [source for source in sources if not isinstance(source, dict)]
    if invalid_sources:
        raise SyncError("Each item in sources must be an object")
    selected = [source for source in sources if not only or source.get("name") in only]
    if only:
        missing = only - {source.get("name") for source in selected}
        if missing:
            raise SyncError(f"Unknown source name(s): {', '.join(sorted(missing))}")
    if not selected:
        print("No sources configured; nothing to sync.")
        return 0

    for source in selected:
        sync_source(source, project_root, dry_run)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/config.json", help="Path to the JSON config file")
    parser.add_argument("--only", action="append", help="Sync only this named source; may be repeated")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and render without writing files")
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[1]
    try:
        return run(project_root / args.config, project_root, set(args.only or []), args.dry_run)
    except SyncError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
