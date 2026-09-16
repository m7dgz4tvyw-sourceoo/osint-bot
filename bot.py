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

import httpx
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
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

    max_concurrency: int = Field(
        default=8,
        alias="MAX_CONCURRENCY",
    )

    cache_ttl: int = Field(
        default=300,
        alias="CACHE_TTL",
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
    ERROR = "ERROR"


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
    return bool(
        USERNAME_RE.fullmatch(username)
    )


def esc(value: Any) -> str:
    return html.escape(str(value))


def status_icon(status: Status) -> str:
    return {
        Status.CONFIRMED: "🟢",
        Status.NOT_FOUND: "🔴",
        Status.UNKNOWN: "🟠",
        Status.RATE_LIMITED: "🟡",
        Status.ERROR: "⚫",
    }.get(status, "⚪")


def status_name(status: Status) -> str:
    return {
        Status.CONFIRMED: "CONFIRMED",
        Status.NOT_FOUND: "NOT FOUND",
        Status.UNKNOWN: "UNKNOWN",
        Status.RATE_LIMITED: "RATE LIMITED",
        Status.ERROR: "ERROR",
    }.get(status, "UNKNOWN")


def confidence_bar(value: int) -> str:
    value = max(
        0,
        min(100, value)
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

    if not url.startswith(
        ("https://", "http://")
    ):
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
        f'{esc(text)}'
        f'</a>'
    )


# ============================================================
# CACHE
# ============================================================

@dataclass
class CacheEntry:
    created_at: float
    result: CheckResult


class ResultCache:

    def __init__(
        self,
        ttl: int,
    ):
        self.ttl = ttl

        self._cache: dict[
            str,
            CacheEntry
        ] = {}

        self._lock = asyncio.Lock()

    async def get(
        self,
        key: str,
    ) -> CheckResult | None:

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

            return entry.result

    async def set(
        self,
        key: str,
        result: CheckResult,
    ) -> None:

        async with self._lock:

            self._cache[key] = CacheEntry(
                created_at=time.monotonic(),
                result=result,
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


# ============================================================
# HTTP CLIENT
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
                    "UsernameChecker/1.0)"
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
                        f"HTTP "
                        f"{response.status_code}"
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


# ============================================================
# PROVIDER BASE
# ============================================================

class Provider:

    name = "Unknown"

    async def check(
        self,
        username: str,
        http: HTTPClient,
    ) -> CheckResult:

        raise NotImplementedError

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
                    "GitHub API request "
                    "failed or timed out."
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
                reason=(
                    "Invalid JSON returned "
                    "by GitHub."
                ),
            )

        login = str(
            data.get(
                "login",
                ""
            )
        )

        if login.lower() != username.lower():

            return self.result(
                username,
                Status.UNKNOWN,
                reason=(
                    "GitHub response did not "
                    "match requested username."
                ),
            )

        return self.result(
            username,
            Status.CONFIRMED,
            confidence=100,
            profile_url=data.get(
                "html_url",
                f"https://github.com/{username}",
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
                "created_at": data.get(
                    "created_at"
                ),
            },
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
                    "UsernameChecker/1.0",
            },
        )

        if response is None:

            return self.result(
                username,
                Status.UNKNOWN,
                reason=(
                    "Reddit request failed "
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
                    "Reddit returned HTTP "
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
                reason=(
                    "Invalid JSON returned "
                    "by Reddit."
                ),
            )

        account_name = str(
            data.get(
                "name",
                ""
            )
        )

        if (
            account_name.lower()
            != username.lower()
        ):

            return self.result(
                username,
                Status.UNKNOWN,
                reason=(
                    "Reddit response did not "
                    "match username."
                ),
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
            },
        )


# ============================================================
# HTML PROVIDER
# ============================================================

class HTMLProvider(
    Provider
):

    PROFILE_TEMPLATE = ""

    NOT_FOUND_MARKERS: tuple[str, ...] = ()

    async def check(
        self,
        username: str,
        http: HTTPClient,
    ) -> CheckResult:

        url = self.PROFILE_TEMPLATE.format(
            username=username
        )

        response = await http.get(
            url
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
            403,
            429,
        }:

            return self.result(
                username,
                Status.RATE_LIMITED,
                profile_url=url,
                reason=(
                    "Platform returned HTTP "
                    f"{response.status_code}."
                ),
            )

        if response.status_code >= 500:

            return self.result(
                username,
                Status.UNKNOWN,
                profile_url=url,
                reason=(
                    "Platform server returned "
                    f"HTTP {response.status_code}."
                ),
            )

        if response.status_code != 200:

            return self.result(
                username,
                Status.UNKNOWN,
                profile_url=url,
                reason=(
                    "Platform returned HTTP "
                    f"{response.status_code}."
                ),
            )

        text = response.text.lower()

        for marker in self.NOT_FOUND_MARKERS:

            if marker.lower() in text:

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


