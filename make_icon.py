"""Turns a picture you drop in this folder (icon.png / icon.jpg / icon.webp) into the Great Sage icon."""
import sys
from pathlib import Path
from PIL import Image

here = Path(__file__).resolve().parent
src = next((here / f"icon{ext}" for ext in (".png", ".jpg", ".jpeg", ".webp") if (here / f"icon{ext}").exists()), None)
if not src:
    print("No picture found. Put an image named icon.png (or icon.jpg) in this folder, then run again.")
    sys.exit(1)
backup = here / "sage_original.ico"
if not backup.exists() and (here / "sage.ico").exists():
    backup.write_bytes((here / "sage.ico").read_bytes())  # keep the original orb icon
img = Image.open(src).convert("RGBA")
side = min(img.size)  # crop to a centered square so it isn't squished
left, top = (img.width - side) // 2, (img.height - side) // 2
img = img.crop((left, top, left + side, top + side)).resize((256, 256), Image.LANCZOS)
img.save(here / "sage.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(f"Made sage.ico from {src.name}")
