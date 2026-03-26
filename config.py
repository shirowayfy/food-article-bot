from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    bot_token: str
    imgbb_api_key: str
    telegraph_author_name: str = "Food Diary"
    telegraph_author_url: str = ""
    db_path: str = "food_diary.db"

    @classmethod
    def from_env(cls) -> Config:
        bot_token = os.getenv("BOT_TOKEN")
        if not bot_token:
            raise ValueError("BOT_TOKEN environment variable is required")

        imgbb_api_key = os.getenv("IMGBB_API_KEY")
        if not imgbb_api_key:
            raise ValueError("IMGBB_API_KEY environment variable is required")

        return cls(
            bot_token=bot_token,
            imgbb_api_key=imgbb_api_key,
            telegraph_author_name=os.getenv("TELEGRAPH_AUTHOR_NAME", "Food Diary"),
            telegraph_author_url=os.getenv("TELEGRAPH_AUTHOR_URL", ""),
            db_path=os.getenv("DB_PATH", "food_diary.db"),
        )
