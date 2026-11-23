"""Render the synthetic demo images. The manifest is ground truth, never intake data."""

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SPECIMEN_DATE = "2025-02-17"
COLS = (62, 474, 676, 878)
PAGE = (1120, 800)


def report(font_path, version, potassium, degraded=False):
    image = Image.new("RGB", PAGE, "white")
    draw = ImageDraw.Draw(image)
    ink = "#1f2d3d"
    title = ImageFont.truetype(font_path, 45)
    normal = ImageFont.truetype(font_path, 28)
    small = ImageFont.truetype(font_path, 23)
    draw.text((60, 45), "Northfield Lab", font=title, fill=ink)
    draw.text((62, 110), "Laboratory results • Synthetic fixture", font=small, fill=ink)
    draw.line((62, 157, 1050, 157), fill="#718090", width=2)
    draw.text((62, 181), "Patient: Avery Example", font=normal, fill=ink)
    draw.text((62, 226), f"Version: {version}", font=normal, fill=ink)
    draw.text((62, 271), "Specimen: Plasma", font=normal, fill=ink)
    draw.rectangle((60, 333, 1055, 383), fill="#e6edf2")
    for x, label in zip(COLS, ("Test", "Value", "Unit", "Date")):
        draw.text((x, 340), label, font=normal, fill=ink)
    rows = [("Glucose", "98", "mg/dL"), ("Potassium", potassium, "mmol/L"),
            ("Sodium", "140", "mmol/L"), ("Creatinine", "0.91", "mg/dL")]
    for index, row in enumerate(rows):
        y = 405 + index * 62
        for x, value in zip(COLS, (*row, SPECIMEN_DATE)):
            if degraded and index == 1 and x == COLS[1]:
                # A washout leaves the value faintly visible at the correct source location.
                draw.text((x, y), value, font=normal, fill="#dedede")
            else:
                draw.text((x, y), value, font=normal, fill=ink)
        draw.line((62, y + 46, 1050, y + 46), fill="#d3dbe1", width=1)
    draw.text((62, 674), "Notes: Fasting specimen.", font=small, fill=ink)
    draw.text((62, 716), "End of report", font=small, fill=ink)
    if degraded:
        image = image.resize((1064, 760), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(.35))
    return image, [dict(test=test, value=value, unit=unit, date=SPECIMEN_DATE, version=version)
                   for test, value, unit in rows]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", default="DejaVuSans.ttf", help="Path to a TrueType font")
    args = parser.parse_args()
    FIXTURES.mkdir(exist_ok=True)
    manifest = {"synthetic_patient": "Avery Example", "reports": {}}
    for filename, version, potassium, degraded in (
        ("clean-v1.png", "V1", "4.2", False),
        ("degraded-v1.jpg", "V1", "4.2", True),
        ("corrected-v2.png", "V2", "5.1", False),
    ):
        image, rows = report(args.font, version, potassium, degraded)
        image.save(FIXTURES / filename, quality=66 if degraded else 95)
        manifest["reports"][filename] = {"version": version, "rows": rows}
    Image.new("RGB", PAGE, "#e5e2dd").save(FIXTURES / "unreadable.png")
    manifest["reports"]["unreadable.png"] = {"version": None, "rows": []}
    (FIXTURES / "ground-truth.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
