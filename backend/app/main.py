"""ASGI entry point: `uvicorn app.main:app`. Importing this module requires a valid SG_AUTH_SECRET."""

from app.api.main import create_app

app = create_app()
