# AI NSFW Website Discovery CLI

Python CLI for discovering English-language websites that provide AI-generated adult NSFW content, enriching them with short descriptions and public contact channels, and storing results in SQLite.

The crawler is metadata-focused: it fetches public HTML pages, extracts text and contact links, and avoids downloading or storing explicit media.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Configure

The app automatically loads `.env` from the current working directory before reading environment variables. Copy the example file and fill in your credentials:

```bash
cp .env.example .env
```

```dotenv
SERPAPI_API_KEY=...
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=...
LLM_MODEL=gpt-4o-mini
```

Shell environment variables still work and take precedence over values in `.env`. To load a different file for a run, use `--env-file path/to/file.env`.

## Run

Small test run:

```bash
nsfw-discovery run --db data/discovery.sqlite --max-queries 2 --results-per-query 10 --max-domains 20
```

Resume incomplete work from the same database:

```bash
nsfw-discovery run --db data/discovery.sqlite --resume
```

Inspect status:

```bash
nsfw-discovery status --db data/discovery.sqlite
```

Export accepted and uncertain records:

```bash
nsfw-discovery export --db data/discovery.sqlite --format json --output results.json
nsfw-discovery export --db data/discovery.sqlite --format csv --output results.csv
```

Run the read-only web dashboard:

```bash
nsfw-discovery serve --db data/discovery.sqlite --host 127.0.0.1 --port 8080
```

Then open:

```text
http://127.0.0.1:8080
```

## How It Works

The app is a staged pipeline:

1. Search discovery with SerpAPI
2. Domain normalization and deduplication
3. HTML-first crawling of candidate websites
4. Optional external-link discovery from crawled pages
5. Contact extraction from public text and links
6. LLM classification and summarization
7. SQLite persistence and optional export

### Default Search Queries

By default, `nsfw-discovery run` uses every built-in query:

```text
AI NSFW generator
AI adult image generator
uncensored AI image generator
```

The full built-in query list is:

```text
AI NSFW generator
AI adult image generator
uncensored AI image generator
AI porn generator
AI girlfriend NSFW
adult AI art generator
NSFW AI chatbot
AI hentai generator
```

Limit query usage with:

```bash
--max-queries 3
```

The default is `--max-queries 0`, which means use every built-in query.

To provide your own queries, create a newline-delimited file:

```text
AI adult image generator
uncensored AI chatbot
NSFW AI art generator
```

Then run:

```bash
nsfw-discovery run --query-file queries.txt --max-queries 0
```

### Search Discovery

For each selected query, the app calls SerpAPI's Google search endpoint with English/US settings:

```text
engine=google
hl=en
gl=us
num=<--results-per-query>
```

For each organic result, it stores:

- search query
- result title
- result URL
- result snippet
- normalized registrable domain

Multiple URLs from the same root domain are deduplicated into one domain record, while the source search evidence is kept.

### Site Crawling

After discovery, the app crawls each queued domain with static HTTP requests. It starts with:

```text
https://<domain>/
http://<domain>/
```

Then it follows same-domain links that look useful for business metadata:

```text
about
contact
support
pricing
terms
privacy
```

The crawler:

- fetches HTML pages only
- ignores images, videos, archives, PDFs, and other media-like URLs
- strips scripts/styles and extracts readable page text
- extracts page links for contact discovery
- flags pages as `needs_js_review` when the static HTML appears too sparse or JavaScript-dependent

v1 does not use browser rendering or Playwright. JavaScript-heavy sites are flagged rather than rendered.

### External-Link Discovery

To crawl only domains discovered from search results, disable external discovery:

```bash
--external-depth 0
```

By default, external discovery allows up to 10 link-discovery layers:

```bash
nsfw-discovery run --external-depth 10
```

When enabled, the app reviews outbound links found on crawled pages as possible same-topic candidate domains. It does not follow every external link.

An external link can become a new pending domain when:

- it points to a different registrable domain
- it is not a media/static asset URL
- it is not a denylisted social, payment, CDN, analytics, app store, or support platform domain
- the context-aware LLM screening step decides the source-page text around the link is relevant to the configured topics

Useful controls:

```bash
--external-depth 10
--max-external-candidates 1000
```

Depth is graph depth:

