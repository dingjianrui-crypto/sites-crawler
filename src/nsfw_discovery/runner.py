from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .config import Settings
from .crawler import HtmlCrawler
from .extract import extract_contacts
from .external import discover_external_candidates
from .llm import LlmClient
from .search import SerpApiClient
from .storage import Database


@dataclass
class RunCounters:
    search_results: int = 0
    domains_processed: int = 0
    accepted: int = 0
    uncertain: int = 0
    external_candidates: int = 0
    external_queued: int = 0
    errors: int = 0


class ProgressReporter:
    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def update(self, message: str, counters: RunCounters) -> None:
        print(
            f"{message} | results={counters.search_results} processed={counters.domains_processed} "
            f"accepted={counters.accepted} uncertain={counters.uncertain} "
            f"external={counters.external_candidates}/{counters.external_queued} errors={counters.errors}",
            flush=True,
        )


async def run_discovery(settings: Settings, db: Database, reporter: ProgressReporter) -> RunCounters:
    counters = RunCounters()
    reporter.start()
    try:
        if settings.serpapi_api_key:
            await discover(settings, db, reporter, counters)
        else:
            reporter.update("SERPAPI_API_KEY not set; skipping search discovery", counters)

        remaining = settings.max_domains
        while remaining > 0:
            domains = db.pending_domains(remaining, include_errors=True)
            if not domains:
                break
            reporter.update(f"Queued {len(domains)} domains for crawl/classification", counters)
            processed = await process_domains(settings, db, domains, reporter, counters)
            remaining -= processed
        return counters
    finally:
        reporter.stop()


async def discover(
    settings: Settings,
    db: Database,
    reporter: ProgressReporter,
    counters: RunCounters,
) -> int:
    client = SerpApiClient(settings.serpapi_api_key or "", timeout=settings.timeout_seconds)
    for query in settings.selected_queries:
        reporter.update(f"Searching: {query}", counters)
        try:
            results = await client.search(query, settings.results_per_query)
            for result in results:
                db.upsert_search_result(result)
            counters.search_results += len(results)
            reporter.update(f"Stored {len(results)} results for query", counters)
        except Exception as exc:  # noqa: BLE001
            counters.errors += 1
            reporter.update(f"Search failed for {query}: {exc}", counters)


async def process_domains(
    settings: Settings,
    db: Database,
    domains: list[str],
    reporter: ProgressReporter,
    counters: RunCounters,
) -> None:
    semaphore = asyncio.Semaphore(settings.concurrency)
    crawler = HtmlCrawler(
        timeout=settings.timeout_seconds,
        retries=settings.retry_count,
        user_agent=settings.user_agent,
    )
    llm = LlmClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout=max(20.0, settings.timeout_seconds),
    )

    async def process_one(domain: str) -> None:
        async with semaphore:
            try:
                db.mark_crawling(domain)
                reporter.update(f"Crawling {domain}", counters)
                pages = await crawler.crawl_domain(domain, settings.max_pages_per_domain)
                if not pages:
                    raise RuntimeError("no HTML pages fetched")
                for page in pages:
                    db.save_page(domain, page)
                current_depth = db.domain_depth(domain)
                if settings.external_depth > current_depth:
                    external_candidates = discover_external_candidates(
                        source_domain=domain,
                        pages=pages,
                        depth=current_depth + 1,
                        score_threshold=settings.external_score_threshold,
                        max_candidates=settings.max_external_candidates,
                    )
                    counters.external_candidates += len(external_candidates)
                    for candidate in external_candidates:
                        if db.upsert_external_candidate(candidate, queue=True):
                            counters.external_queued += 1
                all_text = " ".join(page.text for page in pages)
                all_links = [link for page in pages for link in page.links]
                contacts = extract_contacts(all_text, all_links)
                db.mark_classifying(domain)
                classification = await llm.classify(domain, pages, contacts)
                needs_js_review = any(page.needs_js_review for page in pages)
                db.save_classification(domain, classification, contacts, needs_js_review)
                counters.domains_processed += 1
                counters.accepted += int(classification.accepted)
                counters.uncertain += int(classification.uncertain)
                reporter.update(f"Classified {domain}", counters)
            except Exception as exc:  # noqa: BLE001
                db.mark_error(domain, str(exc))
                counters.errors += 1
                reporter.update(f"Failed {domain}: {exc}", counters)

    await asyncio.gather(*(process_one(domain) for domain in domains))
    return len(domains)
