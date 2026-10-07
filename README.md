# KY-BOT
KY BOT is a sophisticated, all-in-one Discord bot created around simplicity, versatility, and ease of use. From everyday essentials to more advanced functionality, every feature is designed to feel intuitive, cohesive, and effortlessly accessible. Powerful by nature. Simple by design.

## Foundation

### Welcome messages and server settings

Administrators can use `/settings` to open a private Command Center panel. The branded
**COMMAND CENTER** header appears at the top, with Welcome and Logging rows beneath it.
Each row has an inline **Manage** button beside its description. **Choose channel** opens
Discord's channel picker;
no channel IDs are needed. Each server saves its own configuration across restarts.

In **Welcome**, choose a channel to enable messages on member joins. Use **Edit welcome message**
to customize the text with `{member}` (new member mention) and `{server}` (server name).
Only the joining member can be pinged; role and everyone mentions are suppressed.
**Disable welcomes** stops messages and keeps the custom text for later.
The bot needs View Channel, Send Messages, and Embed Links in the selected channel,
and Server Members Intent must be enabled in the Discord Developer Portal.
Restart the bot to load these changes; the existing `/settings` command needs no new registration.
The panel uses Discord Components V2, supported by discord.py 2.6 and later. The title artwork
is packaged with the bot, so hosting it does not require fonts. Regenerate just this header with
`python tools/build_command_center_header.py` when the licensed local VONCA fonts are available.

A slash-command-first discord.py application with modular cogs, private Discord UI menus,
validated environment configuration, rotating logs, safe error responses, and async resource cleanup.

Available now: `/help`, `/ping`, `/footer`, `/header`, and an administrator-only `/settings` menu with persistent
server configuration. Moderation, autodelete, autothreading, and games remain planned modules.
The help menu labels them accordingly.

## First launch on Windows