- `0`: search result domains only
- `1`: search result domains may discover external candidates
- `2`: external candidates may discover another layer of candidates
- `10`: allows deeper recursive discovery, still bounded by `--max-domains` and `--max-external-candidates`

Queued external candidates still go through the normal crawl and LLM classification flow. The external-link screening step only decides whether a domain is worth inspecting.

### Contact Extraction

Contacts are extracted deterministically before the LLM step. The app looks for:

- email addresses in page text
- `mailto:` links
- Discord links
- Telegram links
- other obvious public social/community links such as X/Twitter, Instagram, and Reddit

These are stored in SQLite as structured JSON:

```json
{
  "emails": ["support@example.com"],
  "discord": ["https://discord.gg/example"],
  "telegram": ["https://t.me/example"],
  "other": ["https://x.com/example"]
}
```

### Where the LLM Is Used

The LLM is used after search and crawling, once the app has gathered public metadata for a domain.

For each domain, the app sends the OpenAI-compatible LLM:

- domain name
- fetched page URLs
- page titles
- text excerpts from fetched HTML pages
- `needs_js_review` indicators
- extracted contact candidates

The LLM returns structured JSON with:

- whether the site appears to provide AI-generated adult NSFW content
- a short neutral description
- confidence: `high`, `medium`, or `low`
- whether the domain should be accepted
- whether the domain is uncertain
- safety or review flags

The expected LLM output shape is:

```json
{
  "provides_ai_nsfw": true,
  "description": "Adult AI image generation platform with public contact channels.",
  "confidence": "medium",
  "flags": [],
  "accepted": true,
  "uncertain": false
}
```

The request body also includes:

```json
{
  "thinking": {
    "type": "disabled"
  }
}
```

This is intended for OpenAI-compatible LLM providers that support disabling reasoning/thinking mode for lower latency.

If LLM credentials are missing, the app falls back to a simple local keyword heuristic. That fallback is useful for tests and smoke runs, but real discovery quality depends on the configured LLM.

### SQLite Output

SQLite is the source of truth. The main tables are:

- `domains`: one row per normalized domain, including status, description, confidence, contacts, flags, and error state
- `search_sources`: search queries and result URLs that discovered each domain
- `pages`: fetched HTML page metadata and text excerpts
- `external_candidates`: outbound links that were screened as possible same-topic domains

The final useful fields for each accepted or uncertain domain are:

- domain
- short description
- confidence
- accepted/uncertain status
- contact JSON
- safety/review flags
- update timestamp

Use `status` to inspect run counts:

```bash
nsfw-discovery status --db data/discovery.sqlite
```

Use `export` to produce JSON or CSV from SQLite:

```bash
nsfw-discovery export --db data/discovery.sqlite --format json --output results.json
```

### Web Dashboard

The `serve` command starts a read-only FastAPI dashboard backed by the SQLite database:

```bash
nsfw-discovery serve --db data/discovery.sqlite --host 127.0.0.1 --port 8080
```

The dashboard includes:

- `/`: HTML table with filters and pagination
- `/domains/{domain}`: detail page for one domain
- `/api/domains`: JSON list endpoint
- `/api/domains/{domain}`: JSON detail endpoint
- `/api/stats`: JSON status counts
- `/healthz`: health check endpoint

The dashboard can filter by:

- status
- confidence
- accepted/uncertain state
- JS review flag
- contact type
- domain/description/contact search text

The dashboard is read-only. It does not start or stop crawler runs and does not edit SQLite records.

## Parameter Reference

### Environment Variables

These can be set in `.env` or in your shell. Shell variables take precedence over `.env`.

| Parameter | Required | Default | Used by | Description | Example |
| --- | --- | --- | --- | --- | --- |
| `SERPAPI_API_KEY` | Yes for search | None | Search discovery | SerpAPI key used to call the Google search API. If missing, search discovery is skipped and only existing pending domains in SQLite are processed. | `SERPAPI_API_KEY=abc123` |
| `LLM_BASE_URL` | Recommended | `https://api.openai.com/v1` | LLM classification | Base URL for an OpenAI-compatible API. Do not include `/chat/completions`; the app appends that path. | `LLM_BASE_URL=https://api.openai.com/v1` |
| `LLM_API_KEY` | Recommended | None | LLM classification | API key for the OpenAI-compatible LLM provider. If missing, the app uses a local keyword heuristic instead of the LLM. | `LLM_API_KEY=sk-...` |
| `LLM_MODEL` | Recommended | `gpt-4o-mini` | LLM classification | Model name sent in the LLM request body. | `LLM_MODEL=gpt-4o-mini` |

