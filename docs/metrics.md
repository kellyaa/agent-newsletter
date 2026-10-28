# Metrics

The project's metrics are documented in [metrics/README.md](metrics/README.md): pipeline metrics (`runs`), reliability metrics (`run_attempts`) and engineering metrics, with the exact columns and how to query them. `tests/test_metrics_docs.py` keeps that document in sync with the database schema.

This pointer exists so tools that look for `docs/metrics.md` (the ACMM `acmm:pr-acceptance-metric` criterion) find it. PR acceptance itself is read from the repository's pull-request history, as listed under Engineering metrics there.
