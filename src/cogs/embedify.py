import html
import asyncio
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

import discord
from discord.ext import commands
import httpx


GUILD_ID = os.getenv("GUILD_ID")
GUILD_IDS = [int(GUILD_ID)] if GUILD_ID else None

HTTP_TIMEOUT = 10
YTDLP_TIMEOUT = 20
USER_AGENT = (
    "LanternBot/5.1 (+https://github.com/tastykiwi/lantern-bot; Discord bot link preview)"
)

PLATFORM_COLORS = {
    "X": 0x000000,
    "Reddit": 0xFF4500,
    "TikTok": 0xFE2C55,
    "Instagram": 0xE1306C,
}

SUPPORTED_HOSTS = {
    "X": {"x.com", "www.x.com", "twitter.com", "mobile.twitter.com"},
    "Reddit": {"reddit.com", "www.reddit.com", "old.reddit.com", "redd.it"},
    "TikTok": {
        "tiktok.com",
        "www.tiktok.com",
        "m.tiktok.com",
        "vm.tiktok.com",
        "vt.tiktok.com",
    },
    "Instagram": {"instagram.com", "www.instagram.com"},
}


class TweetNotFound(Exception):
    pass


@dataclass
class EmbedMetadata:
    platform: str
    url: str
    title: str | None = None
    description: str | None = None
    author: str | None = None
    author_url: str | None = None
    author_icon_url: str | None = None
    image_url: str | None = None
    thumbnail_url: str | None = None
    media_url: str | None = None
    media_kind: str | None = None
    timestamp: datetime | None = None
    footer: str | None = None


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str):
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self) -> str:
        return normalize_whitespace(" ".join(self.parts))


class MetaTagParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag.lower() != "meta":
            return

        attr_map = {name.lower(): value for name, value in attrs if value}
        key = attr_map.get("property") or attr_map.get("name")
        content = attr_map.get("content")

        if key and content:
            self.meta[key.lower()] = html.unescape(content)


class YtDlpLogger:
    def debug(self, message: str):
        pass

    def warning(self, message: str):
        pass

    def error(self, message: str):
        pass


def normalize_whitespace(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    parser = TextExtractor()
    parser.feed(value)
    return parser.text()


def truncate(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = html.unescape(value).strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def valid_media_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(html.unescape(value))
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return urlunparse(parsed)
    return None


def classify_url(url: str) -> tuple[str | None, str | None]:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None, "Please provide a valid `http` or `https` URL."

    host = parsed.netloc.lower()
    for platform, hosts in SUPPORTED_HOSTS.items():
        if host in hosts:
            if platform == "X":
                if "/status/" not in parsed.path:
                    return None, "Please provide a direct X/Twitter status URL."
                tweet_id = extract_tweet_id(url)
                if not tweet_id or not (1 <= len(tweet_id) <= 19):
                    return None, "That doesn't look like a valid X/Twitter status URL."
            if (
                platform == "Reddit"
                and host != "redd.it"
                and "/comments/" not in parsed.path
            ):
                return None, "Please provide a direct Reddit post URL."
            if (
                platform == "TikTok"
                and host not in {"vm.tiktok.com", "vt.tiktok.com"}
                and "/video/" not in parsed.path
                and "/photo/" not in parsed.path
                and not parsed.path.startswith("/t/")
            ):
                return None, "Please provide a direct TikTok video or photo URL."
            if platform == "Instagram" and not re.match(
                r"^/(p|reel|tv)/", parsed.path
            ):
                return None, "Please provide a direct Instagram post, reel, or TV URL."
            return platform, None

    return None, "Supported platforms are X/Twitter, Reddit, TikTok, and Instagram."


def canonical_url(url: str) -> str:
    parsed = urlparse(url.strip())
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc.lower(),
            parsed.path,
            "",
            parsed.query,
            "",
        )
    )


def extract_tweet_id(url: str) -> str | None:
    match = re.search(r"/status/(\d+)", urlparse(url).path)
    return match.group(1) if match else None


def reddit_json_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") + ".json"
    return urlunparse(
        (parsed.scheme, parsed.netloc, path, "", urlencode({"raw_json": 1}), "")
    )


def parse_meta_tags(body: str) -> dict[str, str]:
    parser = MetaTagParser()
    parser.feed(body)
    return parser.meta


