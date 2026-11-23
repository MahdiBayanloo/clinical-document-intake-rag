"""SQLite persistence, review history and the scoped FTS5 index."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .ocr import FIELDS

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL,
    digest TEXT NOT NULL,
    filename TEXT NOT NULL,
    version TEXT,
    reviewed_version TEXT,
    status TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    image BLOB NOT NULL,
    original BLOB NOT NULL,
    raw_text TEXT NOT NULL,
    tsv TEXT NOT NULL,
    UNIQUE (case_id, digest)
);
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    ocr TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY,
    row_id INTEGER NOT NULL REFERENCES results(id),
    revision INTEGER NOT NULL,
    fields TEXT NOT NULL,
    action TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (row_id, revision)
);
CREATE TABLE IF NOT EXISTS passages (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS preferences (
    id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL,
    test TEXT NOT NULL,
    specimen_date TEXT NOT NULL,
    row_id INTEGER NOT NULL REFERENCES results(id),
    revision INTEGER NOT NULL,
    version TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS docs_scope ON documents(case_id, version);
CREATE INDEX IF NOT EXISTS rows_doc ON results(document_id);
CREATE INDEX IF NOT EXISTS preferences_scope ON preferences(case_id, test, specimen_date);
CREATE VIRTUAL TABLE IF NOT EXISTS evidence_search USING fts5(
    text, key UNINDEXED, document_id UNINDEXED, kind UNINDEXED, tokenize='unicode61'
);
"""
DOCUMENT_FIELDS = ("id, case_id, filename, COALESCE(reviewed_version, version) AS version, "
                   "version AS ocr_version, status, width, height")


@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript(SCHEMA)


def index_row(db, row):
    fields = row["fields"]
    text = " ".join(["Result", fields["test"], fields["value"], fields["unit"], fields["date"],
                     fields["version"]])
    key = f"row-{row['id']}"
    db.execute("DELETE FROM evidence_search WHERE key = ?", (key,))
    db.execute("INSERT INTO evidence_search VALUES (?, ?, ?, 'row')",
               (text, key, row["document_id"]))


def find_upload(db, case_id, raw):
    existing = db.execute("SELECT id FROM documents WHERE case_id = ? AND digest = ?",
                          (case_id, hashlib.sha256(raw).hexdigest())).fetchone()
    return existing["id"] if existing else None


def insert_document(db, case_id, filename, original, image, width, height, tsv, parsed):
    db.execute("BEGIN IMMEDIATE")
    existing = find_upload(db, case_id, original)
    if existing:
        return existing, False
    doc = db.execute(
        """INSERT INTO documents (case_id, digest, filename, version, status, width, height,
           image, original, raw_text, tsv) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (case_id, hashlib.sha256(original).hexdigest(), filename, parsed["version"],
         parsed["status"], width, height, image, original, parsed["raw_text"], tsv),
    ).lastrowid
    for ocr in parsed["rows"]:
        row_id = db.execute("INSERT INTO results(document_id, ocr) VALUES (?, ?)",
                            (doc, json.dumps(ocr))).lastrowid
        index_row(db, serialize_row(db, row_id))
    for passage in parsed["passages"]:
        passage_id = db.execute("INSERT INTO passages(document_id, source) VALUES (?, ?)",
                                (doc, json.dumps(passage))).lastrowid
        db.execute("INSERT INTO evidence_search VALUES (?, ?, ?, 'ocr')",
                   (passage["text"], f"ocr-{passage_id}", doc))
    return doc, True


def serialize_row(db, row_id, history=False):
    item = db.execute(
        """SELECT r.*, d.case_id, COALESCE(d.reviewed_version, d.version) AS version
           FROM results r JOIN documents d ON r.document_id = d.id WHERE r.id = ?""",
        (row_id,),
    ).fetchone()
    if item is None:
        return None
    query = "SELECT revision, fields, action, recorded_at FROM reviews WHERE row_id = ? ORDER BY revision DESC"
    reviews = [{"revision": entry["revision"], "fields": json.loads(entry["fields"]),
                "action": entry["action"], "recorded_at": entry["recorded_at"]}
               for entry in db.execute(query if history else query + " LIMIT 1", (row_id,))]
    ocr = json.loads(item["ocr"])
    latest = reviews[0] if reviews else None
    fields = dict(latest["fields"]) if latest else {key: ocr["fields"][key]["text"] for key in FIELDS}
    fields["version"] = item["version"] or ""
    pending = {key: reasons for key, reasons in ocr["flags"].items()
               if key != "version" or not item["version"]}
    return {
        "id": row_id, "document_id": item["document_id"], "fields": fields, "ocr": ocr,
        "case_id": item["case_id"],
        "revision": latest["revision"] if latest else 0,
        "status": latest["action"] if latest else ("needs_review" if pending else "unreviewed"),
        "pending_flags": {} if latest else pending,
        "preferred": bool(latest and is_preferred(db, item["case_id"], row_id, latest["revision"], fields)),
        "history": reviews if history else [],
    }


def review_row(db, row, fields):
    changed = fields != {key: row["ocr"]["fields"][key]["text"] for key in FIELDS}
    db.execute("INSERT INTO reviews(row_id, revision, fields, action) VALUES (?, ?, ?, ?)",
               (row["id"], row["revision"] + 1, json.dumps(fields),
                "corrected" if changed else "confirmed"))
    changed_version = row["fields"]["version"] != fields["version"]
    if changed_version:
        db.execute("UPDATE documents SET reviewed_version = ? WHERE id = ?",
                   (fields["version"], row["document_id"]))
        for item in db.execute("SELECT id FROM results WHERE document_id = ?", (row["document_id"],)):
            index_row(db, serialize_row(db, item["id"]))
    result = serialize_row(db, row["id"], history=True)
    if not changed_version:
        index_row(db, result)
    return result


def is_preferred(db, case_id, row_id, revision, fields):
    preference = db.execute(
        """SELECT row_id, revision, version FROM preferences WHERE case_id = ? AND test = ?
           AND specimen_date = ? ORDER BY id DESC LIMIT 1""",
        (case_id, fields["test"].casefold(), fields["date"]),
    ).fetchone()
    return preference and (preference["row_id"], preference["revision"], preference["version"]) == (
        row_id, revision, fields["version"],
    )


def prefer_row(db, row):
    fields = row["fields"]
    db.execute(
        """INSERT INTO preferences(case_id, test, specimen_date, row_id, revision, version)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (row["case_id"], fields["test"].casefold(), fields["date"], row["id"], row["revision"], fields["version"]),
    )


