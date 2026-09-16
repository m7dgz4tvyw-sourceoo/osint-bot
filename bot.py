from __future__ import annotations

import asyncio
import html
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
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


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

    # ضع التوكن هنا إذا كنت مصممًا على وضعه داخل الكود.
    # لا ترفع الملف إلى GitHub وهو يحتوي على توكن حقيقي.
    bot_token: str = "8974546244:AAGSIwbh9FmENOiKYP2tS33_Z-ixjPl0cl4"

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


settings = Settings()


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


# ============================================================
# MEDIA TYPES
# ============================================================

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

    evidence: list[Evidence] = field(
        default_factory=list
    )

    reason: str | None = None

    data: dict[str, Any] = field(
        default_factory=dict
    )

    checked_at: datetime = field(
        default_factory=lambda: datetime.now(
            timezone.utc
        )
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


def normalize_username(
    username: str,
) -> str:

    username = username.strip()

    if username.startswith("@"):
        username = username[1:]

    return username.strip()


def valid_username(
    username: str,
) -> bool:

    return bool(
        USERNAME_RE.fullmatch(username)
    )


def esc(
    value: Any,
) -> str:

    return html.escape(
        str(value)
    )


def status_icon(
    status: Status,
) -> str:

    return {
        Status.CONFIRMED: "🟢",
        Status.NOT_FOUND: "🔴",
        Status.UNKNOWN: "🟠",
        Status.RATE_LIMITED: "🟡",
        Status.PRIVATE: "🔒",
        Status.UNAVAILABLE: "⚪",
        Status.ERROR: "⚫",
    }.get(
        status,
        "⚪",
    )


def status_name(
    status: Status,
) -> str:

    return {
        Status.CONFIRMED: "CONFIRMED",
        Status.NOT_FOUND: "NOT FOUND",
        Status.UNKNOWN: "UNKNOWN",
        Status.RATE_LIMITED: "RATE LIMITED",
        Status.PRIVATE: "PRIVATE",
        Status.UNAVAILABLE: "UNAVAILABLE",
        Status.ERROR: "ERROR",
    }.get(
        status,
        "UNKNOWN",
    )


def confidence_bar(
    value: int,
) -> str:

    value = max(
        0,
        min(100, value),
    )

    filled = round(
        value / 10
    )

    return (
        "█" * filled
        + "░" * (10 - filled)
    )


def safe_profile_url(
    url: str | None,
) -> str | None:

    if not url:
        return None

    parsed = urlparse(url)

    if parsed.scheme not in {
        "https",
        "http",
    }:
        return None

    return url


def make_link(
    text: str,
    url: str | None,
) -> str:

    url = safe_profile_url(
        url
    )

    if not url:
        return esc(text)

    return (
        f'<a href="{esc(url)}">'
        f'{esc(text)}'
        f'</a>'
    )


def compact_number(
    value: Any,
) -> str:

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


# ============================================================
# CACHE
# ============================================================

@dataclass
class CacheEntry:
    created_at: float
    value: Any


class TTLCache:

    def __init__(
        self,
        ttl: int,
    ):
        self.ttl = ttl

        self._cache: dict[
            str,
            CacheEntry,
        ] = {}

        self._lock = asyncio.Lock()

    async def get(
        self,
        key: str,
    ) -> Any | None:

        async with self._lock:

            entry = self._cache.get(
                key
            )

            if entry is None:
                return None

            age = (
                time.monotonic()
                - entry.created_at
            )

            if age > self.ttl:

                self._cache.pop(
                    key,
                    None,
                )

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

    async def delete(
        self,
        key: str,
    ) -> None:

        async with self._lock:

            self._cache.pop(
                key,
                None,
            )


result_cache = TTLCache(
    settings.cache_ttl
)

media_cache = TTLCache(
    settings.media_cache_ttl
)


# ============================================================
# HTTP
# ============================================================

class RetryableHTTPError(
    Exception
):
    pass


class HTTPClient:

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
                    "UsernameChecker/2.0)"
                ),
                "Accept": "*/*",
            },
        )

    async def close(self):

        await self.client.aclose()

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response | None:

        async def request():

            try:

                response = (
                    await self.client.get(
                        url,
                        headers=headers,
                        params=params,
                    )
                )

                if response.status_code in {
                    408,
                    425,
                    429,
                    500,
                    502,
                    503,
                    504,
                }:

                    raise RetryableHTTPError(
                        f"HTTP {response.status_code}"
                    )

                return response

            except (
                httpx.TimeoutException,
                httpx.NetworkError,
            ) as exc:

                raise RetryableHTTPError(
                    str(exc)
                ) from exc

        try:

            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type(
                    RetryableHTTPError
                ),
                wait=wait_exponential(
                    multiplier=1,
                    min=1,
                    max=8,
                ),
                stop=stop_after_attempt(3),
                reraise=True,
            ):

                with attempt:

                    return await request()

        except Exception as exc:

            logger.warning(
                "HTTP request failed: %s | %s",
                url,
                exc,
            )

            return None

    async def stream_to_file(
        self,
        url: str,
        path: str,
        max_bytes: int,
    ) -> tuple[bool, str]:

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
                        "image/*,"
                        "*/*"
                    ),
                },
            ) as response:

                if response.status_code in {
                    401,
                    403,
                    404,
                }:

                    return (
                        False,
                        f"HTTP {response.status_code}",
                    )

                if response.status_code == 429:

                    return (
                        False,
                        "RATE_LIMITED",
                    )

                if response.status_code >= 400:

                    return (
                        False,
                        f"HTTP {response.status_code}",
                    )

                content_length = response.headers.get(
                    "content-length"
                )

                if content_length:

                    try:

                        if int(
                            content_length
                        ) > max_bytes:

                            return (
                                False,
                                "FILE_TOO_LARGE",
                            )

                    except ValueError:
                        pass

                total = 0

                with open(
                    path,
                    "wb",
                ) as file:

                    async for chunk in response.aiter_bytes(
                        1024 * 64
                    ):

                        total += len(chunk)

                        if total > max_bytes:

                            return (
                                False,
                                "FILE_TOO_LARGE",
                            )

                        file.write(chunk)

                return (
                    True,
                    "OK",
                )

        except (
            httpx.TimeoutException,
        ):

            return (
                False,
                "TIMEOUT",
            )

        except (
            httpx.NetworkError,
        ):

            return (
                False,
                "NETWORK_ERROR",
            )

        except Exception as exc:

            logger.exception(
                "Media download failed: %s",
                url,
            )

            return (
                False,
                str(exc),
            )


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
            confidence=confidence,
            profile_url=profile_url,
            evidence=evidence or [],
            reason=reason,
            data=data or {},
        )


