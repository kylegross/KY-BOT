"""Remove test-server copies only where a global command already exists."""

import asyncio
import json
import sys

import aiohttp

from ky_bot.config import Settings


async def cleanup(session, application_id, guild_id, *, apply=False):
    base = f"/applications/{application_id}"
    guild_route = f"{base}/guilds/{guild_id}/commands"

    async def call(method, route):
        async with session.request(method, "https://discord.com/api/v10" + route) as response:
            if response.status >= 400:
                raise RuntimeError(f"Discord returned HTTP {response.status}; cleanup stopped.")
            return await response.json() if response.status != 204 else None

    globals_found = await call("GET", base + "/commands")
    guild_commands = await call("GET", guild_route)
    keys = {(c["name"], c["type"]) for c in globals_found}
    duplicates = [c for c in guild_commands if (c["name"], c["type"]) in keys]
    print(
        json.dumps(
            {
                "duplicate_server_commands": [c["name"] for c in duplicates],
                "server_only_commands_preserved": [
                    c["name"] for c in guild_commands if (c["name"], c["type"]) not in keys
                ],
            }
        )
    )
    if apply:
        # Never remove a server-only command or publish uncommitted local definitions.
        for command in duplicates:
            current = await call("GET", base + "/commands")
            if not any(
                (c["name"], c["type"]) == (command["name"], command["type"]) for c in current
            ):
                raise RuntimeError("Global replacement disappeared; server command retained.")
            await call("DELETE", guild_route + "/" + command["id"])
        remaining = await call("GET", guild_route)
        print(
            json.dumps(
                {
                    "remaining_duplicates": [
                        c["name"] for c in remaining if (c["name"], c["type"]) in keys
                    ]
                }
            )
        )


async def main():
    config = Settings.from_env()
    if not config.dev_guild_id:
        raise RuntimeError("Set DEV_GUILD_ID to the server with duplicate commands.")
    async with aiohttp.ClientSession(
        headers={"Authorization": "Bot " + config.token},
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
        async with session.get("https://discord.com/api/v10/oauth2/applications/@me") as response:
            if response.status != 200:
                raise RuntimeError(f"Application lookup returned HTTP {response.status}.")
            application = await response.json()
        await cleanup(session, application["id"], config.dev_guild_id, apply="--apply" in sys.argv)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        print(
            "Cleanup failed: "
            + (str(error) if isinstance(error, RuntimeError) else type(error).__name__)
        )
        raise SystemExit(1)
