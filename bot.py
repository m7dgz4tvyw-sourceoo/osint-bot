from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable
from urllib.parse import quote

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from bs4 import BeautifulSoup
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
# CONFIGURATION
# ============================================================

class Settings(BaseSettings):
    bot_token: str = "8974546244:AAGSIwbh9FmENOiKYP2tS33_Z-ixjPl0cl4"
    request_timeout: float = 12.0
    max_concurrency: int = 8
    cache_ttl: int = 300
    max_username_length: int = 100

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

if not settings.bot_token:
    raise RuntimeError(
        "BOT_TOKEN is missing. Add BOT_TOKEN to the environment."
    )


# ============================================================
# RESULT MODEL
# ============================================================

class Status(str, Enum):
    CONFIRMED = "confirmed"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


@dataclass(slots=True)
class Evidence:
    kind: str
    description: str
    strength: int


@dataclass(slots=True)
class CheckResult:
    platform: str
    username: str
    status: Status
    profile_url: str
    confidence: int = 0
    evidence: list[Evidence] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None
    checked_at: float = field(default_factory=time.time)

    @property
    def icon(self) -> str:
        return {
            Status.CONFIRMED: "🟢",
            Status.NOT_FOUND: "🔴",
            Status.UNKNOWN: "🟠",
            Status.RATE_LIMITED: "🟡",
            Status.ERROR: "⚫",
        }[self.status]


# ============================================================
# CACHE
# ============================================================

@dataclass(slots=True)
class CacheEntry:
    expires_at: float
    results: list[CheckResult]


class ResultCache:
    def __init__(self, ttl: int) -> None:
        self.ttl = ttl
        self._data: dict[str, CacheEntry] = {}
        self._lock = asyncio.Lock()

    async def get(
        self,
        username: str,
    ) -> list[CheckResult] | None:

        async with self._lock:
            entry = self._data.get(username)

            if not entry:
                return None

            if entry.expires_at <= time.time():
                self._data.pop(username, None)
                return None

            return entry.results

    async def set(
        self,
        username: str,
        results: list[CheckResult],
    ) -> None:

        async with self._lock:
            self._data[username] = CacheEntry(
                expires_at=time.time() + self.ttl,
                results=results,
            )


cache = ResultCache(settings.cache_ttl)


# ============================================================
# HTTP CLIENT
# ============================================================

class HTTPClient:
    RETRYABLE_STATUS_CODES = {
        408,
        425,
        429,
        500,
        502,
        503,
        504,
    }

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                settings.request_timeout,
                connect=8.0,
            ),
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "PublicUsernameChecker/2.0 "
                    "(public-profile-verification)"
                ),
                "Accept": "*/*",
            },
            limits=httpx.Limits(
                max_connections=30,
                max_keepalive_connections=15,
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
    ) -> httpx.Response | None:

        async def request() -> httpx.Response:
            response = await self.client.get(
                url,
                headers=headers,
                params=params,
            )

            if response.status_code in self.RETRYABLE_STATUS_CODES:
                raise RetryableHTTPError(
                    response.status_code
                )

            return response

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(
                    multiplier=0.7,
                    min=0.5,
                    max=5,
                ),
                retry=retry_if_exception_type(
                    (
                        httpx.TimeoutException,
                        httpx.NetworkError,
                        RetryableHTTPError,
                    )
                ),
                reraise=True,
            ):
                with attempt:
                    return await request()

        except RetryableHTTPError as exc:
            logger.warning(
                "Retryable HTTP failure: %s %s",
                url,
                exc.status_code,
            )
            return None

        except (
            httpx.TimeoutException,
            httpx.NetworkError,
        ) as exc:
            logger.warning(
                "Network failure: %s: %s",
                url,
                exc,
            )
            return None

        except Exception:
            logger.exception(
                "Unexpected HTTP failure: %s",
                url,
            )
            return None


class RetryableHTTPError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


http = HTTPClient()


# ============================================================
# HELPERS
# ============================================================

USERNAME_RE = re.compile(
    r"^[A-Za-z0-9._-]{1,100}$"
)


def normalize_username(value: str) -> str:
    value = value.strip()

    if value.startswith("@"):
        value = value[1:]

    return value.strip()


def valid_username(username: str) -> bool:
    return bool(USERNAME_RE.fullmatch(username))


