from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import logging
import os
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable
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

    bot_token: str = Field(default="", alias="BOT_TOKEN")
    render_url: str = Field(default="", alias="RENDER_URL")

    port: int = Field(default=10000, alias="PORT")

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

    max_cache_entries: int = Field(
        default=5000,
        alias="MAX_CACHE_ENTRIES",
    )


settings = Settings()


# ============================================================
# DISPATCHER
# ============================================================

dp = Dispatcher()


# ============================================================
# ENUMS
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


STATUS_NAMES_AR = {
    Status.CONFIRMED: "تم التأكد",
    Status.NOT_FOUND: "غير موجود",
    Status.UNKNOWN: "غير معروف",
    Status.RATE_LIMITED: "تم تقييد الطلبات",
    Status.PRIVATE: "خاص",
    Status.UNAVAILABLE: "غير متاح",
    Status.ERROR: "خطأ",
}

MEDIA_NAMES_AR = {
    MediaType.VIDEO: "فيديو",
    MediaType.IMAGE: "صورة",
    MediaType.STORY: "قصة",
    MediaType.UNKNOWN: "غير معروف",
}


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
# GENERAL HELPERS
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
    return STATUS_NAMES_AR.get(
        status,
        "غير معروف",
    )


def media_type_name(media_type: MediaType) -> str:
    return MEDIA_NAMES_AR.get(
        media_type,
        "غير معروف",
    )


def confidence_bar(value: int) -> str:
    value = max(0, min(100, value))

    filled = round(value / 10)

    return (
        "█" * filled
        + "░" * (10 - filled)
    )


def safe_profile_url(
    url: str | None,
) -> str | None:

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


def make_link(
    text: str,
    url: str | None,
) -> str:

    url = safe_profile_url(url)

    if not url:
        return esc(text)

    return (
        f'<a href="{esc(url)}">'
        f"{esc(text)}"
        f"</a>"
    )


def parse_human_number(
    value: Any,
) -> int | None:

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if value < 0:
            return None

        return int(value)

    text = str(value).strip()

    if not text:
        return None

    # Arabic-Indic digits -> Latin digits.
    translation = str.maketrans(
        "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
        "01234567890123456789",
    )

    text = text.translate(translation)

    text = (
        text.replace("٬", ",")
        .replace("٫", ".")
        .strip()
    )

    # 1,234
    text_clean = text.replace(",", "")

    match = re.fullmatch(
        r"(\d+(?:\.\d+)?)\s*([KkMmBb])?",
        text_clean,
    )

    if match:
        try:
            number = float(match.group(1))
        except ValueError:
            return None

        suffix = (
            match.group(2) or ""
        ).lower()

        multiplier = {
            "": 1,
            "k": 1_000,
            "m": 1_000_000,
            "b": 1_000_000_000,
        }.get(suffix)

        if multiplier is None:
            return None

        return int(number * multiplier)

    # Fallback: first integer-looking number.
    match = re.search(
        r"\d[\d,]*",
        text,
    )

    if not match:
        return None

    try:
        return int(
            match.group(0).replace(",", "")
        )
    except ValueError:
        return None


def compact_number(value: Any) -> str:

    if value is None:
        return "غير متاح"

    number = parse_human_number(value)

    if number is None:
        return "غير متاح"

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
        result = urljoin(
            base_url,
            value,
        )

        parsed = urlparse(result)

        if parsed.scheme not in {
            "http",
            "https",
        }:
            return None

        if not parsed.hostname:
            return None

        return result

    except Exception:
        return None


def hostname_is_basic_safe(
    hostname: str | None,
) -> bool:

    if not hostname:
        return False

    hostname = hostname.lower().strip(".")

    if hostname in {
        "localhost",
        "localhost.localdomain",
    }:
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
        pass

    return True


# ============================================================
# MEDIA URL SECURITY
# ============================================================

def is_safe_media_url(
    url: str,
) -> bool:

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme != "https":
        return False

    return hostname_is_basic_safe(
        parsed.hostname
    )


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
        max_entries: int,
    ):
        self.ttl = max(
            1,
            ttl,
        )

        self.max_entries = max(
            10,
            max_entries,
        )

        self._cache: OrderedDict[
            str,
            CacheEntry,
        ] = OrderedDict()

        self._lock = asyncio.Lock()

    async def get(
        self,
        key: str,
    ) -> Any | None:

        async with self._lock:

            entry = self._cache.get(key)

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

            self._cache.move_to_end(
                key
            )

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

            self._cache.move_to_end(
                key
            )

            while len(self._cache) > self.max_entries:
                self._cache.popitem(
                    last=False
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

    async def delete_prefix(
        self,
        prefix: str,
    ) -> None:

        async with self._lock:

            keys = [
                key
                for key in self._cache
                if key.startswith(prefix)
            ]

            for key in keys:
                self._cache.pop(
                    key,
                    None,
                )

    async def clear(self) -> None:

        async with self._lock:
            self._cache.clear()


result_cache = TTLCache(
    settings.cache_ttl,
    settings.max_cache_entries,
)

profile_cache = TTLCache(
    settings.profile_cache_ttl,
    settings.max_cache_entries,
)

media_cache = TTLCache(
    settings.media_cache_ttl,
    settings.max_cache_entries,
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
                    "UsernameChecker/4.0)"
                ),
                "Accept": "*/*",
                "Accept-Language": (
                    "en-US,en;q=0.9"
                ),
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

        last_response = None

        for attempt in range(
            1,
            attempts + 1,
        ):

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

                retry_after = response.headers.get(
                    "retry-after"
                )

                if retry_after:
                    try:
                        delay = min(
                            float(retry_after),
                            15,
                        )
                    except ValueError:
                        delay = min(
                            2 ** (attempt - 1),
                            8,
                        )
                else:
                    delay = min(
                        2 ** (attempt - 1),
                        8,
                    )

            except (
                httpx.TimeoutException,
                httpx.NetworkError,
            ) as exc:

                logger.warning(
                    "HTTP error | attempt=%s | %s | %s",
                    attempt,
                    url,
                    exc,
                )

                delay = min(
                    2 ** (attempt - 1),
                    8,
                )

            if attempt < attempts:
                await asyncio.sleep(delay)

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
                follow_redirects=True,
                headers={
                    "Accept": (
                        "video/*,"
                        "image/*,"
                        "application/octet-stream"
                    ),
                },
            ) as response:

                # مهم:
                # نتأكد من الرابط النهائي بعد الـredirect.
                final_url = str(
                    response.url
                )

                if not is_safe_media_url(
                    final_url
                ):
                    return False, "UNSAFE_REDIRECT"

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
                    .get(
                        "content-type",
                        "",
                    )
                    .lower()
                    .split(";")[0]
                    .strip()
                )

                if (
                    content_type == "text/html"
                    or content_type
                    == "application/xhtml+xml"
                ):
                    return False, "NOT_DIRECT_MEDIA"

                if (
                    "mpegurl" in content_type
                    or "x-mpegurl" in content_type
                    or content_type
                    == "application/vnd.apple.mpegurl"
                ):
                    return False, "STREAM_MANIFEST"

                allowed_type = (
                    content_type.startswith(
                        "video/"
                    )
                    or content_type.startswith(
                        "image/"
                    )
                    or content_type
                    == "application/octet-stream"
                    or content_type == ""
                )

                if not allowed_type:
                    return False, (
                        "UNSUPPORTED_CONTENT_TYPE"
                    )

                content_length = (
                    response.headers.get(
                        "content-length"
                    )
                )

                if content_length:

                    try:

                        if (
                            int(content_length)
                            > max_bytes
                        ):
                            return False, (
                                "FILE_TOO_LARGE"
                            )

                    except ValueError:
                        pass

                total = 0

                with open(
                    path,
                    "wb",
                ) as file:

                    async for chunk in response.aiter_bytes(
                        64 * 1024
                    ):

                        if not chunk:
                            continue

                        total += len(chunk)

                        if total > max_bytes:

                            try:
                                file.close()
                            except Exception:
                                pass

                            try:
                                os.remove(path)
                            except Exception:
                                pass

                            return False, (
                                "FILE_TOO_LARGE"
                            )

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
# JSON / HTML EXTRACTION
# ============================================================

