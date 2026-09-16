from __future__ import annotations

import asyncio
import html
import ipaddress
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from aiohttp import web
from bs4 import BeautifulSoup

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("username-checker")


# ============================================================
# SETTINGS
# ============================================================

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    bot_token: str = Field(
        default="",
        alias="BOT_TOKEN",
    )

    render_url: str = Field(
        default="",
        alias="RENDER_URL",
    )

    port: int = Field(
        default=10000,
        alias="PORT",
    )

    request_timeout: float = Field(
        default=15.0,
        alias="REQUEST_TIMEOUT",
    )

    media_timeout: float = Field(
        default=45.0,
        alias="MEDIA_TIMEOUT",
    )

    max_concurrency: int = Field(
        default=8,
        alias="MAX_CONCURRENCY",
    )

    cache_ttl: int = Field(
        default=300,
        alias="CACHE_TTL",
    )

    profile_cache_ttl: int = Field(
        default=300,
        alias="PROFILE_CACHE_TTL",
    )

    media_cache_ttl: int = Field(
        default=120,
        alias="MEDIA_CACHE_TTL",
    )

    max_media_size_mb: int = Field(
        default=45,
        alias="MAX_MEDIA_SIZE_MB",
    )

    max_media_items: int = Field(
        default=20,
        alias="MAX_MEDIA_ITEMS",
    )

    max_username_length: int = Field(
        default=100,
        alias="MAX_USERNAME_LENGTH",
    )

    max_sessions: int = Field(
        default=1000,
        alias="MAX_SESSIONS",
    )


settings = Settings()


# ============================================================
# DISPATCHER
# ============================================================

# IMPORTANT:
# يجب أن يكون Dispatcher معرفًا قبل أي @dp.message
# أو @dp.callback_query
dp = Dispatcher()


# ============================================================
# STATUS
# ============================================================

class Status(str, Enum):
    CONFIRMED = "CONFIRMED"
    NOT_FOUND = "NOT_FOUND"
    UNKNOWN = "UNKNOWN"
    RATE_LIMITED = "RATE_LIMITED"
    PRIVATE = "PRIVATE"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"


class MediaType(str, Enum):
    VIDEO = "VIDEO"
    IMAGE = "IMAGE"
    STORY = "STORY"
    UNKNOWN = "UNKNOWN"


# ============================================================
# MODELS
# ============================================================

@dataclass
class Evidence:
    message: str
    weight: int = 0


@dataclass
class CheckResult:
    platform: str
    username: str
    status: Status
    confidence: int = 0
    profile_url: str | None = None
    evidence: list[Evidence] = field(default_factory=list)
    reason: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    checked_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass
class PublicProfile:
    platform: str
    username: str
    display_name: str | None = None
    bio: str | None = None
    avatar_url: str | None = None
    followers: int | None = None
    following: int | None = None
    likes: int | None = None
    videos: int | None = None
    verified: bool | None = None
    website: str | None = None
    profile_url: str | None = None
    raw_source: str | None = None


@dataclass
class PublicMedia:
    platform: str
    username: str
    media_type: MediaType
    url: str
    thumbnail_url: str | None = None
    title: str | None = None
    published_at: str | None = None
    source_url: str | None = None
    downloadable: bool = False


# ============================================================
# HELPERS
# ============================================================

USERNAME_RE = re.compile(
    r"^[A-Za-z0-9._-]{1,100}$"
)


def normalize_username(username: str) -> str:
    username = username.strip()

    if username.startswith("@"):
        username = username[1:]

    return username.strip()


def valid_username(username: str) -> bool:
    return bool(USERNAME_RE.fullmatch(username))


def esc(value: Any) -> str:
    return html.escape(str(value))


def status_icon(status: Status) -> str:
    return {
        Status.CONFIRMED: "🟢",
        Status.NOT_FOUND: "🔴",
        Status.UNKNOWN: "🟠",
        Status.RATE_LIMITED: "🟡",
        Status.PRIVATE: "🔒",
        Status.UNAVAILABLE: "⚪",
        Status.ERROR: "⚫",
    }.get(status, "⚪")


def status_name(status: Status) -> str:
    return {
        Status.CONFIRMED: "CONFIRMED",
        Status.NOT_FOUND: "NOT FOUND",
        Status.UNKNOWN: "UNKNOWN",
        Status.RATE_LIMITED: "RATE LIMITED",
        Status.PRIVATE: "PRIVATE",
        Status.UNAVAILABLE: "UNAVAILABLE",
        Status.ERROR: "ERROR",
    }.get(status, "UNKNOWN")


def confidence_bar(value: int) -> str:
    value = max(0, min(100, value))
    filled = round(value / 10)

    return "█" * filled + "░" * (10 - filled)


def safe_profile_url(url: str | None) -> str | None:
    if not url:
        return None

    try:
        parsed = urlparse(url)
    except Exception:
        return None

    if parsed.scheme not in {"https", "http"}:
        return None

    if not parsed.netloc:
        return None

    return url


def make_link(text: str, url: str | None) -> str:
    url = safe_profile_url(url)

    if not url:
        return esc(text)

    return f'<a href="{esc(url)}">{esc(text)}</a>'


def compact_number(value: Any) -> str:
    if value is None:
        return "—"

    try:
        number = int(value)
    except Exception:
        return esc(value)

    if number >= 1_000_000_000:
        return f"{number / 1_000_000_000:.1f}B"

    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"

    if number >= 1_000:
        return f"{number / 1_000:.1f}K"

    return str(number)


def normalize_url(
    value: str | None,
    base_url: str,
) -> str | None:
    if not value:
        return None

    value = value.strip()

    if not value:
        return None

    try:
        result = urljoin(base_url, value)
        parsed = urlparse(result)

        if parsed.scheme not in {"http", "https"}:
            return None

        if not parsed.hostname:
            return None

        return result
    except Exception:
        return None


# ============================================================
# MEDIA URL SECURITY
# ============================================================

def is_safe_media_url(url: str) -> bool:
    """
    Basic protection:
    - HTTPS only
    - rejects localhost
    - rejects private/link-local/loopback IP literals
    - rejects unsupported schemes
    """

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme != "https":
        return False

    hostname = parsed.hostname

    if not hostname:
        return False

    hostname = hostname.lower().strip(".")

    blocked_hosts = {
        "localhost",
        "localhost.localdomain",
    }

    if hostname in blocked_hosts:
        return False

    try:
        ip = ipaddress.ip_address(hostname)

        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False

    except ValueError:
        # Normal hostname.
        pass

    return True


# ============================================================
# CACHE
# ============================================================

@dataclass
class CacheEntry:
    created_at: float
    value: Any


class TTLCache:

    def __init__(self, ttl: int):
        self.ttl = max(1, ttl)

        self._cache: dict[str, CacheEntry] = {}

        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._cache.get(key)

            if entry is None:
                return None

            age = time.monotonic() - entry.created_at

            if age > self.ttl:
                self._cache.pop(key, None)
                return None

            return entry.value

    async def set(
        self,
        key: str,
        value: Any,
    ) -> None:
        async with self._lock:
            self._cache[key] = CacheEntry(
                created_at=time.monotonic(),
                value=value,
            )

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._cache.pop(key, None)

    async def clear(self) -> None:
        async with self._lock:
            self._cache.clear()


result_cache = TTLCache(
    settings.cache_ttl
)

profile_cache = TTLCache(
    settings.profile_cache_ttl
)

media_cache = TTLCache(
    settings.media_cache_ttl
)


# ============================================================
# HTTP CLIENT
# ============================================================