def encoded(username: str) -> str:
    return quote(username, safe="")


def esc(value: Any) -> str:
    if value is None:
        return "غير متوفر"

    text = str(value).strip()

    if not text:
        return "غير متوفر"

    return html.escape(text)


def number(value: Any) -> str:
    if value is None:
        return "غير متوفر"

    try:
        return f"{int(value):,}"
    except (ValueError, TypeError):
        return esc(value)


def meta(
    soup: BeautifulSoup,
    *,
    property_name: str | None = None,
    name: str | None = None,
) -> str | None:

    attrs: dict[str, str] = {}

    if property_name:
        attrs["property"] = property_name

    if name:
        attrs["name"] = name

    tag = soup.find("meta", attrs=attrs)

    if tag:
        content = tag.get("content")

        if content:
            return content.strip()

    return None


def metadata(response: httpx.Response) -> dict[str, str | None]:
    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    title = None

    if soup.title:
        title = soup.title.get_text(
            " ",
            strip=True,
        )

    return {
        "title": title,
        "description": (
            meta(
                soup,
                property_name="og:description",
            )
            or meta(
                soup,
                name="description",
            )
        ),
        "image": meta(
            soup,
            property_name="og:image",
        ),
        "url": meta(
            soup,
            property_name="og:url",
        ),
        "type": meta(
            soup,
            property_name="og:type",
        ),
    }


def obvious_not_found(text: str) -> bool:
    normalized = text.lower()

    patterns = (
        "page not found",
        "user not found",
        "profile not found",
        "account not found",
        "doesn't exist",
        "does not exist",
        "this page isn't available",
        "this page is not available",
        "404 not found",
    )

    return any(
        pattern in normalized
        for pattern in patterns
    )


def confidence_from_evidence(
    evidence: list[Evidence],
) -> int:

    if not evidence:
        return 0

    total = sum(
        item.strength
        for item in evidence
    )

    return min(total, 100)


# ============================================================
# PROVIDER BASE
# ============================================================

class Provider:
    name: str = "Unknown"
    key: str = "unknown"

    async def check(
        self,
        username: str,
    ) -> CheckResult:
        raise NotImplementedError


# ============================================================
# GITHUB PROVIDER
# ============================================================

class GitHubProvider(Provider):
    name = "GitHub"
    key = "github"

    async def check(
        self,
        username: str,
    ) -> CheckResult:

        user_url = (
            f"https://api.github.com/users/"
            f"{encoded(username)}"
        )

        response = await http.get(
            user_url,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2026-03-10",
                "User-Agent": "PublicUsernameChecker/2.0",
            },
        )

        profile_url = (
            f"https://github.com/{encoded(username)}"
        )

        if response is None:
            return CheckResult(
                platform=self.name,
                username=username,
                status=Status.UNKNOWN,
                profile_url=profile_url,
                reason="تعذر الوصول إلى GitHub API.",
            )

        if response.status_code == 404:
            return CheckResult(
                platform=self.name,
                username=username,
                status=Status.NOT_FOUND,
                profile_url=profile_url,
                confidence=95,
                evidence=[
                    Evidence(
                        "official_api",
                        "GitHub API لم يجد المستخدم.",
                        95,
                    )
                ],
            )

        if response.status_code in {403, 429}:
            return CheckResult(
                platform=self.name,
                username=username,
                status=Status.RATE_LIMITED,
                profile_url=profile_url,
                reason=(
                    "GitHub رفض الطلب أو فرض حدًا "
                    "على معدل الطلبات."
                ),
            )

        if response.status_code != 200:
            return CheckResult(
                platform=self.name,
                username=username,
                status=Status.UNKNOWN,
                profile_url=profile_url,
                reason=(
                    f"GitHub API returned "
                    f"HTTP {response.status_code}."
                ),
            )

        try:
            data = response.json()
        except ValueError:
            return CheckResult(
                platform=self.name,
                username=username,
                status=Status.UNKNOWN,
                profile_url=profile_url,
                reason="استجابة GitHub غير صالحة.",
            )

        login = data.get("login")

        if not login:
            return CheckResult(
                platform=self.name,
                username=username,
                status=Status.UNKNOWN,
                profile_url=profile_url,
                reason="لم يتم العثور على login في الاستجابة.",
            )

        evidence = [
            Evidence(
                "official_api",
                "تم العثور على الحساب عبر GitHub API.",
                70,
            ),
            Evidence(
                "exact_login",
                "اسم المستخدم مطابق للبحث.",
                30,
            ),
        ]

        repos: list[dict[str, Any]] = []

        repos_url = (
            f"https://api.github.com/users/"
            f"{encoded(username)}/repos"
        )

        repos_response = await http.get(
            repos_url,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2026-03-10",
                "User-Agent": "PublicUsernameChecker/2.0",
            },
            params={
                "per_page": 5,
                "sort": "updated",
                "direction": "desc",
            },
        )

        if (
            repos_response
            and repos_response.status_code == 200
        ):
            try:
                repos = repos_response.json()
            except ValueError:
                repos = []

        return CheckResult(
            platform=self.name,
            username=username,
            status=Status.CONFIRMED,
            profile_url=data.get(
                "html_url",
                profile_url,
            ),
            confidence=confidence_from_evidence(
                evidence
            ),
            evidence=evidence,
            data={
                "login": login,
                "name": data.get("name"),
                "bio": data.get("bio"),
                "company": data.get("company"),
                "blog": data.get("blog"),
                "followers": data.get("followers"),
                "following": data.get("following"),
                "public_repos": data.get("public_repos"),
                "public_gists": data.get("public_gists"),
                "id": data.get("id"),
                "type": data.get("type"),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "avatar_url": data.get("avatar_url"),
                "repos": repos,
            },
        )


