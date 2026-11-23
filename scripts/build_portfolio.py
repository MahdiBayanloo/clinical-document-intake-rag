"""Build the no-install portfolio demo from an isolated, real intake run."""

import argparse
import base64
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend.main import ROOT, create_app

CASE = "portfolio-demo"


def read(response: httpx.Response):
    response.raise_for_status()
    return response.json()


def capture(client):
    reports = {}
    for name in ("clean-v1.png", "corrected-v2.png", "degraded-v1.jpg"):
        report = read(client.post(
            "/api/documents",
            data={"case_id": CASE},
            files={"file": (name, (ROOT / "fixtures" / name).read_bytes())},
        ))
        image = client.get(f"/api/documents/{report['id']}/image")
        image.raise_for_status()
        reports[report["id"]] = {
            "name": report["filename"],
            "version": report["version"],
            "width": report["width"],
            "height": report["height"],
            "image": "data:image/png;base64," + base64.b64encode(image.content).decode("ascii"),
        }

    row = next(item for item in report["rows"] if item["fields"]["test"].casefold() == "potassium")
    if row["fields"]["value"] == "4.2" or not row["pending_flags"].get("value"):
        raise RuntimeError("The degraded fixture needs a flagged potassium value for this walkthrough.")
    correction = read(client.patch(
        f"/api/rows/{row['id']}",
        json={
            "expected_revision": row["revision"],
            "expected_document_version": report["version"],
            "fields": {**row["fields"], "value": "4.2"},
        },
    ))

    question = "What changed between versions?"
    answer = read(client.post("/api/ask", json={"case_id": CASE, "question": question}))
    potassium = [item for item in answer["citations"]
                 if item["fields"]["test"] == row["fields"]["test"] and item["status"] != "needs_review"]
    if [(item["version"], item["fields"]["value"]) for item in potassium] != [("V1", "4.2"), ("V2", "5.1")]:
        raise RuntimeError("The captured answer needs both cited potassium values in order.")
    citation_ids = {item["id"] for item in potassium}
    conflict = next(line["text"] for line in answer["lines"] if set(line["citation_ids"]) == citation_ids)

    return {
        "reports": {item["document_id"]: reports[item["document_id"]] for item in potassium},
        "degraded_id": report["id"],
        "rows": [item["fields"] for item in report["rows"]],
        "original": row["ocr"]["fields"]["value"],
        "uncertainty": row["pending_flags"]["value"],
        "raw_text": row["ocr"]["raw_text"],
        "correction": correction["history"][0],
        "question": question,
        "conflict": conflict,
        "citations": potassium,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "portfolio-build" / "index.html")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="clinical-portfolio-") as temp, patch.dict(os.environ, {"OPENAI_MODEL": ""}):
        with TestClient(create_app(Path(temp) / "demo.sqlite3")) as client:
            data = capture(client)

    template = (ROOT / "portfolio" / "demo.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(template.replace("<!-- REPLAY_DATA -->", payload), encoding="utf-8")
    print(f"Built {args.output} from Tesseract OCR and FastAPI responses.")


if __name__ == "__main__":
    main()