# ============================================================
# ENGINE
# ============================================================

class CheckEngine:

    def __init__(
        self,
        http: HTTPClient,
        cache: ResultCache,
        max_concurrency: int,
    ):

        self.http = http
        self.cache = cache

        self.semaphore = asyncio.Semaphore(
            max_concurrency
        )

    async def run_provider(
        self,
        provider: Provider,
        username: str,
    ) -> CheckResult:

        cache_key = (
            f"{provider.name.lower()}:"
            f"{username.lower()}"
        )

        cached = await self.cache.get(
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
        }:

            await self.cache.set(
                cache_key,
                result,
            )

        return result

    async def check(
        self,
        username: str,
    ) -> list[CheckResult]:

        tasks = [
            self.run_provider(
                provider,
                username,
            )
            for provider in PROVIDERS
        ]

        return await asyncio.gather(
            *tasks
        )


# ============================================================
# TELEGRAM UI
# ============================================================

def build_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 إعادة الفحص",
                    callback_data="recheck",
                ),
                InlineKeyboardButton(
                    text="📊 التفاصيل",
                    callback_data="details",
                ),
            ],
        ]
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
            f"⚫ Error: "
            f"<b>{counts[Status.ERROR]}</b>"
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
                "⚠️ <i>UNKNOWN لا يعني أن الحساب "
                "غير موجود؛ يعني فقط أنه لم يتم "
                "الحصول على دليل كافٍ.</i>"
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
                f"Reason: "
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

        if result.profile_url:

            lines.append(
                make_link(
                    "🔗 Profile",
                    result.profile_url,
                )
            )

        lines.append("")

    return "\n".join(lines)


# ============================================================
# STATE
# ============================================================

USER_RESULTS: dict[
    int,
    tuple[
        str,
        list[CheckResult]
    ]
] = {}


# ============================================================
# GLOBAL OBJECTS
# ============================================================

http_client = HTTPClient(
    settings.request_timeout
)

cache = ResultCache(
    settings.cache_ttl
)

engine = CheckEngine(
    http_client,
    cache,
    settings.max_concurrency,
)


# ============================================================
# DISPATCHER
# ============================================================

dp = Dispatcher()


# ============================================================
# /START
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
        "وسأفحص المصادر المتاحة."
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
        "أرسل Username بدون @.\n\n"
        "مثال:\n"
        "<code>example</code>\n\n"
        "🟢 CONFIRMED = دليل قوي على وجود الحساب\n"
        "🔴 NOT FOUND = دليل على عدم وجود الحساب\n"
        "🟠 UNKNOWN = لا يوجد دليل كافٍ\n"
        "🟡 RATE LIMITED = المنصة حدّت الطلبات\n"
        "⚫ ERROR = خطأ غير متوقع"
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

    USER_RESULTS[
        message.chat.id
    ] = (
        username,
        results,
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
        reply_markup=build_keyboard(),
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

    stored = USER_RESULTS.get(
        callback.message.chat.id
    )

    if not stored:

        await callback.answer(
            "لا توجد نتيجة محفوظة.",
            show_alert=True,
        )

        return

    username, results = stored

    text = format_details(
        username,
        results,
    )

    await callback.message.edit_text(
        text,
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

    stored = USER_RESULTS.get(
        callback.message.chat.id
    )

    if not stored:

        await callback.answer(
            "لا توجد نتيجة محفوظة.",
            show_alert=True,
        )

        return

    username, results = stored

    await callback.message.edit_text(
        format_summary(
            username,
            results,
        ),
        reply_markup=build_keyboard(),
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

    stored = USER_RESULTS.get(
        callback.message.chat.id
    )

    if not stored:

        await callback.answer(
            "لا توجد نتيجة لإعادة الفحص.",
            show_alert=True,
        )

        return

    username, _ = stored

    await callback.answer(
        "🔄 جاري إعادة الفحص..."
    )

    # Delete cached results for this username.
    for provider in PROVIDERS:

        key = (
            f"{provider.name.lower()}:"
            f"{username.lower()}"
        )

        await cache.delete(
            key
        )

    started = time.monotonic()

    results = await engine.check(
        username
    )

    elapsed = (
        time.monotonic()
        - started
    )

    USER_RESULTS[
        callback.message.chat.id
    ] = (
        username,
        results,
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
        reply_markup=build_keyboard(),
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


# ============================================================
# WEB APP
# ============================================================

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
            "BOT_TOKEN is missing. "
            "Add it to Render Environment Variables."
        )

    if not settings.render_url:

        raise RuntimeError(
            "RENDER_URL is missing. "
            "Add your Render service URL "
            "to Environment Variables."
        )

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        ),
    )

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

        # Keep process alive.
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
