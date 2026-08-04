import json
import logging

from alertengine.logging_config import _JsonFormatter


def test_json_formatter_promotes_event_fields_for_logs_insights():
    record = logging.LogRecord(
        name="alertengine.decision",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="event=rule_evaluation symbol=AMC rsi=74.94 bb_pass=true",
        args=(),
        exc_info=None,
    )

    payload = json.loads(_JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["event"] == "rule_evaluation"
    assert payload["symbol"] == "AMC"
    assert payload["rsi"] == "74.94"
    assert payload["bb_pass"] == "true"
