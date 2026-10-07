"""Expire private checklist notices without closing interactive menus."""

import asyncio
import logging

import discord

_pending = set()


def dismiss_later(delete):
    async def run():
        await asyncio.sleep(5)
        try:
            await delete()
        except discord.NotFound:
            pass
        except discord.HTTPException:
            logging.exception("Could not dismiss private checklist notice")

    task = asyncio.create_task(run())
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def notice(interaction, *args, **kwargs):
    kwargs["ephemeral"] = True
    if interaction.response.is_done():
        message = await interaction.followup.send(*args, wait=True, **kwargs)
        dismiss_later(message.delete)
    else:
        await interaction.response.send_message(*args, **kwargs)
        dismiss_later(interaction.delete_original_response)
