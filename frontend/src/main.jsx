import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const fields = [
  ['test', 'Test'], ['value', 'Value'], ['unit', 'Unit'], ['date', 'Date'], ['version', 'Version'],
];
const stateLabels = {
  ready: 'OCR complete', needs_review: 'Needs review', reviewed: 'Reviewed',
  unreadable: 'Unreadable scan', unsupported_layout: 'Unsupported layout', no_rows: 'No rows found',
  unreviewed: 'OCR', confirmed: 'Confirmed', corrected: 'Corrected',
  supported: 'Supported', unresolved: 'Unresolved', conflict: 'Conflict', resolved: 'Resolved',
};

function Badge({ status }) {
  return <span className={`badge ${status}`}>{stateLabels[status] || status}</span>;
}

async function request(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(item => item.msg).join(' ') : data.detail;
    throw new Error(detail || 'The request could not be completed.');
  }
  return data;
}

function write(path, data, method = 'POST') {
  return request(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
}

function UploadDialog({ currentCase, onClose, onUpload }) {
  const dialog = useRef(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { dialog.current.showModal(); }, []);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const doc = await request('/api/documents', { method: 'POST', body: new FormData(event.currentTarget) });
      await onUpload(doc);
      onClose();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <dialog ref={dialog} onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}
      aria-labelledby="upload-title">
      <h2 id="upload-title">Upload a lab report</h2>
      <p>Use a PNG or JPEG in the supported four-column layout.</p>
      <form className="upload-form" onSubmit={submit}>
        <label>Case ID
          <input name="case_id" defaultValue={currentCase || 'demo-1042'}
            pattern="[A-Za-z0-9][A-Za-z0-9_\-]{0,63}" maxLength={64} required />
        </label>
        <label>Report image
          <input name="file" type="file" accept="image/png,image/jpeg" required />
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        <div className="actions">
          <button type="button" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className="primary" disabled={busy}>{busy ? 'Running OCR…' : 'Upload and extract'}</button>
        </div>
      </form>
    </dialog>
  );
}

function SourceViewer({ doc, source, viewerRef }) {
  const box = source?.documentId === doc.id ? source?.bbox : null;
  const percent = (size, total) => `${size / total * 100}%`;
  const overlay = box && {
    left: percent(box[0], doc.width), top: percent(box[1], doc.height),
    width: percent(box[2], doc.width), height: percent(box[3], doc.height),
  };
  return (
    <section className="panel source" ref={viewerRef} aria-labelledby="source-title">
      <div className="panel-heading">
        <div><h2 id="source-title">Original scan</h2><p className="muted">{doc.filename} · Page 1</p></div>
        {source?.label && source.documentId === doc.id && <span className="source-tag">{source.label}</span>}
      </div>
      <div className="scan-frame">
        <div className="scan">
          <img src={`/api/documents/${doc.id}/image`} alt={`Original page of ${doc.filename}`}
            width={doc.width} height={doc.height} />
          {overlay && <div className={`highlight ${source.inferred ? 'approximate' : ''}`} style={overlay}
            role="img" aria-label={source.inferred ? 'Highlighted expected source region' : 'Highlighted source region'} />}
        </div>
      </div>
      <p className="scan-caption" aria-live="polite">
        {source?.documentId !== doc.id ? 'Select a field or citation to locate it on the scan.'
          : box ? (source.inferred ? 'Dashed outline marks the expected cell. OCR did not locate text there.' : 'Highlighted on page 1.')
            : 'OCR could not locate this field on the scan.'}
      </p>
    </section>
  );
}

