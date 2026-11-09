"""Process-level logging configuration for the pipeline's entry scripts (pure).

Every stage (``fetch``, ``prefilter``, ``rank``, ``write``, ``publish``,
``run_attempt``) and tool (``backfill``, ``replay_writer``, ``reset_day``,
``migrate_layout``) writes into the same per-day log via
``scripts/run_edition.sh``, so they must share one line format. Scripts call
``configure()`` only under ``if __name__ == "__main__":``; importing a script
(as ``backfill`` imports ``rank``/``write``, or the tests import any of them)
must not configure the root logger as a side effect.
``tests/test_logging_config_single_source.py`` enforces this.
"""
from __future__ import annotations

import logging

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_LEVEL = logging.INFO


def configure() -> None:
    """Configure the root logger for a script run as the process entry point.

    Delegates to ``logging.basicConfig``, so it is a no-op when the root
    logger already has handlers (first configuration wins, as before).
    """
    logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