### `run` Command Parameters

Use these with:

```bash
nsfw-discovery run [parameters]
```

| Parameter | Default | Description | Example |
| --- | --- | --- | --- |
| `--db` | `data/discovery.sqlite` | SQLite database path. The app stores discovered domains, search evidence, fetched pages, contacts, classifications, and external candidates here. | `--db data/discovery.sqlite` |
| `--query-file` | None | Optional newline-delimited file of search queries. If omitted, the built-in query list is used. Blank lines and lines starting with `#` are ignored. | `--query-file queries.txt` |
| `--max-queries` | `0` | Number of queries to use from the selected query list. Use `0` to use all queries. | `--max-queries 3` |
| `--results-per-query` | `100` | Number of SerpAPI organic results to request per query. Higher values increase discovery breadth and API usage. | `--results-per-query 50` |
| `--max-domains` | `5000` | Maximum number of pending domains to crawl/classify in this run, including domains discovered through external-link expansion. | `--max-domains 500` |
| `--max-pages-per-domain` | `6` | Maximum number of HTML pages to fetch per domain. The crawler starts with the homepage, then prioritizes useful internal pages such as contact, about, pricing, terms, and privacy pages. | `--max-pages-per-domain 8` |
| `--concurrency` | `5` | Number of domains processed in parallel. Higher values are faster but can increase failures, rate limits, and bandwidth use. | `--concurrency 10` |
| `--timeout` | `30.0` | HTTP timeout in seconds for search and crawl requests. | `--timeout 45` |
| `--retries` | `3` | Number of retry attempts for failed page fetches. | `--retries 2` |
| `--user-agent` | Built-in crawler user agent | User-Agent header sent during site crawling. Useful if you want to identify your own crawler/contact URL. | `--user-agent "Mozilla/5.0 (compatible; ResearchBot/1.0)"` |
| `--external-depth` | `10` | External-link discovery depth. `0` disables external expansion, `1` lets search-discovered domains add external candidates, and higher values allow deeper recursive discovery bounded by `--max-domains` and `--max-external-candidates`. | `--external-depth 0` |
| `--max-external-candidates` | `1000` | Maximum number of external candidates collected per crawled domain before dedupe/queueing. | `--max-external-candidates 100` |
| `--env-file` | `.env` | Environment file loaded before reading API settings. Use an empty string to disable `.env` loading. | `--env-file prod.env` |
| `--resume` | Disabled | Keeps using the same SQLite database and processes pending/error domains. Existing discovered domains are always reused from SQLite. | `--resume` |

Example large run with external discovery:

```bash
nsfw-discovery run \
  --db data/discovery.sqlite \
  --max-queries 0 \
  --results-per-query 100 \
  --max-domains 5000 \
  --max-pages-per-domain 8 \
  --concurrency 10 \
  --external-depth 10 \
  --max-external-candidates 1000
```

### `status` Command Parameters

Use this to inspect counts in a SQLite database:

```bash
nsfw-discovery status [parameters]
```

| Parameter | Default | Description | Example |
| --- | --- | --- | --- |
| `--db` | `data/discovery.sqlite` | SQLite database path to inspect. | `--db data/discovery.sqlite` |

Example:

```bash
nsfw-discovery status --db data/discovery.sqlite
```

### `export` Command Parameters

Use this to export accepted and uncertain records from SQLite:

```bash
nsfw-discovery export [parameters]
```

| Parameter | Default | Description | Example |
| --- | --- | --- | --- |
| `--db` | `data/discovery.sqlite` | SQLite database path to export from. | `--db data/discovery.sqlite` |
| `--format` | `json` | Export format. Supported values are `json` and `csv`. | `--format csv` |
| `--output` | Required | Output file path. Parent directories are created automatically. | `--output results.csv` |
| `--accepted-only` | Disabled | Export only accepted domains. By default, accepted and uncertain domains are exported. | `--accepted-only` |

Examples:

```bash
nsfw-discovery export --db data/discovery.sqlite --format json --output results.json
nsfw-discovery export --db data/discovery.sqlite --format csv --output results.csv --accepted-only
```

