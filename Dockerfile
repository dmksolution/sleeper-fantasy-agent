FROM python:3.12-slim

# Fantasy data is time sensitive and cron here runs on local wall clock.
ENV TZ=America/New_York \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/data/sleeper.db

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sleeper_agent/ ./sleeper_agent/
COPY sql/ ./sql/
COPY cli.py mcp_server.py ./
COPY scripts/ ./scripts/
RUN chmod +x scripts/*.sh

VOLUME ["/data"]

# Default: run the scheduler. Override with `command:` for one-off CLI runs.
CMD ["/app/scripts/entrypoint.sh"]
