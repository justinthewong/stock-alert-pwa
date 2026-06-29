# Stock Alert PWA (IBKR + FastAPI)

ASX stock depth notification app running on a single VPS with:

- **FastAPI** web UI + API + PWA
- **SQLite** storage
- **IB Gateway** (Docker) for IBKR market depth
- **pywebpush** for iPhone/desktop notifications

## Prerequisites

- Ubuntu 24.04 VPS (Sydney region recommended), 2 GB+ RAM
- Docker + Docker Compose
- IBKR account with **ASX Total (NP, L2)** subscription (AUD 25/month)
- Domain name pointed at the VPS (for HTTPS + iOS Web Push)

## Quick start

1. Copy configuration files:

```bash
cp .env.example .env
cp config/secrets.example.yaml config/secrets.yaml
```

2. Generate secrets:

```bash
python -m venv .venv
. .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/generate_vapid.py
python scripts/hash_password.py 'your-password'
python scripts/generate_icons.py
```

3. Fill in:

- `.env` — IBKR username/password and gateway settings
- `config/secrets.yaml` — web login hash, VAPID keys, `base_url`, `secret_key`

4. Start services:

```bash
docker compose up -d --build
```

5. Approve IBKR 2FA on your phone when the gateway container starts.

6. Open `https://your-domain/dashboard`, log in, enable notifications, and create an ASX alert.

## Services

| Service | Purpose |
|---|---|
| `ib-gateway` | IB Gateway + IBC auto-login |
| `app` | FastAPI + depth worker + SQLite |
| `nginx` | Reverse proxy (add TLS certs in `deploy/nginx/certs`) |

Gateway API ports are bound to localhost only (`127.0.0.1:4001/4002`).

## Alert logic

- **Buy:** sum ask sizes at prices `<= target_price` must be `>= share_count`
- **Sell:** sum bid sizes at prices `>= target_price` must be `>= share_count`

Depth is streamed via `reqMktDepth(..., isSmartDepth=True)` against ASX symbols.

## HTTPS

The included Nginx config serves HTTP on port 80. Obtain certificates with Certbot and update `deploy/nginx/default.conf` to terminate TLS on port 443.

Set `app.secure_cookies: true` in `config/secrets.yaml` when serving over HTTPS.

## iPhone notifications

1. Open the site in Safari
2. Share → Add to Home Screen
3. Open the app from the home screen icon
4. Tap **Enable notifications** (must be a user gesture)

## Backup

Copy `data/alerts.db` periodically.

## Local development (without IB Gateway)

The app starts without IBKR connectivity; the worker retries in the background. For UI testing only, you can run:

```bash
export SECRETS_PATH=config/secrets.yaml
uvicorn app.main:app --reload --port 8000
```

Use `app.secure_cookies: false` locally over HTTP.
