from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from django.utils.timezone import now
from rest_framework.fields import CharField, ChoiceField, DateTimeField, DictField
from structlog import configure, get_config
from structlog.stdlib import NAME_TO_LEVEL, ProcessorFormatter, get_logger
from structlog.testing import LogCapture
from structlog.types import EventDict

from authentik.core.api.utils import PassiveSerializer
from authentik.events.utils import sanitize_dict


@dataclass()
class LogEvent:

    event: str
    log_level: str
    logger: str
    timestamp: datetime = field(default_factory=now)
    attributes: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_event_dict(item: EventDict) -> LogEvent:
        event = item.pop("event")
        log_level = item.pop("level").lower()
        timestamp = datetime.fromisoformat(item.pop("timestamp")).replace(tzinfo=UTC)
        item.pop("pid", None)
        # Sometimes log entries have both `level` and `log_level` set, but `level` is always set
        item.pop("log_level", None)
        return LogEvent(
            event, log_level, item.pop("logger"), timestamp, attributes=sanitize_dict(item)
        )

    def log(self):
        get_logger(self.logger).log(NAME_TO_LEVEL[self.log_level], self.event, **self.attributes)


class LogEventSerializer(PassiveSerializer):
    """Single log message with all context logged."""

    timestamp = DateTimeField()
    log_level = ChoiceField(choices=tuple((x, x) for x in NAME_TO_LEVEL.keys()))
    logger = CharField()
    event = CharField()
    attributes = DictField()

    # TODO(2024.6?): This is a migration helper to return a correct API response for logs that
    # have been saved in an older format (mostly just list[str] with just the messages)
    def to_representation(self, instance):
        if isinstance(instance, str):
            instance = LogEvent(instance, "", "")
        elif isinstance(instance, list):
            instance = [LogEvent(x, "", "") for x in instance]
        return super().to_representation(instance)


# The captures open in a context, innermost last. A context is a thread or a task, so
# a capture holds only what is logged where it was opened.
_CAPTURES: ContextVar[tuple[LogCapture, ...]] = ContextVar("authentik_log_captures", default=())
_INSTALLING = Lock()


def _capture(logger, method_name: str, event_dict: EventDict) -> EventDict:
    """Hand an entry logged inside capture_logs to the innermost capture of its context in
    place of its output; an entry logged outside every capture passes on"""
    captures = _CAPTURES.get()
    if not captures:
        return event_dict
    return captures[-1](logger, method_name, event_dict)


def _install_capture():
    """Put _capture in the processors, once, where the formatter's wrapping would run.
    The list instance is kept, as bound loggers hold references to it."""
    processors: list = get_config()["processors"]
    if _capture in processors:
        return
    with _INSTALLING:
        if _capture in processors:
            return
        at = len(processors)
        if ProcessorFormatter.wrap_for_formatter in processors:
            at = processors.index(ProcessorFormatter.wrap_for_formatter)
        processors.insert(at, _capture)
        configure(processors=processors)


@contextmanager
def capture_logs(log_default_output=True) -> Generator[list[LogEvent]]:
    """Capture log entries created in this context, in place of their output. Entries other
    threads log at the same time are neither captured nor dropped."""
    _install_capture()
    logs = []
    cap = LogCapture()
    token = _CAPTURES.set((*_CAPTURES.get(), cap))
    try:
        yield logs
        for raw_log in cap.entries:
            logs.append(LogEvent.from_event_dict(raw_log))
    finally:
        _CAPTURES.reset(token)
