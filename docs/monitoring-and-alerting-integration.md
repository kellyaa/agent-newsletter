# Monitoring and Alerting Integration

This guide covers how to integrate the newsletter pipeline with external monitoring and alerting systems, track operational health, and set up notifications for failures and anomalies.

## Quick Start: Basic Health Monitoring

### Check Pipeline Status

```bash
EDITION=agents
DB="editions/$EDITION/state.db"

# Did today's run succeed?
LATEST_RUN=$(sqlite3 "$DB" "SELECT date, outcome FROM run_attempts WHERE date = date('now') ORDER BY start_time DESC LIMIT 1;")
if echo "$LATEST_RUN" | grep -q "ok"; then
  echo "✓ Today's run succeeded"
else
  echo "✗ Today's run failed or is in progress"
  echo "$LATEST_RUN"
fi
```

### Monitor Run Duration

```bash
sqlite3 "$DB" << 'SQL'
SELECT 
  date,
  duration_seconds as duration_s,
  items_featured,
  CASE 
    WHEN duration_seconds > 1200 THEN 'SLOW'
    WHEN duration_seconds > 900 THEN 'NORMAL_SLOW'
    ELSE 'NORMAL'
  END as status
FROM runs
WHERE date >= date('now', '-7 days')
ORDER BY date DESC;
SQL
```

---

## Integration Patterns

### 1. Email Alerts (via cron + mail)

Send email on pipeline failure:

```bash
#!/bin/bash
# /path/to/repo/alert-on-failure.sh

EDITION=agents
DB="editions/$EDITION/state.db"
EMAIL="team@example.com"

# Check latest run
OUTCOME=$(sqlite3 "$DB" "SELECT outcome FROM run_attempts WHERE date = date('now') ORDER BY start_time DESC LIMIT 1;")

if [ "$OUTCOME" != "ok" ]; then
  FAILED_STAGE=$(sqlite3 "$DB" "SELECT failed_stage FROM run_attempts WHERE date = date('now') ORDER BY start_time DESC LIMIT 1;")
  cat << MSG | mail -s "⚠ Newsletter pipeline failed: $FAILED_STAGE" "$EMAIL"
The $EDITION newsletter pipeline failed today.

Failed Stage: $FAILED_STAGE
Date: $(date)
Edition: $EDITION

Check: tail -100 logs/$EDITION/run-$(date +%Y-%m-%d).log

-- Automated alert from agent-newsletter
MSG
  exit 1
fi

exit 0
```

Add to crontab (runs at 10am, 30 min after the scheduled 07:00 run):

```bash
# Check for failures daily at 10am
0 10 * * * /path/to/repo/alert-on-failure.sh
```

### 2. Slack Notifications

Post run summaries to a Slack channel:

```bash
#!/bin/bash
# scripts/slack-webhook.sh

EDITION=agents
DB="editions/$EDITION/state.db"
SLACK_WEBHOOK="${SLACK_WEBHOOK:-}" # Set env var before calling

if [ -z "$SLACK_WEBHOOK" ]; then
  echo "SLACK_WEBHOOK not set, skipping Slack notification"
  exit 0
fi

# Query latest run
RUN_DATA=$(sqlite3 "$DB" << 'SQL'
SELECT
  date,
  items_featured,
  tokens_in + tokens_out as total_tokens,
  ROUND(duration_seconds, 0) as duration_s,
  (SELECT outcome FROM run_attempts WHERE date = runs.date ORDER BY start_time DESC LIMIT 1) as outcome
FROM runs
WHERE date = date('now')
LIMIT 1;
SQL
)

if [ -z "$RUN_DATA" ]; then
  echo "No run today yet"
  exit 0
fi

# Parse and format
read -r DATE FEATURED TOKENS DURATION OUTCOME <<< "$RUN_DATA"

STATUS_EMOJI="✅"
[ "$OUTCOME" != "ok" ] && STATUS_EMOJI="❌"

# Send to Slack
curl -X POST "$SLACK_WEBHOOK" \
  -H 'Content-type: application/json' \
  --data @- << MSG
{
  "text": "$STATUS_EMOJI $EDITION Newsletter — $DATE",
  "blocks": [
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "*$EDITION Newsletter Daily Run* — $DATE\n*Status:* $STATUS_EMOJI $OUTCOME\n*Featured:* $FEATURED items\n*Tokens:* $TOKENS\n*Duration:* ${DURATION}s"
      }
    }
  ]
}
MSG

echo "Slack notification sent"
```

Add to `run.sh` or run via cron after the pipeline:

```bash
# In run.sh, after publish.py succeeds:
export SLACK_WEBHOOK="https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
./scripts/slack-webhook.sh
```

