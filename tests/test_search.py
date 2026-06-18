from nsfw_discovery.search import parse_serpapi_results


def test_parse_serpapi_results_deduplicates_to_domains_later() -> None:
    payload = {
        "organic_results": [
            {
                "title": "Example",
                "link": "https://www.example.com/page",
                "snippet": "AI NSFW generator",
            },
            {
                "title": "No URL",
                "snippet": "ignored",
            },
        ]
    }
    results = parse_serpapi_results("AI NSFW generator", payload, 10)
    assert len(results) == 1
    assert results[0].domain == "example.com"
    assert results[0].query == "AI NSFW generator"
