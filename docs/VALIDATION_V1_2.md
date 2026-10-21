# PaperRadar 1.2.0 validation

Date: 2026-10-20. Platform: Windows, Python 3.12.15, PySide6 6.7.3,
PyMuPDF 1.24.10, httpx 0.27.2, rapidfuzz 3.10.1, pytest 8.3.3,
PyInstaller 6.10.0. Tests and live checks used isolated data directories.
No real user database, stored PDFs, API credentials or personal profiles were used.

## Offline tests

The original source plus 21 new regression cases produced 224 passes and 20 failures.
Nineteen failures reproduced the newly identified identity, merge, request,
discovery and resume defects. One original long-path test assumed the pytest
temporary directory itself was short; that assumption was corrected.

The final source passes **262 tests, no skipped modules** (final full suite).
This includes real generated PDFs, a local HTTP server, SQLite tests,
single/batch acquisition, CLI resume, and Qt model/panel/table tests.

Changed historical expectations are intentional:

- A DOI occurring beside an unrelated title no longer validates a document.
- Different DOI records with an identical title are retained separately.
- A title appearing in an audit CSV is insufficient to count as successful.
- New installations do not acquire remote-sensing venues or negative terms.
- Download fixtures now contain the authors that their metadata claims.

New regressions cover DOI prefixes and URL decoding, citations on page one,
author/identifier conflicts, numbered/Part I-II titles, database/search agreement,
Crossref singleton parameters and server errors, late arXiv discovery, stale-link
recovery with retained attempt history, retries, and query/profile/year sorting.

## Live checks

Six exact DOI lookups returned the expected records across physics, biology,
statistics, ecology, medicine and economics. Crossref's collection-only `select`
parameter is omitted from singleton DOI requests. See the
[official API guidance](https://www.crossref.org/documentation/retrieve-metadata/rest-api/tips-for-using-the-crossref-rest-api/).

One initially prepared ecology sample contained the wrong DOI. The API returned
"Getting the measure of biodiversity", correctly exposing the mismatch. The
positive sample was corrected against the
[Nature publisher page](https://www.nature.com/articles/35002501), and the original
mismatch was retained separately as a negative control. The original check
record is preserved; it is not counted as a successful positive lookup.

Two real PDFs from the official arXiv host were downloaded and validated:
1602.03837 and 1706.03762. These checks verify acquisition and identity heuristics,
not scientific content or comprehensive cross-disciplinary retrieval quality.

## Packaged application

GUI and CLI are built as onedir applications; both report version 1.2.0.
The real GUI was rendered and inspected. The offscreen test needs
`QT_QPA_FONTDIR=C:\Windows\Fonts`; without it the test image has missing glyphs.
This is a headless test setting, not a change to the user's desktop fonts.

The packaged GUI self-test searched DOI 10.1103/PhysRevLett.116.061102,
downloaded a public PDF, returned VALIDATED, and stored an existing local file.
Semantic Scholar returned HTTP 429 during enrichment; other sources still
completed the workflow. The final build is checked again before installation.

Two combined shell checks exited with -1 before creating their expected logs;
separate arrangement/startup checks completed successfully. These interrupted
checks are not counted as successful validation.

## Data compatibility and limits

- Database schema remains version 3. Existing identifiers retain priority.
- Conflicting title fallbacks are rejected in both search and database lookup.
- The legacy title fallback remains when conflicting metadata is unavailable;
  similarity alone is still heuristic evidence rather than identity proof.
- Existing personal profiles remain unchanged. Neutral defaults apply to new data roots.
- Existing audit CSV headers and history are preserved on resume. New CSVs add
  attempt number and UTC time; older headers cannot store these new columns.
- Existing incorrect merges and already-validated PDFs have not been automatically
  repaired or rechecked. No original library files are deleted.
- The original broken development venv is preserved. Validation uses a separate
  Python 3.12 environment; the packaged executables do not depend on that venv.
- Multi-project profiles, query expansion/pagination, CVF/PMLR/author-page
  discovery, structured reading cards and a 30-50-paper held-out benchmark are
  not part of this first reliability release.

The live checks are small functional smoke tests. No corpus-level precision,
recall, full-text coverage percentage or universal identity accuracy is claimed.

Installation checks also reproduced a pre-existing recovery defect: access/lock/read-only errors were treated as corruption. Three failing regressions were added; only SQLITE_CORRUPT and SQLITE_NOTADB now trigger quarantine. Other errors leave the original file untouched. Automated GUI check failures exit instead of waiting in a hidden dialog.