class HTTPClient:

    RETRY_STATUS_CODES = {
        408,
        425,
        429,
        500,
        502,
        503,
        504,
    }

    def __init__(
        self,
        timeout: float,
    ):
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                timeout
            ),
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(compatible; "
                    "UsernameChecker/3.0)"
                ),
                "Accept": "*/*",
            },
            limits=httpx.Limits(
                max_connections=30,
                max_keepalive_connections=10,
            ),
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        attempts: int = 3,
    ) -> httpx.Response | None:

        last_response: httpx.Response | None = None

        for attempt in range(1, attempts + 1):

            try:
                response = await self.client.get(
                    url,
                    headers=headers,
                    params=params,
                )

                last_response = response

                if (
                    response.status_code
                    not in self.RETRY_STATUS_CODES
                ):
                    return response

                logger.warning(
                    "Retryable HTTP %s | attempt=%s | %s",
                    response.status_code,
                    attempt,
                    url,
                )

            except (
                httpx.TimeoutException,
                httpx.NetworkError,
            ) as exc:

                logger.warning(
                    "HTTP network error | attempt=%s | %s | %s",
                    attempt,
                    url,
                    exc,
                )

            if attempt < attempts:
                await asyncio.sleep(
                    min(2 ** (attempt - 1), 8)
                )

        # مهم:
        # نعيد آخر response حتى نستطيع معرفة 429
        # أو 503 بدل تحويله إلى UNKNOWN.
        return last_response

    async def stream_to_file(
        self,
        url: str,
        path: str,
        max_bytes: int,
    ) -> tuple[bool, str]:

        if not is_safe_media_url(url):
            return False, "UNSAFE_URL"

        try:
            timeout = httpx.Timeout(
                settings.media_timeout
            )

            async with self.client.stream(
                "GET",
                url,
                timeout=timeout,
                headers={
                    "Accept": (
                        "video/*,"
                        "image/*"
                    ),
                },
            ) as response:

                status = response.status_code

                if status == 401:
                    return False, "HTTP 401"

                if status == 403:
                    return False, "HTTP 403"

                if status == 404:
                    return False, "HTTP 404"

                if status == 429:
                    return False, "RATE_LIMITED"

                if status >= 500:
                    return False, f"HTTP {status}"

                if status >= 400:
                    return False, f"HTTP {status}"

                content_type = (
                    response.headers
                    .get("content-type", "")
                    .lower()
                )

                # لا نحفظ HTML على أنه صورة/فيديو.
                if (
                    "text/html" in content_type
                    or "application/xhtml" in content_type
                ):
                    return False, "NOT_DIRECT_MEDIA"

                if (
                    "mpegurl" in content_type
                    or "x-mpegurl" in content_type
                ):
                    return False, "STREAM_MANIFEST"

                allowed_type = (
                    content_type.startswith("video/")
                    or content_type.startswith("image/")
                    or content_type.startswith(
                        "application/octet-stream"
                    )
                )

                if not allowed_type:
                    return False, "UNSUPPORTED_CONTENT_TYPE"

                content_length = response.headers.get(
                    "content-length"
                )

                if content_length:

                    try:
                        if (
                            int(content_length)
                            > max_bytes
                        ):
                            return False, "FILE_TOO_LARGE"
                    except ValueError:
                        pass

                total = 0

                with open(path, "wb") as file:

                    async for chunk in response.aiter_bytes(
                        64 * 1024
                    ):
                        if not chunk:
                            continue

                        total += len(chunk)

                        if total > max_bytes:
                            return False, "FILE_TOO_LARGE"

                        file.write(chunk)

                if total <= 0:
                    return False, "EMPTY_FILE"

                return True, "OK"

        except httpx.TimeoutException:
            return False, "TIMEOUT"

        except httpx.NetworkError:
            return False, "NETWORK_ERROR"

        except Exception as exc:
            logger.exception(
                "Media download failed: %s",
                url,
            )

            return False, str(exc)


http_client = HTTPClient(
    settings.request_timeout
)


# ============================================================
# PROVIDER BASE
# ============================================================

