from __future__ import annotations

import json
import logging
import mimetypes
from dataclasses import dataclass, field

import aiohttp

logger = logging.getLogger(__name__)

TELEGRAPH_API = "https://api.telegra.ph"
TELEGRAPH_UPLOAD = "https://graph.org/upload"

UPLOAD_HEADERS = {
    "accept": "application/json, text/javascript, */*; q=0.01",
    "x-requested-with": "XMLHttpRequest",
    "referer": "https://graph.org/",
}


@dataclass
class TelegraphClient:
    author_name: str = "Food Diary"
    author_url: str = ""
    _access_token: str | None = field(default=None, repr=False)
    _session: aiohttp.ClientSession | None = field(default=None, repr=False)

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
        """Upload image to Telegraph, return the full URL."""
        session = await self._get_session()

        content_type = mimetypes.guess_type(filename)[0] or "image/jpeg"
        form = aiohttp.FormData()
        form.add_field("file", image_bytes, filename="blob", content_type=content_type)

        async with session.post(TELEGRAPH_UPLOAD, data=form, headers=UPLOAD_HEADERS) as resp:
            raw = await resp.text()
            logger.debug("Telegraph upload response (%s): %s", resp.status, raw[:500])
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                raise RuntimeError(f"Telegraph returned non-JSON: {raw[:200]}")

        if isinstance(data, list) and data and "src" in data[0]:
            return f"https://graph.org{data[0]['src']}"

        logger.error("Telegraph upload failed. Size=%d, response=%s", len(image_bytes), data)
        raise RuntimeError(f"Failed to upload image to Telegraph: {data}")

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
    entries: list[tuple[str, str | None]],
) -> list[dict]:
    """Build Telegraph Node content from (image_url, caption) pairs.

    Returns a list of Telegraph Node elements ready for createPage.
    """
    content: list[dict] = []

    for i, (image_url, caption) in enumerate(entries):
        if i > 0:
            content.append({"tag": "hr"})

        # Image
        content.append({
            "tag": "figure",
            "children": [
                {"tag": "img", "attrs": {"src": image_url}},
                *(
                    [{"tag": "figcaption", "children": [caption]}]
                    if caption
                    else []
                ),
            ],
        })

        # Caption as separate paragraph if long
        if caption and len(caption) > 100:
            content.append({"tag": "p", "children": [caption]})

    return content