### `serve` Command Parameters

Use this to run the read-only HTML dashboard:

```bash
nsfw-discovery serve [parameters]
```

| Parameter | Default | Description | Example |
| --- | --- | --- | --- |
| `--db` | `data/discovery.sqlite` | SQLite database path to render. Use the same database used by crawler runs. | `--db /opt/nsfw-discovery/data/discovery.sqlite` |
| `--host` | `127.0.0.1` | Host interface for the web server. Use `127.0.0.1` behind Nginx; use `0.0.0.0` only on a private network or protected server. | `--host 127.0.0.1` |
| `--port` | `8080` | TCP port for the dashboard server. | `--port 8080` |
| `--reload` | Disabled | Enables Uvicorn auto-reload for development. Do not use this in production. | `--reload` |

Example:

```bash
nsfw-discovery serve --db data/discovery.sqlite --host 127.0.0.1 --port 8080
```

## Linux Service Deployment

This is a practical read-only dashboard deployment for a Linux server. It assumes the project lives at `/opt/nsfw-discovery` and the database is `/opt/nsfw-discovery/data/discovery.sqlite`.

### 1. Install the App

```bash
sudo mkdir -p /opt/nsfw-discovery
sudo chown "$USER":"$USER" /opt/nsfw-discovery
cd /opt/nsfw-discovery
```

Copy or clone the project into that directory, then install:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Create the config file:

```bash
cp .env.example .env
```

Edit `.env` with your keys:

```dotenv
SERPAPI_API_KEY=...
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=...
LLM_MODEL=gpt-4o-mini
```

Run the crawler manually or from cron/systemd timer so the database has data:

```bash
/opt/nsfw-discovery/.venv/bin/nsfw-discovery run \
  --db /opt/nsfw-discovery/data/discovery.sqlite
```

### 2. Create a `systemd` Service

Create `/etc/systemd/system/nsfw-discovery-dashboard.service`:

```ini
[Unit]
Description=AI NSFW Discovery Dashboard
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/nsfw-discovery
EnvironmentFile=/opt/nsfw-discovery/.env
ExecStart=/opt/nsfw-discovery/.venv/bin/nsfw-discovery serve --db /opt/nsfw-discovery/data/discovery.sqlite --host 127.0.0.1 --port 8080
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Make sure the service user can read the project and database:

```bash
sudo chown -R www-data:www-data /opt/nsfw-discovery
```

Start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nsfw-discovery-dashboard
sudo systemctl status nsfw-discovery-dashboard
```

Check the local health endpoint:

```bash
curl http://127.0.0.1:8080/healthz
```

### 3. Put Nginx in Front

Do not expose the dashboard publicly without access control. A simple Nginx reverse proxy with basic auth is a reasonable first deployment.

Install auth tooling:

```bash
sudo apt-get update
sudo apt-get install -y nginx apache2-utils
```

Create a password file:

```bash
sudo htpasswd -c /etc/nginx/.htpasswd admin
```

Create `/etc/nginx/sites-available/nsfw-discovery`:

```nginx
server {
    listen 80;
    server_name your-domain.example;

    auth_basic "Restricted";
    auth_basic_user_file /etc/nginx/.htpasswd;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable and reload Nginx:

```bash
sudo ln -s /etc/nginx/sites-available/nsfw-discovery /etc/nginx/sites-enabled/nsfw-discovery
sudo nginx -t
sudo systemctl reload nginx
```

For HTTPS, add a certificate with Certbot or your normal TLS process.

### 4. Operational Notes

- Keep the dashboard bound to `127.0.0.1` when using Nginx on the same server.
- Use Nginx basic auth, VPN, SSH tunnel, or private networking before exposing the dashboard.
- The dashboard reads SQLite on each request, so new crawler results appear without restarting the web service.
- If the crawler and dashboard run at the same time, SQLite WAL mode allows normal read/write coexistence for this workload.
- Back up `data/discovery.sqlite` regularly if the crawl results matter.

## Notes

- SerpAPI is used for search discovery.
- The LLM endpoint must be OpenAI API compatible.
- v1 uses static HTML fetching only. JavaScript-heavy sites are flagged with `needs_js_review`.
- SQLite is the primary source of truth.
- The web dashboard is read-only and intended to be protected behind private access controls.
