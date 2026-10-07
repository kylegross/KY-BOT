"""Guild-scoped checklist access; setup remains a server administrator operation."""

from ky_bot.checklists.notices import notice


def admin(member, board=None):
    if getattr(member, "bot", True):
        return False
    permissions = getattr(member, "guild_permissions", None)
    if permissions and (permissions.administrator or permissions.manage_guild):
        return True
    return bool(
        board
        and board.get("role_id")
        and any(role.id == board["role_id"] for role in getattr(member, "roles", ()))
    )


async def access(interaction):
    service = getattr(interaction.client, "checklists", None)
    board = service.store.board(interaction.channel_id) if service else None
    if interaction.guild and board and board["guild"] == interaction.guild.id:
        if admin(interaction.user, board):
            return True
    await notice(
        interaction,
        ("Only server administrators and this checklist's authorized role can use these controls."),
    )
    return False
