#!/bin/sh
# Apply database migrations, then start the service (web or worker).
set -e
alembic upgrade head
exec "$@"
