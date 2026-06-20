from nsfw_discovery.extract import extract_contacts, parse_html


def test_parse_html_extracts_title_text_and_links() -> None:
    html = """
    <html>
      <head><title>Example AI</title><script>ignored()</script></head>
      <body>
        <h1>AI adult generator</h1>
        <p>Compare partner services for uncensored AI images.
        <a href="/contact">Contact</a>
        and read more details.</p>
      </body>
    </html>
    """
    title, text, links, link_details = parse_html(html, "https://example.com/")
    assert title == "Example AI"
    assert "AI adult generator" in text
    assert links == ["https://example.com/contact"]
    assert link_details[0].url == "https://example.com/contact"
    assert link_details[0].text == "Contact"
    assert "uncensored AI images" in link_details[0].context


def test_extract_contacts_from_text_and_links() -> None:
    contacts = extract_contacts(
        "Email support@example.com",
        [
            "mailto:hello@example.com",
            "https://discord.gg/example",
            "https://t.me/example",
            "https://x.com/example",
        ],
    )
    assert contacts.emails == ["hello@example.com", "support@example.com"]
    assert contacts.discord == ["https://discord.gg/example"]
    assert contacts.telegram == ["https://t.me/example"]
    assert contacts.other == ["https://x.com/example"]
