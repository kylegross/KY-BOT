# KY BOT Typography Library and Style Reference

VONCA is the standard typeface for all designed KY BOT text: display titles, headers, embed graphics, image text, menus, feature artwork, subheadings, body copy, labels, captions, numbers, and taglines. The visual direction is elegant, mature, architectural, and understated. Use scale, whitespace, and a restrained weight hierarchy to create emphasis.

The brand tagline is **POWERFUL BY NATURE. SIMPLE BY DESIGN.** Preserve its wording, capitalization, and punctuation.

## Verified font inventory

Source package: `graphicriver-WpiBGjgj-vonca.zip`, inspected October 6, 2026. The package contains 21 font files: seven OTF files, seven TTF files, and seven WOFF files. There are seven static upright styles; no italic or variable font files are included.

Paths below are relative to the unpacked package. OTF is the default for design production; the corresponding TTF is an alternative when the application requires it. Install one format per style to avoid duplicate font menu entries.

| Style | Weight | OTF file | TTF alternative |
| --- | ---: | --- | --- |
| Extra Light | 200 | `Vonca-ExtraLight.otf` | `TTF/Vonca-ExtraLight.ttf` |
| Light | 300 | `Vonca-Light.otf` | `TTF/Vonca-Light.ttf` |
| Regular | 400 | `Vonca-Regular.otf` | `TTF/Vonca-Regular.ttf` |
| Medium | 500 | `Vonca-Medium.otf` | `TTF/Vonca-Medium.ttf` |
| Semibold | 600 | `Vonca-Semibold.otf` | `TTF/Vonca-Semibold.ttf` |
| Bold | 700 | `Vonca-Bold.otf` | `TTF/Vonca-Bold.ttf` |
| Extra Bold | 800 | `Vonca-ExtraBold.otf` | `TTF/Vonca-ExtraBold.ttf` |

The WOFF files follow the same naming pattern under `WOFF/`, for example `WOFF/Vonca-Regular.woff`. Their presence is an inventory fact, not confirmation of web embedding permission. No license document or usage instructions are included in this ZIP; retain the purchase license separately.

All 14 OTF/TTF files have readable font tables, report 414 mapped Unicode characters, and contain every character in KY BOT and the tagline. They declare kerning (`kern`), standard ligatures (`liga`), discretionary ligatures (`dlig`), and alternate access (`aalt`). Metadata inspection confirms these features exist; it does not establish how every application renders them.

Some applications use legacy family names: Medium may appear as **Vonca Medium / Regular**, Semibold as **Vonca Semibold / Regular**, and Light as **Vonca Light / Regular**. Their typographic family is Vonca and their actual weights remain those listed above. Select the named style or exact file; do not infer weight from a legacy menu's “Regular” label.

## Default hierarchy

These values are practical starting settings, not settings supplied by the font vendor. Sizes are pixels at the final displayed size. For a graphic exported at twice its display dimensions, double the pixel sizes and spacing distances. Tracking in em scales with font size; 0.04 em equals 40 in applications that measure tracking in thousandths of an em.

| Role | Actual default file | Size | Line height | Tracking | Capitalization |
| --- | --- | --- | --- | --- | --- |
| Display and KY BOT title | `Vonca-Bold.otf` | 64–96 px | 1.05–1.12 | 0 to 0.02 em | KY BOT always uppercase; short feature titles uppercase |
| Main heading and embed header graphic | `Vonca-Medium.otf` | 32–48 px | 1.10–1.20 | 0.01–0.03 em | Uppercase for short headers; sentence case for longer titles |
| Subheading and section title | `Vonca-Regular.otf` | 22–28 px | 1.20–1.30 | 0–0.02 em | Sentence case by default |
| Body and short graphic description | `Vonca-Regular.otf` | 18–22 px | 1.40–1.55 | 0 em | Sentence case |
| Label, caption, and metadata | `Vonca-Regular.otf` | 14–16 px | 1.25–1.40 | 0.04–0.08 em for uppercase; 0 for sentence case | Uppercase for brief labels |
| Tagline | `Vonca-Light.otf` | 16–20 px | 1.30–1.40 | 0.06–0.10 em | Uppercase, exactly as specified |