class Provider:

    name = "Unknown"

    PROFILE_TEMPLATE = ""

    NOT_FOUND_MARKERS: tuple[str, ...] = ()

    PRIVATE_MARKERS: tuple[str, ...] = ()

    async def check(
        self,
        username: str,
        http: HTTPClient,
    ) -> CheckResult:
        raise NotImplementedError

    async def profile(
        self,
        username: str,
        http: HTTPClient,
    ) -> PublicProfile | None:
        return None

    async def media(
        self,
        username: str,
        http: HTTPClient,
    ) -> list[PublicMedia]:
        return []

    def result(
        self,
        username: str,
        status: Status,
        *,
        confidence: int = 0,
        profile_url: str | None = None,
        evidence: list[Evidence] | None = None,
        reason: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> CheckResult:

        return CheckResult(
            platform=self.name,
            username=username,
            status=status,
            confidence=max(
                0,
                min(100, confidence),
            ),
            profile_url=profile_url,
            evidence=evidence or [],
            reason=reason,
            data=data or {},
        )


# ============================================================
# HTML HELPERS
# ============================================================

def meta_content(
    soup: BeautifulSoup,
    *,
    name: str | None = None,
    prop: str | None = None,
) -> str | None:

    tag = None

    if name:
        tag = soup.find(
            "meta",
            attrs={"name": name},
        )

    if tag is None and prop:
        tag = soup.find(
            "meta",
            attrs={"property": prop},
        )

    if tag is None:
        return None

    value = tag.get("content")

    if not value:
        return None

    return str(value).strip()


def extract_json_number(
    text: str,
    keys: list[str],
) -> int | None:

    for key in keys:

        patterns = [
            rf'"{re.escape(key)}"\s*:\s*(\d+)',
            rf'"{re.escape(key)}"\s*:\s*"([\d,]+)"',
        ]

        for pattern in patterns:

            match = re.search(
                pattern,
                text,
                re.IGNORECASE,
            )

            if not match:
                continue

            raw = match.group(1).replace(
                ",",
                "",
            )

            try:
                return int(raw)
            except ValueError:
                continue

    return None


def extract_boolean(
    text: str,
    keys: list[str],
) -> bool | None:

    for key in keys:

        pattern = (
            rf'"{re.escape(key)}"'
            r'\s*:\s*(true|false)'
        )

        match = re.search(
            pattern,
            text,
            re.IGNORECASE,
        )

        if match:
            return (
                match.group(1).lower()
                == "true"
            )

    return None


def extract_json_string(
    text: str,
    keys: list[str],
) -> str | None:

    for key in keys:

        pattern = (
            rf'"{re.escape(key)}"'
            r'\s*:\s*"([^"]*)"'
        )

        match = re.search(
            pattern,
            text,
            re.IGNORECASE,
        )

        if match:

            value = match.group(1).replace(
                '\\"',
                '"',
            )

            if value.strip():
                return value.strip()

    return None


def username_appears_in_metadata(
    username: str,
    values: list[str | None],
) -> bool:

    target = username.casefold()

    for value in values:

        if not value:
            continue

        normalized = re.sub(
            r"[^a-z0-9._-]+",
            " ",
            value.casefold(),
        )

        tokens = normalized.split()

        if target in tokens:
            return True

        if (
            f"@{target}"
            in value.casefold()
        ):
            return True

    return False


def extract_media_from_html(
    platform: str,
    username: str,
    page_url: str,
    soup: BeautifulSoup,
) -> list[PublicMedia]:

    items: list[PublicMedia] = []

    seen: set[str] = set()

    og_image = meta_content(
        soup,
        prop="og:image",
    )

    og_title = meta_content(
        soup,
        prop="og:title",
    )

    # --------------------------------------------------------
    # Videos
    # --------------------------------------------------------

    video_urls: list[str] = []

    for prop in (
        "og:video",
        "og:video:url",
        "og:video:secure_url",
    ):

        value = meta_content(
            soup,
            prop=prop,
        )

        normalized = normalize_url(
            value,
            page_url,
        )

        if normalized:
            video_urls.append(
                normalized
            )

    for video in soup.find_all("video"):

        src = video.get("src")

        normalized = normalize_url(
            src,
            page_url,
        )

        if normalized:
            video_urls.append(
                normalized
            )

        for source in video.find_all("source"):

            src = source.get("src")

            normalized = normalize_url(
                src,
                page_url,
            )

            if normalized:
                video_urls.append(
                    normalized
                )

    for video_url in video_urls:

        if video_url in seen:
            continue

        seen.add(video_url)

        downloadable = is_safe_media_url(
            video_url
        )

        items.append(
            PublicMedia(
                platform=platform,
                username=username,
                media_type=MediaType.VIDEO,
                url=video_url,
                thumbnail_url=normalize_url(
                    og_image,
                    page_url,
                ),
                title=og_title,
                source_url=page_url,
                downloadable=downloadable,
            )
        )

        if len(items) >= settings.max_media_items:
            return items

    # --------------------------------------------------------
    # OpenGraph image
    # --------------------------------------------------------

    normalized_image = normalize_url(
        og_image,
        page_url,
    )

    if (
        normalized_image
        and normalized_image not in seen
    ):

        seen.add(normalized_image)

        items.append(
            PublicMedia(
                platform=platform,
                username=username,
                media_type=MediaType.IMAGE,
                url=normalized_image,
                thumbnail_url=normalized_image,
                title=og_title,
                source_url=page_url,
                downloadable=is_safe_media_url(
                    normalized_image
                ),
            )
        )

    # --------------------------------------------------------
    # Images
    # --------------------------------------------------------

    for image in soup.find_all("img"):

        if len(items) >= settings.max_media_items:
            break

        src = (
            image.get("src")
            or image.get("data-src")
            or image.get("data-lazy-src")
        )

        normalized = normalize_url(
            src,
            page_url,
        )

        if not normalized:
            continue

        if normalized in seen:
            continue

        seen.add(normalized)

        items.append(
            PublicMedia(
                platform=platform,
                username=username,
                media_type=MediaType.IMAGE,
                url=normalized,
                title=image.get("alt") or None,
                source_url=page_url,
                downloadable=is_safe_media_url(
                    normalized
                ),
            )
        )

    return items[:settings.max_media_items]


# ============================================================
# GITHUB
# ============================================================

class GitHubProvider(Provider):

    name = "GitHub"

    API_URL = (
        "https://api.github.com/users/{username}"
    )

    async def check(
        self,
        username: str,
        http: HTTPClient,
    ) -> CheckResult:

        url = self.API_URL.format(
            username=username
        )

        response = await http.get(
            url,
            headers={
                "Accept": (
                    "application/vnd.github+json"
                ),
                "X-GitHub-Api-Version":
                    "2022-11-28",
            },
        )

        if response is None:
            return self.result(
                username,
                Status.UNKNOWN,
                reason="GitHub request failed.",
            )

        if response.status_code == 404:
            return self.result(
                username,
                Status.NOT_FOUND,
                confidence=95,
                evidence=[
                    Evidence(
                        "Official GitHub API returned 404.",
                        95,
                    )
                ],
            )

        if response.status_code in {403, 429}:
            return self.result(
                username,
                Status.RATE_LIMITED,
                reason=(
                    "GitHub API rate limit "
                    "or access restriction."
                ),
            )

        if response.status_code != 200:
            return self.result(
                username,
                Status.UNKNOWN,
                reason=(
                    f"GitHub returned HTTP "
                    f"{response.status_code}."
                ),
            )

        try:
            data = response.json()
        except Exception:
            return self.result(
                username,
                Status.UNKNOWN,
                reason="Invalid GitHub JSON.",
            )

        login = str(
            data.get("login", "")
        )

        if login.casefold() != username.casefold():
            return self.result(
                username,
                Status.UNKNOWN,
                reason="GitHub username mismatch.",
            )

        return self.result(
            username,
            Status.CONFIRMED,
            confidence=100,
            profile_url=data.get("html_url"),
            evidence=[
                Evidence(
                    "Official GitHub API confirmed the account.",
                    70,
                ),
                Evidence(
                    "Returned login matches the requested username.",
                    30,
                ),
            ],
            data={
                "name": data.get("name"),
                "bio": data.get("bio"),
                "company": data.get("company"),
                "location": data.get("location"),
                "public_repos": data.get("public_repos"),
                "followers": data.get("followers"),
                "following": data.get("following"),
                "public_gists": data.get("public_gists"),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
            },
        )

    async def profile(
        self,
        username: str,
        http: HTTPClient,
    ) -> PublicProfile | None:

        result = await self.check(
            username,
            http,
        )

        if result.status != Status.CONFIRMED:
            return None

        data = result.data

        return PublicProfile(
            platform=self.name,
            username=username,
            display_name=data.get("name"),
            bio=data.get("bio"),
            followers=data.get("followers"),
            following=data.get("following"),
            profile_url=result.profile_url,
        )


# ============================================================
# REDDIT
# ============================================================

class RedditProvider(Provider):

    name = "Reddit"

    API_URL = (
        "https://www.reddit.com/"
        "user/{username}/about.json"
    )

    async def check(
        self,
        username: str,
        http: HTTPClient,
    ) -> CheckResult:

        url = self.API_URL.format(
            username=username
        )

        response = await http.get(
            url,
            headers={
                "User-Agent":
                    "UsernameChecker/3.0",
            },
        )

        if response is None:
            return self.result(
                username,
                Status.UNKNOWN,
                reason="Reddit request failed.",
            )

        if response.status_code == 404:
            return self.result(
                username,
                Status.NOT_FOUND,
                confidence=95,
                evidence=[
                    Evidence(
                        "Reddit returned 404.",
                        95,
                    )
                ],
            )

        if response.status_code in {403, 429}:
            return self.result(
                username,
                Status.RATE_LIMITED,
                reason=(
                    "Reddit rate limit "
                    "or access restriction."
                ),
            )

        if response.status_code != 200:
            return self.result(
                username,
                Status.UNKNOWN,
                reason=(
                    f"Reddit returned HTTP "
                    f"{response.status_code}."
                ),
            )

        try:
            payload = response.json()
            data = payload.get("data") or {}
        except Exception:
            return self.result(
                username,
                Status.UNKNOWN,
                reason="Invalid Reddit JSON.",
            )

        account_name = str(
            data.get("name", "")
        )

        if (
            account_name.casefold()
            != username.casefold()
        ):
            return self.result(
                username,
                Status.UNKNOWN,
                reason="Reddit username mismatch.",
            )

        profile_url = (
            f"https://www.reddit.com/"
            f"user/{account_name}/"
        )

        return self.result(
            username,
            Status.CONFIRMED,
            confidence=100,
            profile_url=profile_url,
            evidence=[
                Evidence(
                    "Reddit returned a matching public user.",
                    100,
                )
            ],
            data={
                "name": account_name,
                "created_utc": data.get("created_utc"),
                "link_karma": data.get("link_karma"),
                "comment_karma": data.get("comment_karma"),
                "is_gold": data.get("is_gold"),
            },
        )

    async def profile(
        self,
        username: str,
        http: HTTPClient,
    ) -> PublicProfile | None:

        result = await self.check(
            username,
            http,
        )

        if result.status != Status.CONFIRMED:
            return None

        return PublicProfile(
            platform=self.name,
            username=username,
            display_name=result.data.get("name"),
            profile_url=result.profile_url,
        )


# ============================================================
# GENERIC HTML PROVIDER
# ============================================================

class HTMLProvider(Provider):

    PROFILE_TEMPLATE = ""

    NOT_FOUND_MARKERS: tuple[str, ...] = ()

    PRIVATE_MARKERS: tuple[str, ...] = ()

    async def fetch_page(
        self,
        username: str,
        http: HTTPClient,
    ) -> tuple[str, httpx.Response | None]:

        url = self.PROFILE_TEMPLATE.format(
            username=username
        )

        response = await http.get(url)

        return url, response

    def metadata_confidence(
        self,
        username: str,
        title: str | None,
        og_title: str | None,
        description: str | None,
        text: str,
    ) -> tuple[int, list[Evidence]]:

        evidence: list[Evidence] = []

        score = 0

        values = [
            title,
            og_title,
            description,
        ]

        if username_appears_in_metadata(
            username,
            values,
        ):
            score += 60

            evidence.append(
                Evidence(
                    "Requested username appears in public page metadata.",
                    60,
                )
            )

        if og_title:
            score += 10

            evidence.append(
                Evidence(
                    "OpenGraph title is present.",
                    10,
                )
            )

        if description:
            score += 5

            evidence.append(
                Evidence(
                    "Public description metadata is present.",
                    5,
                )
            )

        # Search for common profile indicators.
        indicators = (
            "followers",
            "following",
            "profile",
            "username",
            "verified",
        )

        indicator_hits = sum(
            1
            for item in indicators
            if item in text.lower()
        )

        if indicator_hits >= 2:
            score += 15

            evidence.append(
                Evidence(
                    "Page contains multiple profile-related indicators.",
                    15,
                )
            )

        return min(score, 100), evidence

    async def check(
        self,
        username: str,
        http: HTTPClient,
    ) -> CheckResult:

        url, response = await self.fetch_page(
            username,
            http,
        )

        if response is None:
            return self.result(
                username,
                Status.UNKNOWN,
                profile_url=url,
                reason="Request failed or timed out.",
            )

        if response.status_code == 404:
            return self.result(
                username,
                Status.NOT_FOUND,
                confidence=90,
                profile_url=url,
                evidence=[
                    Evidence(
                        "Platform returned HTTP 404.",
                        90,
                    )
                ],
            )

        if response.status_code == 429:
            return self.result(
                username,
                Status.RATE_LIMITED,
                profile_url=url,
                reason="Platform rate limited the request.",
            )

        if response.status_code in {401, 403}:
            return self.result(
                username,
                Status.UNKNOWN,
                profile_url=url,
                reason=(
                    f"Platform returned HTTP "
                    f"{response.status_code}."
                ),
            )

        if response.status_code >= 500:
            return self.result(
                username,
                Status.UNKNOWN,
                profile_url=url,
                reason=(
                    f"Platform server returned HTTP "
                    f"{response.status_code}."
                ),
            )

        if response.status_code != 200:
            return self.result(
                username,
                Status.UNKNOWN,
                profile_url=url,
                reason=(
                    f"Platform returned HTTP "
                    f"{response.status_code}."
                ),
            )

        text = response.text

        if not text:
            return self.result(
                username,
                Status.UNKNOWN,
                profile_url=url,
                reason="Empty response.",
            )

        lower_text = text.lower()

        # ----------------------------------------------------
        # Not found markers
        # ----------------------------------------------------

        for marker in self.NOT_FOUND_MARKERS:

            if marker.lower() in lower_text:

                return self.result(
                    username,
                    Status.NOT_FOUND,
                    confidence=85,
                    profile_url=url,
                    evidence=[
                        Evidence(
                            f"Page contained not-found marker: {marker}",
                            85,
                        )
                    ],
                )

        # ----------------------------------------------------
        # Private markers
        # ----------------------------------------------------

        for marker in self.PRIVATE_MARKERS:

            if marker.lower() in lower_text:

                return self.result(
                    username,
                    Status.PRIVATE,
                    confidence=85,
                    profile_url=url,
                    evidence=[
                        Evidence(
                            f"Page indicated private/restricted content: {marker}",
                            85,
                        )
                    ],
                )

        soup = BeautifulSoup(
            text,
            "html.parser",
        )

        title = (
            soup.title.string.strip()
            if soup.title
            and soup.title.string
            else None
        )

        og_title = meta_content(
            soup,
            prop="og:title",
        )

        description = meta_content(
            soup,
            prop="og:description",
        )

        og_image = meta_content(
            soup,
            prop="og:image",
        )

        confidence, evidence = (
            self.metadata_confidence(
                username,
                title,
                og_title,
                description,
                text,
            )
        )

        # Do not confirm simply because HTTP 200 exists.
        if confidence >= 70:

            return self.result(
                username,
                Status.CONFIRMED,
                confidence=confidence,
                profile_url=url,
                evidence=evidence,
                data={
                    "title": title,
                    "og_title": og_title,
                    "og_description": description,
                    "og_image": og_image,
                },
            )

        return self.result(
            username,
            Status.UNKNOWN,
            confidence=confidence,
            profile_url=url,
            evidence=evidence,
            reason=(
                "Page loaded, but there was not enough "
                "evidence to confirm the requested account."
            ),
        )

    async def profile(
        self,
        username: str,
        http: HTTPClient,
    ) -> PublicProfile | None:

        url, response = await self.fetch_page(
            username,
            http,
        )

        if response is None:
            return None

        if response.status_code != 200:
            return None

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        title = meta_content(
            soup,
            prop="og:title",
        )

        description = meta_content(
            soup,
            prop="og:description",
        )

        image = meta_content(
            soup,
            prop="og:image",
        )

        text = response.text

        followers = extract_json_number(
            text,
            [
                "followerCount",
                "followers",
                "followersCount",
            ],
        )

        following = extract_json_number(
            text,
            [
                "followingCount",
                "following",
            ],
        )

        likes = extract_json_number(
            text,
            [
                "heartCount",
                "likes",
                "likeCount",
            ],
        )

        videos = extract_json_number(
            text,
            [
                "videoCount",
                "videos",
                "video_count",
            ],
        )

        verified = extract_boolean(
            text,
            [
                "verified",
                "isVerified",
            ],
        )

        website = extract_json_string(
            text,
            [
                "website",
                "websiteUrl",
                "bioLink",
            ],
        )

        display_name = (
            extract_json_string(
                text,
                [
                    "nickname",
                    "displayName",
                    "name",
                ],
            )
            or title
        )

        return PublicProfile(
            platform=self.name,
            username=username,
            display_name=display_name,
            bio=description,
            avatar_url=normalize_url(
                image,
                url,
            ),
            followers=followers,
            following=following,
            likes=likes,
            videos=videos,
            verified=verified,
            website=website,
            profile_url=url,
        )

    async def media(
        self,
        username: str,
        http: HTTPClient,
    ) -> list[PublicMedia]:

        url, response = await self.fetch_page(
            username,
            http,
        )

        if response is None:
            return []

        if response.status_code != 200:
            return []

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        return extract_media_from_html(
            self.name,
            username,
            url,
            soup,
        )


# ============================================================
# INSTAGRAM
# ============================================================

class InstagramProvider(HTMLProvider):

    name = "Instagram"

    PROFILE_TEMPLATE = (
        "https://www.instagram.com/{username}/"
    )

    NOT_FOUND_MARKERS = (
        "page isn't available",
        "sorry, this page isn't available",
        "the link you followed may be broken",
    )

    PRIVATE_MARKERS = (
        "this account is private",
        "account is private",
    )


# ============================================================
# TIKTOK
# ============================================================

class TikTokProvider(HTMLProvider):

    name = "TikTok"

    PROFILE_TEMPLATE = (
        "https://www.tiktok.com/@{username}"
    )

    NOT_FOUND_MARKERS = (
        "couldn't find this account",
        "couldn't find this page",
        "page not found",
        "account doesn't exist",
    )

    PRIVATE_MARKERS = (
        "private account",
        "this account is private",
    )


# ============================================================
# SNAPCHAT
# ============================================================

class SnapchatProvider(HTMLProvider):

    name = "Snapchat"

    PROFILE_TEMPLATE = (
        "https://www.snapchat.com/add/{username}"
    )

    NOT_FOUND_MARKERS = (
        "page not found",
        "couldn't find",
        "doesn't exist",
    )

    # لا نستخدم marker عام مثل "private"
    # لأنه يسبب false positives.
    PRIVATE_MARKERS = ()


# ============================================================
# X
# ============================================================

class XProvider(HTMLProvider):

    name = "X"

    PROFILE_TEMPLATE = (
        "https://x.com/{username}"
    )

    NOT_FOUND_MARKERS = (
        "this account doesn't exist",
        "this account does not exist",
    )


# ============================================================
# PINTEREST
# ============================================================

class PinterestProvider(HTMLProvider):

    name = "Pinterest"

    PROFILE_TEMPLATE = (
        "https://www.pinterest.com/{username}/"
    )

    NOT_FOUND_MARKERS = (
        "page not found",
        "sorry, we couldn't find that page",
    )


# ============================================================
# TWITCH
# ============================================================

class TwitchProvider(HTMLProvider):

    name = "Twitch"

    PROFILE_TEMPLATE = (
        "https://www.twitch.tv/{username}"
    )

    NOT_FOUND_MARKERS = (
        "sorry. unless you've got a time machine",
        "page not found",
    )


# ============================================================
# PROVIDERS
# ============================================================

PROVIDERS: list[Provider] = [
    GitHubProvider(),
    RedditProvider(),
    InstagramProvider(),
    TikTokProvider(),
    SnapchatProvider(),
    XProvider(),
    PinterestProvider(),
    TwitchProvider(),
]

PROVIDER_MAP: dict[str, Provider] = {
    provider.name.lower(): provider
    for provider in PROVIDERS
}


# ============================================================
# ENGINE
# ============================================================

class CheckEngine:

    def __init__(
        self,
        http: HTTPClient,
        max_concurrency: int,
    ):
        self.http = http

        self.semaphore = asyncio.Semaphore(
            max(1, max_concurrency)
        )

    async def run_provider(
        self,
        provider: Provider,
        username: str,
        *,
        force: bool = False,
    ) -> CheckResult:

        cache_key = (
            f"check:"
            f"{provider.name.lower()}:"
            f"{username.lower()}"
        )

        if not force:

            cached = await result_cache.get(
                cache_key
            )

            if cached is not None:
                return cached

        async with self.semaphore:

            try:

                result = await provider.check(
                    username,
                    self.http,
                )

            except asyncio.CancelledError:
                raise

            except Exception as exc:

                logger.exception(
                    "Provider check failed: %s",
                    provider.name,
                )

                result = provider.result(
                    username,
                    Status.ERROR,
                    reason=str(exc),
                )

        # Cache only stable results.
        if result.status in {
            Status.CONFIRMED,
            Status.NOT_FOUND,
            Status.PRIVATE,
        }:

            await result_cache.set(
                cache_key,
                result,
            )

        return result

    async def check(
        self,
        username: str,
        *,
        force: bool = False,
    ) -> list[CheckResult]:

        tasks = [
            self.run_provider(
                provider,
                username,
                force=force,
            )
            for provider in PROVIDERS
        ]

        results = await asyncio.gather(
            *tasks,
            return_exceptions=False,
        )

        return results

    async def get_profile(
        self,
        provider: Provider,
        username: str,
        *,
        force: bool = False,
    ) -> PublicProfile | None:

        key = (
            f"profile:"
            f"{provider.name.lower()}:"
            f"{username.lower()}"
        )

        if not force:

            cached = await profile_cache.get(
                key
            )

            if cached is not None:
                return cached

        async with self.semaphore:

            try:

                profile = await provider.profile(
                    username,
                    self.http,
                )

            except asyncio.CancelledError:
                raise

            except Exception:

                logger.exception(
                    "Profile extraction failed: %s",
                    provider.name,
                )

                profile = None

        if profile is not None:

            await profile_cache.set(
                key,
                profile,
            )

        return profile

    async def get_media(
        self,
        provider: Provider,
        username: str,
        *,
        force: bool = False,
    ) -> list[PublicMedia]:

        key = (
            f"media:"
            f"{provider.name.lower()}:"
            f"{username.lower()}"
        )

        if not force:

            cached = await media_cache.get(
                key
            )

            if cached is not None:
                return cached

        async with self.semaphore:

            try:

                items = await provider.media(
                    username,
                    self.http,
                )

            except asyncio.CancelledError:
                raise

            except Exception:

                logger.exception(
                    "Media extraction failed: %s",
                    provider.name,
                )

                items = []

        await media_cache.set(
            key,
            items,
        )

        return items


engine = CheckEngine(
    http_client,
    settings.max_concurrency,
)


# ============================================================
# USER STATE
# ============================================================

@dataclass
class UserSession:
    username: str
    results: list[CheckResult]
    selected_platform: str | None = None
    media: list[PublicMedia] = field(
        default_factory=list
    )
    media_page: int = 0
    created_at: float = field(
        default_factory=time.monotonic
    )


USER_SESSIONS: dict[int, UserSession] = {}


def save_session(
    chat_id: int,
    session: UserSession,
) -> None:

    USER_SESSIONS[chat_id] = session

    # Simple bounded session store.
    if len(USER_SESSIONS) <= settings.max_sessions:
        return

    oldest_chat = min(
        USER_SESSIONS,
        key=lambda key: (
            USER_SESSIONS[key].created_at
        ),
    )

    USER_SESSIONS.pop(
        oldest_chat,
        None,
    )


# ============================================================
# TELEGRAM UI
# ============================================================

def platform_callback(
    platform: str,
) -> str:

    normalized = (
        platform.lower()
        .replace(" ", "_")
    )

    return f"platform:{normalized}"


def normalized_platform(
    platform: str,
) -> str:

    return (
        platform.lower()
        .replace(" ", "_")
    )


def platform_from_callback(
    value: str,
) -> str:

    return value.replace(
        "_",
        " ",
    )


def build_summary_keyboard(
    results: list[CheckResult],
) -> InlineKeyboardMarkup:

    rows = []

    current = []

    for result in results:

        if result.status != Status.CONFIRMED:
            continue

        current.append(
            InlineKeyboardButton(
                text=(
                    f"🟢 {result.platform}"
                ),
                callback_data=platform_callback(
                    result.platform
                ),
            )
        )

        if len(current) == 2:
            rows.append(current)
            current = []

    if current:
        rows.append(current)

    rows.append(
        [
            InlineKeyboardButton(
                text="📊 التفاصيل",
                callback_data="details",
            ),
            InlineKeyboardButton(
                text="🔄 إعادة الفحص",
                callback_data="recheck",
            ),
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def build_platform_keyboard(
    platform: str,
) -> InlineKeyboardMarkup:

    normalized = normalized_platform(
        platform
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👤 معلومات الحساب",
                    callback_data=(
                        f"profile:{normalized}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎬 المحتوى العام",
                    callback_data=(
                        f"media:{normalized}:0"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ رجوع",
                    callback_data="summary",
                )
            ],
        ]
    )


def format_summary(
    username: str,
    results: list[CheckResult],
) -> str:

    counts = {
        status: sum(
            result.status == status
            for result in results
        )
        for status in Status
    }

    lines = [
        "🔎 <b>Username Checker</b>",
        "",
        (
            "👤 Username: "
            f"<code>{esc(username)}</code>"
        ),
        "",
        (
            "🟢 Confirmed: "
            f"<b>{counts[Status.CONFIRMED]}</b>\n"
            "🔴 Not Found: "
            f"<b>{counts[Status.NOT_FOUND]}</b>\n"
            "🟠 Unknown: "
            f"<b>{counts[Status.UNKNOWN]}</b>\n"
            "🟡 Rate Limited: "
            f"<b>{counts[Status.RATE_LIMITED]}</b>\n"
            "🔒 Private: "
            f"<b>{counts[Status.PRIVATE]}</b>\n"
            "⚪ Unavailable: "
            f"<b>{counts[Status.UNAVAILABLE]}</b>"
        ),
        "",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    for result in results:

        line = (
            f"{status_icon(result.status)} "
            f"<b>{esc(result.platform)}</b>"
            f" — "
            f"{status_name(result.status)}"
        )

        if result.profile_url:
            line += (
                " "
                + make_link(
                    "فتح",
                    result.profile_url,
                )
            )

        lines.append(line)

    lines.extend(
        [
            "",
            (
                "👇 <b>اضغط على المنصة</b> "
                "لعرض التفاصيل المتاحة."
            ),
            "",
            (
                "⚠️ <i>UNKNOWN لا يعني أن الحساب "
                "غير موجود.</i>"
            ),
        ]
    )

    return "\n".join(lines)


def format_details(
    username: str,
    results: list[CheckResult],
) -> str:

    lines = [
        "📊 <b>Detailed Results</b>",
        "",
        f"👤 <code>{esc(username)}</code>",
        "",
    ]

    for result in results:

        lines.append(
            f"{status_icon(result.status)} "
            f"<b>{esc(result.platform)}</b>"
        )

        lines.append(
            "Status: "
            f"<code>{status_name(result.status)}</code>"
        )

        lines.append(
            "Confidence: "
            f"<code>{result.confidence}%</code>"
        )

        if result.confidence:
            lines.append(
                f"<code>"
                f"{confidence_bar(result.confidence)}"
                f"</code>"
            )

        if result.reason:
            lines.append(
                "Reason: "
                f"{esc(result.reason)}"
            )

        if result.evidence:
            lines.append("Evidence:")

            for evidence in result.evidence:
                lines.append(
                    f"• {esc(evidence.message)}"
                )

        if result.data:

            lines.append("Public data:")

            for key, value in result.data.items():

                if value is None:
                    continue

                if (
                    isinstance(value, str)
                    and len(value) > 250
                ):
                    value = value[:247] + "..."

                lines.append(
                    f"• <b>{esc(key)}</b>: "
                    f"{esc(value)}"
                )

        if result.profile_url:
            lines.append(
                make_link(
                    "🔗 Profile",
                    result.profile_url,
                )
            )

        lines.append("")

    return "\n".join(lines)


def format_profile(
    profile: PublicProfile,
) -> str:

    lines = [
        (
            f"👤 <b>{esc(profile.platform)} "
            f"Profile</b>"
        ),
        "",
        (
            "Username: "
            f"<code>@{esc(profile.username)}</code>"
        ),
    ]

    if profile.display_name:
        lines.append(
            "Display name: "
            f"<b>{esc(profile.display_name)}</b>"
        )

    if profile.bio:

        bio = profile.bio

        if len(bio) > 600:
            bio = bio[:597] + "..."

        lines.extend(
            [
                "",
                "📝 <b>Bio</b>",
                esc(bio),
            ]
        )

    lines.extend(
        [
            "",
            "📊 <b>Statistics</b>",
            (
                "Followers: "
                f"<b>{compact_number(profile.followers)}</b>"
            ),
            (
                "Following: "
                f"<b>{compact_number(profile.following)}</b>"
            ),
            (
                "Likes: "
                f"<b>{compact_number(profile.likes)}</b>"
            ),
            (
                "Videos: "
                f"<b>{compact_number(profile.videos)}</b>"
            ),
        ]
    )

    if profile.verified is not None:
        lines.append(
            "Verified: "
            f"<b>{'YES' if profile.verified else 'NO'}</b>"
        )

    if profile.website:
        lines.append(
            make_link(
                "🌐 Website",
                profile.website,
            )
        )

    if profile.profile_url:
        lines.extend(
            [
                "",
                make_link(
                    "🔗 Open profile",
                    profile.profile_url,
                ),
            ]
        )

    lines.extend(
        [
            "",
            (
                "ℹ️ <i>المعلومات المعروضة هي فقط "
                "المعلومات المتاحة للعامة.</i>"
            ),
        ]
    )

    return "\n".join(lines)


def format_media_list(
    platform: str,
    username: str,
    items: list[PublicMedia],
    page: int,
) -> str:

    per_page = 5

    total_pages = max(
        1,
        (
            len(items)
            + per_page
            - 1
        )
        // per_page,
    )

    page = max(
        0,
        min(
            page,
            total_pages - 1,
        ),
    )

    start = page * per_page

    current = items[
        start:start + per_page
    ]

    lines = [
        (
            f"🎬 <b>{esc(platform)} "
            f"Public Content</b>"
        ),
        "",
        f"👤 <code>@{esc(username)}</code>",
        "",
    ]

    if not current:

        lines.extend(
            [
                "⚪ <b>لا يوجد محتوى قابل للاستخراج.</b>",
                "",
                (
                    "قد يكون المحتوى ديناميكيًا، "
                    "خاصًا، أو غير مكشوف كرابط مباشر."
                ),
            ]
        )

    else:

        for index, item in enumerate(
            current,
            start=start + 1,
        ):

            icon = (
                "🎬"
                if item.media_type == MediaType.VIDEO
                else "🖼️"
            )

            lines.append(
                f"{index}. {icon} "
                f"<b>{item.media_type.value}</b>"
            )

            if item.title:

                title = item.title

                if len(title) > 100:
                    title = title[:97] + "..."

                lines.append(
                    f"   {esc(title)}"
                )

            lines.append(
                "   "
                + (
                    "📥 قابل للجلب"
                    if item.downloadable
                    else "⚪ غير متاح مباشرة"
                )
            )

            lines.append("")

    lines.append(
        f"📄 Page {page + 1}/{total_pages}"
    )

    return "\n".join(lines)


def build_media_keyboard(
    platform: str,
    items: list[PublicMedia],
    page: int,
) -> InlineKeyboardMarkup:

    normalized = normalized_platform(
        platform
    )

    per_page = 5

    total_pages = max(
        1,
        (
            len(items)
            + per_page
            - 1
        )
        // per_page,
    )

    page = max(
        0,
        min(
            page,
            total_pages - 1,
        ),
    )

    start = page * per_page

    current = items[
        start:start + per_page
    ]

    rows = []

    for index, item in enumerate(
        current,
        start=start,
    ):

        icon = (
            "🎬"
            if item.media_type == MediaType.VIDEO
            else "🖼️"
        )

        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{icon} المحتوى "
                        f"#{index + 1}"
                    ),
                    callback_data=(
                        f"getmedia:"
                        f"{normalized}:"
                        f"{index}"
                    ),
                )
            ]
        )

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=(
                    f"media:"
                    f"{normalized}:"
                    f"{page - 1}"
                ),
            )
        )

    navigation.append(
        InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data="noop",
        )
    )

    if page < total_pages - 1:
        navigation.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=(
                    f"media:"
                    f"{normalized}:"
                    f"{page + 1}"
                ),
            )
        )

    rows.append(navigation)

    rows.append(
        [
            InlineKeyboardButton(
                text="👤 الحساب",
                callback_data=(
                    f"profile:{normalized}"
                ),
            ),
            InlineKeyboardButton(
                text="⬅️ رجوع",
                callback_data=(
                    f"platform:{normalized}"
                ),
            ),
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# ============================================================
# START
# ============================================================

@dp.message(Command("start"))
async def start_handler(
    message: Message,
):

    await message.answer(
        "👋 <b>Username Checker</b>\n\n"
        "أرسل اسم المستخدم فقط.\n\n"
        "مثال:\n"
        "<code>example</code>\n\n"
        "بعد الفحص ستظهر لك المنصات التي تم "
        "العثور عليها مع مستوى الثقة."
    )


# ============================================================
# HELP
# ============================================================

@dp.message(Command("help"))
async def help_handler(
    message: Message,
):

    await message.answer(
        "ℹ️ <b>طريقة الاستخدام</b>\n\n"
        "1️⃣ أرسل Username بدون @.\n"
        "2️⃣ انتظر النتائج.\n"
        "3️⃣ اختر المنصة.\n"
        "4️⃣ يمكنك عرض المعلومات العامة والمحتوى المكشوف.\n\n"
        "🟢 CONFIRMED = دليل قوي\n"
        "🔴 NOT FOUND = دليل على عدم العثور\n"
        "🟠 UNKNOWN = لا يوجد دليل كافٍ\n"
        "🟡 RATE LIMITED = المصدر حدّ الطلبات\n"
        "🔒 PRIVATE = محتوى/حساب خاص\n"
        "⚪ UNAVAILABLE = غير متاح للطلب العام\n\n"
        "⚠️ الأداة لا تتجاوز تسجيل الدخول أو CAPTCHA "
        "أو أنظمة الحماية."
    )


# ============================================================
# USERNAME CHECK
# ============================================================

@dp.message(F.text)
async def username_handler(
    message: Message,
):

    raw = message.text or ""

    username = normalize_username(
        raw
    )

    if not username:
        await message.answer(
            "❌ أرسل Username صالح."
        )
        return

    if len(username) > settings.max_username_length:
        await message.answer(
            "❌ Username طويل جدًا."
        )
        return

    if not valid_username(username):
        await message.answer(
            "❌ Username غير صالح.\n\n"
            "المسموح:\n"
            "A-Z / a-z / 0-9 / . / _ / -"
        )
        return

    status_message = await message.answer(
        "🔎 <b>جاري الفحص...</b>\n\n"
        f"Username: <code>{esc(username)}</code>\n\n"
        "⏳ يتم فحص المصادر العامة..."
    )

    started = time.monotonic()

    try:

        results = await engine.check(
            username
        )

    except Exception as exc:

        logger.exception(
            "Engine error"
        )

        await status_message.edit_text(
            "❌ حدث خطأ أثناء الفحص."
        )

        return

    elapsed = (
        time.monotonic()
        - started
    )

    save_session(
        message.chat.id,
        UserSession(
            username=username,
            results=results,
        ),
    )

    text = format_summary(
        username,
        results,
    )

    text += (
        f"\n\n⏱️ <code>{elapsed:.2f}s</code>"
    )

    await status_message.edit_text(
        text,
        reply_markup=build_summary_keyboard(
            results
        ),
    )


# ============================================================
# PLATFORM
# ============================================================

@dp.callback_query(
    F.data.startswith("platform:")
)
async def platform_callback_handler(
    callback: CallbackQuery,
):

    if not callback.message:
        await callback.answer()
        return

    raw_platform = callback.data.split(
        ":",
        1,
    )[1]

    platform = platform_from_callback(
        raw_platform
    )

    session = USER_SESSIONS.get(
        callback.message.chat.id
    )

    if not session:
        await callback.answer(
            "لا توجد جلسة.",
            show_alert=True,
        )
        return

    provider = PROVIDER_MAP.get(
        platform.lower()
    )

    if not provider:
        await callback.answer(
            "المنصة غير مدعومة.",
            show_alert=True,
        )
        return

    session.selected_platform = provider.name

    result = next(
        (
            item
            for item in session.results
            if item.platform.lower()
            == provider.name.lower()
        ),
        None,
    )

    if not result:
        await callback.answer(
            "لا توجد نتيجة لهذه المنصة.",
            show_alert=True,
        )
        return

    text = (
        f"{status_icon(result.status)} "
        f"<b>{esc(provider.name)}</b>\n\n"
        f"Username: "
        f"<code>@{esc(session.username)}</code>\n\n"
        f"Status: "
        f"<b>{status_name(result.status)}</b>\n"
        f"Confidence: "
        f"<b>{result.confidence}%</b>"
    )

    if result.profile_url:
        text += (
            "\n\n"
            + make_link(
                "🔗 فتح الحساب",
                result.profile_url,
            )
        )

    if result.reason:
        text += (
            "\n\n"
            f"ℹ️ {esc(result.reason)}"
        )

    if result.status != Status.CONFIRMED:
        text += (
            "\n\n"
            "⚠️ لا يتم اعتبار المحتوى متاحًا "
            "إلا إذا كان مكشوفًا للعامة."
        )

    await callback.message.edit_text(
        text,
        reply_markup=build_platform_keyboard(
            provider.name
        ),
    )

    await callback.answer()


# ============================================================
# PROFILE
# ============================================================

@dp.callback_query(
    F.data.startswith("profile:")
)
async def profile_callback_handler(
    callback: CallbackQuery,
):

    if not callback.message:
        await callback.answer()
        return

    raw_platform = callback.data.split(
        ":",
        1,
    )[1]

    platform = platform_from_callback(
        raw_platform
    )

    session = USER_SESSIONS.get(
        callback.message.chat.id
    )

    if not session:
        await callback.answer(
            "لا توجد جلسة.",
            show_alert=True,
        )
        return

    provider = PROVIDER_MAP.get(
        platform.lower()
    )

    if not provider:
        await callback.answer(
            "المنصة غير مدعومة.",
            show_alert=True,
        )
        return

    await callback.answer(
        "⏳ جاري استخراج المعلومات العامة..."
    )

    profile = await engine.get_profile(
        provider,
        session.username,
    )

    if not profile:

        await callback.message.edit_text(
            (
                f"⚪ <b>{esc(provider.name)}</b>\n\n"
                "لم أتمكن من استخراج بيانات عامة "
                "كافية من الصفحة الحالية.\n\n"
                "هذا لا يعني أن الحساب غير موجود."
            ),
            reply_markup=build_platform_keyboard(
                provider.name
            ),
        )

        return

    normalized = normalized_platform(
        provider.name
    )

    await callback.message.edit_text(
        format_profile(profile),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎬 المحتوى العام",
                        callback_data=(
                            f"media:{normalized}:0"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ رجوع",
                        callback_data=(
                            f"platform:{normalized}"
                        ),
                    )
                ],
            ]
        ),
    )


