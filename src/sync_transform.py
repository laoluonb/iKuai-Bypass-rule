#!/usr/bin/env python3
"""Fetch upstream content and write transformed records."""

from __future__ import annotations

import argparse
from bisect import bisect_right
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
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?$", re.IGNORECASE)
CLASH_DOMAIN_RULES = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-FULL"}
CLASH_IP_RULES = {"IP", "IP-CIDR", "IP-CIDR6"}
V2RAY_DOMAIN_PREFIXES = {"domain", "full"}
V2RAY_IP_PREFIXES = {"ip", "cidr", "ip-cidr", "ip-cidr6"}
V2RAY_VALUE_PREFIXES = V2RAY_DOMAIN_PREFIXES | V2RAY_IP_PREFIXES | {"regexp"}


class SyncError(Exception):
    """An expected configuration, network, parsing, or output error."""


def strip_json_comments(text: str) -> str:
    """Remove JSONC // and /* */ comments without touching quoted URLs."""
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    in_line_comment = False
    in_block_comment = False

    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""

        if in_line_comment:
            if char in "\r\n":
                in_line_comment = False
                output.append(char)
            else:
                output.append(" ")
            index += 1
            continue

        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
                output.extend((" ", " "))
                index += 2
            else:
                output.append("\n" if char in "\r\n" else " ")
                index += 1
            continue

        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            output.append(char)
            index += 1
        elif char == "/" and next_char == "/":
            in_line_comment = True
            output.extend((" ", " "))
            index += 2
        elif char == "/" and next_char == "*":
            in_block_comment = True
            output.extend((" ", " "))
            index += 2
        else:
            output.append(char)
            index += 1

    return "".join(output)


def load_config(path: Path) -> dict[str, Any]:
    try:
        config_text = strip_json_comments(path.read_text(encoding="utf-8"))
        config = json.loads(config_text)
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
    """Return only a real domain suitable for iKuai domain lists."""
    # Keep the argument for compatibility with older callers, but never pass
    # through arbitrary rule expressions such as regexes or keywords.
    del preserve_case
    candidate = value.strip().strip("\"'")
    if not candidate or any(char in candidate for char in "^$()[]{}|+?*\\"):
        return None
    if candidate.startswith(("||", "!", ".", "http://", "https://")):
        return None
    domain = normalize_domain(candidate)
    # iKuai's domain split API accepts fully qualified domain names, not
    # single-label hostnames such as localhost, google, or ca.
    if not domain or "." not in domain:
        return None
    return domain


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
        rule_type = parts[0].upper()
        if len(parts) >= 2:
            if rule_type not in CLASH_DOMAIN_RULES | CLASH_IP_RULES:
                stats["skipped"] += 1
                continue
            candidate = parts[1]
        else:
            # Some upstream .list files contain one bare domain per line.
            candidate = parts[0]
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
        if separator:
            if prefix not in V2RAY_DOMAIN_PREFIXES | V2RAY_IP_PREFIXES:
                # regexp:, keyword:, geosite:, and unknown expressions cannot
                # be represented by iKuai's plain domain/IP list format.
                stats["skipped"] += 1
                continue
            candidate = payload.strip()
        else:
            candidate = line
        _classify_rule_value(candidate, domains, ips, stats)
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


def remove_file_if_exists(path: Path, dry_run: bool) -> bool:
    if dry_run or not path.exists():
        return False
    path.unlink()
    return True


