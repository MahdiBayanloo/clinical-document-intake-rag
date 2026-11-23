"""OCR and geometric extraction for the supported four-column lab form."""

import csv
import io
import os
import re
import subprocess
import warnings
from dataclasses import dataclass
from datetime import date

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 20_000_000
FIELDS = ("test", "value", "unit", "date", "version")
COLUMNS = FIELDS[:-1]
HEADERS = ("test", "value", "unit", "date")
ANCHORS = (0.055, 0.42, 0.60, 0.78)
BOUNDARIES = (0.0, 0.40, 0.58, 0.76, 1.0)
VERSION = re.compile(r"V[1-9][0-9]{0,5}", re.IGNORECASE)
VALUE = re.compile(r"(?:[<>]=?)?\s*[+-]?(?:\d+(?:\.\d+)?|\.\d+)")
UNIT = re.compile(r"[A-Za-zµμ%0-9^/.*-]{1,24}")
DATE_HYPHENS = str.maketrans("‐‑‒–—−", "------")


class InvalidImage(Exception):
    pass


class OCRError(Exception):
    pass


@dataclass(frozen=True)
class Word:
    text: str
    conf: float
    x: int
    y: int
    w: int
    h: int

    @property
    def center_y(self):
        return self.y + self.h / 2


def valid_date(text):
    try:
        return date.fromisoformat(text).isoformat() == text
    except ValueError:
        return False


def combine(words):
    if not words:
        return None
    left = min(word.x for word in words)
    top = min(word.y for word in words)
    right = max(word.x + word.w for word in words)
    bottom = max(word.y + word.h for word in words)
    return [left, top, right - left, bottom - top]


def join(words):
    return " ".join(word.text for word in words)


