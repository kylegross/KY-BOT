# Design

Artwork, templates, fonts and profile images live in `assets/branding/`:

- `headers/`: header designs, layers, templates and downloads.
- `footers/`: footer designs, frameworks and downloads.
- `profile/`: bot icon and banners.
- `fonts/private/`: locally installed licensed fonts, excluded from Git.
- `typography/`: typography references.

Builders live in `tools/`. Run them from the repository root:

```powershell
python design/tools/build_headers.py
python design/tools/build_rectangular_footers.py
python design/tools/build_command_center_header.py
```

The installed bot's design code and runtime images live in `src/ky_bot/design/`.
This package includes the header/footer commands, deletion controls, welcome image renderer,
shared typography and packaged images. Keep the runtime copies with this package for Railway.


## Custom metallic palettes

Standard KY BOT header and footer graphics support independent custom palettes:
base hex, optional highlight hex, optional shadow hex, and optional iridescent accent.
Blank optional fields derive coordinated colors from the base. The renderer preserves
source graphics, alpha and metallic luminance detail, then maps the neutral artwork
into the selected palette. Explicit lettering colors and uploaded images/icons retain
their original colors. All four original finishes remain available and unchanged.

- Shared Embed Design: select menus, open Finish, then Custom Header/Footer Colors.
- Tickets (including Opened Ticket Panel): Appearance → Header/Footer Artwork → Custom Colors.
- Custom Embeds: Images → Custom Header/Footer Colors.
- Confessions: Appearance → Artwork → Custom Header/Footer Colors.

Submitting the color form creates a private preview without saving anything. Use
these colors confirms the selected artwork; Discard abandons it. Shared menu designs
still require their existing final Preview/Apply step, and public ticket/custom-embed
posts retain the existing publish workflow. Selecting an original finish removes the
custom palette for that scope. Existing uploads remain a separate source option.