def metadata_from_open_graph(platform: str, url: str, body: str) -> EmbedMetadata:
    meta = parse_meta_tags(body)
    title = (
        meta.get("og:title")
        or meta.get("twitter:title")
        or f"{platform} post"
    )
    description = meta.get("og:description") or meta.get("twitter:description")
    image_url = valid_media_url(meta.get("og:image") or meta.get("twitter:image"))

    return EmbedMetadata(
        platform=platform,
        url=url,
        title=title,
        description=description,
        image_url=image_url,
        footer=f"{platform} preview",
    )


def flatten_info(info: dict[str, Any]) -> dict[str, Any]:
    if info.get("_type") in {"playlist", "multi_video"} and info.get("entries"):
        entries = [entry for entry in info["entries"] if entry]
        if entries:
            return entries[0]
    return info


def media_kind_from_url(url: str | None, ext: str | None) -> str | None:
    ext = (ext or "").lower()
    path = urlparse(url or "").path.lower()
    image_exts = {"jpg", "jpeg", "png", "webp", "gif"}

    if ext in image_exts or any(path.endswith(f".{item}") for item in image_exts):
        return "image"
    if ext or path:
        return "video"
    return None


def select_media(info: dict[str, Any]) -> dict[str, Any] | None:
    formats = info.get("formats") or []
    merged_videos = [
        item
        for item in formats
        if item.get("url")
        and item.get("vcodec") != "none"
        and item.get("acodec") != "none"
        and (item.get("protocol") or "").startswith(("http", "https"))
    ]
    if merged_videos:
        return max(
            merged_videos,
            key=lambda item: (
                item.get("height") or 0,
                -(item.get("filesize") or item.get("filesize_approx") or 0),
            ),
        )

    media_url = valid_media_url(info.get("url"))
    if media_url:
        return {
            "url": media_url,
            "ext": info.get("ext"),
            "filesize": info.get("filesize") or info.get("filesize_approx"),
        }

    return None


def metadata_from_ytdlp(platform: str, url: str) -> EmbedMetadata | None:
    try:
        import yt_dlp
    except ImportError:
        return None

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "cachedir": False,
        "logger": YtDlpLogger(),
        "http_headers": {"User-Agent": USER_AGENT},
        "extractor_args": {
            "twitter": {"force_graphql": ["true"]},
        },
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info = flatten_info(ydl.extract_info(url, download=False))

    media = select_media(info)
    media_url = valid_media_url(media.get("url")) if media else None
    media_ext = media.get("ext") if media else None
    media_kind = media_kind_from_url(media_url, media_ext)
    thumbnail_url = valid_media_url(info.get("thumbnail"))

    return EmbedMetadata(
        platform=platform,
        url=info.get("webpage_url") or url,
        title=info.get("title") or f"{platform} post",
        description=info.get("description"),
        author=info.get("uploader") or info.get("channel"),
        author_url=info.get("uploader_url") or info.get("channel_url"),
        image_url=media_url if media_kind == "image" else None,
        thumbnail_url=thumbnail_url,
        media_url=media_url,
        media_kind=media_kind,
        footer=f"{platform} preview",
    )


async def extract_with_ytdlp(platform: str, url: str) -> EmbedMetadata | None:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(metadata_from_ytdlp, platform, url),
            timeout=YTDLP_TIMEOUT,
        )
    except (asyncio.TimeoutError, Exception):
        return None


async def fetch_json(client: httpx.AsyncClient, url: str) -> Any:
    response = await client.get(url)
    response.raise_for_status()
    return response.json()


