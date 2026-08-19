import json
import ipaddress
import io
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from sync_transform import (
    extract_clash_all_values,
    extract_clash_ikuai_values,
    extract_v2ray_all_values,
    extract_v2ray_ikuai_values,
    collect_proxy_outputs,
    format_csv,
    format_markdown,
    list_github_archive_files,
    _render_directory_readme,
    parse_xml_records,
    strip_json_comments,
    transform_records,
)


def test_transform_json_records_with_root_mapping_and_defaults():
    payload = {
        "data": {
            "items": [
                {"name": "Second", "meta": {"url": "/second"}, "date": "2026-08-02"},
                {"name": "First", "meta": {"url": "/first"}},
            ]
        }
    }
    result = transform_records(
        payload,
        {
            "root": "data.items",
            "fields": {"title": "name", "url": "meta.url", "published_at": "date"},
            "defaults": {"published_at": "unknown"},
            "sort_by": "title",
            "reverse": True,
        },
    )
    assert result == [
        {"title": "Second", "url": "/second", "published_at": "2026-08-02"},
        {"title": "First", "url": "/first", "published_at": "unknown"},
    ]


def test_parse_rss_and_render_markdown():
    xml = b"""<rss><channel><item><title>News</title><link>https://example.com/n</link><description>Body</description></item></channel></rss>"""
    records = transform_records(
        parse_xml_records(xml),
        {"fields": {"title": "title", "url": "link", "summary": "description"}},
    )
    assert format_markdown(records, {"markdown_template": "- [{title}]({url})\n  {summary}"}) == "- [News](https://example.com/n)\n  Body\n"


def test_format_csv_contains_union_of_fields():
    csv_text = format_csv([{"title": "A", "url": "u"}, {"title": "B", "summary": "s"}])
    assert csv_text.splitlines() == [
        "title,url,summary",
        "A,u,",
        "B,,s",
    ]


def test_json_output_is_utf8_friendly():
    from sync_transform import format_json

    result = json.loads(format_json([{"title": "中文"}], "source", "2026-08-19T00:00:00+00:00"))
    assert result["items"][0]["title"] == "中文"


def test_jsonc_comments_are_removed_without_breaking_urls():
    config = strip_json_comments(
        '{\n'
        '  // 中文注释\n'
        '  "url": "https://example.com/rules//latest", /* 行内注释 */\n'
        '  "name": "测试"\n'
        '}\n'
    )
    parsed = json.loads(config)
    assert parsed == {"url": "https://example.com/rules//latest", "name": "测试"}


def test_extract_all_clash_values_from_yaml_and_ip_rules():
    content = """# comment
payload:
DOMAIN,Example.COM
- DOMAIN-SUFFIX,example.org
DOMAIN-KEYWORD,ignored
DOMAIN-SUFFIX,*.invalid.example
DOMAIN,example.com
"""
    values, stats = extract_clash_all_values(content)
    assert values == ["*.invalid.example", "example.com", "example.org", "ignored"]
    assert stats["duplicates"] == 1


def test_extract_v2ray_all_values_into_one_output():
    content = """example.com
full:api.example.com
domain:cdn.example.com
ip:1.2.3.4
cidr:1.2.3.0/24
regexp:(^|\\.)example\\.net$
unknown:value
"""
    values, stats = extract_v2ray_all_values(content)
    assert values == [
        "(^|\\.)example\\.net$",
        "1.2.3.0/24",
        "1.2.3.4",
        "api.example.com",
        "cdn.example.com",
        "example.com",
        "value",
    ]
    assert stats["accepted"] == 7


def test_extract_clash_values_into_ikuai_domain_and_isp_lists():
    content = """# comment
payload:
DOMAIN,Example.COM
- DOMAIN-SUFFIX,example.org
IP-CIDR,1.2.3.0/24
IP,1.2.3.4
IP-CIDR6,2001:db8::/32
"""
    domains, ips, stats = extract_clash_ikuai_values(content)
    assert domains == ["example.com", "example.org"]
    assert ips == ["1.2.3.0/24", "1.2.3.4"]
    assert stats["domains"] == 2
    assert stats["ips"] == 2


def test_extract_v2ray_values_into_ikuai_domain_and_isp_lists():
    content = """example.com
full:api.example.com
domain:cdn.example.com
ip:1.2.3.4
cidr:1.2.3.0/24
regexp:(^|\\.)example\\.net$
"""
    domains, ips, stats = extract_v2ray_ikuai_values(content)
    assert domains == ["(^|\\.)example\\.net$", "api.example.com", "cdn.example.com", "example.com"]
    assert ips == ["1.2.3.0/24", "1.2.3.4"]
    assert stats["accepted"] == 6


def test_collect_proxy_outputs_deduplicates_and_removes_china_values():
    with tempfile.TemporaryDirectory() as temporary_dir:
        tmp_path = Path(temporary_dir)
        source_dir = tmp_path / "data" / "source"
        source_dir.mkdir(parents=True)
        (source_dir / "a_domain.txt").write_text(
            "example.com\ncn.example\nexample.com\n", encoding="utf-8"
        )
        (source_dir / "a_isp.txt").write_text(
            "1.2.3.0/24\n1.2.3.4\n10.0.0.0/8\n", encoding="utf-8"
        )
        domains, ips, stats = collect_proxy_outputs(
            tmp_path,
            ["data/source"],
            {"cn.example"},
            [ipaddress.ip_network("10.0.0.0/8")],
        )
        assert domains == ["example.com"]
        assert ips == ["1.2.3.0/24"]
        assert stats["raw_domains"] == 2
        assert stats["raw_ips"] == 3


def test_directory_readme_lists_available_outputs_and_raw_links():
    with tempfile.TemporaryDirectory() as temporary_dir:
        output_root = Path(temporary_dir) / "data" / "source"
        output_root.mkdir(parents=True)
        (output_root / "rules_domain.txt").write_text("example.com\n", encoding="utf-8")
        (output_root / "rules_isp.txt").write_text("1.2.3.0/24\n", encoding="utf-8")
        readme = _render_directory_readme(
            output_root,
            output_root,
            Path(temporary_dir),
            [{"name": "demo", "upstream_url": "https://example.com/rules"}],
            {
                "repository_url": "https://github.com/example/repo",
                "repository_branch": "main",
            },
        )
        assert "[demo](https://example.com/rules)" in readme
        assert "rules_domain.txt" in readme
        assert "rules_isp.txt" in readme
        assert "https://raw.githubusercontent.com/example/repo/main/data/source/rules_domain.txt" in readme


def test_github_archive_listing_keeps_nested_paths():
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("repo-main/rule/Clash/App/App.list", "DOMAIN,app.example")
        archive.writestr("repo-main/rule/Other/ignored.list", "DOMAIN,ignored.example")

    import sync_transform

    original_fetch = sync_transform.fetch
    sync_transform.fetch = lambda *args, **kwargs: archive_buffer.getvalue()
    try:
        files, contents = list_github_archive_files(
            "https://example.com/archive.zip",
            "rule/Clash",
            30,
            {},
        )
    finally:
        sync_transform.fetch = original_fetch
    assert [item["path"] for item in files] == ["rule/Clash/App/App.list"]
    assert contents["repo-main/rule/Clash/App/App.list"] == b"DOMAIN,app.example"
