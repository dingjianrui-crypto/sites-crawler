from nsfw_discovery.domain import registrable_domain, same_domain


def test_registrable_domain_normalizes_common_hosts() -> None:
    assert registrable_domain("https://www.example.com/path") == "example.com"
    assert registrable_domain("app.example.co.uk") == "example.co.uk"


def test_same_domain_accepts_subdomains() -> None:
    assert same_domain("https://contact.example.com/about", "example.com")
    assert not same_domain("https://example.net/about", "example.com")