async def fetch_text(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(url)
    response.raise_for_status()
    return response.text


async def fetch_x_metadata(client: httpx.AsyncClient, url: str) -> EmbedMetadata:
    tweet_id = extract_tweet_id(url)
    syndication_data = None
    not_found = False

    if tweet_id:
        try:
            syndication_data = await fetch_json(
                client,
                "https://cdn.syndication.twimg.com/tweet-result?"
                f"{urlencode({'id': tweet_id, 'lang': 'en'})}",
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                not_found = True
            syndication_data = None
        except (httpx.HTTPError, TypeError, ValueError, json.JSONDecodeError):
            syndication_data = None

    if syndication_data:
        user = syndication_data.get("user") or {}
        media_details = syndication_data.get("mediaDetails") or []

        video_entry = next(
            (
                item
                for item in media_details
                if item.get("type") in {"video", "animated_gif"}
            ),
            None,
        )
        photo_entry = next(
            (
                item
                for item in media_details
                if item.get("type") == "photo" and item.get("media_url_https")
            ),
            None,
        )

        author_name = (user.get("name") or "").strip()
        screen_name = (user.get("screen_name") or "").strip()
        if author_name and screen_name:
            author = f"{author_name} (@{screen_name})"
        else:
            author = author_name or None
        author_url = (
            f"https://x.com/{screen_name}" if screen_name else None
        )
        author_icon_url = valid_media_url(
            user.get("profile_image_url_https")
        )

        image_url = None
        media_url = None
        media_kind = None
        thumbnail_url = None

        if video_entry:
            variants = (
                video_entry.get("video_info", {}).get("variants", []) or []
            )
            mp4_variants = [
                variant
                for variant in variants
                if variant.get("content_type") == "video/mp4"
                and variant.get("url")
            ]
            if mp4_variants:
                best = max(
                    mp4_variants, key=lambda variant: variant.get("bitrate") or 0
                )
                media_url = valid_media_url(best.get("url"))
                if media_url:
                    media_kind = "video"
            thumbnail_url = valid_media_url(video_entry.get("media_url_https"))
        elif photo_entry:
            image_url = valid_media_url(photo_entry.get("media_url_https"))
            if image_url:
                media_kind = "image"

        timestamp = None
        created_at = syndication_data.get("created_at")
        if created_at:
            try:
                timestamp = datetime.strptime(
                    created_at, "%a %b %d %H:%M:%S %z %Y"
                )
            except (TypeError, ValueError):
                timestamp = None

        text = syndication_data.get("text")
        description = html.unescape(text).strip() if text else None

        return EmbedMetadata(
            platform="X",
            url=url,
            title=None,
            description=description,
            author=author,
            author_url=author_url,
            author_icon_url=author_icon_url,
            image_url=image_url,
            thumbnail_url=thumbnail_url,
            media_url=media_url,
            media_kind=media_kind,
            timestamp=timestamp,
            footer="X preview",
        )

    params = urlencode({"url": url, "omit_script": "true", "dnt": "true"})
    try:
        data = await fetch_json(
            client, f"https://publish.twitter.com/oembed?{params}"
        )
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 404:
            not_found = True
        data = None
    except (httpx.HTTPError, TypeError, ValueError, json.JSONDecodeError):
        data = None

    if not_found:
        raise TweetNotFound(url)

    if data is None:
        raise httpx.HTTPError("X oembed returned no data")

    title = strip_html(data.get("html")) or "X post"
    author = data.get("author_name")

    return EmbedMetadata(
        platform="X",
        url=url,
        title=title,
        author=author,
        author_url=data.get("author_url"),
        footer="X/Twitter preview",
    )


async def fetch_reddit_metadata(client: httpx.AsyncClient, url: str) -> EmbedMetadata:
    data = await fetch_json(client, reddit_json_url(url))
    post = data[0]["data"]["children"][0]["data"]

    title = post.get("title") or "Reddit post"
    description = post.get("selftext") or post.get("url_overridden_by_dest")
    subreddit = post.get("subreddit_name_prefixed")
    author = post.get("author")
    image_url = None
    thumbnail_url = None

    preview_images = (
        post.get("preview", {}).get("images", [{}])[0]
        .get("source", {})
        .get("url")
    )
    if preview_images:
        image_url = valid_media_url(preview_images)

    thumbnail = post.get("thumbnail")
    if thumbnail not in {None, "", "self", "default", "nsfw", "spoiler"}:
        thumbnail_url = valid_media_url(thumbnail)

    return EmbedMetadata(
        platform="Reddit",
        url=url,
        title=title,
        description=description,
        author=f"u/{author}" if author else None,
        image_url=image_url,
        thumbnail_url=thumbnail_url,
        footer=subreddit or "Reddit preview",
    )


async def fetch_tiktok_metadata(client: httpx.AsyncClient, url: str) -> EmbedMetadata:
    response = await client.get(url)
    response.raise_for_status()
    resolved_url = str(response.url)
    data = await fetch_json(
        client,
        f"https://www.tiktok.com/oembed?{urlencode({'url': resolved_url})}",
    )

    return EmbedMetadata(
        platform="TikTok",
        url=resolved_url,
        title=data.get("title") or "TikTok video",
        author=data.get("author_name"),
        author_url=data.get("author_url"),
        thumbnail_url=valid_media_url(data.get("thumbnail_url")),
        footer="TikTok preview",
    )


async def fetch_instagram_metadata(client: httpx.AsyncClient, url: str) -> EmbedMetadata:
    body = await fetch_text(client, url)
    return metadata_from_open_graph("Instagram", url, body)


async def fetch_fallback_metadata(
    client: httpx.AsyncClient,
    platform: str,
    url: str,
) -> EmbedMetadata:
    try:
        body = await fetch_text(client, url)
        metadata = metadata_from_open_graph(platform, url, body)
        if metadata.description or metadata.image_url:
            return metadata
    except httpx.HTTPError:
        pass

    description = "Lantern could not fetch rich metadata for this link. The original URL is included below."
    if platform == "TikTok":
        description += (
            " Tip: TikTok often blocks bot access — try a direct "
            "tiktok.com/.../video/<id> link."
        )

    return EmbedMetadata(
        platform=platform,
        url=url,
        title=f"{platform} post",
        description=description,
    )


async def fetch_metadata(platform: str, url: str) -> EmbedMetadata:
    if platform != "X":
        extracted = await extract_with_ytdlp(platform, url)
        if extracted and (
            extracted.media_url or extracted.description or extracted.thumbnail_url
        ):
            return extracted

    headers = {
        "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        "User-Agent": USER_AGENT,
    }

    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
        headers=headers,
    ) as client:
        try:
            if platform == "X":
                return await fetch_x_metadata(client, url)
            if platform == "Reddit":
                return await fetch_reddit_metadata(client, url)
            if platform == "TikTok":
                return await fetch_tiktok_metadata(client, url)
            if platform == "Instagram":
                return await fetch_instagram_metadata(client, url)
        except TweetNotFound:
            raise
        except (
            httpx.HTTPError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            return await fetch_fallback_metadata(client, platform, url)

    return EmbedMetadata(
        platform=platform,
        url=url,
        title=f"{platform} post",
    )


def build_embed(metadata: EmbedMetadata) -> discord.Embed:
    title = truncate(metadata.title, 256) if metadata.title else None
    embed = discord.Embed(
        title=title,
        url=metadata.url,
        description=truncate(metadata.description, 4096),
        color=PLATFORM_COLORS.get(metadata.platform, discord.Color.blurple().value),
        timestamp=metadata.timestamp,
    )

    if metadata.author:
        embed.set_author(
            name=truncate(metadata.author, 256) or metadata.author,
            url=metadata.author_url,
            icon_url=metadata.author_icon_url,
        )

    image_url = valid_media_url(metadata.image_url)
    thumbnail_url = valid_media_url(metadata.thumbnail_url)
    media_url = valid_media_url(metadata.media_url)

    if image_url:
        embed.set_image(url=image_url)
    elif metadata.media_kind == "video" and media_url:
        embed._video = {"url": media_url}
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
    elif thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)

    has_rich_content = bool(
        image_url
        or (metadata.media_kind == "video" and media_url)
        or thumbnail_url
    )
    if metadata.footer and has_rich_content:
        embed.set_footer(text=truncate(metadata.footer, 2048) or metadata.platform)

    return embed


class Embedify(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.slash_command(
        name="embedify",
        description="Turn a social media link into a cleaner Discord embed.",
        guild_ids=GUILD_IDS,
    )
    async def embedify(
        self,
        ctx: discord.ApplicationContext,
        link: str = discord.Option(
            str,
            description="A public X, Reddit, TikTok, or Instagram post link.",
            required=True,
        ),
    ):
        url = canonical_url(link)
        platform, error = classify_url(url)

        if error or not platform:
            await ctx.send_response(error, ephemeral=True)
            return

        await ctx.defer()

        try:
            metadata = await fetch_metadata(platform, url)
        except TweetNotFound:
            await ctx.send_followup(
                "That X/Twitter post wasn't found — it may be private or deleted."
            )
            return
        except Exception:
            message = "Something went wrong fetching that link — please try again."
            if platform == "TikTok":
                message += (
                    " Tip: TikTok often blocks bot access — try a direct "
                    "tiktok.com/.../video/<id> link."
                )
            await ctx.send_followup(message)
            return

        embed = build_embed(metadata)
        source_lines = [f"Source: <{url}>"]

        await ctx.send_followup(content="\n".join(source_lines), embed=embed)


def setup(bot):
    bot.add_cog(Embedify(bot))
