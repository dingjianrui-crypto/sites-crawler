from nsfw_discovery.crawler import select_priority_links


def test_select_priority_links_stays_domain_bound_and_prioritized() -> None:
    links = [
        "https://example.com/gallery/image.jpg",
        "https://other.com/contact",
        "https://example.com/pricing?ref=home",
        "https://example.com/contact#footer",
        "https://blog.example.com/about",
    ]
    selected = select_priority_links("example.com", links, 3)
    assert selected == [
        "https://blog.example.com/about",
        "https://example.com/contact",
        "https://example.com/pricing",
    ]
