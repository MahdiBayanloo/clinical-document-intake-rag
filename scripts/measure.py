"""Compare a fresh OCR run with the synthetic fixture ground truth."""

import json
from pathlib import Path
from time import perf_counter

from backend.ocr import FIELDS, extract, normalize_image, run_tesseract

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def main():
    truth = json.loads((FIXTURES / "ground-truth.json").read_text())
    print(f"{'Fixture':22} {'Status':14} {'Rows':5} {'Flagged':7} {'Fields':7} {'Time (s)':>8}")
    for name, expected in truth["reports"].items():
        start = perf_counter()
        image, width, _ = normalize_image((FIXTURES / name).read_bytes())
        parsed = extract(run_tesseract(image), width)
        elapsed = perf_counter() - start
        fields = [{key: row["fields"][key]["text"] for key in FIELDS} for row in parsed["rows"]]
        total = max(len(fields), len(expected["rows"])) * len(FIELDS)
        matched = sum(actual[key] == target[key] for actual, target in zip(fields, expected["rows"]) for key in FIELDS)
        score = f"{matched}/{total}" if total else "—"
        flagged = sum(bool(row["flags"]) for row in parsed["rows"])
        print(f"{name:22} {parsed['status']:14} {len(fields):5} {flagged:7} {score:7} {elapsed:8.2f}")


if __name__ == "__main__":
    main()
