"""Inline Command Center leveling controls and revision-checked configuration forms."""

import asyncio

import discord

from ky_bot.checklists.board import safe
from ky_bot.design.collection import COLOURS
from ky_bot.design.menu_design import menu_files, profile_for
from ky_bot.errors import report_error, send_private
from ky_bot.leveling.artwork import card_file
from ky_bot.leveling.service import role_allowed
from ky_bot.leveling.store import MAX_LEVEL, defaults, level_for, threshold
from ky_bot.views.private_style import private_heading
from ky_bot.views.sections import section_dividers

NAMES = ("ky_leveling_header.png", "ky_leveling_footer.png", "ky_leveling_title.png")
HEADINGS = {
    "main": "LEVELING SETTINGS",
    "xp": "XP RULES",
    "exclusions": "EXCLUSIONS",
    "rewards": "ROLE REWARDS",
    "announcements": "LEVEL-UP MESSAGES",
    "leaderboard": "LEADERBOARD",
}


def artwork(design, page="main"):
    profile = dict(design)
    profile["title"] = "COMMAND CENTER 𖥸 " + (profile["title"] or "LEVELING")
    profile["_rotated_divider"] = True
    return menu_files(profile, profile["title"], HEADINGS.get(page, "LEVELING SETTINGS"), NAMES)


