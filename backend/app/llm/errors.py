"""Errors raised by the model gateway. Graph nodes catch ModelUnavailable and degrade to a fixed, honest reply."""


class ModelUnavailable(Exception):
    """The model could not answer (down, timed out, over budget). Never carries user text or secrets."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ExternalEndpointRefused(RuntimeError):
    """A non-local endpoint is configured but SG_ALLOW_EXTERNAL_LLM is not true."""
