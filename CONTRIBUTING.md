# Contributing to Feedler

Thanks for helping out! Bug reports, ideas and pull requests are all welcome.

## Reporting bugs and ideas

Open an [issue](https://github.com/olizimmermann/feedler/issues). For bugs, include what you did, what you expected, what happened instead, and the relevant part of `docker compose logs web worker`. Remove API keys and personal data from anything you paste.

Report security problems privately instead; see [SECURITY.md](SECURITY.md).

## Development setup

```bash
git clone https://github.com/olizimmermann/feedler.git
cd feedler
cp .env.example .env        # set SECRET_KEY; an LLM key is optional for the tests
docker compose up -d --build
docker compose exec web pytest
```

The tests run against the `feedler_test` database, which the `db` container creates on its first start. LLM calls in the tests use a fake provider, so they need no API keys and cost nothing.

To run the tests outside Docker, use Python 3.12+ and a Postgres you can reach:

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
TEST_DATABASE_URL=postgresql+asyncpg://feedler:feedler@localhost:5432/feedler_test .venv/bin/pytest
```

## Making changes

- Keep pull requests focused on one change, and describe the why in the PR.
- Add or update tests for behaviour changes (`tests/`).
- After changing `app/models.py`, generate a migration and commit it:
  ```bash
  docker compose run --rm -v "$PWD/alembic:/srv/alembic" migrate \
    alembic revision --autogenerate -m "describe change"
  ```
  Rename the file to the next number (`0003_...py`) and check the generated code.
- New LLM providers implement `complete_json()` from `app/llm/base.py` and raise `LLMError(..., retryable=True)` for temporary failures, so the back-off queue can handle them. Register them in `app/llm/registry.py`.
- Never commit `.env` or real API keys. CI runs a secret scan on every push.

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
