from pathlib import Path
from PIL import Image
from ky_bot.services.typography import text_mask
root = Path.cwd()
out = root / 'assets/branding/previews'
out.mkdir(exist_ok=True)
mask = text_mask('SERVER SETTINGS', role='display', size=44, min_size=44, max_width=1500)
lettering = Image.new('RGBA', mask.size, '#D8BB78')
lettering.putalpha(mask)
label = Image.new('RGBA', (1600, 80))
label.alpha_composite(lettering, (0, (80-mask.height)//2))
label.save(out / 'server_settings_vonca_gold.png')
preview = Image.new('RGBA', (1680, 650), '#2b2d38')
header = Image.open(root / 'src/ky_bot/assets/header/ky_header_command_center.png').convert('RGBA')
header = header.resize((1600, round(header.height * 1600 / header.width)))
preview.alpha_composite(header, (40, 24))
preview.alpha_composite(label, (40, 310))
footer = Image.open(root / 'src/ky_bot/assets/footer/ky_footer_floral_wreath_centered_framed.png')
footer = footer.convert('RGBA').resize((1600, round(footer.height * 1600 / footer.width)))
preview.alpha_composite(footer, (40, 420))
preview.save(out / 'server_settings_vonca_gold_preview.png')