def extract_json_blobs(
    soup: BeautifulSoup,
) -> list[str]:

    blobs: list[str] = []

    for script in soup.find_all(
        "script"
    ):

        raw = (
            script.string
            or script.get_text()
            or ""
        ).strip()

        if len(raw) < 10:
            continue

        blobs.append(raw)

    return blobs


def walk_json(
    value: Any,
    path: tuple[str, ...] = (),
) -> Iterable[
    tuple[
        tuple[str, ...],
        str | None,
        Any,
    ]
]:

    if isinstance(value, dict):

        for key, child in value.items():

            key_string = str(key)

            yield (
                path,
                key_string,
                child,
            )

            yield from walk_json(
                child,
                path + (key_string,),
            )

    elif isinstance(value, list):

        for index, child in enumerate(
            value
        ):

            yield from walk_json(
                child,
                path + (str(index),),
            )


def parse_json_documents(
    soup: BeautifulSoup,
) -> list[Any]:

    documents: list[Any] = []

    for raw in extract_json_blobs(
        soup
    ):

        candidate = raw.strip()

        if not candidate:
            continue

        # Normal JSON / JSON-LD.
        try:

            parsed = json.loads(
                candidate
            )

            documents.append(parsed)

            continue

        except Exception:
            pass

        # بعض الصفحات تضع JSON داخل
        # window.__SOMETHING__ = {...}
        first_object = candidate.find("{")
        last_object = candidate.rfind("}")

        if (
            first_object >= 0
            and last_object > first_object
        ):

            fragment = candidate[
                first_object:last_object + 1
            ]

            try:

                parsed = json.loads(
                    fragment
                )

                documents.append(
                    parsed
                )

            except Exception:
                continue

    return documents


def normalized_key(
    value: str,
) -> str:

    return re.sub(
        r"[^a-z0-9]",
        "",
        value.casefold(),
    )


def value_to_number(
    value: Any,
) -> int | None:

    if isinstance(
        value,
        dict,
    ):

        # Structures like:
        # {"count": 123}
        # {"value": 123}
        for key in (
            "count",
            "value",
            "total",
        ):

            if key in value:

                number = parse_human_number(
                    value[key]
                )

                if number is not None:
                    return number

        return None

    return parse_human_number(
        value
    )


def find_json_value_by_aliases(
    documents: list[Any],
    aliases: set[str],
    *,
    parent_aliases: set[str] | None = None,
) -> Any | None:

    normalized_aliases = {
        normalized_key(alias)
        for alias in aliases
    }

    normalized_parents = {
        normalized_key(alias)
        for alias in (
            parent_aliases or set()
        )
    }

    candidates: list[
        tuple[int, Any]
    ] = []

    for document in documents:

        for path, key, value in walk_json(
            document
        ):

            if key is None:
                continue

            key_normalized = normalized_key(
                key
            )

            if key_normalized not in normalized_aliases:
                continue

            score = 0

            if parent_aliases:
                parent_match = any(
                    normalized_key(
                        part
                    ) in normalized_parents
                    for part in path[-4:]
                    if part
                )

                if parent_match:
                    score += 50

            if isinstance(
                value,
                (
                    int,
                    float,
                    str,
                ),
            ):
                score += 20

            elif isinstance(
                value,
                dict,
            ):
                score += 10

            candidates.append(
                (
                    score,
                    value,
                )
            )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    return candidates[0][1]


def find_json_number(
    documents: list[Any],
    aliases: set[str],
    *,
    parent_aliases: set[str] | None = None,
) -> int | None:

    value = find_json_value_by_aliases(
        documents,
        aliases,
        parent_aliases=parent_aliases,
    )

    return value_to_number(
        value
    )


def find_json_bool(
    documents: list[Any],
    aliases: set[str],
) -> bool | None:

    normalized_aliases = {
        normalized_key(alias)
        for alias in aliases
    }

    for document in documents:

        for _, key, value in walk_json(
            document
        ):

            if key is None:
                continue

            if (
                normalized_key(key)
                not in normalized_aliases
            ):
                continue

            if isinstance(
                value,
                bool,
            ):
                return value

            if isinstance(
                value,
                str,
            ):

                lowered = value.strip().lower()

                if lowered == "true":
                    return True

                if lowered == "false":
                    return False

    return None


def find_json_string(
    documents: list[Any],
    aliases: set[str],
) -> str | None:

    normalized_aliases = {
        normalized_key(alias)
        for alias in aliases
    }

    for document in documents:

        for _, key, value in walk_json(
            document
        ):

            if key is None:
                continue

            if (
                normalized_key(key)
                not in normalized_aliases
            ):
                continue

            if isinstance(
                value,
                str,
            ):

                value = value.strip()

                if value:
                    return value

    return None


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