def _relative_markdown_path(target: Path, base: Path) -> str:
    """Return a portable relative link for a Markdown file."""
    return Path(os.path.relpath(target, base)).as_posix()


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _repository_raw_url(config: dict[str, Any], relative_path: str) -> str | None:
    repository_url = str(config.get("repository_url", "")).rstrip("/")
    if not repository_url:
        server = os.getenv("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
        repository = os.getenv("GITHUB_REPOSITORY", "").strip("/")
        if repository:
            repository_url = f"{server}/{repository}"
    if not repository_url:
        return None
    branch = str(config.get("repository_branch", "main"))
    encoded_path = quote(relative_path.replace("\\", "/"), safe="/")
    parsed = urlparse(repository_url)
    if parsed.hostname == "github.com":
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2:
            owner, repository = parts[0], parts[1]
            return (
                f"https://raw.githubusercontent.com/{quote(owner, safe='')}/"
                f"{quote(repository, safe='')}/{quote(branch, safe='')}/{encoded_path}"
            )
    return f"{repository_url}/raw/refs/heads/{quote(branch, safe='')}/{encoded_path}"


def _repository_cdn_url(config: dict[str, Any], relative_path: str) -> str | None:
    """Return a jsDelivr URL for a file in a configured GitHub repository."""
    repository_url = str(config.get("repository_url", "")).rstrip("/")
    parsed = urlparse(repository_url)
    if parsed.hostname != "github.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    owner, repository = parts[0], parts[1]
    branch = str(config.get("repository_branch", "main"))
    encoded_path = quote(relative_path.replace("\\", "/"), safe="/")
    return (
        f"https://cdn.jsdelivr.net/gh/{quote(owner, safe='')}/"
        f"{quote(repository, safe='')}@{quote(branch, safe='')}/{encoded_path}"
    )


def _source_upstream_url(source: dict[str, Any]) -> str:
    return str(source.get("upstream_url") or source.get("url") or source.get("api_url") or "")


def _readme_source_lines(sources: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    seen: set[tuple[str, str]] = set()
    for source in sources:
        name = str(source.get("name", "同步源"))
        url = _source_upstream_url(source)
        key = (name, url)
        if key in seen:
            continue
        seen.add(key)
        if url:
            lines.append(f"- [{name}]({url})")
        elif source.get("description"):
            lines.append(f"- {name}：{source['description']}")
        else:
            lines.append(f"- {name}")
    return lines


def _manifest_source_relative_path(source_path: str, sources: list[dict[str, Any]]) -> str:
    """Normalize old manifests that still include a GitHub directory prefix."""
    normalized = source_path.replace("\\", "/").lstrip("/")
    for source in sources:
        prefix = str(source.get("path_prefix", "")).strip("/")
        if prefix and (normalized == prefix or normalized.startswith(prefix + "/")):
            return normalized[len(prefix):].lstrip("/")
    return normalized


def _render_directory_readme(
    directory: Path,
    output_root: Path,
    project_root: Path,
    sources: list[dict[str, Any]],
    config: dict[str, Any],
) -> str:
    entries: dict[str, dict[str, Path]] = {}
    for path in sorted(directory.glob("*_domain.txt")) + sorted(directory.glob("*_isp.txt")):
        if path.name.endswith("_domain.txt"):
            base = path.name[: -len("_domain.txt")]
            entries.setdefault(base, {})["domain"] = path
        elif path.name.endswith("_isp.txt"):
            base = path.name[: -len("_isp.txt")]
            entries.setdefault(base, {})["isp"] = path

    title = directory.name or output_root.name
    lines = [
        f"# {title}",
        "",
        "本目录由 `upstream-sync-transformer` 自动同步上游规则，并转换为爱快可用的逐行格式。",
        "",
        "## 上游",
        "",
    ]
    upstream_lines = _readme_source_lines(sources)
    lines.extend(upstream_lines or ["- 未配置上游链接"])
    lines.extend([
        "",
        "## 规则文件",
        "",
        "| 规则名称 | 域名格式 | ISP/IP 格式 |",
        "| --- | --- | --- |",
    ])

    if not entries:
        lines.append("| 暂无有效规则 | - | - |")
    else:
        for base, paths in sorted(entries.items(), key=lambda item: item[0].lower()):
            domain_path = paths.get("domain")
            isp_path = paths.get("isp")
            domain_link = "-"
            isp_link = "-"
            if domain_path:
                relative = domain_path.relative_to(project_root).as_posix()
                local_link = _relative_markdown_path(domain_path, directory)
                raw_url = _repository_raw_url(config, relative)
                domain_link = f"[下载]({local_link})"
                if raw_url:
                    domain_link += f" / [Raw]({raw_url})"
            if isp_path:
                relative = isp_path.relative_to(project_root).as_posix()
                local_link = _relative_markdown_path(isp_path, directory)
                raw_url = _repository_raw_url(config, relative)
                isp_link = f"[下载]({local_link})"
                if raw_url:
                    isp_link += f" / [Raw]({raw_url})"
            lines.append(f"| `{base}` | {domain_link} | {isp_link} |")

    lines.extend([
        "",
        "## 格式说明",
        "",
        "- 域名文件：每行一条经过校验的真实域名；正则、关键词、通配符和未知格式会跳过。",
        "- ISP/IP 文件：每行一条 IPv4 或 IPv4/CIDR；没有有效内容时不生成。",
        "- 同一文件内及合并文件均已去重并排序。",
        "",
        "本文件由 GitHub Actions 每日自动更新。",
        "",
    ])
    return "\n".join(lines)


def _render_adblock_readme(
    output_root: Path,
    project_root: Path,
    aggregate: dict[str, Any],
    config: dict[str, Any],
) -> str:
    """Render a detailed SmartDNS ad-blocking README."""
    output_path = safe_relative_path(
        project_root, str(aggregate["domain_output"]), "adblock domain_output"
    )
    domains = _read_nonempty_lines(output_path)
    relative_output = output_path.relative_to(project_root).as_posix()
    local_link = _relative_markdown_path(output_path, output_root)
    raw_url = _repository_raw_url(config, relative_output)
    cdn_url = _repository_cdn_url(config, relative_output)
    updated_at = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "# 去广告",
        "",
        "## 前言",
        "",
        "去广告规则由本项目每日自动同步、转换和去重生成。",
        "",
        "规则数据来自互联网公开项目，仅用于 SmartDNS 域名级广告、跟踪和劫持拦截。",
        "",
        "## 规则说明",
        "",
        "- 输出只包含合法完整域名，每行一条，不包含 Clash 类型前缀、IP、CIDR、正则或通配符。",
        "- 多个上游规则会合并、去重，并排除配置的直连和放行域名。",
        "- DNS 去广告无法处理与正常内容共用同一域名的广告，且可能存在误拦截。",
        "",
        "## 规则统计",
        "",
        f"最后更新时间：`{updated_at}`（北京时间）",
        "",
        "| 类型 | 数量（条） |",
        "| --- | ---: |",
        f"| DOMAIN | {len(domains)} |",
        f"| TOTAL | {len(domains)} |",
        "",
        "## SmartDNS",
        "",
        "### 下载设置",
        "",
        "```text",
        "文件名：ad_domain.txt",
        "保存目录：/etc/smartdns/domain-set",
        f"下载地址：{raw_url or relative_output}",
        "```",
        "",
        "### 自定义配置",
        "",
        "```conf",
        "# 加载广告域名集",
        "domain-set -name adblock -type list -file /etc/smartdns/domain-set/ad_domain.txt",
        "",
        "# 命中广告域名后返回空地址，同时阻止 A 和 AAAA 解析",
        "address /domain-set:adblock/#",
        "```",
        "",
        "广告拦截规则应与代理规则同时保留；`address` 负责拦截广告域名，代理域名仍使用 `nameserver /domain-set:proxylist/proxy` 转发到 OpenClash。",
        "",
        "## 规则链接",
        "",
        "### MAIN 分支（每日更新）",
        "",
        f"- [下载规则]({local_link})",
    ]
    if raw_url:
        lines.append(f"- [Raw 订阅]({raw_url})")
    if cdn_url:
        lines.extend(["", "### MAIN 分支 CDN", "", f"- [jsDelivr 订阅]({cdn_url})"])

    lines.extend(["", "## 包含规则", ""])
    included_names = [str(value) for value in aggregate.get("included_rule_names", [])]
    lines.extend(f"- `{name}`" for name in included_names)
    if not included_names:
        lines.append("- 以配置文件中的输入规则为准")

    lines.extend(["", "## 排除规则", ""])
    excluded_names = [str(value) for value in aggregate.get("excluded_rule_names", [])]
    lines.extend(f"- `{name}`" for name in excluded_names)
    if not excluded_names:
        lines.append("- 未配置额外排除规则")

    lines.extend(["", "## 数据来源", ""])
    source_urls = [str(value) for value in aggregate.get("source_urls", [])]
    lines.extend(f"- {url}" for url in source_urls)
    if not source_urls:
        lines.append("- 以 `config/config.json` 中的输入文件为准")

    lines.extend([
        "",
        "感谢各上游规则维护者的持续更新。规则由 GitHub Actions 每天北京时间 `03:00` 自动生成。",
        "",
    ])
    return "\n".join(lines)


def generate_directory_readmes(
    source_specs: list[dict[str, Any]],
    project_root: Path,
    config: dict[str, Any],
    dry_run: bool,
) -> bool:
    """Create an index README in every generated output directory."""
    grouped: dict[Path, list[dict[str, Any]]] = {}
    for source in source_specs:
        configured = source.get("output_dir")
        if not configured:
            continue
        output_root = safe_relative_path(project_root, str(configured), "README output_dir")
        grouped.setdefault(output_root, []).append(source)

    changed = False
    for output_root, sources in grouped.items():
        generated_dirs: set[Path] = {output_root}
        if output_root.exists():
            generated_dirs.update(path.parent for path in output_root.rglob("*_domain.txt"))
            generated_dirs.update(path.parent for path in output_root.rglob("*_isp.txt"))
            for manifest_path in output_root.glob(".sync-manifest*.json"):
                manifest = _read_json_object(manifest_path)
                for item in manifest.get("files", []):
                    source_path = str(item.get("source", "")) if isinstance(item, dict) else ""
                    source_path = _manifest_source_relative_path(source_path, sources)
                    if source_path:
                        generated_dirs.add(output_root / Path(source_path).parent)
        readme_manifest_path = output_root / ".readme-manifest.json"
        old_manifest = _read_json_object(readme_manifest_path)
        old_readmes = {str(path) for path in old_manifest.get("generated_readmes", [])}
        new_readmes: set[str] = set()
        for directory in sorted(generated_dirs, key=lambda path: path.as_posix()):
            readme_path = directory / "README.md"
            adblock_source = next(
                (source for source in sources if source.get("readme_style") == "adblock"),
                None,
            )
            if directory == output_root and adblock_source:
                content = _render_adblock_readme(
                    output_root, project_root, adblock_source, config
                )
            else:
                content = _render_directory_readme(
                    directory, output_root, project_root, sources, config
                )
            changed = write_text_if_changed(readme_path, content, dry_run) or changed
            new_readmes.add(readme_path.relative_to(project_root).as_posix())

        for stale in old_readmes - new_readmes:
            stale_path = safe_relative_path(project_root, stale, "managed README")
            changed = remove_file_if_exists(stale_path, dry_run) or changed

        readme_manifest = {
            "generated_readmes": sorted(new_readmes),
            "sources": [str(source.get("name", "")) for source in sources],
        }
        changed = write_text_if_changed(
            readme_manifest_path,
            json.dumps(readme_manifest, ensure_ascii=False, indent=2) + "\n",
            dry_run,
        ) or changed
    return changed


def _read_nonempty_lines(path: Path) -> list[str]:
    if not path.exists() or not path.is_file():
        return []
    lines: list[str] = []
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = _clean_rule_line(raw_line).split("#", 1)[0].strip()
        if line:
            lines.append(line)
    return lines


def fetch_china_exclusions(
    aggregate: dict[str, Any],
) -> tuple[set[str], list[ipaddress.IPv4Network]]:
    """Fetch configured Chinese domain and IPv4 exclusion lists."""
    timeout = int(aggregate.get("timeout_seconds", 60))
    headers = {"User-Agent": "upstream-sync-transformer/0.1"}
    headers.update({str(k): str(v) for k, v in aggregate.get("headers", {}).items()})
    blocked_domains: set[str] = set()
    blocked_ips: list[ipaddress.IPv4Network] = []

    for url in aggregate.get("china_domain_urls", []):
        content = fetch(str(url), timeout, headers).decode("utf-8-sig")
        for raw_line in content.splitlines():
            line = _clean_rule_line(raw_line).split("#", 1)[0].strip()
            if not line:
                continue
            prefix, separator, payload = line.partition(":")
            candidate = payload.strip() if separator and prefix.lower() in V2RAY_DOMAIN_PREFIXES else line
            domain = normalize_domain(candidate)
            if domain:
                blocked_domains.add(domain)

    for url in aggregate.get("china_isp_urls", []):
        content = fetch(str(url), timeout, headers).decode("utf-8-sig")
        for raw_line in content.splitlines():
            line = _clean_rule_line(raw_line).split("#", 1)[0].strip()
            if not line:
                continue
            prefix, separator, payload = line.partition(":")
            candidate = payload.strip() if separator else line
            if separator and prefix.lower() in V2RAY_IP_PREFIXES:
                line = candidate
            value = normalize_ip_cidr(line)
            if not value:
                continue
            network = ipaddress.ip_network(value, strict=False)
            if network.version == 4:
                blocked_ips.append(network)

    return blocked_domains, list(ipaddress.collapse_addresses(blocked_ips))


def is_china_domain(value: str, blocked_domains: set[str]) -> bool:
    """Match an exact Chinese domain or any subdomain of one."""
    domain = normalize_domain(value)
    if not domain:
        return False
    labels = domain.split(".")
    return any(".".join(labels[index:]) in blocked_domains for index in range(len(labels)))


def subtract_china_network(
    candidate: ipaddress.IPv4Network,
    blocked: list[ipaddress.IPv4Network],
    blocked_starts: list[int],
) -> list[ipaddress.IPv4Network]:
    """Remove blocked IPv4 ranges from one candidate network."""
    start = int(candidate.network_address)
    end = int(candidate.broadcast_address)
    index = max(0, bisect_right(blocked_starts, start) - 1)
    while index < len(blocked) and int(blocked[index].broadcast_address) < start:
        index += 1

    result: list[ipaddress.IPv4Network] = []
    cursor = start
    while index < len(blocked):
        blocked_start = int(blocked[index].network_address)
        blocked_end = int(blocked[index].broadcast_address)
        if blocked_start > end:
            break
        if blocked_end < cursor:
            index += 1
            continue
        if blocked_start > cursor:
            result.extend(
                ipaddress.summarize_address_range(
                    ipaddress.ip_address(cursor),
                    ipaddress.ip_address(min(end, blocked_start - 1)),
                )
            )
        cursor = max(cursor, blocked_end + 1)
        if cursor > end:
            break
        index += 1

    if cursor <= end:
        result.extend(
            ipaddress.summarize_address_range(
                ipaddress.ip_address(cursor), ipaddress.ip_address(end)
            )
        )
    return result


def format_ikuai_network(network: ipaddress.IPv4Network) -> str:
    """Keep host IPs compact while retaining CIDR notation for networks."""
    if network.prefixlen == network.max_prefixlen:
        return str(network.network_address)
    return str(network)


def collect_proxy_outputs(
    project_root: Path,
    input_dirs: list[str],
    blocked_domains: set[str],
    blocked_ips: list[ipaddress.IPv4Network],
) -> tuple[list[str], list[str], dict[str, int]]:
    """Collect, deduplicate, and filter all generated source outputs."""
    raw_domains: set[str] = set()
    raw_ips: set[str] = set()
    for configured_dir in input_dirs:
        root = safe_relative_path(project_root, str(configured_dir), "proxy input_dir")
        if not root.exists():
            continue
        for path in root.rglob("*_domain.txt"):
            raw_domains.update(_read_nonempty_lines(path))
        for path in root.rglob("*_isp.txt"):
            raw_ips.update(_read_nonempty_lines(path))

    domains = sorted(value for value in raw_domains if not is_china_domain(value, blocked_domains))
    blocked_starts = [int(network.network_address) for network in blocked_ips]
    filtered_networks: list[ipaddress.IPv4Network] = []
    for value in raw_ips:
        normalized = normalize_ip_cidr(value)
        if not normalized:
            continue
        candidate = ipaddress.ip_network(normalized, strict=False)
        if candidate.version != 4:
            continue
        filtered_networks.extend(
            subtract_china_network(candidate, blocked_ips, blocked_starts)
        )

    ips = sorted(
        {format_ikuai_network(network) for network in ipaddress.collapse_addresses(filtered_networks)},
        key=lambda value: (":" in value, value),
    )
    stats = {
        "raw_domains": len(raw_domains),
        "raw_ips": len(raw_ips),
        "domains": len(domains),
        "ips": len(ips),
        "blocked_domains": len(raw_domains) - len(domains),
    }
    return domains, ips, stats


def collect_adblock_outputs(
    project_root: Path,
    input_files: list[str],
    exclude_files: list[str],
) -> tuple[list[str], dict[str, int]]:
    """Collect and deduplicate ad domains while honoring allow-list files."""
    raw_domains: set[str] = set()
    excluded_domains: set[str] = set()

    for configured_file in input_files:
        path = safe_relative_path(project_root, configured_file, "adblock input_file")
        for value in _read_nonempty_lines(path):
            domain = normalize_for_domain_output(value)
            if domain:
                raw_domains.add(domain)

    for configured_file in exclude_files:
        path = safe_relative_path(project_root, configured_file, "adblock exclude_file")
        for value in _read_nonempty_lines(path):
            domain = normalize_for_domain_output(value)
            if domain:
                excluded_domains.add(domain)

    domains = sorted(
        domain for domain in raw_domains if not is_china_domain(domain, excluded_domains)
    )
    return domains, {
        "raw_domains": len(raw_domains),
        "excluded_domains": len(raw_domains) - len(domains),
        "domains": len(domains),
        "allowlist_domains": len(excluded_domains),
    }


def sync_adblock_aggregate(
    aggregate: dict[str, Any],
    project_root: Path,
    dry_run: bool,
) -> bool:
    """Generate one SmartDNS-compatible, deduplicated advertising domain list."""
    if not aggregate.get("enabled", True):
        return False
    for key in ("output_dir", "domain_output"):
        if not aggregate.get(key):
            raise SyncError(f"adblock_aggregate requires '{key}'")

    domains, stats = collect_adblock_outputs(
        project_root,
        [str(value) for value in aggregate.get("input_files", [])],
        [str(value) for value in aggregate.get("exclude_files", [])],
    )
    domain_path = safe_relative_path(
        project_root, str(aggregate["domain_output"]), "adblock domain_output"
    )
    changed = False
    if domains:
        changed = write_text_if_changed(
            domain_path, "\n".join(domains) + "\n", dry_run
        ) or changed
    else:
        changed = remove_file_if_exists(domain_path, dry_run) or changed

    manifest_path = safe_relative_path(
        project_root,
        str(aggregate.get("manifest", "data/adblock/.sync-manifest.json")),
        "adblock manifest",
    )
    generated_files = [manifest_path.relative_to(project_root).as_posix()]
    if domains:
        generated_files.append(domain_path.relative_to(project_root).as_posix())
    manifest = {
        "source": "adblock-aggregate",
        "input_files": [str(value) for value in aggregate.get("input_files", [])],
        "exclude_files": [str(value) for value in aggregate.get("exclude_files", [])],
        "generated_files": sorted(generated_files),
        **stats,
    }
    changed = write_text_if_changed(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        dry_run,
    ) or changed
    status = "would update" if dry_run else ("updated" if changed else "unchanged")
    print(
        f"[adblock-aggregate] {status}: domains={len(domains)}, "
        f"deduplicated from {stats['raw_domains']}, excluded={stats['excluded_domains']}"
    )
    return changed


def sync_proxy_aggregate(
    aggregate: dict[str, Any],
    project_root: Path,
    dry_run: bool,
) -> bool:
    """Generate complete deduplicated proxy-only domain and IPv4 lists."""
    if not aggregate.get("enabled", True):
        return False
    required = ("output_dir", "domain_output", "isp_output")
    for key in required:
        if not aggregate.get(key):
            raise SyncError(f"proxy_aggregate requires '{key}'")

    blocked_domains, blocked_ips = fetch_china_exclusions(aggregate)
    domains, ips, stats = collect_proxy_outputs(
        project_root,
        [str(value) for value in aggregate.get("input_dirs", [])],
        blocked_domains,
        blocked_ips,
    )
    domain_path = safe_relative_path(project_root, str(aggregate["domain_output"]), "domain_output")
    isp_path = safe_relative_path(project_root, str(aggregate["isp_output"]), "isp_output")
    changed = False
    if domains:
        changed = write_text_if_changed(domain_path, "\n".join(domains) + "\n", dry_run) or changed
    else:
        changed = remove_file_if_exists(domain_path, dry_run) or changed
    if ips:
        changed = write_text_if_changed(isp_path, "\n".join(ips) + "\n", dry_run) or changed
    else:
        changed = remove_file_if_exists(isp_path, dry_run) or changed

    manifest_path = safe_relative_path(
        project_root,
        str(aggregate.get("manifest", "data/proxy/.sync-manifest.json")),
        "proxy manifest",
    )
    generated_files: list[str] = []
    if domains:
        generated_files.append(domain_path.relative_to(project_root).as_posix())
    if ips:
        generated_files.append(isp_path.relative_to(project_root).as_posix())
    generated_files.append(manifest_path.relative_to(project_root).as_posix())
    manifest = {
        "source": "proxy-aggregate",
        "input_dirs": [str(value) for value in aggregate.get("input_dirs", [])],
        "generated_files": sorted(generated_files),
        "blocked_domain_urls": [str(value) for value in aggregate.get("china_domain_urls", [])],
        "blocked_isp_urls": [str(value) for value in aggregate.get("china_isp_urls", [])],
        "raw_domains": stats["raw_domains"],
        "raw_ips": stats["raw_ips"],
        "domains": stats["domains"],
        "ips": stats["ips"],
        "blocked_domains": stats["blocked_domains"],
    }
    changed = write_text_if_changed(
        manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", dry_run
    ) or changed
    status = "would update" if dry_run else ("updated" if changed else "unchanged")
    print(
        f"[proxy-aggregate] {status}: domains={len(domains)}, IPv4/CIDR={len(ips)}, "
        f"deduplicated from domains={stats['raw_domains']}, IPs={stats['raw_ips']}"
    )
    return changed


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
    archive_contents: dict[str, bytes] = {}
    archive_url = str(source.get("archive_url", ""))
    if archive_url:
        directory_files, archive_contents = list_github_archive_files(
            archive_url,
            str(source.get("path_prefix", "")),
            timeout,
            headers,
        )
    else:
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
        if not item.get("download_url") and not item.get("archive_member"):
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
        output_basename = (
            source_relative_path.name
            if bool(source.get("include_source_extension", False))
            else output_stem
        )
        domain_path = output_dir / relative_parent / f"{output_basename}_domain.txt"
        isp_path = output_dir / relative_parent / f"{output_basename}_isp.txt"
        domain_relative = domain_path.relative_to(project_root).as_posix()
        isp_relative = isp_path.relative_to(project_root).as_posix()
        archive_member = str(item.get("archive_member", ""))
        if archive_member:
            raw_content = archive_contents[archive_member]
        else:
            raw_content = fetch(str(item["download_url"]), timeout, headers)
        content = raw_content.decode("utf-8-sig")
        domains, ips, value_stats = extract_clash_ikuai_values(content)
        if domains:
            generated_files.append(domain_relative)
        if ips:
            generated_files.append(isp_relative)
        all_domains.update(domains)
        all_ips.update(ips)
        manifest_files.append({
            "source": source_relative,
            "domain_output": domain_relative if domains else None,
            "isp_output": isp_relative if ips else None,
            "domains": len(domains),
            "ips": len(ips),
            "skipped": value_stats["skipped"],
        })
        if domains:
            changed = write_text_if_changed(domain_path, "\n".join(domains) + "\n", dry_run) or changed
        else:
            changed = remove_file_if_exists(domain_path, dry_run) or changed
        if ips:
            changed = write_text_if_changed(isp_path, "\n".join(ips) + "\n", dry_run) or changed
        else:
            changed = remove_file_if_exists(isp_path, dry_run) or changed
        status = "would update" if dry_run else "updated"
        if domains:
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
    if all_domains:
        generated_files.append(merged_domain_relative)
    if all_ips:
        generated_files.append(merged_isp_relative)
    merged_domains = sorted(all_domains)
    merged_ips = sorted(all_ips, key=lambda value: (":" in value, value))
    if merged_domains:
        changed = write_text_if_changed(merged_domain_output, "\n".join(merged_domains) + "\n", dry_run) or changed
    else:
        changed = remove_file_if_exists(merged_domain_output, dry_run) or changed
    if merged_ips:
        changed = write_text_if_changed(merged_isp_output, "\n".join(merged_ips) + "\n", dry_run) or changed
    else:
        changed = remove_file_if_exists(merged_isp_output, dry_run) or changed

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
    if merged_domains:
        print(f"[{source['name']}] {status}: {merged_domain_relative} ({len(merged_domains)} unique domains total)")
    if merged_ips:
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
    generated_files: list[str] = []
    domain_relative = domain_path.relative_to(project_root).as_posix()
    if domains:
        generated_files.append(domain_relative)
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
    if domains:
        changed = write_text_if_changed(domain_path, "\n".join(domains) + "\n", dry_run) or changed
    else:
        changed = remove_file_if_exists(domain_path, dry_run) or changed
    if ips:
        changed = write_text_if_changed(isp_path, "\n".join(ips) + "\n", dry_run) or changed
    else:
        changed = remove_file_if_exists(isp_path, dry_run) or changed

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
    if domains:
        print(f"[{source['name']}] {status}: {domain_relative} ({len(domains)} domains; {stats['skipped']} skipped)")
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


def list_github_archive_files(
    archive_url: str,
    path_prefix: str,
    timeout: int,
    headers: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    """Read a GitHub ZIP archive once and expose files below path_prefix."""
    archive = zipfile.ZipFile(io.BytesIO(fetch(archive_url, timeout, headers)))
    normalized_prefix = path_prefix.strip("/")
    files: list[dict[str, Any]] = []
    contents: dict[str, bytes] = {}
    for member in archive.infolist():
        if member.is_dir():
            continue
        member_name = member.filename.replace("\\", "/").lstrip("/")
        marker = f"/{normalized_prefix}/" if normalized_prefix else "/"
        marker_index = member_name.find(marker)
        if normalized_prefix:
            if marker_index < 0:
                continue
            source_path = member_name[marker_index + 1 :]
        else:
            source_path = member_name.split("/", 1)[-1]
        contents[member_name] = archive.read(member)
        files.append(
            {
                "type": "file",
                "name": Path(source_path).name,
                "path": source_path,
                "archive_member": member_name,
            }
        )
    return sorted(files, key=lambda item: str(item.get("path", ""))), contents


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

    aggregate = config.get("proxy_aggregate")
    if not only and isinstance(aggregate, dict):
        sync_proxy_aggregate(aggregate, project_root, dry_run)

    adblock_aggregate = config.get("adblock_aggregate")
    if not only and isinstance(adblock_aggregate, dict):
        sync_adblock_aggregate(adblock_aggregate, project_root, dry_run)

    readme_sources = list(sources)
    if isinstance(aggregate, dict) and aggregate.get("enabled", True):
        readme_sources.append(
            {
                "name": "proxy-aggregate",
                "output_dir": aggregate.get("output_dir", "data/proxy"),
                "description": "汇总全部同步源，去重并排除中国域名和中国 IPv4/CIDR",
            }
        )
    if isinstance(adblock_aggregate, dict) and adblock_aggregate.get("enabled", True):
        readme_sources.append(
            {
                **adblock_aggregate,
                "name": "adblock-aggregate",
                "readme_style": "adblock",
                "description": "汇总广告、隐私和劫持域名，去重并排除直连放行规则",
            }
        )
    generate_directory_readmes(readme_sources, project_root, config, dry_run)
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
