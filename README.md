# Feedler

[![CI](https://github.com/olizimmermann/feedler/actions/workflows/ci.yml/badge.svg)](https://github.com/olizimmermann/feedler/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A self-hosted reader for subreddits and RSS feeds that uses an LLM to show you only the posts that match your interests.

You describe what you want in plain language, for example *"Prusa printer bugs and their fixes"*. Feedler fetches new posts and has an LLM score each one from 0 to 100, with a reason and a short summary (including the fix, if the post or its comments contain one). It then shows the relevant posts in a small Reddit-style UI. Your up- and down-votes feed a preference profile that the LLM rewrites over time, so the filter gets better the more you use it.

## Features

- **Sources:** Reddit (subreddits) and RSS/Atom feeds, with OPML import. The same link arriving from several sources is merged into one item.
- **Any LLM:** Anthropic (Claude), OpenAI, Google Gemini (including the free tier) or a local model via Ollama. Each user picks their own provider and model.
- **Reads the comments:** with Reddit API credentials, the LLM also reads the top comments, which is often where the fix is. Comments of relevant posts are re-read after about 2h and 12h.
- **Learns from your votes:** votes, with an optional note on why, drive an automatically refined **preference profile** that you can view, edit, lock and roll back.
- **Filtered out tab:** see what the LLM rejected, and upvote anything it got wrong.
- **Save for later:** save feed items or any URL, with tags and notes. The page text is archived and full-text searchable, so it survives link rot.
- **Patient when the LLM is busy:** overloads and rate limits pause scoring with growing waits. Unscored posts stay queued, and nothing is lost.
- **Multi-user:** the first account is the admin, and the admin can close registration. Each user gets a private Atom feed of their filtered posts.
- **Distraction-free reader:** tapping a post opens it in the app: title, AI summary, the post or the extracted article text, and the comments. You don't get bounced to Reddit. **Next unread →** takes you through the feed one post at a time.
- **Made for phones:** a bottom tab bar, large tap targets and dark mode. Add it to your home screen (Share → *Add to Home Screen*) to use it like a native app, full-screen.
- **Keyboard shortcuts:** `j`/`k` to move, `Enter` to read, `n` for next unread, `u`/`d` to vote, `s` to save, `o` to open the original, `x` to hide, `?` for help.

## Quick start

You need Docker with Docker Compose.

```bash
git clone https://github.com/olizimmermann/feedler.git
cd feedler
cp .env.example .env
```

Edit `.env`:

1. Set `SECRET_KEY` to a random value, for example the output of `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. The app refuses to start with the placeholder.
2. Add at least one LLM key and set `DEFAULT_LLM_PROVIDER` to match, or use a local model with Ollama (see below).

Then start it:

```bash
docker compose up -d
```

This pulls the prebuilt image. Use `docker compose up -d --build` to build from source instead.

Open <http://localhost:8000> and:

1. Register. The first account becomes the admin.
2. **Settings → Sources:** add `r/prusa3d` or any feed URL.
3. **Settings → Interests:** describe what you care about. Be specific, including what you *don't* want.
4. **Settings → LLM & profile:** check the provider and model.

The worker polls each source every `DEFAULT_POLL_INTERVAL_MINUTES` minutes and scores new posts right after each poll. When you subscribe to a source, only its posts from the last `ITEM_MAX_AGE_DAYS` days are backfilled, so a new account doesn't pay for scoring the whole history.

### Updating

Every commit on `main` that passes CI is published as `ghcr.io/olizimmermann/feedler:latest` (amd64 and arm64). To update by hand:

```bash
git pull                 # picks up changes to docker-compose.yml and .env.example
docker compose pull
docker compose up -d     # database migrations run automatically on start
```

Use `docker compose up -d --build` instead to run your own local changes.

### Automatic updates

Add this to `.env` on your server:

```
COMPOSE_PROFILES=autoupdate
```

Then run `docker compose up -d`. This starts [Watchtower](https://github.com/nicholas-fedor/watchtower), which checks for a new image every 5 minutes (`WATCHTOWER_POLL_INTERVAL`, in seconds). When it finds one, it restarts `web` and `worker` with the new image and removes the old one. Only Feedler's own containers are touched, because Watchtower only updates containers labelled `com.centurylinklabs.watchtower.enable`. Watchtower needs access to the Docker socket.

Watchtower updates the image only. Changes to `docker-compose.yml` itself (new services or settings) still need a `git pull && docker compose up -d`.

## LLM providers

Set the keys in `.env`. They're shared by all users on the server.

| Provider | Variables | Notes |
|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` (default `claude-haiku-4-5`), `ANTHROPIC_PROFILE_MODEL` (default `claude-sonnet-5`) | Structured JSON output |
| OpenAI | `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_PROFILE_MODEL` | Model must support `json_schema` response format |
| Gemini | `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_PROFILE_MODEL` | |
| Ollama | `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OLLAMA_PROFILE_MODEL` | Batches of 5 posts with shorter excerpts, to fit small context windows |

The *profile model* is used only for the occasional preference-profile rewrite, so a stronger model is fine there. Classification uses the cheaper model.

### Free option: Gemini

Google's Gemini API has a free tier. Create a key at <https://aistudio.google.com/apikey>, put it into `GEMINI_API_KEY`, and set `DEFAULT_LLM_PROVIDER=gemini`. The example config uses `gemini-3.5-flash-lite` for scoring and `gemini-3.8-flash` for profile rewrites. Free-tier requests are the first to be turned away when Google is busy. Feedler then waits and retries, as described under *When the LLM is busy*.

### Local models with Ollama

```bash
docker compose --profile ollama up -d
# in .env: DEFAULT_LLM_PROVIDER=ollama, OLLAMA_MODEL=qwen2.5:7b
```

The model is downloaded automatically the first time it's needed. That can take a few minutes, and posts are scored once it's ready. Models of 7B and up give noticeably better scores and profiles than 3B models. To use an Ollama server that is already running on the host instead, set `OLLAMA_BASE_URL=http://host.docker.internal:11434/v1`.

## Reddit API credentials (recommended)

Without credentials, Feedler reads each subreddit's public RSS feed. That feed has no scores and no comments, and Reddit blocks unauthenticated JSON requests. To enable comments:

1. Go to <https://www.reddit.com/prefs/apps> and click **create another app…**
2. Choose type **script**, and use any redirect URI (e.g. `http://localhost`).
3. Put the client ID (the string under the app name) and the secret into `.env` as `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`, and set a descriptive `REDDIT_USER_AGENT` with a contact address.
4. `docker compose up -d worker` to restart the worker.

## How the learning works

- Every vote increments a counter. After `PROFILE_REFINE_AFTER_VOTES` votes (and nightly for anyone with new votes), the profile model reads your current profile, your interests and your last 60 votes. Each vote includes the filter's earlier score and reason, plus your note if you wrote one. The model then writes a new *Wants / Doesn't want* profile.
- A vote that disagrees with the filter's score (an upvote on a low score, or a downvote on a high one) is the strongest signal.
- The profile is a new version each time. You can edit it, **Lock** it to stop automatic rewrites, or restore an older version.
- Posts are scored once, with the profile that is current at that time. The card's *Why?* shows the model and profile version used.

## When the LLM is busy

Posts that haven't been scored yet wait in a queue in the database. Temporary errors pause scoring for that provider and model, for all users: an overloaded model (e.g. Gemini's free tier returning 503), rate limits, network errors, or an Ollama model that is still downloading. The waits grow from 1 minute to 2, 4 and 8, up to 30 minutes, or follow the provider's `Retry-After` hint. No requests are sent while paused, and the first successful call resets the wait. The feed and Settings show when the next attempt is and how many posts are queued. Posts fetched after you subscribed stay queued for up to 14 days, so an outage delays scoring without losing posts. Permanent errors, such as a wrong API key or an unknown model, are shown in Settings instead.

## Services

| Service | Role |
|---|---|
| `db` | Postgres 16 (also creates `feedler_test` for the test suite) |
| `web` | FastAPI + HTMX UI on `WEB_PORT` (default 8000) |
| `worker` | Polls sources, scores posts, refreshes comments, refines profiles |
| `ollama` | Optional (profile `ollama`) |
| `watchtower` | Optional (profile `autoupdate`): installs new releases automatically |

`web` and `worker` apply database migrations when they start, so an update never needs a manual step.

## Development

```bash
docker compose exec web pytest            # runs against the feedler_test database
docker compose logs -f worker             # watch polling and scoring
docker compose run --rm -v "$PWD/alembic:/srv/alembic" web \
  alembic revision --autogenerate -m "describe change"   # after editing app/models.py
```

Layout: `app/fetchers` (Reddit, RSS, article extraction), `app/llm` (providers and prompts), `app/pipeline` (ingest, classify, profile), `app/routers` + `app/templates` (web UI), and `app/worker.py` (scheduler).

## Security notes

- Set a strong `SECRET_KEY`. Session cookies are marked `Secure` automatically when `BASE_URL` starts with `https://`.
- Put the app behind a TLS-terminating reverse proxy if it's reachable from outside your network.
- The Atom output feed is protected only by its unguessable token URL.
- Users can add any feed URL and save any link, and the server fetches them. On a shared deployment that means a user could make the server request hosts on your internal network (SSRF). Close registration after creating the accounts you need, or run the containers on a network that can't reach internal services.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup, and [SECURITY.md](SECURITY.md) for reporting vulnerabilities.

## License

[MIT](LICENSE). Feedler bundles [htmx](https://htmx.org) (`app/static/htmx.min.js`, Zero-Clause BSD License).
