"""The Teams app package (manifest.json and two icons, zipped) an admin uploads to Teams to add the bot.

Built on request from the platform's bot id and URL, so every environment gets a package that points at itself.
"""

import io
import json
import struct
import zipfile
import zlib
from collections.abc import Callable
from urllib.parse import urlparse

from app.core.config import Settings

NAVY = (0x00, 0x20, 0x46, 0xFF)
ORANGE = (0xC8, 0x7B, 0x14, 0xFF)
WHITE = (0xFF, 0xFF, 0xFF, 0xFF)
CLEAR = (0, 0, 0, 0)


def png(size: int, pixel: Callable[[int, int], tuple[int, int, int, int]]) -> bytes:
    """A small RGBA PNG; pixel(x, y) gives each pixel's colour."""
    rows = b"".join(b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(size)) for y in range(size))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")


def color_icon() -> bytes:
    """192×192: navy, with an orange bar like the app's report accents."""
    return png(192, lambda x, y: ORANGE if 48 <= x < 144 and 84 <= y < 108 else NAVY)


def outline_icon() -> bytes:
    """32×32: white outline on transparent, as Teams requires."""
    return png(32, lambda x, y: WHITE if (x in (4, 5, 26, 27) or y in (4, 5, 26, 27)) and 4 <= x <= 27 and 4 <= y <= 27 else CLEAR)


def manifest(settings: Settings) -> dict[str, object]:
    base = settings.app_url.rstrip("/")
    commands = [
        {"title": "mine", "description": "Your overdue and upcoming tasks"},
        {"title": "search", "description": "Find projects, tasks, risks and more"},
        {"title": "new", "description": "new <task name> in <project name>"},
        {"title": "link", "description": "Connect your Project Hub account"},
        {"title": "connect", "description": "connect <code>: link this organization (account owners)"},
    ]
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/teams/v1.17/MicrosoftTeams.schema.json",
        "manifestVersion": "1.17",
        "version": "1.0.0",
        "id": settings.teams_bot_app_id,
        "developer": {"name": "Project Hub", "websiteUrl": base, "privacyUrl": base, "termsOfUseUrl": base},
        "name": {"short": "Project Hub", "full": "Project Hub"},
        "description": {
            "short": "Project updates and your tasks, in Teams.",
            "full": "Posts project updates to the channels your account owner chooses, sends you your Project Hub "
            "mentions and assignments, and lets you look up your work, search and add tasks.",
        },
        "icons": {"color": "color.png", "outline": "outline.png"},
        "accentColor": "#002046",
        "bots": [
            {
                "botId": settings.teams_bot_app_id,
                "scopes": ["personal", "team"],
                "supportsFiles": False,
                "isNotificationOnly": False,
                "commandLists": [{"scopes": ["personal", "team"], "commands": commands}],
            }
        ],
        "permissions": ["identity", "messageTeamMembers"],
        "validDomains": [urlparse(base).hostname or ""],
    }


def build_package(settings: Settings) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest(settings), indent=2))
        archive.writestr("color.png", color_icon())
        archive.writestr("outline.png", outline_icon())
    return buffer.getvalue()
