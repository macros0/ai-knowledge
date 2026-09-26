"""Stream an already-open artifact so publication cannot invalidate its path."""
import os
from pathlib import Path
from secrets import token_hex

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.responses import FileResponse


class OpenedFileResponse(FileResponse):
    """Keep FileResponse's range/header handling, but read the pinned descriptor.

    The caller opens the file under its document read lock. The ASGI server must
    never reopen its old path (including through the pathsend extension).
    """

    def __init__(self, path: Path, *, media_type: str, headers: dict | None = None, filename: str | None = None):
        self._file = Path(path).open("rb")
        try:
            super().__init__(Path(path), media_type=media_type, headers=headers, filename=filename,
                             stat_result=os.fstat(self._file.fileno()))
        except BaseException:
            self._file.close()
            raise

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._file.close()

    async def _send_bytes(self, send, start: int, end: int):
        await run_in_threadpool(self._file.seek, start)
        while start < end:
            chunk = await run_in_threadpool(self._file.read, min(self.chunk_size, end - start))
            if not chunk:
                raise OSError("Published artifact was truncated during download")
            start += len(chunk)
            await send({"type": "http.response.body", "body": chunk, "more_body": True})

    async def _handle_simple(self, send, send_header_only: bool, send_pathsend: bool):
        await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
        if not send_header_only:
            await self._send_bytes(send, 0, self.stat_result.st_size)
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def _handle_single_range(self, send, start: int, end: int, file_size: int, send_header_only: bool):
        headers = MutableHeaders(raw=list(self.raw_headers))
        headers["content-range"] = f"bytes {start}-{end - 1}/{file_size}"
        headers["content-length"] = str(end - start)
        await send({"type": "http.response.start", "status": 206, "headers": headers.raw})
        if not send_header_only:
            await self._send_bytes(send, start, end)
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def _handle_multiple_ranges(self, send, ranges, file_size: int, send_header_only: bool):
        boundary = token_hex(13)
        length, range_header = self.generate_multipart(ranges, boundary, file_size, self.headers["content-type"])
        headers = MutableHeaders(raw=list(self.raw_headers))
        headers["content-type"] = f"multipart/byteranges; boundary={boundary}"
        headers["content-length"] = str(length)
        await send({"type": "http.response.start", "status": 206, "headers": headers.raw})
        if send_header_only:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        for start, end in ranges:
            await send({"type": "http.response.body", "body": range_header(start, end), "more_body": True})
            await self._send_bytes(send, start, end)
            await send({"type": "http.response.body", "body": b"\r\n", "more_body": True})
        await send({"type": "http.response.body", "body": f"--{boundary}--".encode("ascii"), "more_body": False})
