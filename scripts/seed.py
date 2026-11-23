"""Seed isolated synthetic demo cases by running the ordinary intake pipeline."""

import os

from backend.main import ROOT, ingest
from backend.store import initialize


def main():
    path = os.environ.get("INTAKE_DB", ROOT / "runtime" / "intake.sqlite3")
    initialize(path)
    for case_id, filename in (
        ("demo-1042", "clean-v1.png"),
        ("demo-1042", "corrected-v2.png"),
        ("demo-review", "degraded-v1.jpg"),
        ("demo-unreadable", "unreadable.png"),
    ):
        doc, created = ingest(path, case_id, filename, (ROOT / "fixtures" / filename).read_bytes())
        print(f"{'Imported' if created else 'Existing'} {case_id}/{filename}: "
              f"{doc['status']}, {doc['row_count']} rows, {doc['flagged_count']} flagged")


if __name__ == "__main__":
    main()
