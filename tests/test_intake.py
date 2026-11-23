import json as json_codec
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import ocr, qa, store
from backend.main import create_app

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    with TestClient(create_app(tmp_path / "test.sqlite")) as instance:
        yield instance


def upload(client, filename, case="case-demo", expected=201):
    path = FIXTURES / filename
    response = client.post("/api/documents", data={"case_id": case}, files={"file": (filename, path.read_bytes())})
    assert response.status_code == expected, response.text
    return response.json()


def get_row(doc, name="Potassium"):
    return next(row for row in doc["rows"] if row["fields"]["test"] == name)


def ask(client, question, versions=(), case="case-demo"):
    response = client.post("/api/ask", json={"case_id": case, "versions": list(versions), "question": question})
    assert response.status_code == 200
    return response.json()


def review(client, doc, row, **changes):
    return client.patch(f"/api/rows/{row['id']}", json={
        "expected_revision": row["revision"], "expected_document_version": doc["version"],
        "fields": {**row["fields"], **changes},
    })


def test_demo_upload_review_compare_prefer_and_scope(client):
    v1 = upload(client, "clean-v1.png")
    v2 = upload(client, "corrected-v2.png")
    uploaded = upload(client, "degraded-v1.jpg")
    assert upload(client, "degraded-v1.jpg", expected=200)["id"] == uploaded["id"]
    assert v1["row_count"] == v2["row_count"] == uploaded["row_count"] == 4
    assert v1["flagged_count"] == v2["flagged_count"] == 0
    assert uploaded["status"] == "needs_review"

    row = get_row(uploaded)
    assert row["pending_flags"]["value"]
    bbox = row["ocr"]["fields"]["value"]["bbox"]
    assert len(bbox) == 4 and bbox[2] > 0 and bbox[3] > 0
    page = client.get(f"/api/documents/{uploaded['id']}/image")
    assert page.status_code == 200 and page.content.startswith(b"\x89PNG")

    saved = review(client, uploaded, row, value="4.2")
    assert saved.status_code == 200, saved.text
    revised = saved.json()
    assert revised["status"] == "corrected"
    assert revised["fields"]["value"] == "4.2"
    assert revised["ocr"] == row["ocr"]
    assert revised["history"][0]["fields"]["value"] == "4.2"
    assert review(client, uploaded, row, value="6.4").status_code == 409

    result = ask(client, "What changed between V1 and V2?")
    assert result["mode"] == "extractive" and result["status"] == "conflict"
    assert [(c["version"], c["fields"]["value"]) for c in result["citations"]] == [("V1", "4.2"), ("V2", "5.1")]
    citations = {c["version"]: c for c in result["citations"]}
    assert citations["V1"]["document_id"] == uploaded["id"]
    assert citations["V1"]["bbox"] == bbox
    assert citations["V2"]["document_id"] == v2["id"]
    assert all(c["page"] == 1 and c["bbox"] for c in citations.values())
    assert {key for line in result["lines"] for key in line["citation_ids"]} == {c["id"] for c in citations.values()}

    only_v2 = ask(client, "What was the potassium result?", ["V2"])
    assert [c["document_id"] for c in only_v2["citations"]] == [v2["id"]]
    assert ask(client, "What was the potassium result?", case="other")["citations"] == []
    assert ask(client, "What changed between V1 and V2?", ["V2"])["status"] == "unresolved"

    selected = review(client, v2, get_row(v2)).json()
    assert selected["status"] == "confirmed"
    preference = {"expected_revision": selected["revision"], "expected_document_version": "V2"}
    preferred = client.post(f"/api/rows/{selected['id']}/prefer", json=preference)
    assert preferred.status_code == 200 and preferred.json()["preferred"]
    result = ask(client, "What changed between versions?")
    assert result["status"] == "resolved" and len(result["citations"]) == 2
    assert "reviewer selected V2" in result["lines"][0]["text"]
    assert review(client, v2, selected, value="5.2").status_code == 200
    assert ask(client, "What changed between versions?")["status"] == "conflict"