# ============================================================
# MEDIA LIST
# ============================================================

@dp.callback_query(
    F.data.startswith("media:")
)
async def media_callback_handler(
    callback: CallbackQuery,
):

    if not callback.message:
        await callback.answer()
        return

    parts = callback.data.split(":")

    if len(parts) != 3:
        await callback.answer(
            "طلب غير صالح.",
            show_alert=True,
        )
        return

    raw_platform = parts[1]

    try:
        page = int(parts[2])
    except ValueError:
        page = 0

    page = max(0, page)

    platform = platform_from_callback(
        raw_platform
    )

    session = USER_SESSIONS.get(
        callback.message.chat.id
    )

    if not session:
        await callback.answer(
            "لا توجد جلسة.",
            show_alert=True,
        )
        return

    provider = PROVIDER_MAP.get(
        platform.lower()
    )

    if not provider:
        await callback.answer(
            "المنصة غير مدعومة.",
            show_alert=True,
        )
        return

    await callback.answer(
        "⏳ جاري البحث عن المحتوى العام..."
    )

    items = await engine.get_media(
        provider,
        session.username,
    )

    session.selected_platform = provider.name
    session.media = items
    session.media_page = page

    await callback.message.edit_text(
        format_media_list(
            provider.name,
            session.username,
            items,
            page,
        ),
        reply_markup=build_media_keyboard(
            provider.name,
            items,
            page,
        ),
    )


