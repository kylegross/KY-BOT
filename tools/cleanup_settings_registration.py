"""Inspect and remove the test-server /settings duplicate after checking global registration."""

import asyncio
import json
import sys
from pathlib import Path

import aiohttp

from ky_bot.bot import KYBot
from ky_bot.config import Settings


async def main():
    config = Settings.from_env()
    if not config.dev_guild_id:
        print("No test server is configured; no registrations changed.")
        return
    async with KYBot(Settings(config.token, database_path=Path(":memory:"))) as bot:
        await bot.setup_hook()
        expected = bot.tree.get_command("settings").to_dict(bot.tree)
    async with aiohttp.ClientSession(
        headers={"Authorization": "Bot " + config.token},
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:

        async def call(method, route, data=None):
            async with session.request(
                method, "https://discord.com/api/v10" + route, json=data
            ) as response:
                if response.status >= 400:
                    raise RuntimeError(
                        f"Discord returned HTTP {response.status} during {method}; stopped."
                    )
                return await response.json() if response.status != 204 else None

        app = await call("GET", "/oauth2/applications/@me")
        base = "/applications/" + app["id"]
        global_commands = await call("GET", base + "/commands")
        guild_route = base + "/guilds/" + str(config.dev_guild_id) + "/commands"
        guild_commands = await call("GET", guild_route)
        def matches(commands):
            return [c for c in commands if c["name"] == "settings" and c["type"] == 1]
        globals_found, guilds_found = matches(global_commands), matches(guild_commands)

        def current(command):
            return (
                all(command.get(key) == expected.get(key) for key in ("name", "description"))
                and str(command.get("default_member_permissions"))
                == str(expected.get("default_member_permissions"))
                and command.get("options", []) == expected.get("options", [])
            )

        print(
            json.dumps(
                {
                    "global_settings": len(globals_found),
                    "test_server_settings": len(guilds_found),
                    "global_definition_current": bool(globals_found and current(globals_found[0])),
                    "local_sync_mode": config.command_sync,
                }
            )
        )
        if "--apply" not in sys.argv:
            return
        if not globals_found:
            await call("POST", base + "/commands", expected)
        elif not current(globals_found[0]):
            await call("PATCH", base + "/commands/" + globals_found[0]["id"], expected)
        verified = matches(await call("GET", base + "/commands"))
        if len(verified) != 1 or not current(verified[0]):
            raise RuntimeError(
                "Global /settings verification failed; test-server commands retained."
            )
        for command in guilds_found:
            await call("DELETE", guild_route + "/" + command["id"])
        remaining = matches(await call("GET", guild_route))
        print(
            json.dumps(
                {"global_settings_verified": True, "remaining_test_server_settings": len(remaining)}
            )
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        print(
            "Cleanup failed: "
            + type(error).__name__
            + (": " + str(error) if isinstance(error, RuntimeError) else "")
        )
        raise SystemExit(1)