function RowEditor({ doc, row, onSave, onSource, onClose }) {
  const [values, setValues] = useState(row.fields);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const isChanged = fields.some(([key]) => values[key].trim() !== row.fields[key]);
  const review = { expected_revision: row.revision, expected_document_version: doc.version };

  async function act(event, prefer = false) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const path = `/api/rows/${row.id}${prefer ? '/prefer' : ''}`;
      const saved = await write(path, prefer ? review : { ...review, fields: values }, prefer ? 'POST' : 'PATCH');
      await onSave(saved, prefer);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="editor" onSubmit={event => act(event)}>
      <div className="editor-heading">
        <div><h3>Review {row.fields.test || 'result'}</h3>
          {row.preferred && <span className="preferred-label">Preferred result</span>}
        </div>
        <button type="button" className="quiet" onClick={onClose}>Close review</button>
      </div>
      <div className="edit-fields">
        {fields.map(([key, label]) => (
          <div key={key}>
            <label htmlFor={`edit-${key}`}>{label}</label>
            <input id={`edit-${key}`} value={values[key]} required
              onFocus={() => onSource(row, key)}
              onChange={event => setValues({ ...values, [key]: event.target.value })}
              aria-describedby={row.pending_flags[key] ? `flag-${key}` : undefined} />
            {row.pending_flags[key] && <small id={`flag-${key}`} className="flag">{row.pending_flags[key].join(', ')}</small>}
          </div>
        ))}
      </div>
      {values.version !== (doc.version || '') && <p className="flag">Saving this version updates the entire report.</p>}
      <p className="ocr-line">Original OCR <code>{row.ocr.raw_text}</code></p>
      {row.history.length > 0 && (
        <details className="audit">
          <summary>Review history ({row.history.length})</summary>
          <ul>{row.history.map(entry => (
            <li key={entry.revision}>
              {entry.action === 'corrected' ? 'Corrected' : 'Confirmed'} · revision {entry.revision}
              {' · '}{new Date(entry.recorded_at).toLocaleString()}
              <div>{fields.map(([key]) => entry.fields[key]).join(' · ')}</div>
            </li>
          ))}</ul>
        </details>
      )}
      {error && <p role="alert" className="error">{error}</p>}
      <div className="actions">
        {row.revision > 0 && !row.preferred &&
          <button type="button" disabled={busy || isChanged} onClick={event => act(event, true)}>Use as preferred result</button>}
        <button type="submit" className="primary" disabled={busy || (!isChanged && row.revision > 0)}>
          {isChanged ? 'Save correction' : 'Confirm row'}
        </button>
      </div>
    </form>
  );
}