# ============================================================
# GET MEDIA
# ============================================================

@dp.callback_query(
    F.data.startswith("getmedia:")
)
async def get_media_callback_handler(
    callback: CallbackQuery,
):

    if not callback.message:
        await callback.answer()
        return

    parts = callback.data.split(":")

    if len(parts) != 3:
        await callback.answer(
            "طلب غير صالح.",
            show_alert=True,
        )
        return

    raw_platform = parts[1]

    try:
        index = int(parts[2])
    except ValueError:
        await callback.answer(
            "رقم المحتوى غير صالح.",
            show_alert=True,
        )
        return

    platform = platform_from_callback(
        raw_platform
    )

    session = USER_SESSIONS.get(
        callback.message.chat.id
    )

    if not session:
        await callback.answer(
            "لا توجد جلسة.",
            show_alert=True,
        )
        return

    provider = PROVIDER_MAP.get(
        platform.lower()
    )

    if not provider:
        await callback.answer(
            "المنصة غير مدعومة.",
            show_alert=True,
        )
        return

    # --------------------------------------------------------
    # Load media if necessary.
    # --------------------------------------------------------

    if (
        not session.media
        or session.selected_platform != provider.name
    ):

        await callback.answer(
            "⏳ جاري جلب المحتوى..."
        )

        session.media = await engine.get_media(
            provider,
            session.username,
        )

        session.selected_platform = provider.name

    if index < 0 or index >= len(session.media):
        await callback.answer(
            "المحتوى غير موجود.",
            show_alert=True,
        )
        return

    item = session.media[index]

    if not item.downloadable:
        await callback.answer(
            "هذا المحتوى غير متاح كملف مباشر.",
            show_alert=True,
        )
        return

    if not is_safe_media_url(item.url):
        await callback.answer(
            "رابط المحتوى غير صالح للتنزيل.",
            show_alert=True,
        )
        return

    await callback.answer(
        "⏳ جاري جلب الملف العام..."
    )

    temp_dir = "/tmp/username_checker"

    os.makedirs(
        temp_dir,
        exist_ok=True,
    )

    extension = (
        ".mp4"
        if item.media_type == MediaType.VIDEO
        else ".jpg"
    )

    filename = (
        f"{callback.message.chat.id}_"
        f"{int(time.time() * 1000)}"
        f"{extension}"
    )

    path = os.path.join(
        temp_dir,
        filename,
    )

    max_bytes = (
        settings.max_media_size_mb
        * 1024
        * 1024
    )

    try:

        ok, reason = (
            await http_client.stream_to_file(
                item.url,
                path,
                max_bytes,
            )
        )

        if not ok:

            if reason == "RATE_LIMITED":
                message = (
                    "🟡 المصدر حدّ طلبات الوصول."
                )

            elif reason == "FILE_TOO_LARGE":
                message = (
                    "⚠️ الملف أكبر من الحد المسموح "
                    f"({settings.max_media_size_mb} MB)."
                )

            elif reason in {
                "HTTP 401",
                "HTTP 403",
            }:
                message = (
                    "⚪ الملف غير متاح للطلب العام."
                )

            elif reason in {
                "NOT_DIRECT_MEDIA",
                "STREAM_MANIFEST",
                "UNSUPPORTED_CONTENT_TYPE",
            }:
                message = (
                    "⚪ الرابط ليس ملف صورة/فيديو مباشرًا."
                )

            elif reason == "UNSAFE_URL":
                message = (
                    "⚪ تم رفض رابط غير آمن."
                )

            else:
                message = (
                    "⚪ لم أتمكن من جلب الملف العام.\n"
                    f"Reason: <code>{esc(reason)}</code>"
                )

            await callback.message.answer(
                message
            )

            return

        caption = (
            f"🎬 <b>{esc(provider.name)}</b>\n"
            f"👤 <code>@{esc(session.username)}</code>\n"
            f"📦 {item.media_type.value}\n\n"
            "تم جلب المحتوى المتاح للعامة."
        )

        if item.source_url:
            caption += (
                "\n\n"
                + make_link(
                    "🔗 المصدر",
                    item.source_url,
                )
            )

        if item.media_type == MediaType.VIDEO:

            await callback.message.answer_video(
                video=FSInputFile(path),
                caption=caption,
                supports_streaming=True,
            )

        elif item.media_type == MediaType.IMAGE:

            await callback.message.answer_photo(
                photo=FSInputFile(path),
                caption=caption,
            )

        else:

            await callback.message.answer_document(
                document=FSInputFile(path),
                caption=caption,
            )

    except Exception as exc:

        logger.exception(
            "Telegram media send failed."
        )

        await callback.message.answer(
            "❌ فشل إرسال الملف إلى Telegram."
        )

    finally:

        try:

            if os.path.exists(path):
                os.remove(path)

        except Exception:

            logger.warning(
                "Failed to remove temporary file: %s",
                path,
            )


