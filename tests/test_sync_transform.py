import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from sync_transform import (
    extract_clash_all_values,
    extract_v2ray_all_values,
    format_csv,
    format_markdown,
    parse_xml_records,
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
