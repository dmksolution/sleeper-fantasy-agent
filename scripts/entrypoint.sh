#!/usr/bin/env bash
# Install the fantasy schedule into cron and hand over to the cron daemon.
set -euo pipefail

cd /app

echo "[entrypoint] initializing database at ${DB_PATH}"
python cli.py sync || echo "[entrypoint] initial sync failed, cron will retry"

# Environment set by docker-compose is not visible to cron jobs, so bake the
# relevant variables into the crontab file itself.
{
  echo "SHELL=/bin/bash"
  echo "PATH=/usr/local/bin:/usr/bin:/bin"
  for var in SLEEPER_USERNAME SLEEPER_LEAGUE_ID SLEEPER_DRAFT_ID SLEEPER_SEASON \
             DB_PATH ROS_HORIZON_WEEKS REGULAR_SEASON_WEEKS \
             DIGEST_WEBHOOK_URL NTFY_TOPIC NTFY_SERVER TZ; do
    if [ -n "${!var:-}" ]; then
      echo "${var}=${!var}"
    fi
  done
  cat /app/scripts/crontab
} > /etc/cron.d/fantasy

chmod 0644 /etc/cron.d/fantasy
crontab /etc/cron.d/fantasy

touch /var/log/cron.log
echo "[entrypoint] schedule installed:"
crontab -l | grep -v '^[A-Z_]*=' || true

cron -f &
tail -f /var/log/cron.log
