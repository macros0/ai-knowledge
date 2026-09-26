"""HTML mail links survive normalization without introducing active markup."""
import socket
from email.message import EmailMessage

import pytest
from docparser import parse_document_result

from tests.mail_fixtures import compound_bytes


@pytest.fixture(params=["eml", "msg"])
def parse_html(tmp_path, request, monkeypatch):
    def no_network(*_args, **_kwargs):
        raise AssertionError("HTML extraction must stay offline")
    monkeypatch.setattr(socket, "create_connection", no_network)

    def parse(html):
        path = tmp_path / f"links.{request.param}"
        if request.param == "eml":
            message = EmailMessage()
            message["From"] = "sender@example.test"
            message.set_content(html, subtype="html")
            payload = message.as_bytes()
        else:
            payload = compound_bytes({
                "__properties_version1.0": b"\0" * 32,
                "__substg1.0_10130102": html.encode("ascii"),
            })
        path.write_bytes(payload)
        return parse_document_result(path)
    return parse


def test_html_mail_preserves_safe_links_in_paragraphs_and_tables(parse_html):
    result = parse_html(
        '<p>Read <a href="https://example.test/a(b)?x=1&amp;y=2">the <b>terms</b></a>.</p>'
        '<table><tr><th>Contact</th></tr><tr><td><a href="mailto:team@example.test">Team</a></td></tr></table>'
    )
    assert [(block.type, block.text) for block in result.blocks] == [
        ("paragraph", "Read [the terms](https://example.test/a%28b%29?x=1\\&y=2)."),
        ("table", "| Contact |\n|---|\n| [Team](mailto:team@example.test) |"),
    ]


@pytest.mark.parametrize("href", [
    "javascript:alert(1)", "java&#9;script:alert(1)", "data:text/html,evil",
    "file:///C:/secret", "//remote.example.test/", "/api/admin", "https://[bad",
])
def test_html_mail_drops_unsafe_or_ambiguous_link_targets_but_keeps_label(parse_html, href):
    result = parse_html(f'<p>See <a href="{href}">the terms</a>.</p>')
    assert [block.text for block in result.blocks] == ["See the terms."]


def test_html_text_and_link_labels_cannot_inject_markdown_images_or_raw_html(parse_html):
    result = parse_html(
        '<p>![pixel](https://tracker.example.test/p.gif) &lt;img src=x&gt; '
        '<a href="https://example.test/terms">[terms] ![other](https://tracker.example.test/q.gif)</a></p>'
    )
    assert [block.text for block in result.blocks] == [
        (r"\!\[pixel\](https://tracker.example.test/p.gif) \<img src=x\> "
         r"[\[terms\] \!\[other\](https://tracker.example.test/q.gif)](https://example.test/terms)"),
    ]


def test_html_nested_and_unclosed_anchors_do_not_swallow_other_paragraphs(parse_html):
    result = parse_html(
        '<p><a href="https://example.test/one">outer<a href="https://example.test/two">inner</a>tail</a></p>'
        '<p><a href="https://example.test/three">unclosed</p><p>last paragraph</p>'
    )
    assert [block.text for block in result.blocks] == [
        "[outer](https://example.test/one)[inner](https://example.test/two)tail",
        "[unclosed](https://example.test/three)", "last paragraph",
    ]


def test_html_link_destination_cannot_break_out_of_markdown_target(parse_html):
    result = parse_html('<p><a href="https://example.test/a)![img](https://tracker.test/p) q|r">label</a></p>')
    assert [block.text for block in result.blocks] == [
        "[label](https://example.test/a%29!%5Bimg%5D%28https://tracker.test/p%29%20q%7Cr)",
    ]


def test_html_table_literal_pipes_and_brackets_do_not_change_columns(parse_html):
    result = parse_html('<table><tr><th>Name</th><th>Value</th></tr><tr><td>A|B</td><td>[text]</td></tr></table>')
    assert result.blocks[0].text == "| Name | Value |\n|---|---|\n| A\\|B | \\[text\\] |"


def test_html_split_entity_like_text_is_not_decoded_again_by_markdown(parse_html):
    result = parse_html('<p><span>&amp;</span><span>copy;</span> &amp;#x21;</p>')
    assert result.blocks[0].text == r"\&copy; \&\#x21;"


def test_html_link_entity_like_query_is_not_decoded_again_by_markdown(parse_html):
    result = parse_html('<p><a href="https://example.test/?q=&amp;copy;&amp;x=1">Target</a></p>')
    assert result.blocks[0].text == r"[Target](https://example.test/?q=\&copy;\&x=1)"


def test_html_literal_paragraphs_do_not_become_markdown_rules_or_lists(parse_html):
    result = parse_html('<p>---</p><p>- item</p><p>1. term</p><p>+ next</p>')
    assert [block.text for block in result.blocks] == [r"\---", r"\- item", r"1\. term", r"\+ next"]
