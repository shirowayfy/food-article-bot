from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field

import aiohttp

logger = logging.getLogger(__name__)

TELEGRAPH_API = "https://api.telegra.ph"
IMGBB_UPLOAD = "https://api.imgbb.com/1/upload"
IMGBB_EXPIRATION = 2_592_000  # 30 days in seconds

BLACK_SQUARE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC"
)


@dataclass
class TelegraphClient:
    imgbb_api_key: str = ""
    author_name: str = "Food Diary"
    author_url: str = ""
    _access_token: str | None = field(default=None, repr=False)
    _session: aiohttp.ClientSession | None = field(default=None, repr=False)
    _placeholder_url: str | None = field(default=None, repr=False)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def ensure_account(self) -> str:
        """Create a Telegraph account if we don't have a token, return token."""
        if self._access_token:
            return self._access_token

        session = await self._get_session()
        async with session.post(
            f"{TELEGRAPH_API}/createAccount",
            data={
                "short_name": self.author_name[:32],
                "author_name": self.author_name[:128],
            },
        ) as resp:
            data = await resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Failed to create Telegraph account: {data}")
            self._access_token = data["result"]["access_token"]
            logger.info("Created Telegraph account: %s", data["result"]["short_name"])
            return self._access_token  # type: ignore[return-value]

    async def upload_image(self, image_bytes: bytes, filename: str = "photo.jpg") -> str:
        """Upload image to imgbb, return the direct display URL."""
        session = await self._get_session()

        b64 = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "key": self.imgbb_api_key,
            "image": b64,
            "expiration": str(IMGBB_EXPIRATION),
        }

        async with session.post(IMGBB_UPLOAD, data=payload) as resp:
            raw = await resp.text()
            logger.debug("imgbb upload response (%s): %s", resp.status, raw[:500])
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                raise RuntimeError(f"imgbb returned non-JSON: {raw[:200]}")

        if data.get("success") and "data" in data:
            url = data["data"]["display_url"]
            logger.info("Uploaded image to imgbb: %s", url)
            return url

        logger.error("imgbb upload failed. Size=%d, response=%s", len(image_bytes), data)
        raise RuntimeError(f"Failed to upload image to imgbb: {data}")

    async def get_placeholder_url(self) -> str:
        if self._placeholder_url:
            return self._placeholder_url
        self._placeholder_url = await self.upload_image(BLACK_SQUARE_PNG, "thumb.png")
        return self._placeholder_url

    async def create_page(
        self,
        title: str,
        content: list[dict],
    ) -> str:
        """Create a Telegraph page, return the URL."""
        token = await self.ensure_account()
        session = await self._get_session()

        async with session.post(
            f"{TELEGRAPH_API}/createPage",
            data={
                "access_token": token,
                "title": title[:256],
                "author_name": self.author_name[:128],
                "author_url": self.author_url[:512] if self.author_url else "",
                "content": json.dumps(content, ensure_ascii=False),
                "return_content": "false",
            },
        ) as resp:
            data = await resp.json()

        if not data.get("ok"):
            raise RuntimeError(f"Failed to create Telegraph page: {data}")

        url = data["result"]["url"]
        logger.info("Created Telegraph page: %s", url)
        return url

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


def build_article_content(
    entries: list[tuple[str, str | None, str]],
    placeholder_url: str | None = None,
) -> list[dict]:
    content: list[dict] = []

    if placeholder_url:
        content.append({
            "tag": "figure",
            "children": [{"tag": "img", "attrs": {"src": placeholder_url}}],
        })

    for i, (image_url, caption, time_str) in enumerate(entries):
        if i > 0:
            content.append({"tag": "hr"})

        children: list[dict | str] = [
            {"tag": "img", "attrs": {"src": image_url}},
        ]
        if caption:
            children.append({"tag": "figcaption", "children": [caption]})

        content.append({"tag": "figure", "children": children})
        content.append({
            "tag": "p",
            "children": [
                {"tag": "em", "children": [time_str]},
            ],
        })

    return content
