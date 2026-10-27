FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY pyproject.toml ./
COPY app/__init__.py app/__init__.py
RUN pip install -e ".[dev]"

COPY . .

# Set by CI so the app can show which build is running (Settings → About)
ARG FEEDLER_COMMIT=""
ARG FEEDLER_RELEASED_AT=""
ENV FEEDLER_COMMIT=$FEEDLER_COMMIT \
    FEEDLER_RELEASED_AT=$FEEDLER_RELEASED_AT

LABEL org.opencontainers.image.revision=$FEEDLER_COMMIT \
      org.opencontainers.image.created=$FEEDLER_RELEASED_AT
LABEL org.opencontainers.image.source="https://github.com/olizimmermann/feedler" \
      org.opencontainers.image.description="Self-hosted Reddit and RSS reader filtered by an LLM" \
      org.opencontainers.image.licenses="MIT"

EXPOSE 8000
ENTRYPOINT ["/srv/docker/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