# ============================================================
# REDDIT PROVIDER
# ============================================================

class RedditProvider(Provider):
    name = "Reddit"
    key = "reddit"

    async def check(
        self,
        username: str,
    ) -> CheckResult:

        profile_url = (
            f"https://www.reddit.com/user/"
            f"{encoded(username)}/"
        )

        api_url = (
            f"https://www.reddit.com/user/"
            f"{encoded(username)}/about.json"
        )

        response = await http.get(
            api_url,
            headers={
                "User-Agent": (
                    "PublicUsernameChecker/2.0 "
                    "by public-profile-checker"
                )
            },
        )

        if response is None:
            return CheckResult(
                platform=self.name,
                username=username,
                status=Status.UNKNOWN,
                profile_url=profile_url,
                reason="تعذر الوصول إلى Reddit.",
            )

        if response.status_code == 404:
            return CheckResult(
                platform=self.name,
                username=username,
                status=Status.NOT_FOUND,
                profile_url=profile_url,
                confidence=95,
                evidence=[
                    Evidence(
                        "official_endpoint",
                        "Reddit لم يعثر على الحساب.",
                        95,
                    )
                ],
            )

        if response.status_code in {403, 429}:
            return CheckResult(
                platform=self.name,
                username=username,
                status=Status.RATE_LIMITED,
                profile_url=profile_url,
                reason="Reddit قيّد الوصول إلى الطلب.",
            )

        if response.status_code != 200:
            return CheckResult(
                platform=self.name,
                username=username,
                status=Status.UNKNOWN,
                profile_url=profile_url,
                reason=(
                    f"Reddit returned HTTP "
                    f"{response.status_code}."
                ),
            )

        try:
            payload = response.json()
            data = payload.get("data") or {}
        except ValueError:
            data = {}

        if not data or not data.get("name"):
            return CheckResult(
                platform=self.name,
                username=username,
                status=Status.UNKNOWN,
                profile_url=profile_url,
                reason="لم نستطع إثبات الحساب من الاستجابة.",
            )

        evidence = [
            Evidence(
                "public_endpoint",
                "تم الحصول على بيانات الحساب من Reddit.",
                70,
            ),
            Evidence(
                "exact_username",
                "اسم المستخدم موجود في بيانات الحساب.",
                30,
            ),
        ]

        return CheckResult(
            platform=self.name,
            username=username,
            status=Status.CONFIRMED,
            profile_url=profile_url,
            confidence=confidence_from_evidence(
                evidence
            ),
            evidence=evidence,
            data={
                "name": data.get("name"),
                "id": data.get("id"),
                "link_karma": data.get("link_karma"),
                "comment_karma": data.get("comment_karma"),
                "total_karma": data.get("total_karma"),
                "created_utc": data.get("created_utc"),
                "is_mod": data.get("is_mod"),
                "icon_img": data.get("icon_img"),
            },
        )


