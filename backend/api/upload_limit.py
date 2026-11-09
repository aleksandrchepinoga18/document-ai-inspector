"""Bound the entire multipart request before Starlette can spool it to disk."""

from starlette.responses import JSONResponse
from starlette.exceptions import HTTPException


class UploadTooLarge(Exception):
    pass


class UploadLimitMiddleware:
    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/documents/check":
            return await self.app(scope, receive, send)
        received = 0
        headers = dict(scope.get("headers", []))

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise HTTPException(status_code=413, detail={
                        "code": "request_too_large", "message": "Превышен лимит размера multipart-запроса.",
                    })
            return message

        try:
            length = headers.get(b"content-length")
            if length is not None:
                try:
                    size = int(length)
                except ValueError:
                    size = self.max_bytes + 1
                if size < 0 or size > self.max_bytes:
                    raise UploadTooLarge
            await self.app(scope, limited_receive, send)
        except UploadTooLarge:
            await JSONResponse(
                {"detail": {"code": "request_too_large", "message": "Превышен лимит размера multipart-запроса."}},
                status_code=413,
            )(scope, receive, send)