def extract_description_stats(
    description: str | None,
) -> dict[str, int | None]:

    result: dict[
        str,
        int | None,
    ] = {
        "followers": None,
        "following": None,
        "likes": None,
        "videos": None,
    }

    if not description:
        return result

    text = description.replace(
        ",",
        "",
    )

    patterns = {
        "followers": (
            r"([\d.]+[KkMmBb]?)\s*"
            r"(?:followers|follower)"
        ),
        "following": (
            r"([\d.]+[KkMmBb]?)\s*"
            r"(?:following|follow)"
        ),
        "likes": (
            r"([\d.]+[KkMmBb]?)\s*"
            r"(?:likes|like)"
        ),
        "videos": (
            r"([\d.]+[KkMmBb]?)\s*"
            r"(?:posts|post|videos|video)"
        ),
    }

    for key, pattern in patterns.items():

        match = re.search(
            pattern,
            text,
            re.IGNORECASE,
        )

        if match:

            result[key] = parse_human_number(
                match.group(1)
            )

    return result


def username_appears_in_metadata(
    username: str,
    values: list[str | None],
) -> bool:

    target = username.casefold()

    for value in values:

        if not value:
            continue

        lower = value.casefold()

        if f"@{target}" in lower:
            return True

        normalized = re.sub(
            r"[^a-z0-9._-]+",
            " ",
            lower,
        )

        tokens = normalized.split()

        if target in tokens:
            return True

    return False


def canonical_username_from_url(
    url: str | None,
) -> str | None:

    if not url:
        return None

    try:
        parsed = urlparse(url)
    except Exception:
        return None

    path = parsed.path.strip("/")

    if not path:
        return None

    parts = [
        part
        for part in path.split("/")
        if part
    ]

    if not parts:
        return None

    candidate = parts[-1]

    if candidate.startswith("@"):
        candidate = candidate[1:]

    return candidate or None


# ============================================================
# MEDIA EXTRACTION
# ============================================================