# ============================================================
# HTML PARSING
# ============================================================

def meta_content(
    soup,
    *,
    name: str | None = None,
    prop: str | None = None,
) -> str | None:

    tag = None

    if name:

        tag = soup.find(
            "meta",
            attrs={
                "name": name,
            },
        )

    if tag is None and prop:

        tag = soup.find(
            "meta",
            attrs={
                "property": prop,
            },
        )

    if tag is None:
        return None

    value = tag.get(
        "content"
    )

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

            raw = (
                match.group(1)
                .replace(",", "")
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

            value = (
                match.group(1)
                .replace(
                    '\\"',
                    '"',
                )
            )

            if value.strip():

                return value.strip()

    return None


def extract_media_from_html(
    platform: str,
    username: str,
    page_url: str,
    text: str,
    soup,
) -> list[PublicMedia]:

    items: list[PublicMedia] = []

    video_urls: list[str] = []

    # --------------------------------------------------------
    # OpenGraph video
    # --------------------------------------------------------

    for prop in (
        "og:video",
        "og:video:url",
        "og:video:secure_url",
    ):

        value = meta_content(
            soup,
            prop=prop,
        )

        if value:
            video_urls.append(
                urljoin(
                    page_url,
                    value,
                )
            )

    # --------------------------------------------------------
    # HTML <video>
    # --------------------------------------------------------

    for video in soup.find_all(
        "video"
    ):

        src = video.get(
            "src"
        )

        if src:

            video_urls.append(
                urljoin(
                    page_url,
                    src,
                )
            )

        for source in video.find_all(
            "source"
        ):

            src = source.get(
                "src"
            )

            if src:

                video_urls.append(
                    urljoin(
                        page_url,
                        src,
                    )
                )

    # --------------------------------------------------------
    # Deduplicate
    # --------------------------------------------------------

    seen: set[str] = set()

    for video_url in video_urls:

        if video_url in seen:
            continue

        seen.add(
            video_url
        )

        items.append(
            PublicMedia(
                platform=platform,
                username=username,
                media_type=MediaType.VIDEO,
                url=video_url,
                thumbnail_url=meta_content(
                    soup,
                    prop="og:image",
                ),
                title=meta_content(
                    soup,
                    prop="og:title",
                ),
                source_url=page_url,
                downloadable=True,
            )
        )

    # --------------------------------------------------------
    # OpenGraph image
    # --------------------------------------------------------

    image_url = meta_content(
        soup,
        prop="og:image",
    )

    if image_url:

        items.append(
            PublicMedia(
                platform=platform,
                username=username,
                media_type=MediaType.IMAGE,
                url=urljoin(
                    page_url,
                    image_url,
                ),
                thumbnail_url=urljoin(
                    page_url,
                    image_url,
                ),
                title=meta_content(
                    soup,
                    prop="og:title",
                ),
                source_url=page_url,
                downloadable=True,
            )
        )

    # --------------------------------------------------------
    # HTML images
    # --------------------------------------------------------

    for image in soup.find_all(
        "img"
    )[:20]:

        src = (
            image.get("src")
            or image.get("data-src")
        )

        if not src:
            continue

        full_url = urljoin(
            page_url,
            src,
        )

        if full_url in seen:
            continue

        seen.add(
            full_url
        )

        items.append(
            PublicMedia(
                platform=platform,
                username=username,
                media_type=MediaType.IMAGE,
                url=full_url,
                title=(
                    image.get("alt")
                    or None
                ),
                source_url=page_url,
                downloadable=True,
            )
        )

    return items[
        :settings.max_media_items
    ]


# ============================================================
# GITHUB
# ============================================================

class GitHubProvider(
    Provider
):

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
                reason=(
                    "GitHub API request failed "
                    "or timed out."
                ),
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

        if response.status_code in {
            403,
            429,
        }:

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
                    "GitHub returned HTTP "
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
            data.get(
                "login",
                "",
            )
        )

        if login.lower() != username.lower():

            return self.result(
                username,
                Status.UNKNOWN,
                reason=(
                    "GitHub username mismatch."
                ),
            )

        return self.result(
            username,
            Status.CONFIRMED,
            confidence=100,
            profile_url=data.get(
                "html_url"
            ),
            evidence=[
                Evidence(
                    "Official GitHub API confirmed account.",
                    70,
                ),
                Evidence(
                    "Returned login matches username.",
                    30,
                ),
            ],
            data={
                "name": data.get(
                    "name"
                ),
                "bio": data.get(
                    "bio"
                ),
                "company": data.get(
                    "company"
                ),
                "location": data.get(
                    "location"
                ),
                "public_repos": data.get(
                    "public_repos"
                ),
                "followers": data.get(
                    "followers"
                ),
                "following": data.get(
                    "following"
                ),
                "public_gists": data.get(
                    "public_gists"
                ),
                "created_at": data.get(
                    "created_at"
                ),
                "updated_at": data.get(
                    "updated_at"
                ),
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
            display_name=data.get(
                "name"
            ),
            bio=data.get(
                "bio"
            ),
            followers=data.get(
                "followers"
            ),
            following=data.get(
                "following"
            ),
            website=None,
            profile_url=result.profile_url,
        )


# ============================================================
# REDDIT
# ============================================================

class RedditProvider(
    Provider
):

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
                    "UsernameChecker/2.0",
            },
        )

        if response is None:

            return self.result(
                username,
                Status.UNKNOWN,
                reason=(
                    "Reddit request failed."
                ),
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

        if response.status_code in {
            403,
            429,
        }:

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
                    f"Reddit returned "
                    f"HTTP {response.status_code}."
                ),
            )

        try:

            payload = response.json()

            data = (
                payload.get("data")
                or {}
            )

        except Exception:

            return self.result(
                username,
                Status.UNKNOWN,
                reason="Invalid Reddit JSON.",
            )

        account_name = str(
            data.get(
                "name",
                "",
            )
        )

        if (
            account_name.lower()
            != username.lower()
        ):

            return self.result(
                username,
                Status.UNKNOWN,
                reason="Reddit username mismatch.",
            )

        return self.result(
            username,
            Status.CONFIRMED,
            confidence=100,
            profile_url=(
                f"https://www.reddit.com/"
                f"user/{username}/"
            ),
            evidence=[
                Evidence(
                    "Reddit returned a matching user.",
                    100,
                )
            ],
            data={
                "name": account_name,
                "created_utc": data.get(
                    "created_utc"
                ),
                "link_karma": data.get(
                    "link_karma"
                ),
                "comment_karma": data.get(
                    "comment_karma"
                ),
                "is_gold": data.get(
                    "is_gold"
                ),
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
            display_name=result.data.get(
                "name"
            ),
            followers=None,
            following=None,
            profile_url=result.profile_url,
        )


# ============================================================
# GENERIC HTML PROVIDER
# ============================================================

class HTMLProvider(
    Provider
):

    PROFILE_TEMPLATE = ""

    NOT_FOUND_MARKERS: tuple[str, ...] = ()

    PRIVATE_MARKERS: tuple[str, ...] = ()

    async def fetch_page(
        self,
        username: str,
        http: HTTPClient,
    ) -> tuple[
        str,
        httpx.Response | None,
    ]:

        url = self.PROFILE_TEMPLATE.format(
            username=username
        )

        response = await http.get(
            url
        )

        if response is None:

            return (
                url,
                None,
            )

        return (
            url,
            response,
        )

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
                reason=(
                    "Request failed, timed out, "
                    "or was unavailable."
                ),
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

        if response.status_code in {
            401,
            403,
            429,
        }:

            if response.status_code == 429:

                status = Status.RATE_LIMITED

            else:

                status = Status.UNKNOWN

            return self.result(
                username,
                status,
                profile_url=url,
                reason=(
                    f"Platform returned "
                    f"HTTP {response.status_code}."
                ),
            )

        if response.status_code >= 500:

            return self.result(
                username,
                Status.UNKNOWN,
                profile_url=url,
                reason=(
                    f"Platform server returned "
                    f"HTTP {response.status_code}."
                ),
            )

        if response.status_code != 200:

            return self.result(
                username,
                Status.UNKNOWN,
                profile_url=url,
                reason=(
                    f"Platform returned "
                    f"HTTP {response.status_code}."
                ),
            )

        text = response.text

        lower_text = text.lower()

        for marker in self.NOT_FOUND_MARKERS:

            if marker.lower() in lower_text:

                return self.result(
                    username,
                    Status.NOT_FOUND,
                    confidence=80,
                    profile_url=url,
                    evidence=[
                        Evidence(
                            (
                                "Page contained "
                                f"not-found marker: {marker}"
                            ),
                            80,
                        )
                    ],
                )

        for marker in self.PRIVATE_MARKERS:

            if marker.lower() in lower_text:

                return self.result(
                    username,
                    Status.PRIVATE,
                    confidence=80,
                    profile_url=url,
                    evidence=[
                        Evidence(
                            (
                                "Page indicated "
                                f"restricted/private content: {marker}"
                            ),
                            80,
                        )
                    ],
                )

        try:

            from bs4 import BeautifulSoup

            soup = BeautifulSoup(
                text,
                "html.parser",
            )

        except Exception:

            return self.result(
                username,
                Status.UNKNOWN,
                profile_url=url,
                reason="HTML parsing failed.",
            )

        og_title = meta_content(
            soup,
            prop="og:title",
        )

        og_description = meta_content(
            soup,
            prop="og:description",
        )

        og_image = meta_content(
            soup,
            prop="og:image",
        )

        title = (
            soup.title.string.strip()
            if soup.title
            and soup.title.string
            else None
        )

        # A page with strong profile metadata
        # is better evidence than HTTP 200 alone.
        strong_evidence = any(
            [
                og_title,
                og_description,
                og_image,
                title,
            ]
        )

        if strong_evidence:

            return self.result(
                username,
                Status.CONFIRMED,
                confidence=75,
                profile_url=url,
                evidence=[
                    Evidence(
                        "Public profile page returned usable metadata.",
                        50,
                    ),
                    Evidence(
                        "Profile page contains OpenGraph/title metadata.",
                        25,
                    ),
                ],
                data={
                    "title": title,
                    "og_title": og_title,
                    "og_description": og_description,
                    "og_image": og_image,
                },
            )

        return self.result(
            username,
            Status.UNKNOWN,
            profile_url=url,
            reason=(
                "Page loaded, but there was "
                "not enough evidence to confirm "
                "the account."
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

        from bs4 import BeautifulSoup

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
            title
            or extract_json_string(
                text,
                [
                    "nickname",
                    "displayName",
                    "name",
                ],
            )
        )

        return PublicProfile(
            platform=self.name,
            username=username,
            display_name=display_name,
            bio=description,
            avatar_url=(
                urljoin(
                    url,
                    image,
                )
                if image
                else None
            ),
            followers=followers,
            following=following,
            likes=likes,
            videos=videos,
            verified=verified,
            website=website,
            profile_url=url,
            raw_source=response.text,
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

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        return extract_media_from_html(
            self.name,
            username,
            url,
            response.text,
            soup,
        )


# ============================================================
# INSTAGRAM
# ============================================================

class InstagramProvider(
    HTMLProvider
):

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

class TikTokProvider(
    HTMLProvider
):

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

class SnapchatProvider(
    HTMLProvider
):

    name = "Snapchat"

    PROFILE_TEMPLATE = (
        "https://www.snapchat.com/add/{username}"
    )

    NOT_FOUND_MARKERS = (
        "page not found",
        "couldn't find",
        "doesn't exist",
    )

    PRIVATE_MARKERS = (
        "private",
    )


# ============================================================
# X
# ============================================================

class XProvider(
    HTMLProvider
):

    name = "X"

    PROFILE_TEMPLATE = (
        "https://x.com/{username}"
    )

    NOT_FOUND_MARKERS = (
        "this account doesn't exist",
        "account suspended",
    )


# ============================================================
# PINTEREST
# ============================================================

class PinterestProvider(
    HTMLProvider
):

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

class TwitchProvider(
    HTMLProvider
):

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


PROVIDER_MAP: dict[
    str,
    Provider,
] = {
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
            max_concurrency
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

            if cached:

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
                    "Provider failed: %s",
                    provider.name,
                )

                result = provider.result(
                    username,
                    Status.ERROR,
                    reason=str(exc),
                )

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

        return await asyncio.gather(
            *tasks
        )

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

            cached = await media_cache.get(
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

            except Exception:

                logger.exception(
                    "Profile extraction failed: %s",
                    provider.name,
                )

                profile = None

        if profile:

            await media_cache.set(
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


USER_SESSIONS: dict[
    int,
    UserSession,
] = {}


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

    return (
        f"platform:{normalized}"
    )


def build_summary_keyboard(
    results: list[CheckResult],
) -> InlineKeyboardMarkup:

    rows: list[
        list[InlineKeyboardButton]
    ] = []

    current: list[
        InlineKeyboardButton
    ] = []

    for result in results:

        if result.status != Status.CONFIRMED:
            continue

        current.append(
            InlineKeyboardButton(
                text=(
                    f"{status_icon(result.status)} "
                    f"{result.platform}"
                ),
                callback_data=platform_callback(
                    result.platform
                ),
            )
        )

        if len(current) == 2:

            rows.append(
                current
            )

            current = []

    if current:
        rows.append(
            current
        )

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

    normalized = (
        platform.lower()
        .replace(" ", "_")
    )

    rows = [
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

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def format_summary(
    username: str,
    results: list[CheckResult],
) -> str:

    counts = {
        status: sum(
            r.status == status
            for r in results
        )
        for status in Status
    }

    lines = [
        "🔎 <b>Username Checker</b>",
        "",
        (
            f"👤 Username: "
            f"<code>{esc(username)}</code>"
        ),
        "",
        (
            f"🟢 Confirmed: "
            f"<b>{counts[Status.CONFIRMED]}</b>\n"
            f"🔴 Not Found: "
            f"<b>{counts[Status.NOT_FOUND]}</b>\n"
            f"🟠 Unknown: "
            f"<b>{counts[Status.UNKNOWN]}</b>\n"
            f"🟡 Rate Limited: "
            f"<b>{counts[Status.RATE_LIMITED]}</b>\n"
            f"🔒 Private: "
            f"<b>{counts[Status.PRIVATE]}</b>\n"
            f"⚪ Unavailable: "
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

        lines.append(
            line
        )

    lines.extend(
        [
            "",
            (
                "👇 <b>اضغط على المنصة</b> "
                "لفتح تفاصيل الحساب والمحتوى العام."
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

            lines.append(
                "Evidence:"
            )

            for evidence in result.evidence:

                lines.append(
                    f"• "
                    f"{esc(evidence.message)}"
                )

        if result.data:

            lines.append(
                "Public data:"
            )

            for key, value in result.data.items():

                if value is None:
                    continue

                if isinstance(
                    value,
                    str,
                ) and len(value) > 250:

                    value = (
                        value[:247]
                        + "..."
                    )

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
            f"Username: "
            f"<code>@{esc(profile.username)}</code>"
        ),
    ]

    if profile.display_name:

        lines.append(
            f"Display name: "
            f"<b>{esc(profile.display_name)}</b>"
        )

    if profile.bio:

        bio = profile.bio

        if len(bio) > 600:

            bio = (
                bio[:597]
                + "..."
            )

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
                "ℹ️ <i>Only information available "
                "from the public page is displayed.</i>"
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
        (
            f"👤 "
            f"<code>@{esc(username)}</code>"
        ),
        "",
    ]

    if not current:

        lines.extend(
            [
                "⚪ <b>لا يوجد محتوى قابل للاستخراج.</b>",
                "",
                (
                    "قد يكون المحتوى غير متاح للطلب العام "
                    "أو يتطلب وصولًا غير متوفر."
                ),
            ]
        )

    else:

        for index, item in enumerate(
            current,
            start=start + 1,
        ):

            lines.append(
                (
                    f"{index}. "
                    f"{'🎬' if item.media_type == MediaType.VIDEO else '🖼️'} "
                    f"<b>{item.media_type.value}</b>"
                )
            )

            if item.title:

                title = item.title

                if len(title) > 100:
                    title = (
                        title[:97]
                        + "..."
                    )

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

    normalized = (
        platform.lower()
        .replace(" ", "_")
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

    rows: list[
        list[InlineKeyboardButton]
    ] = []

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

    navigation: list[
        InlineKeyboardButton
    ] = []

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

    rows.append(
        navigation
    )

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
# /START
# ============================================================

@dp.message(
    Command("start")
)
async def start_handler(
    message: Message,
):

    await message.answer(
        "👋 <b>Username Checker</b>\n\n"
        "أرسل اسم المستخدم فقط.\n\n"
        "مثال:\n"
        "<code>example</code>\n\n"
        "بعد الفحص يمكنك الضغط على أي منصة "
        "لعرض المعلومات العامة والمحتوى المتاح."
    )


# ============================================================
# /HELP
# ============================================================

@dp.message(
    Command("help")
)
async def help_handler(
    message: Message,
):

    await message.answer(
        "ℹ️ <b>طريقة الاستخدام</b>\n\n"
        "1️⃣ أرسل Username بدون @.\n"
        "2️⃣ انتظر نتائج الفحص.\n"
        "3️⃣ اضغط على المنصة.\n"
        "4️⃣ اختر معلومات الحساب أو المحتوى العام.\n\n"
        "🟢 CONFIRMED = دليل قوي\n"
        "🔴 NOT FOUND = دليل على عدم العثور\n"
        "🟠 UNKNOWN = لا يوجد دليل كافٍ\n"
        "🟡 RATE LIMITED = حد طلبات\n"
        "🔒 PRIVATE = حساب/محتوى خاص\n"
        "⚪ UNAVAILABLE = المحتوى غير متاح للطلب العام"
    )


# ============================================================
# USERNAME CHECK
# ============================================================

@dp.message(
    F.text
)
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
        f"Username: "
        f"<code>{esc(username)}</code>\n\n"
        "⏳ يتم فحص المصادر..."
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
            "❌ حدث خطأ أثناء الفحص.\n\n"
            f"<code>{esc(exc)}</code>"
        )

        return

    elapsed = (
        time.monotonic()
        - started
    )

    USER_SESSIONS[
        message.chat.id
    ] = UserSession(
        username=username,
        results=results,
    )

    text = format_summary(
        username,
        results,
    )

    text += (
        f"\n\n⏱️ "
        f"<code>{elapsed:.2f}s</code>"
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

    raw_platform = (
        callback.data
        .split(
            ":",
            1,
        )[1]
    )

    platform = raw_platform.replace(
        "_",
        " ",
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

    session.selected_platform = (
        provider.name
    )

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

    if result.status != Status.CONFIRMED:

        text += (
            "\n\n"
            "⚠️ لا يمكن استخراج المحتوى "
            "إلا عندما يكون متاحًا للعامة."
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

    raw_platform = (
        callback.data
        .split(
            ":",
            1,
        )[1]
    )

    platform = raw_platform.replace(
        "_",
        " ",
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

    await callback.message.edit_text(
        format_profile(
            profile
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎬 المحتوى العام",
                        callback_data=(
                            "media:"
                            + provider.name.lower()
                            .replace(
                                " ",
                                "_",
                            )
                            + ":0"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ رجوع",
                        callback_data=(
                            "platform:"
                            + provider.name.lower()
                            .replace(
                                " ",
                                "_",
                            )
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

    parts = callback.data.split(
        ":"
    )

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

    platform = raw_platform.replace(
        "_",
        " ",
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

    session.selected_platform = (
        provider.name
    )

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

    parts = callback.data.split(
        ":"
    )

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

    platform = raw_platform.replace(
        "_",
        " ",
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

    # If media isn't loaded yet, load it.
    if not session.media:

        await callback.answer(
            "⏳ جاري جلب المحتوى..."
        )

        session.media = await engine.get_media(
            provider,
            session.username,
        )

    if index < 0 or index >= len(
        session.media
    ):

        await callback.answer(
            "المحتوى غير موجود.",
            show_alert=True,
        )

        return

    item = session.media[
        index
    ]

    if not item.downloadable:

        await callback.answer(
            "هذا المحتوى غير متاح كملف مباشر.",
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
        if item.media_type
        == MediaType.VIDEO
        else ".jpg"
    )

    filename = (
        f"{callback.message.chat.id}_"
        f"{int(time.time())}"
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

    ok, reason = await http_client.stream_to_file(
        item.url,
        path,
        max_bytes,
    )

    if not ok:

        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

        if reason == "RATE_LIMITED":

            await callback.message.answer(
                "🟡 المنصة حدّت طلبات الوصول للمحتوى."
            )

        elif reason == "FILE_TOO_LARGE":

            await callback.message.answer(
                (
                    "⚠️ الملف أكبر من الحد المسموح "
                    f"({settings.max_media_size_mb} MB)."
                )
            )

        elif reason in {
            "HTTP 401",
            "HTTP 403",
        }:

            await callback.message.answer(
                (
                    "⚪ الملف غير متاح للطلب العام "
                    "من المصدر الحالي."
                )
            )

        else:

            await callback.message.answer(
                (
                    "⚪ لم أتمكن من جلب الملف العام.\n"
                    f"Reason: <code>{esc(reason)}</code>"
                )
            )

        return

    try:

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
                video=FSInputFile(
                    path
                ),
                caption=caption,
                supports_streaming=True,
            )

        else:

            await callback.message.answer_photo(
                photo=FSInputFile(
                    path
                ),
                caption=caption,
            )

    except Exception as exc:

        logger.exception(
            "Telegram media send failed."
        )

        await callback.message.answer(
            (
                "❌ فشل إرسال الملف إلى Telegram.\n"
                f"<code>{esc(exc)}</code>"
            )
        )

    finally:

        try:

            if os.path.exists(path):

                os.remove(
                    path
                )

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

    results = await engine.check(
        username,
        force=True,
    )

    elapsed = (
        time.monotonic()
        - started
    )

    USER_SESSIONS[
        callback.message.chat.id
    ] = UserSession(
        username=username,
        results=results,
    )

    text = format_summary(
        username,
        results,
    )

    text += (
        f"\n\n⏱️ "
        f"<code>{elapsed:.2f}s</code>"
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
            "version": "2.0",
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
            {
                "ok": True
            }
        )

    except Exception as exc:

        logger.exception(
            "Webhook update failed"
        )

        return web.json_response(
            {
                "ok": False,
                "error": str(exc),
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


# ============================================================
# MAIN
# ============================================================

async def main():

    global bot

    if not settings.bot_token:

        raise RuntimeError(
            "BOT_TOKEN is missing."
        )

    if (
        settings.bot_token
        == "YOUR_TOKEN_HERE"
    ):

        raise RuntimeError(
            "Replace YOUR_TOKEN_HERE "
            "with your bot token."
        )

    if not settings.render_url:

        raise RuntimeError(
            "RENDER_URL is missing. "
            "Add your Render service URL."
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

        logger.info(
            "Starting HTTP server on "
            "0.0.0.0:%s",
            settings.port,
        )

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
            "HTTP server started."
        )

        logger.info(
            "Bot is ONLINE."
        )

        while True:

            await asyncio.sleep(
                3600
            )

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
                    "Failed to cleanup HTTP server."
                )

        try:

            if bot:

                await bot.delete_webhook()

        except Exception:

            logger.exception(
                "Failed to remove webhook."
            )

        await http_client.close()

        if bot:

            await bot.session.close()


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