# ============================================================
# HTML PROVIDER
# ============================================================

class HTMLProvider(Provider):
    url_template: str = ""

    async def check(
        self,
        username: str,
    ) -> CheckResult:

        url = self.url_template.format(
            encoded(username)
        )

        response = await http.get(url)

        if response is None:
            return CheckResult(
                platform=self.name,
                username=username,
                status=Status.UNKNOWN,
                profile_url=url,
                reason="تعذر الوصول إلى الصفحة.",
            )

        if response.status_code == 404:
            return CheckResult(
                platform=self.name,
                username=username,
                status=Status.NOT_FOUND,
                profile_url=url,
                confidence=90,
                evidence=[
                    Evidence(
                        "http_404",
                        "الخادم أعاد HTTP 404.",
                        90,
                    )
                ],
            )

        if response.status_code in {403, 429}:
            return CheckResult(
                platform=self.name,
                username=username,
                status=Status.RATE_LIMITED,
                profile_url=url,
                reason="المنصة قيّدت الوصول.",
            )

        if response.status_code != 200:
            return CheckResult(
                platform=self.name,
                username=username,
                status=Status.UNKNOWN,
                profile_url=url,
                reason=(
                    f"HTTP {response.status_code}"
                ),
            )

        page = metadata(response)

        combined = " ".join(
            value
            for value in page.values()
            if value
        )

        if obvious_not_found(combined):
            return CheckResult(
                platform=self.name,
                username=username,
                status=Status.NOT_FOUND,
                profile_url=url,
                confidence=80,
                evidence=[
                    Evidence(
                        "not_found_marker",
                        "الصفحة تحتوي على مؤشر واضح لعدم وجود الحساب.",
                        80,
                    )
                ],
            )

        # لا نعتبر مجرد HTTP 200 وجودًا للحساب.
        if not page["title"] and not page["description"]:
            return CheckResult(
                platform=self.name,
                username=username,
                status=Status.UNKNOWN,
                profile_url=url,
                reason=(
                    "الصفحة استجابت لكن لم توجد "
                    "أدلة عامة كافية لإثبات الحساب."
                ),
            )

        return CheckResult(
            platform=self.name,
            username=username,
            status=Status.UNKNOWN,
            profile_url=url,
            confidence=0,
            evidence=[],
            data={
                "title": page["title"],
                "description": page["description"],
                "image": page["image"],
                "canonical": page["url"],
            },
            reason=(
                "وجدت استجابة وmetadata، "
                "لكنها غير كافية لإثبات ملكية/وجود الحساب."
            ),
        )


# ============================================================
# PLATFORM PROVIDERS
# ============================================================

class InstagramProvider(HTMLProvider):
    name = "Instagram"
    key = "instagram"
    url_template = "https://www.instagram.com/{}/"


class TikTokProvider(HTMLProvider):
    name = "TikTok"
    key = "tiktok"
    url_template = "https://www.tiktok.com/@{}"


class SnapchatProvider(HTMLProvider):
    name = "Snapchat"
    key = "snapchat"
    url_template = "https://www.snapchat.com/add/{}"


class XProvider(HTMLProvider):
    name = "Twitter / X"
    key = "twitter"
    url_template = "https://x.com/{}"


class PinterestProvider(HTMLProvider):
    name = "Pinterest"
    key = "pinterest"
    url_template = "https://www.pinterest.com/{}/"


class TwitchProvider(HTMLProvider):
    name = "Twitch"
    key = "twitch"
    url_template = "https://www.twitch.tv/{}"


# ============================================================
# PROVIDER REGISTRY
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
# CHECK ENGINE
# ============================================================

class CheckEngine:
    def __init__(
        self,
        providers: list[Provider],
        concurrency: int,
    ) -> None:

        self.providers = providers
        self.semaphore = asyncio.Semaphore(
            concurrency
        )

    async def _run_provider(
        self,
        provider: Provider,
        username: str,
    ) -> CheckResult:

        async with self.semaphore:

            try:
                return await provider.check(
                    username
                )

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                logger.exception(
                    "Provider failed: %s",
                    provider.name,
                )

                return CheckResult(
                    platform=provider.name,
                    username=username,
                    status=Status.ERROR,
                    profile_url="",
                    reason=(
                        "حدث خطأ داخلي أثناء الفحص."
                    ),
                )

    async def check(
        self,
        username: str,
    ) -> list[CheckResult]:

        cached = await cache.get(username)

        if cached is not None:
            logger.info(
                "Cache hit: %s",
                username,
            )
            return cached

        tasks = [
            asyncio.create_task(
                self._run_provider(
                    provider,
                    username,
                )
            )
            for provider in self.providers
        ]

        results = await asyncio.gather(
            *tasks
        )

        await cache.set(
            username,
            results,
        )

        return results