Use the corresponding TTF alternative from the inventory for every role when needed. Keep the same weight and settings across formats.

For a compact composition, start with 64 px display, 36 px heading, 24 px subheading, 18 px body, and 14 px labels. Use only the hierarchy levels the composition needs. Keep labels readable at the actual viewing size; enlarge text before adding heavy effects.

## Weight and spacing rules

- Use Semibold (`Vonca-Semibold.otf`, 600) when a heading needs stronger separation or a small label loses definition. Medium (`Vonca-Medium.otf`, 500) is the first alternative for subheadings and small labels.
- Reserve Extra Bold (`Vonca-ExtraBold.otf`, 800) for occasional large, short titles whose composition needs more presence. Bold is the normal display choice.
- Reserve Extra Light (`Vonca-ExtraLight.otf`, 200) for large decorative accents on simple, high-contrast backgrounds. Keep essential small text in Regular or Medium.
- Give body text natural spacing. Use wider tracking for short uppercase labels and the tagline; avoid wide spacing in sentences and paragraphs.
- Enable font kerning. Check short uppercase titles and KY BOT optically after applying tracking. Keep discretionary ligatures and decorative alternates off by default; use them only when they preserve immediate recognition. Turn ligatures off for tracked uppercase labels and the tagline if they disrupt even spacing.
- Use actual font weights. Avoid synthetic bold, faux italics, stretched letters, or condensed transforms. Fit long copy by shortening it, wrapping it, or adjusting the layout.

## Tagline treatment

**POWERFUL BY NATURE. SIMPLE BY DESIGN.**

Set the tagline in VONCA Light, uppercase, with 0.08 em tracking as the starting point. Position it beneath the KY BOT title at roughly one quarter to one third of the title's size, leaving about one tagline line height of space between them. Keep it visually subordinate while maintaining readable contrast.

Use one line when it fits comfortably. For a narrow composition, break only after the first sentence:

```text
POWERFUL BY NATURE.
SIMPLE BY DESIGN.
```

On a textured or volcanic background, place text over a quiet area or use a restrained background treatment to improve contrast. If Light loses definition at the final viewing size, use `Vonca-Regular.otf` at the same size and tracking.

## Composition and production

Use generous margins, consistent alignment, and few competing text sizes. Keep subheadings and descriptions restrained. Avoid exaggerated glow, thick outlines, bulky shadows, decorative scripts, and unrelated futuristic type treatments. Establish hierarchy through size and weight before adding effects.

Apply the exact VONCA files in the design application or text-rendering step. For generated background artwork, add the final typography afterward with the actual fonts so spelling, letterforms, and spacing stay consistent.

VONCA applies wherever KY BOT controls the rendered lettering. A host application's native embed title or message text uses that application's font unless it supports custom fonts; use a rendered VONCA header graphic where necessary. Keep essential information available as readable text alongside graphics.

Before export, inspect the actual viewing size for thin strokes, clipped accents or descenders, awkward wrapping, uneven uppercase spacing, and readable labels. Confirm the tagline punctuation. Use a heavier real style or a larger size if small text loses clarity.

## Reuse instruction

For future KY BOT artwork: use VONCA for all designed visual text, Bold for display, Medium for headings, Regular for subheadings/body/labels, and Light for the tagline. Preserve the elegant, mature, understated direction and the exact tagline POWERFUL BY NATURE. SIMPLE BY DESIGN. Apply the hierarchy and spacing above, adapting scale to the final viewing size.

The font binaries remain private production assets. This reference and the accompanying style tokens contain only file names, metadata, and design settings; they do not include or redistribute font files.
