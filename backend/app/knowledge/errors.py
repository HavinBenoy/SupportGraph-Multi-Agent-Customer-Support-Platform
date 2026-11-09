"""Refusals of the knowledge store. The API maps `status` to the HTTP status; `detail` is safe to show (never document or customer text)."""


class KbError(Exception):
    status = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotFound(KbError):
    status = 404


class Conflict(KbError):
    status = 409


class Invalid(KbError):
    status = 422


class Forbidden(KbError):
    status = 403