### 3. Prometheus Metrics Export

Export metrics for Prometheus scraping:

```bash
#!/bin/bash
# scripts/prometheus-metrics.sh

EDITION=agents
DB="editions/$EDITION/state.db"
METRIC_FILE="/tmp/agent-newsletter-metrics.prom"

cat > "$METRIC_FILE" << 'METRICS'
# HELP agent_newsletter_items_featured Total featured items in latest run
# TYPE agent_newsletter_items_featured gauge
METRICS

sqlite3 "$DB" << 'SQL' >> "$METRIC_FILE"
SELECT 'agent_newsletter_items_featured{edition="agents"} ' || items_featured
FROM runs
WHERE date = date('now')
ORDER BY date DESC
LIMIT 1;
SQL

cat >> "$METRIC_FILE" << 'METRICS'
# HELP agent_newsletter_tokens_total LLM tokens used in latest run
# TYPE agent_newsletter_tokens_total gauge
METRICS

sqlite3 "$DB" << 'SQL' >> "$METRIC_FILE"
SELECT 'agent_newsletter_tokens_total{edition="agents"} ' || (tokens_in + tokens_out)
FROM runs
WHERE date = date('now')
ORDER BY date DESC
LIMIT 1;
SQL

cat >> "$METRIC_FILE" << 'METRICS'
# HELP agent_newsletter_duration_seconds Pipeline runtime
# TYPE agent_newsletter_duration_seconds gauge
METRICS

sqlite3 "$DB" << 'SQL' >> "$METRIC_FILE"
SELECT 'agent_newsletter_duration_seconds{edition="agents"} ' || ROUND(duration_seconds, 2)
FROM runs
WHERE date = date('now')
ORDER BY date DESC
LIMIT 1;
SQL

# Move to Prometheus textfile directory
cp "$METRIC_FILE" /var/lib/prometheus/node_exporter/agent-newsletter.prom

echo "Metrics updated"
```

Configure Prometheus `prometheus.yml`:

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'node'
    static_configs:
      - targets: ['localhost:9100']
    metric_path: /metrics
    # Textfile collector automatically scrapes .prom files in this directory
```

### 4. Datadog Integration

Send metrics to Datadog:

```bash
#!/bin/bash
# scripts/datadog-metrics.sh

EDITION=agents
DB="editions/$EDITION/state.db"
DATADOG_API_KEY="${DATADOG_API_KEY:-}"
DATADOG_SITE="${DATADOG_SITE:-datadoghq.com}"

if [ -z "$DATADOG_API_KEY" ]; then
  echo "DATADOG_API_KEY not set"
  exit 1
fi

# Query metrics
METRICS=$(sqlite3 "$DB" << 'SQL'
SELECT 
  items_featured,
  tokens_in + tokens_out as total_tokens,
  ROUND(duration_seconds, 2) as duration_s
FROM runs
WHERE date = date('now')
ORDER BY date DESC
LIMIT 1;
SQL
)

read -r FEATURED TOKENS DURATION <<< "$METRICS"

# Send to Datadog
curl -X POST "https://api.${DATADOG_SITE}/api/v1/series" \
  -H "DD-API-KEY: $DATADOG_API_KEY" \
  -H "Content-Type: application/json" \
  -d @- << PAYLOAD
{
  "series": [
    {
      "metric": "agent_newsletter.items_featured",
      "points": [[$(date +%s), $FEATURED]],
      "tags": ["edition:$EDITION"]
    },
    {
      "metric": "agent_newsletter.tokens_total",
      "points": [[$(date +%s), $TOKENS]],
      "tags": ["edition:$EDITION"]
    },
    {
      "metric": "agent_newsletter.duration_seconds",
      "points": [[$(date +%s), $DURATION]],
      "tags": ["edition:$EDITION"]
    }
  ]
}
PAYLOAD

echo "Metrics sent to Datadog"
```

### 5. CloudWatch Integration (AWS)

Send metrics to CloudWatch:

```bash
#!/bin/bash
# scripts/cloudwatch-metrics.sh

EDITION=agents
DB="editions/$EDITION/state.db"

# Query metrics
METRICS=$(sqlite3 "$DB" << 'SQL'
SELECT 
  items_featured,
  tokens_in + tokens_out as total_tokens,
  ROUND(duration_seconds, 2) as duration_s
FROM runs
WHERE date = date('now')
ORDER BY date DESC
LIMIT 1;
SQL
)

read -r FEATURED TOKENS DURATION <<< "$METRICS"

# Send to CloudWatch
aws cloudwatch put-metric-data \
  --namespace "AgentNewsletter" \
  --metric-name "ItemsFeatured" \
  --value "$FEATURED" \
  --dimensions Edition="$EDITION" \
  --timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

