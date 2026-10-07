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
