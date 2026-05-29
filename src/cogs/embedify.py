import html
import asyncio
import io
import json
import os
import re
import tempfile
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

import discord
from discord.ext import commands
import httpx


GUILD_ID = os.getenv("GUILD_ID")
GUILD_IDS = [int(GUILD_ID)] if GUILD_ID else None

HTTP_TIMEOUT = 10
MAX_UPLOAD_BYTES = int(os.getenv("EMBEDIFY_MAX_UPLOAD_MB", "24")) * 1024 * 1024
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


@dataclass
class EmbedMetadata:
    platform: str
    url: str
    title: str
    description: str | None = None
    author: str | None = None
    author_url: str | None = None
    image_url: str | None = None
    thumbnail_url: str | None = None
    media_url: str | None = None
    media_kind: str | None = None
    media_ext: str | None = None
    media_size: int | None = None
    media_headers: dict[str, str] | None = None
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
    text = normalize_whitespace(value)
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
            if platform == "X" and "/status/" not in parsed.path:
                return None, "Please provide a direct X/Twitter status URL."
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
    media_size = media.get("filesize") or media.get("filesize_approx") if media else None
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
        media_ext=media_ext,
        media_size=media_size,
        media_headers=info.get("http_headers"),
        footer=f"{platform} preview",
    )


async def extract_with_ytdlp(platform: str, url: str) -> EmbedMetadata | None:
    try:
        return await asyncio.to_thread(metadata_from_ytdlp, platform, url)
    except Exception:
        return None


def download_media_with_ytdlp(metadata: EmbedMetadata) -> tuple[bytes, str] | None:
    try:
        import yt_dlp
    except ImportError:
        return None

    with tempfile.TemporaryDirectory(prefix="lantern-embedify-") as tmp:
        options = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "cachedir": False,
            "logger": YtDlpLogger(),
            "max_filesize": MAX_UPLOAD_BYTES,
            "outtmpl": os.path.join(tmp, "%(id)s.%(ext)s"),
        }

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.extract_info(metadata.url, download=True)
        except Exception:
            return None

        files = [
            os.path.join(tmp, filename)
            for filename in os.listdir(tmp)
            if os.path.isfile(os.path.join(tmp, filename))
        ]
        if not files:
            return None

        path = max(files, key=os.path.getsize)
        if os.path.getsize(path) > MAX_UPLOAD_BYTES:
            return None

        with open(path, "rb") as file:
            return file.read(), os.path.basename(path)


async def download_media_with_httpx(metadata: EmbedMetadata) -> discord.File | None:
    if not metadata.media_url:
        return None
    if metadata.media_size and metadata.media_size > MAX_UPLOAD_BYTES:
        return None

    headers = {
        "User-Agent": USER_AGENT,
        **(metadata.media_headers or {}),
    }

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers=headers) as client:
            async with client.stream("GET", metadata.media_url) as response:
                response.raise_for_status()

                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > MAX_UPLOAD_BYTES:
                    return None

                buffer = io.BytesIO()
                async for chunk in response.aiter_bytes():
                    buffer.write(chunk)
                    if buffer.tell() > MAX_UPLOAD_BYTES:
                        return None
    except (httpx.HTTPError, ValueError):
        return None

    buffer.seek(0)
    return discord.File(buffer, filename=media_filename(metadata))


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

    if tweet_id:
        try:
            syndication_data = await fetch_json(
                client,
                "https://cdn.syndication.twimg.com/tweet-result?"
                f"{urlencode({'id': tweet_id, 'lang': 'en'})}",
            )
        except (httpx.HTTPError, TypeError, ValueError, json.JSONDecodeError):
            syndication_data = None

    if syndication_data:
        user = syndication_data.get("user") or {}
        media = syndication_data.get("mediaDetails") or []
        photo = next(
            (
                item
                for item in media
                if item.get("type") == "photo" and item.get("media_url_https")
            ),
            None,
        )
        video = next(
            (
                item
                for item in media
                if item.get("type") in {"video", "animated_gif"}
                and item.get("media_url_https")
            ),
            None,
        )

        return EmbedMetadata(
            platform="X",
            url=url,
            title=user.get("name") or "X post",
            description=syndication_data.get("text"),
            author=user.get("screen_name") and f"@{user['screen_name']}",
            author_url=user.get("screen_name")
            and f"https://x.com/{user['screen_name']}",
            image_url=valid_media_url(
                (photo or video or {}).get("media_url_https")
            ),
            media_url=valid_media_url((photo or video or {}).get("media_url_https")),
            media_kind="image",
            footer="X/Twitter preview",
        )

    params = urlencode({"url": url, "omit_script": "true", "dnt": "true"})
    data = await fetch_json(client, f"https://publish.twitter.com/oembed?{params}")

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

    return EmbedMetadata(
        platform=platform,
        url=url,
        title=f"{platform} post",
        description="Lantern could not fetch rich metadata for this link, but the source is attached below.",
        footer=f"{platform} preview",
    )


async def fetch_metadata(platform: str, url: str) -> EmbedMetadata:
    extracted = await extract_with_ytdlp(platform, url)
    if extracted and (extracted.media_url or extracted.description or extracted.thumbnail_url):
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


def media_filename(metadata: EmbedMetadata) -> str:
    ext = (metadata.media_ext or "").lower()
    if not ext:
        ext = "jpg" if metadata.media_kind == "image" else "mp4"
    if ext == "jpeg":
        ext = "jpg"
    return f"{metadata.platform.lower()}-preview.{ext}"


async def downloadable_media_file(metadata: EmbedMetadata) -> discord.File | None:
    if not metadata.media_url or metadata.media_kind != "video":
        return None

    downloaded = await asyncio.to_thread(download_media_with_ytdlp, metadata)
    if downloaded:
        data, filename = downloaded
        return discord.File(io.BytesIO(data), filename=filename)

    return await download_media_with_httpx(metadata)


def build_embed(metadata: EmbedMetadata) -> discord.Embed:
    embed = discord.Embed(
        title=truncate(metadata.title, 256) or f"{metadata.platform} post",
        url=metadata.url,
        description=truncate(metadata.description, 4096),
        color=PLATFORM_COLORS.get(metadata.platform, discord.Color.blurple().value),
    )

    if metadata.author:
        embed.set_author(
            name=truncate(metadata.author, 256) or metadata.author,
            url=metadata.author_url,
        )

    image_url = valid_media_url(metadata.image_url)
    thumbnail_url = valid_media_url(metadata.thumbnail_url)

    if image_url:
        embed.set_image(url=image_url)
    elif thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)

    if metadata.footer:
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

        metadata = await fetch_metadata(platform, url)
        embed = build_embed(metadata)
        source_lines = [f"Source: <{url}>"]
        file = await downloadable_media_file(metadata)

        if metadata.media_url and not file and metadata.media_kind == "video":
            source_lines.append(f"Media: {metadata.media_url}")

        kwargs = {"content": "\n".join(source_lines), "embed": embed}
        if file:
            kwargs["file"] = file

        await ctx.send_followup(**kwargs)


def setup(bot):
    bot.add_cog(Embedify(bot))
