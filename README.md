# proxypool

Automated proxy pool manager. Registers trial accounts on GoLogin, collects the
residential proxies that come with each trial, validates each proxy's exit
country, and publishes the resulting lists as plain-text files over HTTP.

## How it works

- **collector (`main.py`)** — drives a real browser (Camoufox) through the
  GoLogin sign-up form, using a rotating upstream proxy per attempt, and stores
  the proxies returned for each new account.
- **runner (`run.sh`)** — flock-guarded scheduled run: start Xvfb if needed,
  collect N accounts, deduplicate the pool, classify by country.
- **classifier (`country_split.sh`)** — checks every proxy's exit country via
  `ip-api.com` (request goes *through* the proxy itself) and writes
  `proxy_<CC>.txt` per country plus a `proxy_list.txt` index.
- **server (`pxserve.py`)** — minimal HTTP server exposing the proxy list
  files. Only proxy lists and the index are served; credential files are never
  exposed.

## Quick start

```bash
git clone https://github.com/lyft168/proxypool.git /opt/proxypool
cd /opt/proxypool

sudo apt-get install -y python3-pip python3-venv xvfb
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python -m camoufox fetch

cp .env.example .env
# edit .env: at minimum EMAIL_DOMAIN and POOL_FILE (your upstream proxies)

# single collection run (1 account)
venv/bin/python main.py 1

# deduplicate + classify what you have
venv/bin/python main.py --dedupe
bash country_split.sh
```

### Upstream proxy pool

Create a file with one HTTP proxy per line, `host:port:user:pass`. These are
the *outbound* proxies used when opening the sign-up page (one per attempt,
round-robin). They must support HTTPS CONNECT (`curl -x` to an https URL to
verify). Point `POOL_FILE` at it.

## Configuration

Everything is configured through `.env` (see `.env.example`). No paths, ports
or accounts are hardcoded in the scripts.

| Variable | Default | Meaning |
|---|---|---|
| `BASE_DIR` | repo dir | Working directory for all inputs/outputs |
| `POOL_FILE` | `$BASE_DIR/proxies_http.txt` | Upstream proxy pool (`host:port:user:pass` per line) |
| `EMAIL_DOMAIN` | — (required) | Domain used for generated registration addresses |
| `ACCOUNTS_PER_RUN` | `1` | Accounts to register per scheduled run |
| `MAX_ATTEMPTS` | `4` | Retries per account before giving up |
| `DISPLAY` | `:99` | X server display for the headless-with-Xvfb browser |
| `SERVE_HOST` / `SERVE_PORT` | `0.0.0.0` / `8126` | Bind address for `pxserve.py` |
| `GEO_API_URL` | ip-api endpoint | Country lookup URL (must return `countryCode`) |

Output files (all under `BASE_DIR`):

- `gologin_proxies.txt` — full deduplicated pool
- `proxy_<CC>.txt` — proxies per exit country
- `proxy_list.txt` — index: `filename <count>` per country
- `accounts.jsonl` — registered account credentials (keep private, `chmod 600`)

## Scheduling (systemd)

```bash
sudo cp systemd/*.service systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now proxypool-run.timer proxypool-country.timer
```

Units read the same `.env` via `EnvironmentFile=`. Adjust cadence by editing
the `OnCalendar=` lines (`*:0/15` = every 15 minutes, `*:0/30` = every 30).

## Serving the lists

```bash
sudo systemctl enable --now proxypool-serve
```

Then from any machine:

```
http://<host>:8126/proxy_list.txt       # index (filename + count per country)
http://<host>:8126/proxy_US.txt         # one file per country
http://<host>:8126/gologin_proxies.txt  # full pool
```

Put it behind a reverse proxy with TLS for anything exposed to the internet.

## Notes & limits

- The sign-up captcha is invisible and passes automatically; registration must
  go through the real UI (direct API calls are rejected server-side).
- Exit country cannot be selected: the provider assigns a sticky exit IP per
  session from its peer pool. The classifier filters by actual exit country
  instead of trusting configuration.
- Too many parallel workers from one IP triggers rate limiting (429) — keep
  concurrency low; the runner does one account per tick by design.
- Trial proxies are short-lived; the dedupe + re-classify cycle keeps the
  published files current.

## License

MIT