def test_unreadable_upload_gets_no_result_and_no_claim(client):
    doc = upload(client, "unreadable.png")
    assert doc["status"] == "unreadable"
    assert doc["version"] is None and doc["rows"] == [] and not doc["raw_text"]
    result = ask(client, "What was the potassium result?")
    assert result["status"] == "unresolved" and result["citations"] == []
    assert "no readable" in result["lines"][0]["text"]


def test_unsupported_and_ocr_passages(client):
    v1 = upload(client, "clean-v1.png")
    for question in ("What was the blood type?", "What is the reference range for potassium?",
                     "What medication is indicated for potassium?"):
        result = ask(client, question)
        assert result["status"] == "unresolved"
        assert result["citations"] == []
    result = ask(client, "What was the specimen?", ["V1"])
    assert result["status"] == "supported"
    assert result["citations"][0]["document_id"] == v1["id"]
    assert result["citations"][0]["kind"] == "ocr"
    assert result["lines"][0]["text"] == "Specimen: Plasma"


def test_version_correction_changes_scope_and_keeps_ocr(client):
    doc = upload(client, "clean-v1.png")
    row = get_row(doc)
    assert review(client, doc, row, version="V3").status_code == 200
    current = client.get(f"/api/documents/{doc['id']}").json()
    assert current["version"] == "V3" and current["ocr_version"] == "V1"
    assert all(r["fields"]["version"] == "V3" for r in current["rows"])
    assert current["rows"][0]["ocr"]["fields"]["version"]["text"] == "V1"
    assert ask(client, "What was the sodium result?", ["V3"])["citations"][0]["version"] == "V3"
    assert ask(client, "What was the sodium result?", ["V1"])["status"] == "unresolved"
    assert review(client, doc, doc["rows"][0], value="101").status_code == 409


def test_edited_test_name_updates_search_in_its_case(client):
    doc = upload(client, "clean-v1.png")
    row = get_row(doc, "Glucose")
    assert review(client, doc, row, test="Fasting glucose").status_code == 200
    result = ask(client, "What was the fasting glucose result?", ["V1"])
    assert result["status"] == "supported"
    assert result["citations"][0]["fields"]["test"] == "Fasting glucose"
    assert ask(client, "What was the glucose result?")["status"] == "unresolved"


def test_bad_upload_and_review_rejected(client):
    assert client.post("/api/documents", files={"file": ("test.jpg", b"broken image")},
                       data={"case_id": "case-demo"}).status_code == 422
    doc = upload(client, "clean-v1.png")
    assert review(client, doc, get_row(doc), value="unclear").status_code == 422
    assert review(client, doc, get_row(doc), date="2025-02-30").status_code == 422


