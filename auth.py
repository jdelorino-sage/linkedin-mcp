import hmac
import os
from starlette.responses import JSONResponse


class RequireKey:
    """Reject any HTTP request without a matching x-api-key header."""

    def __init__(self, app):
        self.app = app
        self.key = os.environ.get("MCP_API_KEY", "")
        if not self.key:
            raise RuntimeError("MCP_API_KEY is not set. Refusing to start an open public endpoint.")

    async def __call__(self, scope, receive, send):
        # lifespan and websocket scopes must pass straight through
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied = dict(scope["headers"]).get(b"x-api-key", b"").decode("latin-1")
        if not hmac.compare_digest(supplied, self.key):
            await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
            return

        await self.app(scope, receive, send)
