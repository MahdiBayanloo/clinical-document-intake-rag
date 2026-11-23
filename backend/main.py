"""HTTP API and the built React client."""

import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from . import ocr, qa, store

ROOT = Path(__file__).resolve().parents[1]
CASE = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
CaseId = Annotated[str, Field(pattern=CASE)]
Version = Annotated[str, Field(pattern=r"^V[1-9][0-9]{0,5}$")]


class Fields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    test: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=32)
    unit: str = Field(min_length=1, max_length=24)
    date: str = Field(min_length=10, max_length=10)
    version: Version


class Review(BaseModel):
    expected_revision: int = Field(ge=0)
    expected_document_version: Version | None
    fields: Fields


class Question(BaseModel):
    case_id: CaseId
    versions: list[Version] = Field(default_factory=list, max_length=10)
    question: str = Field(min_length=1, max_length=500)


class Preference(BaseModel):
    expected_revision: int = Field(ge=1)
    expected_document_version: Version


def ingest(db_path, case_id, filename, raw):
    with store.connect(db_path) as db:
        existing = store.find_upload(db, case_id, raw)
        if existing:
            return store.serialize_document(db, existing, full=True), False
    image, width, height = ocr.normalize_image(raw)
    tsv = ocr.run_tesseract(image)
    parsed = ocr.extract(tsv, width)
    with store.connect(db_path) as db:
        doc, created = store.insert_document(db, case_id, filename, raw, image, width, height, tsv, parsed)
        return store.serialize_document(db, doc, full=True), created


def create_app(db_path=None):
    path = Path(db_path or os.environ.get("INTAKE_DB", ROOT / "runtime" / "intake.sqlite3"))

    @asynccontextmanager
    async def lifespan(_app):
        store.initialize(path)
        yield

    app = FastAPI(title="Clinical Document Intake & Evidence RAG", lifespan=lifespan)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/documents")
    def documents():
        with store.connect(path) as db:
            ids = db.execute("SELECT id FROM documents ORDER BY id DESC")
            return [store.serialize_document(db, item["id"]) for item in ids]

    @app.post("/api/documents", status_code=201)
    def upload(response: Response, case_id: Annotated[str, Form(pattern=CASE)],
               file: Annotated[UploadFile, File()]):
        raw = file.file.read(ocr.MAX_BYTES + 1)
        if len(raw) > ocr.MAX_BYTES:
            raise HTTPException(413, "Upload a PNG or JPEG of at most 10 MiB.")
        filename = (file.filename or "scan").replace("\\", "/").rsplit("/", 1)[-1][:180]
        try:
            document, created = ingest(path, case_id, filename, raw)
        except ocr.InvalidImage as exc:
            raise HTTPException(422, str(exc)) from exc
        except ocr.OCRError as exc:
            raise HTTPException(503, str(exc)) from exc
        if not created:
            response.status_code = 200
        return document

    @app.get("/api/documents/{doc_id}")
    def document(doc_id: int):
        with store.connect(path) as db:
            result = store.serialize_document(db, doc_id, full=True)
        if result is None:
            raise HTTPException(404, "Report not found.")
        return result

    @app.get("/api/documents/{doc_id}/image")
    def image(doc_id: int):
        with store.connect(path) as db:
            item = db.execute("SELECT image FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if item is None:
            raise HTTPException(404, "Report not found.")
        return Response(item["image"], media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.patch("/api/rows/{row_id}")
    def review(row_id: int, request: Review):
        fields = request.fields.model_dump()
        if (not ocr.VALUE.fullmatch(fields["value"]) or not ocr.UNIT.fullmatch(fields["unit"])
                or not ocr.valid_date(fields["date"]) or not any(char.isalpha() for char in fields["test"])):
            raise HTTPException(422, "Use a readable test name, numeric value, unit, and ISO date (YYYY-MM-DD).")
        if re.search(r"[\x00-\x1f]", fields["test"]):
            raise HTTPException(422, "The test name must fit on one line.")
        with store.connect(path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = store.serialize_row(db, row_id)
            if row is None:
                raise HTTPException(404, "Result row not found.")
            document_version = store.serialize_document(db, row["document_id"])["version"]
            if row["revision"] != request.expected_revision or document_version != request.expected_document_version:
                raise HTTPException(409, "This report changed. Reload it before reviewing.")
            return store.review_row(db, row, fields)

    @app.post("/api/ask")
    def ask(request: Question):
        if not request.question.strip():
            raise HTTPException(422, "Enter a question.")
        with store.connect(path) as db:
            db.execute("BEGIN")
            return qa.answer_question(db, request.case_id, request.versions, request.question.strip())

    @app.post("/api/rows/{row_id}/prefer")
    def prefer(row_id: int, request: Preference):
        with store.connect(path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = store.serialize_row(db, row_id)
            if row is None:
                raise HTTPException(404, "Result row not found.")
            if (row["revision"] != request.expected_revision
                    or row["fields"]["version"] != request.expected_document_version):
                raise HTTPException(409, "Review the current row before choosing it.")
            if not row["preferred"]:
                store.prefer_row(db, row)
            return store.serialize_row(db, row_id, history=True)

    dist = ROOT / "frontend" / "dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def home():
        if (dist / "index.html").is_file():
            return FileResponse(dist / "index.html")
        return Response("Build the React app with npm ci and npm run build in frontend.", status_code=503)

    return app


app = create_app()