def serialize_document(db, doc_id, full=False):
    doc = db.execute(f"SELECT {DOCUMENT_FIELDS} FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if doc is None:
        return None
    result = dict(doc)
    counts = db.execute(
        """SELECT count(*) AS row_count,
           count(*) FILTER (WHERE (CASE WHEN ? IS NULL THEN json_extract(ocr, '$.flags')
                ELSE json_extract(json_remove(ocr, '$.flags.version'), '$.flags') END) != '{}'
               AND NOT EXISTS (SELECT 1 FROM reviews WHERE reviews.row_id = results.id)) AS flagged_count
           FROM results WHERE document_id = ?""", (doc["version"], doc_id),
    ).fetchone()
    result.update(dict(counts))
    if result["status"] == "needs_review" and result["flagged_count"] == 0:
        result["status"] = "reviewed"
    if full:
        result["raw_text"] = db.execute("SELECT raw_text FROM documents WHERE id = ?", (doc_id,)).fetchone()[0]
        row_ids = db.execute("SELECT id FROM results WHERE document_id = ? ORDER BY id", (doc_id,))
        result["rows"] = [serialize_row(db, row["id"], history=True) for row in row_ids]
    return result


def scope_sql(case_id, versions):
    where = "d.case_id = ?"
    params = [case_id]
    if versions:
        placeholders = ", ".join("?" for _ in versions)
        where += f" AND COALESCE(d.reviewed_version, d.version) IN ({placeholders})"
        params.extend(versions)
    return where, params


def scope_documents(db, case_id, versions):
    where, params = scope_sql(case_id, versions)
    return list(db.execute(f"SELECT {DOCUMENT_FIELDS} FROM documents d WHERE {where} ORDER BY id",
                           params))


def scope_tests(db, case_id, versions):
    where, params = scope_sql(case_id, versions)
    query = f"""
        SELECT DISTINCT COALESCE((
            SELECT json_extract(reviews.fields, '$.test') FROM reviews WHERE reviews.row_id = r.id
            ORDER BY revision DESC LIMIT 1
        ), json_extract(r.ocr, '$.fields.test.text')) AS test
        FROM results r JOIN documents d ON r.document_id = d.id WHERE {where}
    """
    return [item["test"] for item in db.execute(query, params) if item["test"]]


def search(db, case_id, versions, expression, kind, limit=40):
    where, params = scope_sql(case_id, versions)
    matches = db.execute(
        f"""SELECT e.key, d.id, d.filename, COALESCE(d.reviewed_version, d.version) AS version
        FROM evidence_search e JOIN documents d ON e.document_id = d.id
        WHERE {where} AND evidence_search MATCH ? AND e.kind = ?
        ORDER BY bm25(evidence_search), d.id, e.rowid LIMIT ?""",
        [*params, expression, kind, limit],
    )
    return [make_evidence(db, match, kind) for match in matches]


def make_evidence(db, match, kind):
    source_id = int(match["key"].split("-")[-1])
    evidence = {"id": match["key"], "document_id": match["id"], "version": match["version"],
                "filename": match["filename"], "kind": kind}
    if kind == "row":
        row = serialize_row(db, source_id)
        fields = row["fields"]
        source = row["ocr"]
        value_source = source["fields"]["value"]
        bbox = value_source["bbox"] or source["bbox"]
        readable = not row["pending_flags"]
        text = (f"{fields['test']} — {fields['value']} {fields['unit']} "
                f"on {fields['date']} ({fields['version']}).") if readable else source["raw_text"]
        evidence.update(row_id=source_id, page=source["page"], bbox=bbox, text=text,
                        raw_text=source["raw_text"], status=row["status"], fields=fields,
                        revision=row["revision"],
                        inferred=value_source["inferred"], preferred=row["preferred"],
                        usable=readable and bool(match["version"]))
    else:
        source = json.loads(db.execute("SELECT source FROM passages WHERE id = ?", (source_id,)).fetchone()[0])
        evidence.update(source, status="raw_ocr", usable=source["confidence"] >= 72)
    return evidence