def normalize_image(raw):
    if not raw or len(raw) > MAX_BYTES:
        raise InvalidImage("Upload a nonempty image of at most 10 MiB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw), formats=["PNG", "JPEG"]) as original:
                if original.width * original.height > MAX_PIXELS or getattr(original, "n_frames", 1) != 1:
                    raise InvalidImage("Use a single image of at most 20 megapixels.")
                oriented = ImageOps.exif_transpose(original).convert("RGBA")
                image = Image.new("RGB", oriented.size, "white")
                image.paste(oriented, mask=oriented.getchannel("A"))
                normalized = io.BytesIO()
                image.save(normalized, format="PNG")
                return normalized.getvalue(), *image.size
    except (UnidentifiedImageError, ValueError, SyntaxError, OSError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise InvalidImage("The upload could not be decoded as a PNG or JPEG.") from exc


def run_tesseract(image):
    command = [os.environ.get("TESSERACT_CMD", "tesseract"), "stdin", "stdout",
               "-l", "eng", "--psm", "6", "tsv"]
    try:
        result = subprocess.run(command, input=image, capture_output=True, check=True, timeout=25)
    except FileNotFoundError as exc:
        raise OCRError("Tesseract is unavailable. Install Tesseract and its English language data.") from exc
    except (subprocess.SubprocessError, OSError) as exc:
        raise OCRError("Tesseract failed or timed out. Check the English language data and try again.") from exc
    tsv = result.stdout.decode("utf-8", errors="replace")
    if not tsv.startswith("level\tpage_num\t"):
        raise OCRError("Tesseract did not produce a valid TSV response.")
    return tsv


def words_from_tsv(tsv):
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row["level"] == "5" and row["page_num"] == "1" and row["text"].strip():
            x, y, width, height = (int(row[key]) for key in ("left", "top", "width", "height"))
            yield Word(row["text"].strip(), float(row["conf"]), x, y, width, height)


def geometric_lines(words):
    # TSV blocks can split columns into different OCR lines, so group by image y.
    lines = []
    for word in sorted(words, key=lambda item: item.center_y):
        if not lines or abs(word.center_y - lines[-1][0].center_y) > max(6, word.h * 0.45):
            lines.append([])
        lines[-1].append(word)
    return [sorted(line, key=lambda item: item.x) for line in lines]


def is_header(line, width):
    matches = sum(
        any(word.text.casefold().strip(":") == label and abs(word.x / width - anchor) < .06
            for word in line)
        for label, anchor in zip(HEADERS, ANCHORS)
    )
    return matches >= 3


def find_version(lines):
    for line in lines:
        if re.match(r"^Version\s*:", join(line), re.IGNORECASE):
            tokens = [word for word in line if VERSION.fullmatch(word.text)]
            if len(tokens) == 1 and tokens[0].conf >= 65:
                return tokens[0].text.upper(), combine(tokens)
            return None, combine(line)
    return None, None


def extract_row(line, width, version, version_box):
    groups = {field: [] for field in COLUMNS}
    flags = {field: [] for field in FIELDS}
    for word in line:
        left, right = word.x / width, (word.x + word.w) / width
        center = (left + right) / 2
        column = next(i for i, edge in enumerate(BOUNDARIES[1:]) if center < edge or edge == 1)
        groups[COLUMNS[column]].append(word)
        for index, edge in enumerate(BOUNDARIES[1:-1]):
            if left - .012 <= edge <= right + .012:
                for field in COLUMNS[index:index + 2]:
                    if "Ambiguous column assignment" not in flags[field]:
                        flags[field].append("Ambiguous column assignment")

    if not (groups["value"] or groups["unit"] or groups["date"]):
        return None

    row_box = combine(line)
    fields = {}
    for column, field in enumerate(COLUMNS):
        words = groups[field]
        raw_text = join(words)
        text = raw_text.translate(DATE_HYPHENS) if field == "date" else raw_text
        box = combine(words)
        if box is None:
            # Empty cells still have a source region the reviewer can inspect.
            left, right = [round(edge * width) for edge in BOUNDARIES[column:column + 2]]
            box = [left, row_box[1] - min(5, row_box[1]), right - left, row_box[3] + 10]
            flags[field].append("Missing or unreadable")
        elif min(word.conf for word in words) < 72:
            flags[field].append("Low OCR confidence")
        fields[field] = {"text": text, "raw_text": raw_text, "bbox": box, "inferred": not bool(words)}

    validators = {"test": lambda value: any(char.isalpha() for char in value),
                  "value": VALUE.fullmatch, "unit": UNIT.fullmatch, "date": valid_date}
    for field, validate in validators.items():
        if fields[field]["text"] and not validate(fields[field]["text"]):
            flags[field].append("Unexpected field format")
    if not version:
        flags["version"].append("Missing or unreadable report version")
    fields["version"] = {"text": version or "", "bbox": version_box, "inferred": False}
    return {"page": 1, "bbox": row_box, "raw_text": join(line), "fields": fields,
            "flags": {key: reasons for key, reasons in flags.items() if reasons}}


def extract(tsv, width):
    lines = geometric_lines(words_from_tsv(tsv))
    header = next((i for i, line in enumerate(lines) if is_header(line, width)), None)
    version, version_box = find_version(lines[:header] if header is not None else lines)
    passages = [{"page": 1, "bbox": combine(line), "text": join(line),
                 "confidence": round(min(word.conf for word in line), 1)} for line in lines]
    rows = []
    if header is not None:
        for line in lines[header + 1:]:
            if re.match(r"^(Notes?|End of report)\b", join(line), re.IGNORECASE):
                break
            row = extract_row(line, width, version, version_box)
            if row:
                rows.append(row)

    if not lines:
        status = "unreadable"
    elif header is None:
        status = "unsupported_layout"
    elif not rows:
        status = "no_rows"
    elif any(row["flags"] for row in rows):
        status = "needs_review"
    else:
        status = "ready"
    return {"version": version, "status": status, "raw_text": "\n".join(p["text"] for p in passages),
            "rows": rows, "passages": passages}