# ============================================================
# DETAILS
# ============================================================

@dp.callback_query(
    F.data == "details"
)
async def details_callback(
    callback: CallbackQuery,
):

    if not callback.message:
        await callback.answer()
        return

    session = USER_SESSIONS.get(
        callback.message.chat.id
    )

    if not session:
        await callback.answer(
            "لا توجد نتيجة محفوظة.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        format_details(
            session.username,
            session.results,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ رجوع",
                        callback_data="summary",
                    )
                ]
            ]
        ),
    )

    await callback.answer()


# ============================================================
# SUMMARY
# ============================================================

@dp.callback_query(
    F.data == "summary"
)
async def summary_callback(
    callback: CallbackQuery,
):

    if not callback.message:
        await callback.answer()
        return

    session = USER_SESSIONS.get(
        callback.message.chat.id
    )

    if not session:
        await callback.answer(
            "لا توجد نتيجة محفوظة.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        format_summary(
            session.username,
            session.results,
        ),
        reply_markup=build_summary_keyboard(
            session.results
        ),
    )

    await callback.answer()


# ============================================================
# RECHECK
# ============================================================

@dp.callback_query(
    F.data == "recheck"
)
async def recheck_callback(
    callback: CallbackQuery,
):

    if not callback.message:
        await callback.answer()
        return

    session = USER_SESSIONS.get(
        callback.message.chat.id
    )

    if not session:
        await callback.answer(
            "لا توجد نتيجة لإعادة الفحص.",
            show_alert=True,
        )
        return

    username = session.username

    await callback.answer(
        "🔄 جاري إعادة الفحص..."
    )

    started = time.monotonic()

    try:

        results = await engine.check(
            username,
            force=True,
        )

    except Exception:

        logger.exception(
            "Recheck failed."
        )

        await callback.message.answer(
            "❌ فشلت إعادة الفحص."
        )

        return

    elapsed = (
        time.monotonic()
        - started
    )

    save_session(
        callback.message.chat.id,
        UserSession(
            username=username,
            results=results,
        ),
    )

    text = format_summary(
        username,
        results,
    )

    text += (
        f"\n\n⏱️ <code>{elapsed:.2f}s</code>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=build_summary_keyboard(
            results
        ),
    )


