# Admin functions

Server configuration and event logging are implemented in `src/ky_bot/admin_functions/`:

- `settings_command.py`: administrator-only settings command and welcome delivery.
- `settings_view.py`: Command Center menus and editing controls.
- `settings_service.py`: configuration validation and welcome preview preparation.
- `settings_repository.py`: persistent server settings and database migrations.
- `activity.py`: message, member, role, ban and invite logging.

Maintenance tools live in this folder's `tools/` directory. The settings-registration
cleanup tool can change Discord command registrations; use it only for that task.

Python uses `admin_functions` with an underscore; this home folder uses `admin-functions`.

## Moderation and leveling

**Command Center → Moderation** offers Kick, Ban and Timeout, with private member selection,
optional audit-log reasons, and confirmation. The matching slash commands are `/kick`,
`/ban`, and `/timeout`. Action-specific permissions and role hierarchy are rechecked before
execution. Implementation lives in `src/ky_bot/moderation/`.

**Leveling → Level-Up Messages → Edit formatting** customizes the heading, announcement
message, card height and text sizes. **Preview** shows the current card privately.
See the main README and `/help` for permissions, placeholders, and usage.
