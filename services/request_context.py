"""Per-task request policy; shared model instances remain safe to use concurrently."""

from contextvars import ContextVar

single_attempt_requests = ContextVar("single_attempt_requests", default=False)