def test_model_request_and_rejected_numeric_claim(client, monkeypatch):
    upload(client, "clean-v1.png")
    upload(client, "corrected-v2.png", case="other")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-api-key")

    def model_response(url, headers, json, **kwargs):
        assert url == "https://model.example/v1/chat/completions"
        assert headers["Authorization"] == "Bearer synthetic-api-key"
        context = json_codec.loads(json["messages"][1]["content"])
        assert context["required_ids"] == [context["evidence"][0]["id"]]
        assert len(context["evidence"]) == 1 and context["evidence"][0]["version"] == "V1"
        evidence = context["evidence"][0]
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"message": {"content": json_codec.dumps({
                "claims": [{"id": evidence["id"], "text": evidence["text"]}],
            })}}],
        })

    monkeypatch.setattr(qa.httpx, "post", model_response)
    good = ask(client, "What was the potassium result?", ["V1"])
    assert good["mode"] == "llm" and good["lines"][0]["citation_ids"]
    assert "4.2" in good["lines"][0]["text"]

    def fake_model(url, **kwargs):
        item = good["citations"][0]
        content = json_codec.dumps({"claims": [{"id": item["id"], "text": item["text"].replace("4.2", "9.9")}]})
        return httpx.Response(200, request=httpx.Request("POST", url),
                              json={"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(qa.httpx, "post", fake_model)
    fallback = ask(client, "What was the potassium result?", ["V1"])
    assert fallback["mode"] == "extractive" and "failed citation validation" in fallback["mode_detail"]
    assert "9.9" not in fallback["lines"][0]["text"]


@pytest.mark.parametrize("reason", ["foreign_id", "wrong_unit", "missing_id"])
def test_claim_validation_rejects_spoofed_evidence(reason):
    item = {"id": "row-1", "text": "Potassium — 4.2 mmol/L on 2025-02-17 (V1).", "usable": True}
    claim = {"id": item["id"], "text": item["text"]}
    required = {item["id"]}
    if reason == "foreign_id":
        claim["id"] = "row-91"
    elif reason == "wrong_unit":
        claim["text"] = claim["text"].replace("mmol/L", "mg/dL")
    else:
        required.add("row-2")
    with pytest.raises(ValueError):
        qa.validate_claims({"claims": [claim]}, {item["id"]: item}, required)


def test_ambiguous_column_word_stays_flagged():
    # Synthetic TSV exercises a geometric edge case independent of OCR engine behavior.
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    words = [
        (62, 60, 55, "Version:"), (132, 60, 26, "V1"),
        (62, 100, 35, "Test"), (420, 100, 40, "Value"), (600, 100, 33, "Unit"), (780, 100, 45, "Date"),
        (62, 150, 80, "Potassium"), (387, 150, 33, "4.2"), (600, 150, 45, "mmol/L"),
        (780, 150, 95, "2025-02-17"),
    ]
    tsv = "\n".join([header, *(f"5\t1\t1\t1\t1\t1\t{x}\t{y}\t{width}\t20\t95\t{text}"
                               for x, y, width, text in words)])
    row = ocr.extract(tsv, 1000)["rows"][0]
    assert "Ambiguous column assignment" in row["flags"]["test"]
    assert "Ambiguous column assignment" in row["flags"]["value"]
    assert row["fields"]["value"]["bbox"] == [387, 150, 33, 20]
    assert row["raw_text"] == "Potassium 4.2 mmol/L 2025-02-17"


def test_db_keeps_raw_tsv_and_exact_original(client, tmp_path):
    doc = upload(client, "clean-v1.png")
    with store.connect(tmp_path / "test.sqlite") as db:
        raw = db.execute("SELECT original, tsv FROM documents WHERE id = ?", (doc["id"],)).fetchone()
    assert raw["original"] == (FIXTURES / "clean-v1.png").read_bytes()
    assert raw["tsv"].startswith("level\tpage_num\t")
    assert "\tPotassium\n" in raw["tsv"]


def test_low_confidence_copy_of_an_identical_readable_value_adds_no_uncertainty():
    fields = {"test": "Glucose", "date": "2025-02-17", "value": "98", "unit": "mg/dL", "version": "V1"}
    readable = {"id": "row-1", "version": "V1", "fields": fields, "usable": True, "preferred": False,
                "status": "unreviewed", "text": "Glucose — 98 mg/dL on 2025-02-17 (V1)."}
    flagged = {**readable, "id": "row-2", "usable": False, "status": "needs_review"}
    answer, _ = qa.summarize_rows([readable, flagged], ["V1"], False, ["Glucose"], [])
    assert answer["status"] == "supported"
    assert [item["id"] for item in answer["citations"]] == ["row-1"]

    flagged["fields"] = {**fields, "value": "89"}
    answer, _ = qa.summarize_rows([readable, flagged], ["V1"], False, ["Glucose"], [])
    assert answer["status"] == "unresolved"
    assert [item["id"] for item in answer["citations"]] == ["row-1", "row-2"]