def extract_media_from_html(
    platform: str,
    username: str,
    page_url: str,
    soup: BeautifulSoup,
) -> list[PublicMedia]:

    items: list[PublicMedia] = []
    seen: set[str] = set()

    def add_item(
        media_type: MediaType,
        url: str | None,
        *,
        thumbnail: str | None = None,
        title: str | None = None,
    ) -> None:

        if len(items) >= settings.max_media_items:
            return

        if not url:
            return

        normalized = normalize_url(
            url,
            page_url,
        )

        if not normalized:
            return

        if normalized in seen:
            return

        if not is_safe_media_url(
            normalized
        ):
            return

        seen.add(normalized)

        items.append(
            PublicMedia(
                platform=platform,
                username=username,
                media_type=media_type,
                url=normalized,
                thumbnail_url=(
                    normalize_url(
                        thumbnail,
                        page_url,
                    )
                    if thumbnail
                    else None
                ),
                title=title,
                source_url=page_url,
                downloadable=True,
            )
        )

    og_image = meta_content(
        soup,
        prop="og:image",
    )

    og_title = meta_content(
        soup,
        prop="og:title",
    )

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

        add_item(
            MediaType.VIDEO,
            value,
            thumbnail=og_image,
            title=og_title,
        )

    # --------------------------------------------------------
    # HTML video/source
    # --------------------------------------------------------

    for video in soup.find_all(
        "video"
    ):

        src = (
            video.get("src")
            or video.get("data-src")
        )

        add_item(
            MediaType.VIDEO,
            src,
            thumbnail=og_image,
            title=og_title,
        )

        for source in video.find_all(
            "source"
        ):

            add_item(
                MediaType.VIDEO,
                source.get("src"),
                thumbnail=og_image,
                title=og_title,
            )

    # --------------------------------------------------------
    # OG image
    # --------------------------------------------------------

    add_item(
        MediaType.IMAGE,
        og_image,
        thumbnail=og_image,
        title=og_title,
    )

    # --------------------------------------------------------
    # Public images
    # --------------------------------------------------------

    for image in soup.find_all(
        "img"
    ):

        if len(items) >= settings.max_media_items:
            break

        src = (
            image.get("src")
            or image.get("data-src")
            or image.get("data-lazy-src")
        )

        alt = image.get(
            "alt"
        )

        # Avoid obvious tracking / spacer images.
        width = image.get("width")
        height = image.get("height")

        try:
            if (
                width
                and height
                and int(width) <= 32
                and int(height) <= 32
            ):
                continue
        except (ValueError, TypeError):
            pass

        add_item(
            MediaType.IMAGE,
            src,
            title=alt or None,
        )

    return items[
        :settings.max_media_items
    ]


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
        *,
        result: CheckResult | None = None,
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
                reason="تعذر الوصول إلى GitHub.",
            )

        if response.status_code == 404:

            return self.result(
                username,
                Status.NOT_FOUND,
                confidence=95,
                evidence=[
                    Evidence(
                        "واجهة GitHub الرسمية أعادت HTTP 404.",
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
                    "تم تقييد طلبات GitHub أو رفض الوصول."
                ),
            )

        if response.status_code != 200:

            return self.result(
                username,
                Status.UNKNOWN,
                reason=(
                    f"GitHub أعاد HTTP "
                    f"{response.status_code}."
                ),
            )

        try:
            data = response.json()
        except Exception:

            return self.result(
                username,
                Status.UNKNOWN,
                reason="استجابة GitHub ليست JSON صالحًا.",
            )

        login = str(
            data.get("login", "")
        )

        if (
            login.casefold()
            != username.casefold()
        ):

            return self.result(
                username,
                Status.UNKNOWN,
                reason="اسم المستخدم المعاد من GitHub لا يطابق المطلوب.",
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
                    "واجهة GitHub الرسمية أكدت الحساب.",
                    70,
                ),
                Evidence(
                    "اسم الحساب المعاد يطابق الاسم المطلوب.",
                    30,
                ),
            ],
            data={
                "name": data.get("name"),
                "bio": data.get("bio"),
                "company": data.get("company"),
                "location": data.get("location"),
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
        *,
        result: CheckResult | None = None,
    ) -> PublicProfile | None:

        if (
            result is None
            or result.status != Status.CONFIRMED
        ):
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
            followers=parse_human_number(
                data.get("followers")
            ),
            following=parse_human_number(
                data.get("following")
            ),
            profile_url=result.profile_url,
            raw_source="GitHub API",
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
                    "UsernameChecker/4.0",
            },
        )

        if response is None:

            return self.result(
                username,
                Status.UNKNOWN,
                reason="تعذر الوصول إلى Reddit.",
            )

        if response.status_code == 404:

            return self.result(
                username,
                Status.NOT_FOUND,
                confidence=95,
                evidence=[
                    Evidence(
                        "Reddit أعاد HTTP 404.",
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
                    "تم تقييد طلبات Reddit أو رفض الوصول."
                ),
            )

        if response.status_code != 200:

            return self.result(
                username,
                Status.UNKNOWN,
                reason=(
                    f"Reddit أعاد HTTP "
                    f"{response.status_code}."
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
                reason="استجابة Reddit ليست JSON صالحًا.",
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
                reason="اسم المستخدم المعاد من Reddit لا يطابق المطلوب.",
            )

        profile_url = (
            "https://www.reddit.com/"
            f"user/{account_name}/"
        )

        return self.result(
            username,
            Status.CONFIRMED,
            confidence=100,
            profile_url=profile_url,
            evidence=[
                Evidence(
                    "Reddit أعاد مستخدمًا عامًا مطابقًا.",
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
        *,
        result: CheckResult | None = None,
    ) -> PublicProfile | None:

        if (
            result is None
            or result.status != Status.CONFIRMED
        ):
            return None

        return PublicProfile(
            platform=self.name,
            username=username,
            display_name=result.data.get(
                "name"
            ),
            profile_url=result.profile_url,
            raw_source="Reddit API",
        )


# ============================================================
# GENERIC HTML PROVIDER
# ============================================================

class HTMLProvider(Provider):

    PROFILE_TEMPLATE = ""

    # --------------------------------------------------------
    # Fetch
    # --------------------------------------------------------

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

        return url, response

    # --------------------------------------------------------
    # Strong evidence
    # --------------------------------------------------------

    def strong_confirmation(
        self,
        username: str,
        requested_url: str,
        soup: BeautifulSoup,
        documents: list[Any],
    ) -> tuple[
        bool,
        int,
        list[Evidence],
    ]:

        evidence: list[Evidence] = []

        score = 0

        # ----------------------------------------------------
        # Canonical / OG URL
        # ----------------------------------------------------

        canonical_tag = soup.find(
            "link",
            attrs={
                "rel": lambda value: (
                    value
                    and (
                        "canonical" in value
                        if isinstance(
                            value,
                            str,
                        )
                        else "canonical" in value
                    )
                )
            },
        )

        canonical_url = (
            canonical_tag.get("href")
            if canonical_tag
            else None
        )

        og_url = meta_content(
            soup,
            prop="og:url",
        )

        for candidate in (
            canonical_url,
            og_url,
        ):

            normalized = normalize_url(
                candidate,
                requested_url,
            )

            candidate_username = (
                canonical_username_from_url(
                    normalized
                )
            )

            if (
                candidate_username
                and candidate_username.casefold()
                == username.casefold()
            ):

                score += 50

                evidence.append(
                    Evidence(
                        "الرابط الأساسي للصفحة يطابق اسم المستخدم المطلوب.",
                        50,
                    )
                )

                break

        # ----------------------------------------------------
        # Metadata username
        # ----------------------------------------------------

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

        if username_appears_in_metadata(
            username,
            [
                title,
                og_title,
                description,
            ],
        ):

            score += 25

            evidence.append(
                Evidence(
                    "اسم المستخدم ظاهر في بيانات الصفحة العامة.",
                    25,
                )
            )

        # ----------------------------------------------------
        # JSON username / unique identifiers
        # ----------------------------------------------------

        username_keys = {
            "username",
            "screen_name",
            "unique_id",
            "uniqueid",
            "uniqueId",
            "handle",
            "login",
            "nickname",
        }

        json_username = find_json_string(
            documents,
            username_keys,
        )

        if (
            json_username
            and json_username.lstrip("@").casefold()
            == username.casefold()
        ):

            score += 40

            evidence.append(
                Evidence(
                    "بيانات JSON العامة تحتوي على اسم مستخدم مطابق.",
                    40,
                )
            )

        # ----------------------------------------------------
        # Public profile object signals
        # ----------------------------------------------------

        public_indicators = 0

        lower_text = (
            soup.get_text(
                " ",
                strip=True,
            )
            .casefold()
        )

        for indicator in (
            "followers",
            "following",
            "verified",
            "profile",
        ):

            if indicator in lower_text:
                public_indicators += 1

        if public_indicators >= 2:

            score += 10

            evidence.append(
                Evidence(
                    "الصفحة تحتوي على مؤشرات متعددة لملف عام.",
                    10,
                )
            )

        # ----------------------------------------------------
        # Threshold
        # ----------------------------------------------------

        score = min(
            score,
            100,
        )

        # مهم:
        # لا نعتمد على HTTP 200 وحده.
        confirmed = (
            score >= 60
            and (
                json_username is not None
                or canonical_username_from_url(
                    normalize_url(
                        canonical_url
                        or og_url,
                        requested_url,
                    )
                )
                == username
                or username_appears_in_metadata(
                    username,
                    [
                        title,
                        og_title,
                        description,
                    ],
                )
            )
        )

        return (
            confirmed,
            score,
            evidence,
        )

    # --------------------------------------------------------
    # Check
    # --------------------------------------------------------

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
                reason="تعذر الوصول إلى الصفحة.",
            )

        if response.status_code == 404:

            return self.result(
                username,
                Status.NOT_FOUND,
                confidence=95,
                profile_url=url,
                evidence=[
                    Evidence(
                        "المصدر أعاد HTTP 404.",
                        95,
                    )
                ],
            )

        if response.status_code == 429:

            return self.result(
                username,
                Status.RATE_LIMITED,
                profile_url=url,
                reason="المصدر قيّد عدد الطلبات.",
            )

        if response.status_code in {
            401,
            403,
        }:

            return self.result(
                username,
                Status.UNKNOWN,
                profile_url=url,
                reason=(
                    f"المصدر أعاد HTTP "
                    f"{response.status_code}."
                ),
            )

        if response.status_code >= 500:

            return self.result(
                username,
                Status.UNKNOWN,
                profile_url=url,
                reason=(
                    f"خادم المصدر أعاد HTTP "
                    f"{response.status_code}."
                ),
            )

        if response.status_code != 200:

            return self.result(
                username,
                Status.UNKNOWN,
                profile_url=url,
                reason=(
                    f"المصدر أعاد HTTP "
                    f"{response.status_code}."
                ),
            )

        text = response.text

        if not text:

            return self.result(
                username,
                Status.UNKNOWN,
                profile_url=url,
                reason="الاستجابة فارغة.",
            )

        lower_text = text.casefold()

        # ----------------------------------------------------
        # Not found
        # ----------------------------------------------------

        for marker in self.NOT_FOUND_MARKERS:

            if marker.casefold() in lower_text:

                return self.result(
                    username,
                    Status.NOT_FOUND,
                    confidence=85,
                    profile_url=url,
                    evidence=[
                        Evidence(
                            "الصفحة تحتوي على مؤشر واضح لعدم وجود الحساب.",
                            85,
                        )
                    ],
                )

        # ----------------------------------------------------
        # Private
        # ----------------------------------------------------

        for marker in self.PRIVATE_MARKERS:

            if marker.casefold() in lower_text:

                return self.result(
                    username,
                    Status.PRIVATE,
                    confidence=85,
                    profile_url=url,
                    evidence=[
                        Evidence(
                            "الصفحة تشير إلى أن الحساب أو المحتوى خاص.",
                            85,
                        )
                    ],
                )

        soup = BeautifulSoup(
            text,
            "html.parser",
        )

        documents = parse_json_documents(
            soup
        )

        confirmed, confidence, evidence = (
            self.strong_confirmation(
                username,
                url,
                soup,
                documents,
            )
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

        if confirmed:

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
                "تم تحميل الصفحة، لكن الأدلة العامة "
                "غير كافية لتأكيد الحساب."
            ),
        )

    # --------------------------------------------------------
    # Profile extraction
    # --------------------------------------------------------

    def extract_profile(
        self,
        username: str,
        url: str,
        soup: BeautifulSoup,
    ) -> PublicProfile:

        documents = parse_json_documents(
            soup
        )

        og_title = meta_content(
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

        # ----------------------------------------------------
        # Generic values
        # ----------------------------------------------------

        display_name = find_json_string(
            documents,
            {
                "displayName",
                "display_name",
                "nickname",
                "screenName",
                "name",
            },
        )

        if not display_name:
            display_name = og_title

        website = find_json_string(
            documents,
            {
                "website",
                "websiteUrl",
                "website_url",
                "bioLink",
                "bio_link",
                "url",
            },
        )

        verified = find_json_bool(
            documents,
            {
                "verified",
                "isVerified",
                "is_verified",
                "blue_verified",
                "is_blue_verified",
            },
        )

        # ----------------------------------------------------
        # Generic stats
        # ----------------------------------------------------

        followers = find_json_number(
            documents,
            {
                "followerCount",
                "followersCount",
                "followers_count",
                "follower_count",
                "followers",
            },
            parent_aliases={
                "stats",
                "authorStats",
                "author",
                "profile",
                "user",
            },
        )

        following = find_json_number(
            documents,
            {
                "followingCount",
                "following_count",
                "following",
                "friends_count",
            },
            parent_aliases={
                "stats",
                "authorStats",
                "profile",
                "user",
            },
        )

        likes = find_json_number(
            documents,
            {
                "heartCount",
                "heart_count",
                "likeCount",
                "like_count",
                "likes",
                "totalLikes",
                "total_likes",
            },
            parent_aliases={
                "stats",
                "authorStats",
                "profile",
                "user",
            },
        )

        videos = find_json_number(
            documents,
            {
                "videoCount",
                "video_count",
                "videos",
                "postCount",
                "post_count",
                "pin_count",
            },
            parent_aliases={
                "stats",
                "authorStats",
                "profile",
                "user",
            },
        )

        # ----------------------------------------------------
        # Platform-specific nested structures
        # ----------------------------------------------------

        if self.name == "Instagram":

            followers = (
                find_json_number(
                    documents,
                    {
                        "count",
                    },
                    parent_aliases={
                        "edge_followed_by",
                        "edgeFollowedBy",
                    },
                )
                or followers
            )

            following = (
                find_json_number(
                    documents,
                    {
                        "count",
                    },
                    parent_aliases={
                        "edge_follow",
                        "edgeFollow",
                    },
                )
                or following
            )

            likes = (
                find_json_number(
                    documents,
                    {
                        "count",
                    },
                    parent_aliases={
                        "edge_media_preview_like",
                        "edgeMediaPreviewLike",
                    },
                )
                or likes
            )

            videos = (
                find_json_number(
                    documents,
                    {
                        "count",
                    },
                    parent_aliases={
                        "edge_owner_to_timeline_media",
                        "edgeOwnerToTimelineMedia",
                    },
                )
                or videos
            )

        elif self.name == "TikTok":

            followers = (
                find_json_number(
                    documents,
                    {
                        "followerCount",
                        "followersCount",
                    },
                    parent_aliases={
                        "stats",
                        "authorStats",
                    },
                )
                or followers
            )

            following = (
                find_json_number(
                    documents,
                    {
                        "followingCount",
                        "following_count",
                    },
                    parent_aliases={
                        "stats",
                        "authorStats",
                    },
                )
                or following
            )

            likes = (
                find_json_number(
                    documents,
                    {
                        "heartCount",
                        "heart_count",
                    },
                    parent_aliases={
                        "stats",
                        "authorStats",
                    },
                )
                or likes
            )

            videos = (
                find_json_number(
                    documents,
                    {
                        "videoCount",
                        "video_count",
                    },
                    parent_aliases={
                        "stats",
                        "authorStats",
                    },
                )
                or videos
            )

        elif self.name == "X":

            followers = (
                find_json_number(
                    documents,
                    {
                        "followers_count",
                    },
                )
                or followers
            )

            following = (
                find_json_number(
                    documents,
                    {
                        "friends_count",
                    },
                )
                or following
            )

            videos = (
                find_json_number(
                    documents,
                    {
                        "statuses_count",
                    },
                )
                or videos
            )

        # ----------------------------------------------------
        # Description fallback
        # ----------------------------------------------------

        description_stats = (
            extract_description_stats(
                description
            )
        )

        if followers is None:
            followers = description_stats[
                "followers"
            ]

        if following is None:
            following = description_stats[
                "following"
            ]

        if likes is None:
            likes = description_stats[
                "likes"
            ]

        if videos is None:
            videos = description_stats[
                "videos"
            ]

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
            website=normalize_url(
                website,
                url,
            ) or website,
            profile_url=url,
            raw_source="Public page metadata / JSON",
        )

    async def profile(
        self,
        username: str,
        http: HTTPClient,
        *,
        result: CheckResult | None = None,
    ) -> PublicProfile | None:

        if (
            result is not None
            and result.status
            != Status.CONFIRMED
        ):
            return None

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

        return self.extract_profile(
            username,
            url,
            soup,
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

    PRIVATE_MARKERS = ()


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
        "this account does not exist",
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

PROVIDER_MAP: dict[str, Provider] = {
    provider.name.casefold(): provider
    for provider in PROVIDERS
}


# ============================================================
# CALLBACK PLATFORM KEYS
# ============================================================

PLATFORM_KEYS = {
    "github": "GitHub",
    "reddit": "Reddit",
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "snapchat": "Snapchat",
    "x": "X",
    "pinterest": "Pinterest",
    "twitch": "Twitch",
}


def platform_key(
    platform: str,
) -> str:

    return platform.casefold()


def platform_from_key(
    key: str,
) -> str | None:

    return PLATFORM_KEYS.get(
        key.casefold()
    )


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
            max(
                1,
                max_concurrency,
            )
        )

    async def run_provider(
        self,
        provider: Provider,
        username: str,
        *,
        force: bool = False,
    ) -> CheckResult:

        cache_key = (
            "check:"
            f"{provider.name.casefold()}:"
            f"{username.casefold()}"
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

        # Stable states only.
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

        if force:

            await self.invalidate_username(
                username
            )

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
        result: CheckResult | None = None,
        force: bool = False,
    ) -> PublicProfile | None:

        key = (
            "profile:"
            f"{provider.name.casefold()}:"
            f"{username.casefold()}"
        )

        if not force:

            cached = await profile_cache.get(
                key
            )

            if cached is not None:
                return cached

        # إذا لم نعطِ النتيجة، حاول أخذها من cache.
        if result is None:

            result = await result_cache.get(
                (
                    "check:"
                    f"{provider.name.casefold()}:"
                    f"{username.casefold()}"
                )
            )

        async with self.semaphore:

            try:

                profile = await provider.profile(
                    username,
                    self.http,
                    result=result,
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
            "media:"
            f"{provider.name.casefold()}:"
            f"{username.casefold()}"
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

    async def invalidate_username(
        self,
        username: str,
    ) -> None:

        username_key = username.casefold()

        for provider in PROVIDERS:

            provider_key = provider.name.casefold()

            await result_cache.delete(
                (
                    "check:"
                    f"{provider_key}:"
                    f"{username_key}"
                )
            )

            await profile_cache.delete(
                (
                    "profile:"
                    f"{provider_key}:"
                    f"{username_key}"
                )
            )

            await media_cache.delete(
                (
                    "media:"
                    f"{provider_key}:"
                    f"{username_key}"
                )
            )


engine = CheckEngine(
    http_client,
    settings.max_concurrency,
)


# ============================================================
# USER SESSIONS
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


USER_SESSIONS: dict[
    int,
    UserSession,
] = {}


def save_session(
    chat_id: int,
    session: UserSession,
) -> None:

    USER_SESSIONS[
        chat_id
    ] = session

    if (
        len(USER_SESSIONS)
        <= settings.max_sessions
    ):
        return

    oldest_chat = min(
        USER_SESSIONS,
        key=lambda key:
        USER_SESSIONS[key].created_at,
    )

    USER_SESSIONS.pop(
        oldest_chat,
        None,
    )


# ============================================================
# TELEGRAM UI
# ============================================================

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

        if (
            result.status
            != Status.CONFIRMED
        ):
            continue

        key = platform_key(
            result.platform
        )

        current.append(
            InlineKeyboardButton(
                text=(
                    f"🟢 "
                    f"{result.platform}"
                ),
                callback_data=(
                    f"platform:{key}"
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

    key = platform_key(
        platform
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👤 معلومات الحساب",
                    callback_data=(
                        f"profile:{key}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎬 المحتوى العام",
                    callback_data=(
                        f"media:{key}:0"
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


# ============================================================
# FORMAT SUMMARY
# ============================================================

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
        "🔎 <b>فاحص أسماء المستخدمين</b>",
        "",
        (
            "👤 اسم المستخدم: "
            f"<code>@{esc(username)}</code>"
        ),
        "",
        (
            "🟢 تم التأكد: "
            f"<b>{counts[Status.CONFIRMED]}</b>\n"
            "🔴 غير موجود: "
            f"<b>{counts[Status.NOT_FOUND]}</b>\n"
            "🟠 غير معروف: "
            f"<b>{counts[Status.UNKNOWN]}</b>\n"
            "🟡 تقييد الطلبات: "
            f"<b>{counts[Status.RATE_LIMITED]}</b>\n"
            "🔒 خاص: "
            f"<b>{counts[Status.PRIVATE]}</b>\n"
            "⚪ غير متاح: "
            f"<b>{counts[Status.UNAVAILABLE]}</b>"
        ),
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    for result in results:

        line = (
            f"{status_icon(result.status)} "
            f"<b>{esc(result.platform)}</b>"
            f" — "
            f"{status_name(result.status)}"
        )

        if result.confidence:

            line += (
                f" "
                f"<code>{result.confidence}%</code>"
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
                "👇 <b>اختر منصة مؤكدة</b> "
                "لعرض المعلومات المتاحة."
            ),
            "",
            (
                "ℹ️ <i>غير معروف لا يعني أن الحساب "
                "غير موجود؛ يعني أن المصدر لم يقدم "
                "أدلة عامة كافية.</i>"
            ),
        ]
    )

    return "\n".join(lines)


# ============================================================
# FORMAT DETAILS
# ============================================================

def format_details(
    username: str,
    results: list[CheckResult],
) -> str:

    lines = [
        "📊 <b>التفاصيل</b>",
        "",
        (
            "👤 "
            f"<code>@{esc(username)}</code>"
        ),
        "",
    ]

    for result in results:

        lines.append(
            f"{status_icon(result.status)} "
            f"<b>{esc(result.platform)}</b>"
        )

        lines.append(
            "الحالة: "
            f"<code>{status_name(result.status)}</code>"
        )

        lines.append(
            "الثقة: "
            f"<code>{result.confidence}%</code>"
        )

        if result.confidence:

            lines.append(
                "<code>"
                f"{confidence_bar(result.confidence)}"
                "</code>"
            )

        if result.reason:

            lines.append(
                "السبب: "
                f"{esc(result.reason)}"
            )

        if result.evidence:

            lines.append(
                "الأدلة:"
            )

            for evidence in result.evidence:

                lines.append(
                    f"• {esc(evidence.message)}"
                )

        if result.data:

            lines.append(
                "البيانات العامة:"
            )

            for key, value in (
                result.data.items()
            ):

                if value is None:
                    continue

                if (
                    isinstance(
                        value,
                        str,
                    )
                    and len(value) > 250
                ):
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
                    "🔗 فتح الحساب",
                    result.profile_url,
                )
            )

        lines.append("")

    return "\n".join(lines)


# ============================================================
# FORMAT PROFILE
# ============================================================

def format_profile(
    profile: PublicProfile,
) -> str:

    lines = [
        (
            f"👤 <b>ملف "
            f"{esc(profile.platform)}</b>"
        ),
        "",
        (
            "اسم المستخدم: "
            f"<code>@{esc(profile.username)}</code>"
        ),
    ]

    if profile.display_name:

        lines.append(
            "الاسم الظاهر: "
            f"<b>{esc(profile.display_name)}</b>"
        )

    if profile.bio:

        bio = profile.bio

        if len(bio) > 600:
            bio = bio[:597] + "..."

        lines.extend(
            [
                "",
                "📝 <b>الوصف</b>",
                esc(bio),
            ]
        )

    lines.extend(
        [
            "",
            "📊 <b>الإحصائيات العامة</b>",
            (
                "المتابعون: "
                f"<b>{compact_number(profile.followers)}</b>"
            ),
            (
                "المتابَعون: "
                f"<b>{compact_number(profile.following)}</b>"
            ),
            (
                "الإعجابات: "
                f"<b>{compact_number(profile.likes)}</b>"
            ),
            (
                "المنشورات/الفيديوهات: "
                f"<b>{compact_number(profile.videos)}</b>"
            ),
        ]
    )

    if profile.verified is not None:

        lines.append(
            "التوثيق: "
            f"<b>{'نعم' if profile.verified else 'لا'}</b>"
        )

    if profile.website:

        lines.append(
            make_link(
                "🌐 الموقع",
                profile.website,
            )
        )

    if profile.profile_url:

        lines.extend(
            [
                "",
                make_link(
                    "🔗 فتح الحساب",
                    profile.profile_url,
                ),
            ]
        )

    lines.extend(
        [
            "",
            (
                "ℹ️ <i>الإحصائيات تظهر فقط إذا كانت "
                "مكشوفة للعامة في استجابة المنصة.</i>"
            ),
        ]
    )

    return "\n".join(lines)


# ============================================================
# FORMAT MEDIA
# ============================================================

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
            f"🎬 <b>المحتوى العام — "
            f"{esc(platform)}</b>"
        ),
        "",
        (
            "👤 "
            f"<code>@{esc(username)}</code>"
        ),
        "",
    ]

    if not current:

        lines.extend(
            [
                "⚪ <b>لا يوجد محتوى قابل للجلب.</b>",
                "",
                (
                    "قد يكون المحتوى ديناميكيًا، "
                    "أو خاصًا، أو غير مكشوف كرابط "
                    "ملف مباشر في الصفحة العامة."
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
                if item.media_type
                == MediaType.VIDEO
                else "🖼️"
            )

            lines.append(
                f"{index}. {icon} "
                f"<b>{media_type_name(item.media_type)}</b>"
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
                    "📥 رابط عام مباشر"
                    if item.downloadable
                    else "⚪ غير متاح مباشرة"
                )
            )

            lines.append("")

    lines.append(
        f"📄 الصفحة {page + 1}/{total_pages}"
    )

    return "\n".join(lines)


def build_media_keyboard(
    platform: str,
    items: list[PublicMedia],
    page: int,
) -> InlineKeyboardMarkup:

    key = platform_key(
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
            if item.media_type
            == MediaType.VIDEO
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
                        f"{key}:"
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
                    f"{key}:"
                    f"{page - 1}"
                ),
            )
        )

    navigation.append(
        InlineKeyboardButton(
            text=(
                f"{page + 1}/"
                f"{total_pages}"
            ),
            callback_data="noop",
        )
    )

    if page < total_pages - 1:

        navigation.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=(
                    f"media:"
                    f"{key}:"
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
                    f"profile:{key}"
                ),
            ),
            InlineKeyboardButton(
                text="⬅️ رجوع",
                callback_data=(
                    f"platform:{key}"
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

@dp.message(Command("start"))
async def start_handler(
    message: Message,
):

    await message.answer(
        "👋 <b>فاحص أسماء المستخدمين</b>\n\n"
        "أرسل اسم المستخدم فقط بدون @.\n\n"
        "مثال:\n"
        "<code>example</code>\n\n"
        "سيتم فحص المصادر العامة ثم عرض النتائج "
        "مع مستوى الثقة والأدلة المتاحة.\n\n"
        "استخدم /help لمعرفة التفاصيل."
    )


# ============================================================
# /HELP
# ============================================================

@dp.message(Command("help"))
async def help_handler(
    message: Message,
):

    await message.answer(
        "ℹ️ <b>طريقة الاستخدام</b>\n\n"
        "1️⃣ أرسل Username.\n"
        "2️⃣ انتظر انتهاء الفحص.\n"
        "3️⃣ اختر المنصة المؤكدة.\n"
        "4️⃣ اعرض البيانات العامة المتاحة.\n\n"
        "<b>الحالات:</b>\n"
        "🟢 تم التأكد = أدلة عامة قوية\n"
        "🔴 غير موجود = المصدر أعاد دليلًا على عدم وجود الحساب\n"
        "🟠 غير معروف = الأدلة غير كافية\n"
        "🟡 تقييد الطلبات = المصدر حدّ الوصول\n"
        "🔒 خاص = الحساب/المحتوى الخاص ظاهر للمصدر\n"
        "⚪ غير متاح = لا توجد بيانات عامة قابلة للاستخراج\n\n"
        "⚠️ الأداة لا تتجاوز تسجيل الدخول أو CAPTCHA "
        "أو أنظمة الحماية، ولا تصل إلى المحتوى الخاص."
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
            "❌ أرسل اسم مستخدم صالح."
        )

        return

    if (
        len(username)
        > settings.max_username_length
    ):

        await message.answer(
            "❌ اسم المستخدم طويل جدًا."
        )

        return

    if not valid_username(
        username
    ):

        await message.answer(
            "❌ اسم المستخدم غير صالح.\n\n"
            "المسموح:\n"
            "A-Z / a-z / 0-9 / . / _ / -"
        )

        return

    status_message = await message.answer(
        "🔎 <b>جاري الفحص...</b>\n\n"
        f"👤 <code>@{esc(username)}</code>\n\n"
        "⏳ يتم فحص المصادر العامة..."
    )

    started = time.monotonic()

    try:

        results = await engine.check(
            username
        )

    except asyncio.CancelledError:
        raise

    except Exception:

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
        "\n\n"
        f"⏱️ <code>{elapsed:.2f}s</code>"
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
    F.data.startswith(
        "platform:"
    )
)
async def platform_callback_handler(
    callback: CallbackQuery,
):

    if not callback.message:

        await callback.answer()
        return

    raw_key = callback.data.split(
        ":",
        1,
    )[1]

    platform = platform_from_key(
        raw_key
    )

    if not platform:

        await callback.answer(
            "المنصة غير معروفة.",
            show_alert=True,
        )

        return

    session = USER_SESSIONS.get(
        callback.message.chat.id
    )

    if not session:

        await callback.answer(
            "لا توجد جلسة محفوظة.",
            show_alert=True,
        )

        return

    provider = PROVIDER_MAP.get(
        platform.casefold()
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
            if (
                item.platform.casefold()
                == provider.name.casefold()
            )
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
        "👤 اسم المستخدم: "
        f"<code>@{esc(session.username)}</code>\n\n"
        "الحالة: "
        f"<b>{status_name(result.status)}</b>\n"
        "الثقة: "
        f"<b>{result.confidence}%</b>"
    )

    if result.confidence:

        text += (
            "\n"
            "<code>"
            f"{confidence_bar(result.confidence)}"
            "</code>"
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

    if result.evidence:

        text += (
            "\n\n"
            "<b>أبرز الأدلة:</b>\n"
        )

        for evidence in result.evidence[:4]:

            text += (
                f"• {esc(evidence.message)}\n"
            )

    if (
        result.status
        != Status.CONFIRMED
    ):

        text += (
            "\n\n"
            "⚠️ لا يتم عرض البيانات أو المحتوى "
            "على أنه مؤكد إلا عند وجود أدلة عامة كافية."
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
    F.data.startswith(
        "profile:"
    )
)
async def profile_callback_handler(
    callback: CallbackQuery,
):

    if not callback.message:

        await callback.answer()
        return

    raw_key = callback.data.split(
        ":",
        1,
    )[1]

    platform = platform_from_key(
        raw_key
    )

    if not platform:

        await callback.answer(
            "المنصة غير معروفة.",
            show_alert=True,
        )

        return

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
        platform.casefold()
    )

    if not provider:

        await callback.answer(
            "المنصة غير مدعومة.",
            show_alert=True,
        )

        return

    result = next(
        (
            item
            for item in session.results
            if (
                item.platform.casefold()
                == provider.name.casefold()
            )
        ),
        None,
    )

    if not result:

        await callback.answer(
            "لا توجد نتيجة لهذه المنصة.",
            show_alert=True,
        )

        return

    if (
        result.status
        != Status.CONFIRMED
    ):

        await callback.answer(
            "لا يمكن استخراج ملف حساب غير مؤكد.",
            show_alert=True,
        )

        return

    await callback.answer(
        "⏳ جاري استخراج البيانات العامة..."
    )

    profile = await engine.get_profile(
        provider,
        session.username,
        result=result,
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

    key = platform_key(
        provider.name
    )

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
                            f"media:{key}:0"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ رجوع",
                        callback_data=(
                            f"platform:{key}"
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
    F.data.startswith(
        "media:"
    )
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

    raw_key = parts[1]

    platform = platform_from_key(
        raw_key
    )

    if not platform:

        await callback.answer(
            "المنصة غير معروفة.",
            show_alert=True,
        )

        return

    try:
        page = int(parts[2])
    except ValueError:
        page = 0

    page = max(
        0,
        page,
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
        platform.casefold()
    )

    if not provider:

        await callback.answer(
            "المنصة غير مدعومة.",
            show_alert=True,
        )

        return

    result = next(
        (
            item
            for item in session.results
            if (
                item.platform.casefold()
                == provider.name.casefold()
            )
        ),
        None,
    )

    if (
        result is None
        or result.status
        != Status.CONFIRMED
    ):

        await callback.answer(
            "الحساب غير مؤكد.",
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
    F.data.startswith(
        "getmedia:"
    )
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

    raw_key = parts[1]

    platform = platform_from_key(
        raw_key
    )

    if not platform:

        await callback.answer(
            "المنصة غير معروفة.",
            show_alert=True,
        )

        return

    try:
        index = int(parts[2])
    except ValueError:

        await callback.answer(
            "رقم المحتوى غير صالح.",
            show_alert=True,
        )

        return

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
        platform.casefold()
    )

    if not provider:

        await callback.answer(
            "المنصة غير مدعومة.",
            show_alert=True,
        )

        return

    result = next(
        (
            item
            for item in session.results
            if (
                item.platform.casefold()
                == provider.name.casefold()
            )
        ),
        None,
    )

    if (
        result is None
        or result.status
        != Status.CONFIRMED
    ):

        await callback.answer(
            "الحساب غير مؤكد.",
            show_alert=True,
        )

        return

    if (
        not session.media
        or session.selected_platform
        != provider.name
    ):

        await callback.answer(
            "⏳ جاري جلب المحتوى..."
        )

        session.media = await engine.get_media(
            provider,
            session.username,
        )

        session.selected_platform = (
            provider.name
        )

    if (
        index < 0
        or index >= len(session.media)
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
            "هذا المحتوى ليس ملفًا مباشرًا عامًا.",
            show_alert=True,
        )

        return

    if not is_safe_media_url(
        item.url
    ):

        await callback.answer(
            "تم رفض رابط غير آمن.",
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

            elif reason in {
                "UNSAFE_URL",
                "UNSAFE_REDIRECT",
            }:

                message = (
                    "⚪ تم رفض رابط غير آمن."
                )

            elif reason == "TIMEOUT":

                message = (
                    "⏱️ انتهت مهلة جلب الملف."
                )

            else:

                message = (
                    "⚪ لم أتمكن من جلب الملف العام.\n"
                    f"السبب: <code>{esc(reason)}</code>"
                )

            await callback.message.answer(
                message
            )

            return

        caption = (
            f"🎬 <b>{esc(provider.name)}</b>\n"
            f"👤 <code>@{esc(session.username)}</code>\n"
            f"📦 {media_type_name(item.media_type)}\n\n"
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

        if (
            item.media_type
            == MediaType.VIDEO
        ):

            await callback.message.answer_video(
                video=FSInputFile(
                    path
                ),
                caption=caption,
                supports_streaming=True,
            )

        elif (
            item.media_type
            == MediaType.IMAGE
        ):

            await callback.message.answer_photo(
                photo=FSInputFile(
                    path
                ),
                caption=caption,
            )

        else:

            await callback.message.answer_document(
                document=FSInputFile(
                    path
                ),
                caption=caption,
            )

    except Exception:

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

    except asyncio.CancelledError:
        raise

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
        "\n\n"
        f"⏱️ <code>{elapsed:.2f}s</code>"
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
# ERROR HANDLER
# ============================================================

@dp.error()
async def global_error_handler(
    event: Any,
):

    logger.exception(
        "Unhandled Telegram update error: %s",
        event.exception,
    )


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
            "version": "4.0",
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
                "error": (
                    "Bot is not initialized"
                ),
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

    except Exception:

        logger.exception(
            "Webhook update failed."
        )

        return web.json_response(
            {
                "ok": False,
                "error": (
                    "Update processing failed"
                ),
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

    if not base_url.startswith(
        "https://"
    ):

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

    info = (
        await telegram_bot.get_webhook_info()
    )

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
            "Starting Username Checker v4.0..."
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
            "HTTP server started on "
            "0.0.0.0:%s",
            settings.port,
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