# ============================================================
# NOOP
# ============================================================

@dp.callback_query(
    F.data == "noop"
)
async def noop_callback(
    callback: CallbackQuery,
):

    await callback.answer()


# ============================================================
# WEB SERVER
# ============================================================

bot: Bot | None = None


async def health_handler(
    request: web.Request,
):

    return web.json_response(
        {
            "status": "ok",
            "service": "username-checker",
            "version": "3.0",
            "time": datetime.now(
                timezone.utc
            ).isoformat(),
        }
    )


async def webhook_handler(
    request: web.Request,
):

    global bot

    if bot is None:
        return web.json_response(
            {
                "ok": False,
                "error": "Bot is not initialized",
            },
            status=503,
        )

    try:

        data = await request.json()

    except Exception:

        return web.json_response(
            {
                "ok": False,
                "error": "Invalid JSON",
            },
            status=400,
        )

    try:

        update = Update.model_validate(
            data
        )

        await dp.feed_update(
            bot,
            update,
        )

        return web.json_response(
            {"ok": True}
        )

    except Exception:

        logger.exception(
            "Webhook update failed."
        )

        # لا نرسل تفاصيل exception إلى المستخدم.
        return web.json_response(
            {
                "ok": False,
                "error": "Update processing failed",
            },
            status=500,
        )


