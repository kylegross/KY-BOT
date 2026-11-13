"""Log viewing is independent of ticket handling and approval permissions."""

import discord


def viewer_roles(settings):
    explicit = settings.get("log_viewer_roles")
    return set(settings["manager_roles"] if explicit is None else explicit)


def destinations(settings):
    return set(settings["log_channels"] + settings["transcript_channels"])


def channel_viewers(config, channel_id, fallback):
    """Only routes sharing this destination contribute viewing roles."""
    candidates = []
    for panel in config["panels"].values():
        base = config["defaults"] | panel.get("overrides", {})
        kinds = panel["types"]
        candidates.extend(
            [base | config["types"][key]["overrides"] for key in kinds] if kinds else [base]
        )
    if not candidates:
        candidates = [config["defaults"]]
    matched = [item for item in candidates if channel_id in destinations(item)]
    return (
        set().union(*(viewer_roles(item) for item in matched))
        if matched
        else viewer_roles(fallback)
    )


def allowed_member(member, roles):
    return member.guild_permissions.administrator or bool(
        {role.id for role in member.roles} & roles
    )


def scope_destinations(config, selected, settings):
    if selected in config["panels"]:
        panel = config["panels"][selected]
        base = config["defaults"] | panel.get("overrides", {})
        routes = [base | config["types"][key]["overrides"] for key in panel["types"]] or [base]
    elif selected in config["types"]:
        routes = [
            config["defaults"] | panel.get("overrides", {}) | config["types"][selected]["overrides"]
            for panel in config["panels"].values()
            if selected in panel["types"]
        ] or [settings]
    else:
        routes = [config["defaults"]]
        for panel in config["panels"].values():
            base = config["defaults"] | panel.get("overrides", {})
            routes.extend(
                [base | config["types"][key]["overrides"] for key in panel["types"]] or [base]
            )
    return set().union(*(destinations(item) for item in routes))


def privacy_overwrites(channel, roles):
    """Preserve other permissions; change viewing only in selected log channels."""
    guild = channel.guild
    selected = []
    for key in roles:
        role = guild.get_role(key)
        if role is None or role.is_default():
            raise ValueError("A log viewer role is unavailable. Reselect Log Viewer Roles.")
        selected.append(role)
    values = {
        target: discord.PermissionOverwrite.from_pair(*overwrite.pair())
        for target, overwrite in channel.overwrites.items()
    }
    for target, overwrite in values.items():
        if isinstance(target, discord.Role):
            if not target.permissions.administrator:
                overwrite.view_channel = target.id in roles
        elif isinstance(target, discord.Member) and target.id != guild.me.id:
            if not target.guild_permissions.administrator:
                # Role grants remain the authority, without per-member bypasses.
                overwrite.view_channel = None if allowed_member(target, roles) else False
    everyone = values.setdefault(guild.default_role, discord.PermissionOverwrite())
    everyone.view_channel = False
    for role in selected:
        value = values.setdefault(role, discord.PermissionOverwrite())
        value.view_channel = True
        value.read_message_history = True
    bot = values.setdefault(guild.me, discord.PermissionOverwrite())
    bot.update(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        attach_files=True,
        embed_links=True,
    )
    return values
