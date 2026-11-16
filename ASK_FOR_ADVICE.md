# Ask for Advice

Open **Command Center → Community → Ask for Advice**. Choose an ask channel,
then configure its separate access/response channel and two existing roles.

- **Join** grants the selected View Advice role.
- **Leave** removes the advice role first, then the View Advice role.
- **Give Advice** grants the selected advice role only after joining.
- **Remove Advice Role** removes only the advice role and keeps channel access.

Use dedicated roles below KY BOT's highest role. KY BOT needs Manage Roles,
Manage Channels, View Channel, Send Messages, Attach Files, and Read Message
History. The access channel should be visible before joining. Publishing its panel
configures the ask channel's visibility; conflicting non-administrator visibility
overrides must be removed first. Other channel permission flags are preserved.

Choose private administrator-only destinations before turning on author logging
or either approval option. Question approval and advice response approval are
independent. Each published question accepts multiple responses through its
Respond to question button. Responses are anonymous by default; Show My Name is
an explicit choice on the private confirmation. Replies link to the original question.

New-question notifications are optional and happen after publication, including
after approval. They mention only the configured advice role. Make that role
mentionable or allow KY BOT to mention it in the ask channel.

Appearance includes the same standard artwork/lettering finishes as Confessions,
custom header/footer uploads, a header image title, decorative text color, accent
color, footer text, question/advice headings, and the ask-channel prompt. The
access-panel editor supports section/row edits, adding/removing/reordering,
decorative side lines, optional text-only rows, button labels and emojis. Discord's
component limits allow up to four sections and six rows, with 2,800 description
characters total. Button actions retain their role dependencies regardless of labels.

Preview both panels privately, enable the feature, then publish each panel. Saved
appearance changes take effect when publishing; the ask panel also uses them on
its next automatic repost. The default repost interval is five member/bot messages;
the submission panel itself does not count. Questions and responses stay in place.
Reposts and failed submission delivery are checked every ten seconds.

Settings, submissions, pending approvals, panel IDs, and counters persist in isolated
`advice_*` tables in the existing SQLite or PostgreSQL database. Restart restores
button actions and pending delivery without requiring old menus to be clicked again.
`/ask-advice channel:` is an optional shortcut to the private question form; sync
commands using the existing deployment workflow to expose this new slash command.

Author identity is disclosed in private approval queues when approval is required
and in author logs when enabled. Unlogged anonymous submissions clear their direct
author ID after publication/rejection; a keyed server-specific identifier supports
cooldowns. Choosing Show My Name intentionally retains the public responder ID.
Turning a setting off does not delete existing Discord log/review messages.

Offline checks cover role dependencies, independent approval combinations,
multiple responses, privacy revisions, delivery recovery, notifications, and layout
limits. A Discord server check is still needed to verify actual rendering and live
role/channel permission changes before production use.