aws cloudwatch put-metric-data \
  --namespace "AgentNewsletter" \
  --metric-name "TokensUsed" \
  --value "$TOKENS" \
  --dimensions Edition="$EDITION" \
  --timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "Metrics sent to CloudWatch"
```

---

## Key Metrics to Monitor

| Metric | Query | Normal Range | Alert Threshold |
|--------|-------|--------------|-----------------|
| Items featured | `SELECT items_featured FROM runs` | 12–18 | < 5 (too few) or > 30 (too many) |
| Tokens per item | `(tokens_in + tokens_out) / items_featured` | 40–80 | > 200 (suggests ranker struggling) |
| Pipeline duration | `duration_seconds FROM runs` | 5–15 min | > 30 min (slow; check rank.py logs) |
| Papers expired unjudged | `papers_expired_unjudged FROM runs` | 0–2 | > 10 (suggests prefilter too tight) |
| Run success rate | `COUNT(*) WHERE outcome='ok' / COUNT(*)` | 100% | < 95% over 7 days |
| Latest run staleness | `date FROM run_attempts MAX(date)` | < 48 hours | > 72 hours (pipeline not running) |

---

## Custom Monitoring Scripts

### Weekly Performance Report

```bash
#!/bin/bash
# scripts/weekly-report.sh

EDITION=agents
DB="editions/$EDITION/state.db"

echo "=== $EDITION Newsletter — Last 7 Days ==="

sqlite3 "$DB" << 'SQL'
SELECT
  printf('%-12s %10s %10s %10s %10s', 'Date', 'Featured', 'Tokens', 'Duration', 'Outcome') as header;

SELECT
  date,
  items_featured,
  tokens_in + tokens_out as total_tokens,
  ROUND(duration_seconds / 60, 1) || 'm' as duration,
  (SELECT outcome FROM run_attempts WHERE run_attempts.date = runs.date ORDER BY start_time DESC LIMIT 1) as outcome
FROM runs
WHERE date >= date('now', '-7 days')
ORDER BY date DESC;

SELECT '';
SELECT 'Weekly Totals:';
SELECT
  'Featured:  ' || SUM(items_featured) || ' items',
  'Tokens:    ' || SUM(tokens_in + tokens_out),
  'Avg time:  ' || printf('%.1f', AVG(duration_seconds) / 60) || ' min'
FROM runs
WHERE date >= date('now', '-7 days');
SQL
```

### Anomaly Detection

Detect unusual patterns:

```bash
#!/bin/bash
# scripts/detect-anomalies.sh

EDITION=agents
DB="editions/$EDITION/state.db"

echo "=== Anomaly Detection ==="

# 1. Unusually slow runs
echo "🔴 Slow runs (>20 min):"
sqlite3 "$DB" "SELECT date, ROUND(duration_seconds/60, 1) as min FROM runs WHERE duration_seconds > 1200 AND date >= date('now', '-30 days') ORDER BY duration_seconds DESC LIMIT 5;"

# 2. Low featured items
echo ""
echo "🔴 Low featured items (<8):"
sqlite3 "$DB" "SELECT date, items_featured FROM runs WHERE items_featured < 8 AND date >= date('now', '-30 days') ORDER BY date DESC LIMIT 5;"

# 3. High token usage
echo ""
echo "🟡 High tokens (>50k):"
sqlite3 "$DB" "SELECT date, tokens_in + tokens_out as total FROM runs WHERE (tokens_in + tokens_out) > 50000 AND date >= date('now', '-30 days') ORDER BY date DESC LIMIT 5;"

# 4. Failed runs
echo ""
echo "🔴 Failed runs:"
sqlite3 "$DB" "SELECT date, COUNT(*) FROM run_attempts WHERE outcome='failed' AND date >= date('now', '-30 days') GROUP BY date ORDER BY date DESC;"
```

---

## Best Practices

1. **Alert on failures first** — set up a "pipeline failed" alert before metrics dashboards
2. **Monitor staleness** — days without a successful run are a bigger red flag than individual metric values
3. **Set reasonable thresholds** — baselines will vary by edition size and LLM model
4. **Log everything** — ensure log files are preserved for debugging (separate from metrics)
5. **Test recovery** — periodically test that your alert system works and you can act on it
6. **Use multiple channels** — email for critical failures, Slack/webhooks for summaries, dashboards for trending

---

## See Also

- [Database Schema and Querying Guide](database-schema-guide.md) — metric definitions and diagnostic queries
- [Debugging Guide](debugging-guide.md) — investigate specific failures
- [Performance and Cost Guide](performance-and-cost-guide.md) — token usage and optimization
