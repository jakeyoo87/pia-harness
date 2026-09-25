"""Read one public web page body for the current Turn's Context."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
from readability import Document  # type: ignore[import-untyped]
from readability.readability import Unparseable  # type: ignore[import-untyped]

DEFAULT_MAX_CHARS = 6000
DEFAULT_MAX_BYTES = 2_000_000
MAX_REDIRECTS = 5
_HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_META_CHARSET = re.compile(rb"""charset\s*=\s*["']?([A-Za-z0-9_.:-]+)""", re.IGNORECASE)
_MIN_MAIN_CHARS = 200

Resolver = Callable[[str, int], Awaitable[list[str]]]


class UrlReadError(RuntimeError):
    """A page could not be read; the message never contains page content."""


class UrlReader:
    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_chars: int = DEFAULT_MAX_CHARS,
        resolve: Resolver | None = None,
        async_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._max_bytes = max_bytes
        self._max_chars = max_chars
        self._resolve = resolve or _resolve
        self._owns_client = async_client is None
        # trust_env=False: an environment proxy would resolve the host itself and
        # bypass the public-address check below.
        self._client = async_client or httpx.AsyncClient(
            timeout=httpx.Timeout(float(timeout_seconds)),
            follow_redirects=False,
            trust_env=False,
        )

    async def read(self, url: str) -> str:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            await self._check_public(current)
            try:
                async with self._client.stream(
                    "GET",
                    current,
                    follow_redirects=False,
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; pia-harness reader)",
                        "Accept": "text/html,application/xhtml+xml",
                    },
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise UrlReadError("redirect without location")
                        current = urljoin(current, location)
                        continue
                    if response.status_code != 200:
                        raise UrlReadError(f"status {response.status_code}")
                    content_type = (
                        response.headers.get("content-type", "")
                        .split(";")[0]
                        .strip()
                        .lower()
                    )
                    if content_type not in _HTML_TYPES:
                        raise UrlReadError("unsupported content type")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) >= self._max_bytes:
                            del data[self._max_bytes :]
                            break
                    charset = response.charset_encoding
            except httpx.HTTPError:
                raise UrlReadError("request failed") from None
            body = _page_text(_decode(bytes(data), charset))
            if not body.strip():
                raise UrlReadError("empty body")
            if len(body) > self._max_chars:
                body = body[: self._max_chars].rstrip() + " [truncated]"
            return body
        raise UrlReadError("too many redirects")

    async def _check_public(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise UrlReadError("only public http and https URLs are readable")
        if parts.username is not None or parts.password is not None:
            raise UrlReadError("credentials in URLs are not allowed")
        try:
            port = parts.port or (443 if parts.scheme == "https" else 80)
        except ValueError:
            raise UrlReadError("invalid port") from None
        try:
            addresses = await self._resolve(parts.hostname, port)
        except OSError:
            raise UrlReadError("host could not be resolved") from None
        if not addresses:
            raise UrlReadError("host could not be resolved")
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address.split("%", 1)[0])
            except ValueError:
                raise UrlReadError("host address is invalid") from None
            if not ip.is_global:
                raise UrlReadError("non-public addresses are not readable")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


async def _resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    )
    return [str(info[4][0]) for info in infos]


def _decode(data: bytes, charset: str | None) -> str:
    candidates = [charset] if charset else []
    match = _META_CHARSET.search(data[:4096])
    if match:
        candidates.append(match.group(1).decode("ascii", "ignore"))
    candidates.append("utf-8")
    for candidate in candidates:
        try:
            return data.decode(candidate, errors="replace")
        except LookupError:
            continue
    return data.decode("utf-8", errors="replace")


class _PageText(HTMLParser):
    _SKIP = frozenset({"script", "style", "noscript", "template", "svg", "head"})
    _BLOCK = frozenset(
        {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "section", "article"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.body: list[str] = []

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        if tag in self._BLOCK:
            self._add("\n")

    def handle_startendtag(self, tag: str, attrs: object) -> None:
        if tag in self._BLOCK:
            self._add("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        if tag in self._BLOCK:
            self._add("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._add(data)

    def _add(self, text: str) -> None:
        self.body.append(text)


def _page_text(document: str) -> str:
    # readability (Firefox Reader View port) keeps the main content; fall back
    # to the whole page text when it finds nothing substantial.
    try:
        main = _html_text(Document(document).summary(html_partial=True))
    except Unparseable:
        main = ""
    if len(main) >= _MIN_MAIN_CHARS:
        return main
    return _html_text(document)


def _html_text(document: str) -> str:
    parser = _PageText()
    parser.feed(document)
    parser.close()
    return _normalize("".join(parser.body))


def _normalize(text: str) -> str:
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)