async def create_web_app():
    app = web.Application()

    app.router.add_get(
        "/",
        health_handler,
    )

    app.router.add_get(
        "/health",
        health_handler,
    )

    app.router.add_post(
        "/telegram-webhook",
        webhook_handler,
    )

    return app


# ============================================================
# WEBHOOK SETUP
# ============================================================

async def setup_webhook(
    telegram_bot: Bot,
):

    if not settings.render_url:
        raise RuntimeError(
            "RENDER_URL is missing."
        )

    base_url = (
        settings.render_url
        .strip()
        .rstrip("/")
    )

    if not base_url.startswith("https://"):
        raise RuntimeError(
            "RENDER_URL must use HTTPS."
        )

    webhook_url = (
        f"{base_url}"
        "/telegram-webhook"
    )

    logger.info(
        "Removing old webhook..."
    )

    await telegram_bot.delete_webhook(
        drop_pending_updates=True
    )

    logger.info(
        "Setting webhook: %s",
        webhook_url,
    )

    await telegram_bot.set_webhook(
        url=webhook_url,
        drop_pending_updates=True,
        allowed_updates=[
            "message",
            "callback_query",
        ],
    )

    info = await telegram_bot.get_webhook_info()

    logger.info(
        "Webhook active: %s",
        info.url,
    )

    if info.last_error_message:
        logger.warning(
            "Telegram webhook last error: %s",
            info.last_error_message,
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    global bot

    if not settings.bot_token:
        raise RuntimeError(
            "BOT_TOKEN is missing. "
            "Add BOT_TOKEN to Render Environment Variables."
        )

    if not settings.render_url:
        raise RuntimeError(
            "RENDER_URL is missing."
        )

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        ),
    )

    runner: web.AppRunner | None = None

    try:

        logger.info(
            "Starting bot..."
        )

        me = await bot.get_me()

        logger.info(
            "Connected to Telegram: "
            "@%s | id=%s",
            me.username,
            me.id,
        )

        await setup_webhook(
            bot
        )

        app = await create_web_app()

        runner = web.AppRunner(
            app
        )

        await runner.setup()

        site = web.TCPSite(
            runner,
            "0.0.0.0",
            settings.port,
        )

        await site.start()

        logger.info(
            "HTTP server started on 0.0.0.0:%s",
            settings.port,
        )

        logger.info(
            "Bot is ONLINE."
        )

        while True:
            await asyncio.sleep(3600)

    except asyncio.CancelledError:

        logger.info(
            "Shutdown requested."
        )

        raise

    except Exception:

        logger.exception(
            "Fatal error."
        )

        raise

    finally:

        logger.info(
            "Shutting down..."
        )

        if runner:

            try:
                await runner.cleanup()
            except Exception:
                logger.exception(
                    "HTTP cleanup failed."
                )

        if bot:

            try:
                await bot.delete_webhook()
            except Exception:
                logger.exception(
                    "Failed to remove webhook."
                )

        try:
            await http_client.close()
        except Exception:
            logger.exception(
                "Failed to close HTTP client."
            )

        if bot:

            try:
                await bot.session.close()
            except Exception:
                logger.exception(
                    "Failed to close Telegram session."
                )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        logger.info(
            "Stopped."
        )