engine = CheckEngine(
    PROVIDERS,
    settings.max_concurrency,
)


# ============================================================
# TELEGRAM FORMATTER
# ============================================================

def status_label(result: CheckResult) -> str:
    labels = {
        Status.CONFIRMED: "مؤكد",
        Status.NOT_FOUND: "غير موجود",
        Status.UNKNOWN: "غير مؤكد",
        Status.RATE_LIMITED: "مقيّد مؤقتًا",
        Status.ERROR: "خطأ",
    }

    return labels[result.status]


def summary_text(
    username: str,
    results: list[CheckResult],
) -> str:

    counts = {
        Status.CONFIRMED: 0,
        Status.NOT_FOUND: 0,
        Status.UNKNOWN: 0,
        Status.RATE_LIMITED: 0,
        Status.ERROR: 0,
    }

    for result in results:
        counts[result.status] += 1

    lines = [
        "🔎 <b>PUBLIC USERNAME SEARCH</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"👤 Username: <code>{esc(username)}</code>",
        f"📏 Length: {len(username)}",
        "",
        "📊 <b>RESULTS</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🟢 Confirmed: {counts[Status.CONFIRMED]}",
        f"🔴 Not found: {counts[Status.NOT_FOUND]}",
        f"🟠 Unknown: {counts[Status.UNKNOWN]}",
        f"🟡 Rate limited: {counts[Status.RATE_LIMITED]}",
        f"⚫ Error: {counts[Status.ERROR]}",
        "",
    ]

    for result in results:
        lines.append(
            f"{result.icon} "
            f"<b>{esc(result.platform)}</b> — "
            f"{status_label(result)}"
        )

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "اضغط على المنصة لعرض الأدلة والتفاصيل.",
    ])

    return "\n".join(lines)


def detail_text(
    result: CheckResult,
) -> str:

    lines = [
        f"{result.icon} <b>{esc(result.platform)}</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"👤 Username: <code>{esc(result.username)}</code>",
        f"📊 Status: <b>{status_label(result)}</b>",
        f"🎯 Confidence: {result.confidence}%",
        f"🔗 Profile: {esc(result.profile_url)}",
        "",
    ]

    if result.reason:
        lines.extend([
            "ℹ️ <b>Reason</b>",
            esc(result.reason),
            "",
        ])

    if result.evidence:
        lines.extend([
            "🧾 <b>Evidence</b>",
            "",
        ])

        for item in result.evidence:
            lines.append(
                f"✓ {esc(item.description)} "
                f"({item.strength}%)"
            )

        lines.append("")

    data = result.data

    if data:
        lines.extend([
            "📋 <b>Public Data</b>",
            "",
        ])

        fields = (
            ("login", "👤 Login"),
            ("name", "📛 Name"),
            ("bio", "📝 Bio"),
            ("company", "🏢 Company"),
            ("blog", "🌐 Website"),
            ("followers", "👥 Followers"),
            ("following", "👤 Following"),
            ("public_repos", "📦 Public repositories"),
            ("public_gists", "📝 Public gists"),
            ("id", "🆔 ID"),
            ("type", "🏷️ Type"),
            ("link_karma", "🏆 Link Karma"),
            ("comment_karma", "💬 Comment Karma"),
            ("total_karma", "⭐ Total Karma"),
            ("created_utc", "📅 Created"),
        )

        for key, label in fields:
            if key not in data:
                continue

            current = data[key]

            if current is None or current == "":
                continue

            if isinstance(
                current,
                (dict, list),
            ):
                continue

            if key.endswith("karma") or key in {
                "followers",
                "following",
                "public_repos",
                "public_gists",
            }:
                current = number(current)

            lines.append(
                f"{label}: {esc(current)}"
            )

        repos = data.get("repos")

        if isinstance(repos, list) and repos:
            lines.extend([
                "",
                "📚 <b>Latest Public Repositories</b>",
            ])

            for index, repo in enumerate(
                repos[:5],
                start=1,
            ):
                if not isinstance(repo, dict):
                    continue

                name = esc(
                    repo.get(
                        "name",
                        "غير متوفر",
                    )
                )

                url = esc(
                    repo.get(
                        "html_url",
                        "",
                    )
                )

                lines.append(
                    f"{index}. 📦 "
                    f"<a href=\"{url}\">{name}</a>"
                )

    return "\n".join(lines)