1. Install Python 3.11 or newer from [python.org](https://www.python.org/downloads/).
   Enable its PATH option if offered, then open a new PowerShell window.
2. Open PowerShell **in this project folder** (the folder containing `pyproject.toml`).
3. Create an isolated environment and install the project:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
   Copy-Item .env.example .env
   notepad .env
   ```

   Skip the copy if `.env` already exists. Activation is unnecessary.
   If using the environment already prepared in this workspace, skip environment creation and installation.

4. In `.env`, set `DISCORD_TOKEN` to the **bot token** from the Developer Portal's Bot page.
   Use neither the application ID nor the client secret. Never send the token in chat or commit it.
   Set `COMMAND_SYNC=guild` and `DEV_GUILD_ID` to your test server ID. To copy the server ID,
   enable Discord's Developer Mode, then right-click the server and choose Copy Server ID.
5. In the [Discord Developer Portal](https://discord.com/developers/applications), enable
   **Server Members Intent** and **Message Content Intent**, and leave **Presence Intent off**.
   The code matches these settings. Guild/message events are enabled; other optional event
   groups and startup member caching are disabled until a feature needs them.
6. Install the bot in your server using a Guild Install link with the `bot` and
   `applications.commands` scopes. For the foundation, select View Channels, Send Messages,
   and Embed Links. You need permission to manage the server to install it.
   Administrator is unnecessary. Add feature-specific permissions when those features exist.
7. Start the bot:

   ```powershell
   .\.venv\Scripts\python.exe -m ky_bot
   ```

8. Wait for `KY BOT online`, then try `/help` and `/ping` in the test server.
   The menu is visible only to you and its controls expire after three minutes of inactivity.
   After the successful first sync, set `COMMAND_SYNC=none` for subsequent starts.
   Stop with Ctrl+C. The bot is online only while this process runs.

On macOS/Linux, use `python3 -m venv .venv`, `.venv/bin/python` in place of the Windows
executable, and `cp .env.example .env` for the initial configuration copy.

## Configuration and command registration

Run from the project folder: `.env` and relative log paths resolve from the working directory.
Real environment variables override `.env`, so production hosts can inject secrets directly.

| Variable | Purpose | Default |
| --- | --- | --- |
| `DISCORD_TOKEN` | Required bot token, excluded from settings repr and redacted from logs | None |
| `COMMAND_SYNC` | `none`, `guild` (test server), or `global` (all installations) | `none` |
| `DEV_GUILD_ID` | Positive server ID; required for guild sync | Empty |
| `LOG_LEVEL` | DEBUG, INFO, WARNING, ERROR, CRITICAL | INFO |
| `LOG_DIR` | Rotating UTF-8 log location | `logs` |
| `DATABASE_PATH` | SQLite settings file; parent folder created automatically | `data/ky-bot.sqlite3` |

Loading cogs registers commands locally; **sync publishes the command definitions to Discord**.
It happens once during setup, never on reconnect. Re-sync after changing command definitions.
Sync replaces this application's command set in the selected scope, so keep the complete
extension registry loaded and use a test server while developing.

For release, set `COMMAND_SYNC=global`, start once, then return to `none`.
Global command visibility may take time to update. Existing test-server registrations are
deliberately not deleted by global sync; they can shadow global versions in that server.
Keep that server for development. If later removing the test registrations, explicitly clear
and sync only that guild after checking the target ID; do not clear global commands.

## Project layout

```text
src/ky_bot/
  __main__.py       Startup, shutdown, and actionable startup failures
  config.py         Environment validation
  bot.py            Intents, resource lifecycle, extension registry, command sync
  errors.py         Central slash-command and component error responses
  logging_setup.py  Console and rotating file logs with token redaction
  cogs/core.py      /help and /ping
  views/           Reusable owner-checked menus and component error handling
  services/        Future domain logic (moderation, automation, games)
  database/        Async SQLite settings repository and schema migration
tests/             Offline foundation tests
```

Add one cog extension per feature, expose `async def setup(bot)`, and list its import path in
`EXTENSIONS` in `bot.py`. Required extension failures stop startup rather than silently leaving
part of the bot unavailable. Keep slash-command callbacks thin: validate permissions and input,
call a service, and return a response. Use cog listeners for message-based automation; prefix
commands are intentionally disabled. Centralize command errors in `errors.py` so local error
handlers do not send duplicate responses. Defer interactions before long-running work.

Future moderation/admin commands must enforce both user and bot permissions at execution time;
UI visibility is not authorization. Use permission decorators and check role hierarchy and target
constraints in the service. Recheck authorization in components that perform privileged actions.
Scheduled autodelete/autothreading jobs should be cancellable, rate-limit-aware, and restarted
from persisted server settings; stop owned background tasks in cog unload hooks.

The async SQLite repository opens before extensions load and closes with the bot, including
on startup failure. Schema v1 is created automatically; a database from a newer schema version
is rejected. Single-statement updates commit atomically and settings are scoped by guild ID.
Future multi-statement operations must use serialized transactions; migrate schemas explicitly.
Enabled member events do not imply a complete cached member list: fetch members as needed.

## Server settings

Restart once with `COMMAND_SYNC=guild` (and your test server ID) to register `/settings`, then
return to `none`. Administrators can open the private menu to choose, clear, or refresh a log
channel. Selections save immediately and survive restarts. Both the slash command and every
component interaction enforce Administrator permission; only the menu opener may use its controls.
The bot must be able to view the chosen text channel, send messages, and embed links.
**Logging is not implemented yet**; choosing a channel prepares configuration for that module.

Only server IDs and selected channel IDs are saved. Opening a menu does not create a database
row. Clear log channel removes that server's row in schema v1. The database is Git-ignored.
Run one bot process per database. Stop the bot before copying the database for a backup or
moving it. For this OneDrive checkout, pause syncing while the bot runs or set `DATABASE_PATH`
to a file outside OneDrive; do not share a live SQLite database between computers.

To check persistence, set a channel, restart, and reopen `/settings`. Also test a non-admin
account, removal of admin permission while a menu is open, a channel the bot cannot access,
and a second server. Refresh reloads current saved values; unavailable saved channels are labeled.

## Custom footer frameworks

`/footer` creates a private PNG download with a user-uploaded background. Choose iridescent silver
with a rainbow tint, original gold, or dark silver/black metallic. All styles use a 2176 Ã— 320 canvas.
The background fills a wide rectangular frame with subtle corners. Dark silver works best on
lighter backgrounds. This exports an image; it does not automatically change server settings or
other bot messages.

Optional inputs: `slogan`, `left_icon`, `right_icon`, `crop_x`, `crop_y`, `dim`, and `icon_scale`.
One left icon is repeated on both sides unless a separate right icon is uploaded. Custom logos
must have transparent backgrounds; their silhouettes receive the selected metallic finish.
Crop coordinates run from 0â€“100, dimming from 0â€“80%, and icon scale from 60â€“120%.
Backgrounds accept still PNG/JPEG/WebP files; icons accept transparent PNG/WebP.
Each upload is limited to 8 MB, 16 megapixels, and 8192 pixels on either side.

The default runtime tagline is rendered from licensed VONCA Light with restrained tracking.
Its PNG layers ship with the bot; licensed font binaries do not. Local custom slogans use
`assets/branding/fonts/private/Vonca-Light.otf` automatically. Set `KY_BOT_FONT_DIR` to an
external font directory on other hosts, or retain `FOOTER_FONT_PATH` for an explicit OTF/TTF
override. Use VONCA Light for that override. Restart the bot after changing configuration.
The default tagline and icon/background customization still work without font binaries.

Install updated dependencies with `python -m pip install -e ".[dev]"` in the project environment.
Restart with `COMMAND_SYNC=guild` to register `/footer` in the test server, then return to `none`.
The bot needs permission to attach files. No command registration or bot restart is performed
by the asset build scripts.

Uploaded backgrounds, logos, and slogans are processed in memory and are not written to the
bot's database or filesystem. Discord handles the uploads and the private output attachment.
There is no saved per-user customization profile; rerun the command to make another version.

Editable PNG layers are in `assets/branding/footers/frameworks/`; runtime layers
ship with the Python package. Approved source artwork is retained in
`assets/branding/footers/masters/approved-materials/`.
The approved collection uses rebuilt backgrounds and a shared glossy molten-metal border,
made 15% thinner across all finishes. Volcanic includes gold ribbons and a softened, darker
area behind the slogan. Native background sources and generation prompts are retained in
`masters/approved-materials/`; final exports are 2176 × 320, not native 4K. The archived approved compositions retain their original lettering; the runtime footer
tagline layers now use exact VONCA Light. The five standard designs now use the approved three-leaf icon:
gold for Volcanic and Botanical, iridescent silver for Abstract Nature, and black metallic
for Floral Wreath and Stone Minimal. The `silver_neon` internal style ID remains compatible;
its displayed name and artwork are now iridescent silver.
`tools/build_footer_frameworks.py` and `tools/standardize_footers.py` are historical builders for
the earlier artwork, not the currently approved exports. Do not run them over the current library.
Draft reviews, preview images, and duplicate ZIP bundles have been removed. Approved source
artwork, editable layers, and final exports remain; earlier tracked versions remain in Git history.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

Tests do not need a Discord token or a network connection. A live smoke test still requires
your application token and installation in a test server. Check `/help`, each category, Close menu,
timeout, `/ping`, restart with sync disabled, and confirm the token does not appear in logs.

## Troubleshooting

- **Missing token:** edit `.env` in the folder you run from. If a process environment variable
  already defines the token, it overrides the file.
- **Invalid token:** copy the bot token locally again; reset it in the Portal if exposed.
- **Privileged intent failure / close code 4014:** align Portal toggles with the intents above.
- **Slash commands absent:** use guild sync with the correct server ID; confirm the app is installed
  with `applications.commands` and the channel allows application commands. Check the log for sync errors.
- **Missing Access during sync:** confirm this bot is installed in the configured test server.
- **Expired controls:** run `/help` again. Menus are session-local and do not survive a restart.
- **Python not found:** install Python, reopen PowerShell, and check `py --version`.

`.env`, logs, environments, caches, and local databases are ignored by Git. Logs contain diagnostics
and IDs; restrict access and retention when hosting. Do not log message content, credentials, or
full interaction payloads when adding modules. Review the existing [Privacy Policy](Privacy-Policy.md)
and [Terms of Service](Terms-of-Service.md) as data-handling features are implemented.

Implementation references: [discord.py commands API](https://discordpy.readthedocs.io/en/stable/ext/commands/api.html),
[interactions and UI API](https://discordpy.readthedocs.io/en/stable/interactions/api.html), and
[gateway intents](https://discordpy.readthedocs.io/en/stable/intents.html).

## Railway deployment

The included Dockerfile installs the bot and its runtime artwork and starts it with
`python -m ky_bot`. Push it to GitHub to use it on Railway. The Docker build excludes
local secrets, databases, logs, development environments, and source artwork.

Use the Dockerfile builder. Clear any custom build or start command overrides
so Railway uses the Dockerfile startup command.

Attach a persistent volume at `/data`, then set these service variables:

| Variable | Value |
| --- | --- |
| `DISCORD_TOKEN` | Enter your bot token privately in Railway |
| `DATABASE_PATH` | `/data/ky-bot.sqlite3` |
| `LOG_DIR` | `/data/logs` |
| `COMMAND_SYNC` | `none` when commands are already registered |

Keep one replica, Serverless disabled, and restart policy On Failure. Leave the
HTTP healthcheck and public domain unset; this bot is a background worker.
Stop any local bot instance before deploying, and look for `KY BOT online` in
Railway logs. Verify `/ping`, `/help`, `/settings`, and `/footer` in Discord.
If commands need registering, use `guild` with `DEV_GUILD_ID` for testing, or
`global` for release; switch back to `none` after a successful sync.

The volume preserves settings across deployments. Existing local settings are not
uploaded automatically. To migrate them, stop the bot and transfer its SQLite
file before the hosted bot starts. Back up any existing destination database first.
Custom slogans require your licensed font on the volume and `FOOTER_FONT_PATH`
set to its Linux path, such as `/data/fonts/Vonca.otf`.

Railway references: [Dockerfiles](https://docs.railway.com/builds/dockerfiles),
[variables](https://docs.railway.com/variables), and
[volumes](https://docs.railway.com/volumes).
## KY BOT typography

VONCA is the standard for all designed visual text. Use Bold for display titles, Medium for
headings, Regular for body and labels, and Light for POWERFUL BY NATURE. SIMPLE BY DESIGN.
The shared graphic renderer is `ky_bot.services.typography`; font roles and tracking live there.
See [the typography reference](assets/branding/typography/KY-BOT-Typography-Reference.md)
and [style tokens](assets/branding/typography/KY-BOT-Typography-Tokens.json) for the complete hierarchy.

Licensed local fonts are ignored by Git and excluded from the Docker build. For hosted custom
slogans, install your private font on the persistent volume and point `KY_BOT_FONT_DIR` at
that directory, or set `FOOTER_FONT_PATH` to `/data/fonts/Vonca-Light.otf`.
Discord controls fonts in native embed text and buttons; exact VONCA applies to rendered graphics.
Historical builders now use VONCA for their text, but must not overwrite the approved library.

## Menu and embed headers

The approved header collection has five backgrounds: Volcanic, Botanical, Abstract Nature,
Floral Wreath, and Stone Minimal. Each is available framed and borderless at 1600 × 520.
All headers are slogan-free. The small icon and KY BOT mark sit at the top left, slightly
inset on framed headers and closer to the corner on borderless headers. Optional titles use
large VONCA Bold lettering with a thin matching line above and below.

All designs and reusable templates share the smaller 190 × 57 corner logo and its readability
plate. Optional titles are centered horizontally and vertically and use the same metallic
finish as the border. Compact 1600 × 280 exports accompany the standard 1600 × 520 assets;
compact filenames include `_compact`. The floral wreath Command Center keeps its pale logo plate.

Headers and footers now use wide rectangles with an 8-pixel corner radius to align with panel
text. Header titles use restrained metallic highlights for readability. Rebuild footer presets,
frameworks, and download archives with `python tools/build_rectangular_footers.py`, then run
`python tools/build_headers.py` and `python tools/build_command_center_header.py` to refresh
the header library and settings artwork. The original raster masters remain the source artwork.

Final designs are in `assets/branding/headers/designs/`. Reusable transparent PNG templates
are in `assets/branding/headers/templates/`, with separate frame and brand source layers in
`assets/branding/headers/layers/`. The manifest documents the layout. No preview sheets or
draft images are included in the project. Original approved footer assets remain intact.

Use `/header` to upload a still PNG, JPEG, or WebP background, choose gold, iridescent silver,
or black metallic, and choose framed or borderless. Optional inputs are `title`, `crop_x`,
`crop_y`, and `dim`. The result is a private PNG download, processed in memory, with no saved
customization profile. The brand mark stays fixed; there is no slogan input. Upload limits
match `/footer`: 8 MB, 16 megapixels, and 8192 pixels per side. Exported graphics can be used
in menus and embeds; the command does not automatically change existing bot messages.

The optional `height` input accepts 240–800 pixels (default 520); try 280 for a compact banner.
Width stays at 1600 pixels. The background crop, frame, and title adapt to the selected height.
Sync commands once after updating the bot to expose this new `/header` option.

Blank headers and brand marks work without fonts on the host. Custom titles require your
licensed `Vonca-Bold.otf` or `Vonca-Bold.ttf` in the private local font directory or in
`KY_BOT_FONT_DIR` on the host. Font binaries are never bundled or committed.

`ky_bot.services.headers.design_file(name, framed=False)` provides a borderless preset;
omit `framed=False` for the framed preset. Attach those bytes as a Discord PNG file and
reference the attachment from the embed image. `render_header` supports custom backgrounds
and titles. Runtime PNGs are included in the Python package and Docker image.
Pass `compact=True` to `design_file` for the compact preset.

Regenerate final headers with `python tools/build_headers.py` when licensed fonts are
available locally. This builder uses approved footer source assets and exports final
designs, templates, and layers only. It does not alter the footers or generate draft sheets.

Restart the bot and sync commands once to register `/header`: use `COMMAND_SYNC=guild` with
your test server ID, or `global` for release, then return to `none`. Live command registration
is not performed by the asset builder or tests.