class Session:
    def __init__(self, cog, interaction, command_center=None):
        self.cog, self.guild, self.owner = cog, interaction.guild, interaction.user.id
        self.command_center = command_center
        self.generation, self.revision, self.message = 0, None, None
        self.view = None
        self.config, self.design = defaults(), profile_for({}, "settings:leveling")
        self.page, self.reward_level = 0, None
        self.ranking = (0, 0, [])
        self.lock = asyncio.Lock()

    async def allowed(self, interaction, generation):
        if (
            interaction.guild_id != self.guild.id
            or interaction.user.id != self.owner
            or not interaction.permissions.administrator
            or generation != self.generation
        ):
            await send_private(interaction, "Reopen Leveling from the Command Center to continue.")
            return False
        if self.command_center and self.command_center.panel_manager:
            return await self.command_center.interaction_check(interaction)
        return True

    async def save(self, **changes):
        self.revision, self.config = await self.cog.store.configure(
            self.guild.id, self.revision, **changes
        )
        self.cog.service.invalidate_voice(self.guild.id)

    async def show(self, interaction, page="main", notice=None):
        self.revision, self.config = await self.cog.store.config(self.guild.id)
        _, profiles = await self.cog.bot.server_settings.repository.get_designs(self.guild.id)
        self.design = profile_for(profiles, "settings:leveling")
        if page == "leaderboard":
            self.ranking = await self.cog.store.leaderboard(self.guild.id, self.page)
            self.page = self.ranking[0]
        self.generation += 1
        view = Menu(self, page, notice)
        uploads = await asyncio.to_thread(artwork, self.design, page) if page in HEADINGS else []
        try:
            if self.command_center and self.command_center.panel_manager:
                self.message = await self.command_center.panel_manager.edit(
                    self.command_center, interaction, view, uploads
                )
            else:
                # Use this button/modal's fresh token, not the original slash token.
                self.message = await interaction.edit_original_response(
                    content=None,
                    embeds=[],
                    view=view,
                    attachments=uploads,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            if self.view:
                self.view.stop()
            self.view = view
        except BaseException:
            view.stop()
            raise
        finally:
            for upload in uploads:
                upload.close()


class Numbers(discord.ui.Modal):
    def __init__(self, session, mode):
        super().__init__(
            title={
                "messages": "Message XP",
                "cooldown": "Message cooldown",
                "voice": "Voice XP",
                "reward": "Role reward level",
            }[mode]
        )
        self.session, self.generation, self.mode = session, session.generation, mode
        self.revision = session.revision
        fields = {
            "messages": [
                ("message_min", "Minimum XP (1–1000)"),
                ("message_max", "Maximum XP (1–1000)"),
            ],
            "cooldown": [("message_cooldown", "Cooldown in seconds (5–86400)")],
            "voice": [
                ("voice_xp", "XP per interval (1–1000)"),
                ("voice_interval", "Interval in seconds (30–86400)"),
            ],
            "reward": [("level", f"Required level (1–{MAX_LEVEL})")],
        }
        self.inputs = {}
        for key, label in fields[mode]:
            item = discord.ui.TextInput(
                label=label,
                default=str(session.config.get(key, 1)),
                max_length=6,
            )
            self.inputs[key] = item
            self.add_item(item)

    async def on_submit(self, interaction):
        s = self.session
        async with s.lock:
            if not await s.allowed(interaction, self.generation):
                return
            await interaction.response.defer()
            try:
                values = {key: int(str(item)) for key, item in self.inputs.items()}
            except ValueError:
                await send_private(interaction, "Use whole numbers in each field.")
                return
            if s.revision != self.revision:
                await send_private(interaction, "Settings changed. Reopen this form.")
                return
            if self.mode == "reward":
                if not 1 <= values["level"] <= MAX_LEVEL:
                    await send_private(interaction, f"Choose a level from 1 to {MAX_LEVEL}.")
                    return
                s.reward_level = values["level"]
                await s.show(interaction, "reward_role")
            else:
                await s.save(**values)
                await s.show(interaction, "xp", "XP rules saved for this server.")

    async def on_error(self, interaction, error):
        if isinstance(error, ValueError):
            await send_private(interaction, str(error))
        else:
            await report_error(interaction, error)


class Menu(discord.ui.LayoutView):
    def __init__(self, session, page="main", notice=None):
        super().__init__(
            timeout=None if session.command_center and session.command_center.panel_manager else 600
        )
        self.session, self.generation = session, session.generation
        s, config = session, session.config
        panel = discord.ui.Container(accent_colour=int(COLOURS[s.design["style"]][1:], 16))
        if page in HEADINGS:
            for name in (NAMES[0], NAMES[2]):
                panel.add_item(
                    discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://" + name))
                )
        else:
            panel.add_item(
                discord.ui.TextDisplay(
                    private_heading(
                        {
                            "exclude_channels": "EXCLUDED CHANNELS",
                            "exclude_roles": "EXCLUDED ROLES",
                            "destination": "ANNOUNCEMENT CHANNEL",
                            "reward_role": "ROLE REWARD",
                            "remove_reward": "REMOVE REWARD",
                        }[page]
                    )
                )
            )
        if notice:
            panel.add_item(discord.ui.TextDisplay(notice))

        def button(label, action, *, disabled=False):
            item = discord.ui.Button(label=label, disabled=disabled)

            async def click(interaction):
                async with s.lock:
                    if not await s.allowed(interaction, self.generation):
                        return
                    if isinstance(action, tuple):
                        await interaction.response.send_modal(Numbers(s, action[0]))
                    else:
                        await interaction.response.defer()
                        await action(interaction)

            item.callback = click
            return item

        def row(title, description, label, action, *, disabled=False):
            panel.add_item(
                discord.ui.Section(
                    f"**{title}**\n-# {description}",
                    accessory=button(label, action, disabled=disabled),
                )
            )

        def go(target):
            async def navigate(interaction):
                await s.show(interaction, target)

            return navigate

        async def toggle(interaction, key):
            await s.save(**{key: not config[key]})
            await s.show(interaction, "main" if key == "enabled" else "xp")

        if page == "main":
            row(
                "XP RULES",
                "Messages "
                + ("enabled" if config["message_enabled"] else "disabled")
                + " · Voice "
                + ("enabled" if config["voice_enabled"] else "disabled"),
                "Configure",
                go("xp"),
            )
            row(
                "EXCLUSIONS",
                "Choose channels and roles that cannot earn XP.",
                "Manage",
                go("exclusions"),
            )
            row("ROLE REWARDS", "Optional roles earned at chosen levels.", "Manage", go("rewards"))
            row(
                "LEVEL-UP MESSAGES",
                "Wide silver card · No role announcements.",
                "Manage",
                go("announcements"),
            )
            row("LEADERBOARD", "Member rankings for this server.", "View", go("leaderboard"))
            row(
                "TURN ON / OFF",
                "XP earning is "
                + ("enabled" if config["enabled"] else "paused")
                + " in this server. Progress is kept.",
                "Pause" if config["enabled"] else "Resume",
                lambda i: toggle(i, "enabled"),
            )
        elif page == "xp":
            row(
                "MESSAGE XP",
                f"{config['message_min']}–{config['message_max']} XP per eligible message.",
                "Edit",
                ("messages",),
            )
            row(
                "MESSAGE COOLDOWN",
                f"{config['message_cooldown']} seconds between awards per member.",
                "Edit",
                ("cooldown",),
            )
            row(
                "MESSAGE EARNING",
                "On" if config["message_enabled"] else "Off",
                "Turn on / off",
                lambda i: toggle(i, "message_enabled"),
            )
            row(
                "VOICE XP",
                "On" if config["voice_enabled"] else "Off · Enable when ready.",
                "Turn on / off",
                lambda i: toggle(i, "voice_enabled"),
            )
            row(
                "VOICE AMOUNT & INTERVAL",
                f"{config['voice_xp']} XP every {config['voice_interval']} seconds while eligible.",
                "Edit",
                ("voice",),
            )
            panel.add_item(
                discord.ui.TextDisplay(
                    "-# ✧ Bots, commands, repeated messages and checklist activity earn no XP.\n"
                    "-# ✧ Voice: two eligible people, unmuted and undeafened; AFK is excluded.\n"
                    "-# ✧ Level 1: 100 total XP · Level 2: 300 · Level 3: 600."
                )
            )
        elif page == "exclusions":
            for key, title, target, prefix in (
                ("excluded_channels", "CHANNELS", "exclude_channels", "#"),
                ("excluded_roles", "ROLES", "exclude_roles", "@&"),
            ):
                description = ", ".join(f"<{prefix}{v}>" for v in config[key]) or "None excluded"
                row(title, description, "Choose", go(target))
            panel.add_item(
                discord.ui.TextDisplay(
                    "-# Channel exclusions also cover their threads. Changes affect future XP only."
                )
            )
        elif page in ("exclude_channels", "exclude_roles", "destination", "reward_role"):
            is_role = page in ("exclude_roles", "reward_role")
            exclusion = page.startswith("exclude_")
            key = "excluded_roles" if is_role else "excluded_channels"
            picker_type = discord.ui.RoleSelect if is_role else discord.ui.ChannelSelect
            kwargs = (
                {}
                if is_role
                else {
                    "channel_types": (
                        [
                            discord.ChannelType.text,
                            discord.ChannelType.voice,
                            discord.ChannelType.forum,
                            discord.ChannelType.stage_voice,
                        ]
                        if exclusion
                        else [discord.ChannelType.text]
                    )
                }
            )
            values = (
                config[key]
                if exclusion
                else (
                    [config["announcement_channel"]]
                    if page == "destination" and config["announcement_channel"]
                    else []
                )
            )
            picker = picker_type(
                placeholder="Choose roles…" if is_role else "Choose channels…",
                min_values=0 if exclusion else 1,
                max_values=25 if exclusion else 1,
                default_values=[discord.Object(id=v) for v in values],
                **kwargs,
            )
            panel.add_item(
                discord.ui.TextDisplay(
                    f"Reward for level **{s.reward_level}**. Choose a role below KY BOT."
                    if page == "reward_role"
                    else "Select exclusions, or clear the selection to remove them."
                    if exclusion
                    else "Choose where to publish level-up cards."
                )
            )

            async def choose(interaction):
                async with s.lock:
                    if not await s.allowed(interaction, self.generation):
                        return
                    await interaction.response.defer()
                    ids = [v.id for v in picker.values]
                    if is_role:
                        if any(s.guild.get_role(v) is None for v in ids):
                            raise ValueError("Choose roles belonging to this server.")
                    elif any(s.guild.get_channel(v) is None for v in ids):
                        raise ValueError("Choose channels belonging to this server.")
                    if exclusion:
                        await s.save(**{key: ids})
                        await s.show(interaction, "exclusions")
                    elif page == "destination":
                        await s.cog.bot.server_settings.validate_channel(
                            s.guild, ids[0], require_images=True
                        )
                        await s.save(announcement_channel=ids[0])
                        await s.show(interaction, "announcements")
                    else:
                        role_allowed(s.guild, s.guild.get_role(ids[0]))
                        await s.save(rewards=config["rewards"] | {str(s.reward_level): ids[0]})
                        await s.show(interaction, "rewards")

            picker.callback = choose
            panel.add_item(discord.ui.ActionRow(picker))
        elif page == "rewards":
            rewards = sorted(config["rewards"].items(), key=lambda pair: int(pair[0]))
            panel.add_item(
                discord.ui.TextDisplay(
                    "\n".join(f"Level **{level}** → <@&{role}>" for level, role in rewards)
                    or "No role rewards configured."
                )
            )
            row(
                "ADD / UPDATE REWARD", "Choose the level, then its role.", "Add reward", ("reward",)
            )
            row(
                "REMOVE REWARD",
                "Stop granting a reward. Existing member roles are kept.",
                "Remove",
                go("remove_reward"),
                disabled=not rewards,
            )
            panel.add_item(
                discord.ui.TextDisplay(
                    "-# Rewards accumulate and are checked when a member next earns XP. "
                    "KY BOT needs Manage Roles and a higher role than its rewards."
                )
            )
        elif page == "remove_reward":
            options = [
                discord.SelectOption(label=f"Level {level}", value=level)
                for level in sorted(config["rewards"], key=int)
            ]
            if options:
                picker = discord.ui.Select(placeholder="Choose reward to remove…", options=options)

                async def remove(interaction):
                    async with s.lock:
                        if not await s.allowed(interaction, self.generation):
                            return
                        await interaction.response.defer()
                        rewards = dict(config["rewards"])
                        rewards.pop(picker.values[0], None)
                        await s.save(rewards=rewards)
                        await s.show(interaction, "rewards")

                picker.callback = remove
                panel.add_item(discord.ui.ActionRow(picker))
            else:
                panel.add_item(discord.ui.TextDisplay("No rewards to remove."))
        elif page == "announcements":
            destination = config["announcement_channel"]
            row(
                "DESTINATION",
                f"<#{destination}>" if destination else "Off · Choose a channel to enable.",
                "Choose channel",
                go("destination"),
            )

            async def clear(interaction):
                await s.save(announcement_channel=None)
                await s.show(interaction, "announcements")

            row(
                "DELIVERY",
                "Stop announcements while keeping XP earning on.",
                "Clear channel",
                clear,
                disabled=destination is None,
            )

            async def preview(interaction):
                image = await s.cog.service.render_card(interaction.user, 10)
                upload = card_file(image)
                try:
                    await interaction.followup.send(
                        content=f"{interaction.user.mention} just leveled up!",
                        file=upload,
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                finally:
                    upload.close()

            row(
                "LEVEL-UP CARD",
                "Silver welcome-style card · Date at top right · No role names.",
                "Preview",
                preview,
            )
        elif page == "leaderboard":
            number, total, records = s.ranking
            lines = []
            for index, (member_id, xp) in enumerate(records, start=number * 10 + 1):
                level = level_for(xp)
                lines.append(f"**{index}.** <@{member_id}> · Level **{level}** · {xp:,} XP")
            panel.add_item(discord.ui.TextDisplay("\n".join(lines) or "No XP earned yet."))
            panel.add_item(
                discord.ui.TextDisplay(
                    f"-# {total} ranked members · Page {number + 1} "
                    f"of {max(1, (total + 9) // 10)}\n"
                    "-# Level XP: 50 × level × (level + 1). "
                    f"Level 10: {threshold(10):,} total XP."
                )
            )
            controls = discord.ui.ActionRow()
            for delta, label in ((-1, "Previous"), (1, "Next")):

                async def turn(interaction, delta=delta):
                    s.page += delta
                    await s.show(interaction, "leaderboard")

                controls.add_item(
                    button(
                        label,
                        turn,
                        disabled=(number == 0 if delta < 0 else (number + 1) * 10 >= total),
                    )
                )
            panel.add_item(controls)

        async def home(interaction):
            s.generation += 1
            if s.command_center and s.command_center.panel_manager:
                await s.command_center.panel_manager.reset(s.guild.id)
            else:
                from ky_bot.admin_functions.settings_view import SettingsView

                parent = SettingsView(s.owner, s.guild.id, s.cog.bot.server_settings)
                await parent.refresh(interaction)
            self.stop()

        async def refresh(interaction):
            await s.show(interaction, page)

        back_to = {
            "exclude_channels": "exclusions",
            "exclude_roles": "exclusions",
            "destination": "announcements",
            "reward_role": "rewards",
            "remove_reward": "rewards",
        }.get(page, "main")
        controls = discord.ui.ActionRow(button("Back", home if page == "main" else go(back_to)))
        if page in HEADINGS:
            controls.add_item(button("Refresh", refresh))
        if s.command_center and s.command_center.panel_manager:
            controls.add_item(button("Home", home))
        else:

            async def close(interaction):
                s.generation += 1
                await interaction.edit_original_response(
                    view=discord.ui.LayoutView().add_item(discord.ui.TextDisplay("Menu closed.")),
                    attachments=[],
                )
                self.stop()

            controls.add_item(button("Close", close))
        if page in HEADINGS:
            panel.add_item(
                discord.ui.TextDisplay(
                    "-# Admin controls · Saved per server · Returns Home after 10 minutes idle"
                    if s.command_center and s.command_center.panel_manager
                    else "-# Private admin controls · Saved per server · 10-minute session"
                )
            )
            panel.add_item(
                discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://" + NAMES[1]))
            )
        panel.add_item(controls)
        section_dividers(panel)
        self.add_item(panel)

    async def interaction_check(self, interaction):
        return await self.session.allowed(interaction, self.generation)

    async def on_error(self, interaction, error, item):
        if isinstance(error, ValueError):
            await send_private(interaction, safe(str(error)))
        else:
            await report_error(interaction, error)

    async def on_timeout(self):
        if self.session.generation == self.generation:
            self.session.generation += 1