def keyboard(
    results: list[CheckResult],
) -> InlineKeyboardMarkup:

    buttons: list[
        list[InlineKeyboardButton]
    ] = []

    for result in results:
        buttons.append([
            InlineKeyboardButton(
                text=(
                    f"{result.icon} "
                    f"{result.platform}"
                ),
                callback_data=(
                    f"result:{result.platform}"
                ),
            )
        ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


# ============================================================
# TELEGRAM STATE
# ============================================================

USER_RESULTS: dict[
    int,
    dict[str, CheckResult],
] = {}


# ============================================================
# BOT
# ============================================================

bot = Bot(
    token=settings.bot_token,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML,
    ),
)

dp = Dispatcher()


# ============================================================
# /START
# ============================================================

@dp.message(CommandStart())
async def start_handler(
    message: Message,
) -> None:

    await message.answer(
        "🤖 <b>PUBLIC USERNAME CHECKER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل Username لفحص المنصات العامة.\n\n"
        "مثال:\n"
        "<code>osvo4</code>\n\n"
        "أو:\n"
        "<code>@osvo4</code>\n\n"
        "🟢 مؤكد = توجد أدلة مباشرة.\n"
        "🔴 غير موجود = توجد إشارة قوية لعدم وجود الحساب.\n"
        "🟠 غير مؤكد = لم تتوفر أدلة كافية.\n"
        "🟡 مقيّد = المنصة حدّت الوصول مؤقتًا."
    )


# ============================================================
# SEARCH
# ============================================================

@dp.message(F.text)
async def search_handler(
    message: Message,
) -> None:

    raw = message.text or ""
    username = normalize_username(raw)

    if not username:
        await message.answer(
            "❌ أرسل Username."
        )
        return

    if len(username) > settings.max_username_length:
        await message.answer(
            "❌ Username طويل جدًا."
        )
        return

    if not valid_username(username):
        await message.answer(
            "❌ Username يحتوي على رموز غير مدعومة."
        )
        return

    waiting = await message.answer(
        "🔎 <b>جاري الفحص...</b>\n"
        "قد يستغرق الأمر عدة ثوانٍ."
    )

    results = await engine.check(
        username
    )

    USER_RESULTS[message.chat.id] = {
        result.platform: result
        for result in results
    }

    try:
        await waiting.delete()
    except Exception:
        pass

    await message.answer(
        summary_text(
            username,
            results,
        ),
        reply_markup=keyboard(results),
    )


# ============================================================
# CALLBACK
# ============================================================

@dp.callback_query(
    F.data.startswith("result:")
)
async def result_callback(
    callback: CallbackQuery,
) -> None:

    if not callback.message:
        await callback.answer()
        return

    platform = (
        callback.data
        .split(":", 1)[1]
    )

    user_results = USER_RESULTS.get(
        callback.message.chat.id,
        {},
    )

    result = user_results.get(
        platform
    )

    if not result:
        await callback.answer(
            "❌ انتهت نتيجة البحث.",
            show_alert=True,
        )
        return

    await callback.answer()

    text = detail_text(result)

    # Telegram message limit safety.
    if len(text) <= 3900:
        await callback.message.answer(
            text
        )
        return

    for start in range(
        0,
        len(text),
        3900,
    ):
        await callback.message.answer(
            text[start:start + 3900]
        )


# ============================================================
# SHUTDOWN
# ============================================================

async def shutdown() -> None:
    logger.info(
        "Shutting down..."
    )

    await http.close()
    await bot.session.close()


# ============================================================
# MAIN
# ============================================================

async def main() -> None:

    logger.info(
        "Starting Public Username Checker..."
    )

    try:
        await dp.start_polling(
            bot
        )

    finally:
        await shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