function Results({ doc, selectedRow, source, onSource, onEdit, onSave }) {
  const row = doc.rows.find(item => item.id === selectedRow);
  const empty = {
    unreadable: 'OCR found no readable text. Upload a clearer scan to extract results.',
    unsupported_layout: 'OCR found text, but the supported Test / Value / Unit / Date header could not be located.',
    no_rows: 'The table header was found, but no result rows could be extracted.',
  };
  return (
    <section className="panel results" aria-labelledby="results-title">
      <div className="panel-heading">
        <div><h2 id="results-title">Extracted results</h2>
          <p className="muted">{doc.row_count} {doc.row_count === 1 ? 'row' : 'rows'} · {doc.flagged_count} flagged</p>
        </div>
        <Badge status={doc.status} />
      </div>
      {doc.rows.length === 0 ? <p className="empty">{empty[doc.status] || 'This report has no rows.'}</p> :
        <div className="table-scroll">
          <table>
            <thead>
              <tr>{fields.map(([key, label]) => <th scope="col" key={key}>{label}</th>)}<th scope="col">Review</th></tr>
            </thead>
            <tbody>
              {doc.rows.map(item => (
                <tr key={item.id} className={item.id === selectedRow ? 'editing' : ''}>
                  {fields.map(([key, label]) => (
                    <td key={key}>
                      <button type="button"
                        className={`cell ${source?.rowId === item.id && source?.field === key ? 'active' : ''}`}
                        onClick={() => onSource(item, key)}
                        aria-label={`Show ${item.fields.test || 'row'} ${label.toLowerCase()} source`}
                        title={item.pending_flags[key]?.join(', ') || `Highlight ${label.toLowerCase()} on scan`}>
                        {item.fields[key] || '—'}{item.pending_flags[key] && <span className="flag-dot" title="Flagged" />}
                      </button>
                    </td>
                  ))}
                  <td>
                    <button className="text-button" onClick={() => onEdit(item.id)}
                      aria-label={`Review ${item.fields.test || 'result'}`}>Review</button>
                    <Badge status={item.status} />
                    {item.preferred && <span className="preferred-label">Preferred</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>}
      {row && <RowEditor key={`${row.id}-${row.revision}-${doc.version}-${row.preferred}`}
        doc={doc} row={row} onSource={onSource} onSave={onSave} onClose={() => onEdit(null)} />}
      {doc.raw_text && <details className="raw">
        <summary>Raw OCR text</summary><pre>{doc.raw_text}</pre>
      </details>}
    </section>
  );
}

function Questions({ caseId, versions, onCitation }) {
  const [question, setQuestion] = useState('');
  const [version, setVersion] = useState('');
  const [answer, setAnswer] = useState(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const selected = versions.includes(version) ? version : '';

  async function ask(event) {
    event.preventDefault();
    setWorking(true);
    setAnswer(null);
    setError('');
    try {
      setAnswer(await write('/api/ask', { case_id: caseId, versions: selected ? [selected] : [], question }));
    } catch (err) {
      setError(err.message);
    } finally {
      setWorking(false);
    }
  }
  const citations = new Map(answer?.citations.map((item, index) => [item.id, { ...item, number: index + 1 }]));
  return (
    <section className="panel questions" aria-labelledby="questions-title">
      <div className="panel-heading">
        <div><h2 id="questions-title">Ask the evidence</h2><p className="muted">Answers cite exact locations in this case’s reports.</p></div>
      </div>
      <form onSubmit={ask} className="ask-form">
        <label className="question-input">Question
          <input placeholder="What changed between report versions?" value={question} maxLength={500} required
            onChange={event => setQuestion(event.target.value)} />
        </label>
        <label>Report version
          <select value={selected} onChange={event => {
            setVersion(event.target.value); setAnswer(null);
          }}>
            <option value="">All report versions</option>
            {versions.map(item => <option key={item}>{item}</option>)}
          </select>
        </label>
        <button type="submit" className="primary" disabled={working}>{working ? 'Searching…' : 'Ask question'}</button>
      </form>
      <div className="suggestions" aria-label="Example questions">
        {['What changed between versions?', 'What was the specimen?', 'What was the blood type?'].map(example => (
          <button type="button" key={example} onClick={() => setQuestion(example)}>{example}</button>
        ))}
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {answer && (
        <div className="answer" aria-live="polite">
          <div className="answer-label">
            <Badge status={answer.status} />
            <strong>{answer.mode === 'llm' ? 'LLM · validated evidence' : 'Extractive fallback'}</strong>
            {answer.mode_detail && <span className="muted">{answer.mode_detail}</span>}
          </div>
          {answer.lines.map((item, index) => (
            <p key={index}>{item.text} {item.citation_ids.map(key => {
              const citation = citations.get(key);
              return <button key={key} className="citation" type="button" onClick={() => onCitation(citation)}
                aria-label={`Open citation ${citation.number}, ${citation.filename}, page ${citation.page}`}>
                [{citation.number}]
              </button>;
            })}</p>
          ))}
          {answer.citations.length > 0 && (
            <ol className="sources" aria-label="Answer sources">
              {answer.citations.map(item => (
                <li key={item.id}>
                  <button className="text-button" onClick={() => onCitation(item)}>
                    {item.filename} · {item.version || 'Version unknown'} · page {item.page}
                  </button>
                  <span className="muted">
                    {item.kind === 'ocr' ? 'OCR passage' : item.status === 'corrected' ? 'Reviewer correction' : 'Result row'}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </section>
  );
}

function App() {
  const [documents, setDocuments] = useState([]);
  const [caseId, setCaseId] = useState('');
  const [docId, setDocId] = useState(null);
  const [document, setDocument] = useState(null);
  const [selectedRow, setSelectedRow] = useState(null);
  const [source, setSource] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const viewer = useRef(null);

  useEffect(() => {
    request('/api/documents').then(items => {
      setDocuments(items);
      const first = items.find(item => item.case_id === 'demo-1042') || items[0];
      if (first) {
        setCaseId(first.case_id);
        setDocId(first.id);
      }
    }).catch(err => setError(err.message)).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (docId === null) return;
    let cancelled = false;
    request(`/api/documents/${docId}`).then(doc => {
      if (!cancelled) setDocument(doc);
    }).catch(err => { if (!cancelled) setError(err.message); });
    return () => { cancelled = true; };
  }, [docId]);

  const active = document?.id === docId ? document : null;
  useEffect(() => {
    if (source?.citation && active?.id === source.documentId) {
      viewer.current?.scrollIntoView({ block: 'start', behavior: 'instant' });
    }
  }, [source, active?.id]);

  function openDocument(id) {
    setDocId(id);
    setSelectedRow(null);
    setSource(null);
    setError('');
    setMessage('');
  }

  async function uploaded(doc) {
    const items = await request('/api/documents');
    setDocuments(items);
    setCaseId(doc.case_id);
    openDocument(doc.id);
    setDocument(doc);
    setMessage(`Processed ${doc.filename}.`);
  }

  async function saved(row, preferred) {
    const [doc, items] = await Promise.all([request(`/api/documents/${row.document_id}`), request('/api/documents')]);
    setDocument(current => current?.id === doc.id ? doc : current);
    setDocuments(items);
    setMessage(preferred ? 'Preferred result saved.' : row.status === 'corrected' ? 'Correction saved.' : 'Row confirmed.');
  }

  function showField(row, field) {
    setSource({ ...row.ocr.fields[field], documentId: row.document_id, rowId: row.id, field,
      label: `${row.fields.test || 'Result'} · ${fields.find(([key]) => key === field)[1]}` });
  }

  function showCitation(item) {
    openDocument(item.document_id);
    setSource({ ...item, documentId: item.document_id, citation: true,
      rowId: item.row_id, field: item.kind === 'row' ? 'value' : null, label: `Citation [${item.number}]` });
  }

  const cases = [...new Set(documents.map(doc => doc.case_id))].sort();
  const reports = documents.filter(doc => doc.case_id === caseId);
  const versions = [...new Set(reports.map(doc => doc.version).filter(Boolean))]
    .sort((a, b) => Number(a.slice(1)) - Number(b.slice(1)));

  return (
    <>
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none"><path d="M5 3.5h10l4 4v13H5v-17Z M15 3.5v4h4 M8 13h8 M12 9v8"
              stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
          </span>
          <div><h1>Clinical Document Intake <span>&amp; Evidence RAG</span></h1><p>Lab report review</p></div>
        </div>
        <button className="primary" onClick={() => setUploading(true)}>+ Upload report</button>
      </header>
      <main className="layout">
        <aside className="sidebar" aria-label="Case navigation">
          <label htmlFor="case-select">Case</label>
          <select id="case-select" value={caseId} disabled={!cases.length} onChange={event => {
            const next = event.target.value;
            setCaseId(next);
            openDocument(documents.find(doc => doc.case_id === next)?.id ?? null);
          }}>{cases.length ? cases.map(id => <option key={id}>{id}</option>) : <option value="">No cases yet</option>}</select>
          <h2>Reports <span>{reports.length}</span></h2>
          <div className="report-list">
            {reports.map(doc => (
              <button key={doc.id} className={`report ${doc.id === docId ? 'chosen' : ''}`}
                aria-current={doc.id === docId ? 'true' : undefined} onClick={() => openDocument(doc.id)}>
                <div><strong>{doc.filename}</strong><Badge status={doc.status} /></div>
                <span className="muted">{doc.version || 'Version unknown'} · {doc.row_count} {doc.row_count === 1 ? 'row' : 'rows'}</span>
              </button>
            ))}
          </div>
        </aside>
        <div className="workspace">
          {error && <p role="alert" className="error">{error}</p>}
          {message && <p className="notice" role="status">{message}</p>}
          {active ? (
            <>
              <div className="review-heading">
                <div><h2>{active.filename}</h2><p>{active.version ? `Report ${active.version}` : 'Report version unknown'}</p></div>
              </div>
              <div className="review-grid">
                <SourceViewer doc={active} source={source} viewerRef={viewer} />
                <Results doc={active} selectedRow={selectedRow} source={source} onSource={showField}
                  onEdit={setSelectedRow} onSave={saved} />
              </div>
            </>
          ) : <div className="panel empty">{loading || docId ? 'Loading reports…' : 'Upload a lab report to start a case.'}</div>}
          {caseId && <Questions key={caseId} caseId={caseId} versions={versions} onCitation={showCitation} />}
        </div>
      </main>
      {uploading && <UploadDialog currentCase={caseId} onClose={() => setUploading(false)} onUpload={uploaded} />}
    </>
  );
}

createRoot(document.getElementById('root')).render(<App />);
