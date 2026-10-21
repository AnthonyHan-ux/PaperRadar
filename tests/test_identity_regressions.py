"""Cross-discipline regressions for false acceptance and destructive merging."""

import csv
from types import SimpleNamespace

import pytest

from cli import _existing_audit_keys
from database.db import Database
from models.paper import Paper, PdfCandidate, PdfStatus
from models.paper import clean_doi
from providers.base import ProviderError, SearchMode, SearchQuery
from providers.crossref import CrossrefProvider
from services.app_service import AppService
from services.download_service import DownloadAttempt, DownloadResult
from services.merge_service import MergeService
from services.oa_finder import OAFinder
from services.pdf_validator import PdfValidator


@pytest.mark.parametrize('suffix', ['abc2', 'abc-extra', 'abc/extra'])
def test_doi_prefix_is_not_identifier_evidence(suffix):
    paper = Paper(title='Genome Sequencing in Marine Organisms', doi='10.1000/abc')
    assert not PdfValidator._identifier_present('DOI: 10.1000/' + suffix, paper)


def test_exact_doi_url_is_identifier_evidence():
    paper = Paper(title='Genome Sequencing in Marine Organisms', doi='10.1000/abc')
    assert PdfValidator._identifier_present('https://doi.org/10.1000/ABC.', paper)


def test_doi_url_decoding_preserves_balanced_parentheses():
    assert clean_doi('https://doi.org/10.1000/ABC%28test%29?utm_source=link') == '10.1000/abc(test)'
    assert clean_doi('(doi:10.1000/abc(test)).') == '10.1000/abc(test)'


@pytest.mark.parametrize('header', [
    'Open Data for Social Research\nBrian Chen\n',
    'Open Data for Social Research\nAlice Smith\nDOI: 10.1000/other\n',
])
def test_matching_title_with_identity_conflict_requires_review(tmp_path, header):
    import fitz
    doc=fitz.open(); page=doc.new_page()
    page.insert_textbox(fitz.Rect(40,40,550,780),header+'Abstract: A study of public social data.',fontsize=11)
    path=tmp_path/'conflict.pdf'; doc.save(str(path)); doc.close()
    paper=Paper(title='Open Data for Social Research',authors=['Alice Smith'],doi='10.1000/target')
    result=PdfValidator(min_bytes=100).validate(path,paper)
    assert result.status==PdfStatus.TITLE_WARNING


def test_citation_on_first_page_does_not_verify_wrong_paper(tmp_path):
    import fitz
    target = 'Bayesian Inference for Rare Genetic Disorders'
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(fitz.Rect(40, 40, 550, 780),
        'Housing Supply and Urban Development\nAlice Jones\nAbstract: Economics study.\n'
        'References\n' + target + '\nDOI: 10.1000/target', fontsize=11)
    path = tmp_path / 'citation.pdf'
    document.save(str(path))
    document.close()
    result = PdfValidator(min_bytes=100).validate(path, Paper(title=target, doi='10.1000/target'))
    assert result.status == PdfStatus.WRONG_PAPER


@pytest.mark.parametrize('first,second', [
    ('Stochastic Optimization for Complex Systems Part I', 'Stochastic Optimization for Complex Systems Part II'),
    ('Clinical Outcomes in Population 2023', 'Clinical Outcomes in Population 2024'),
])
def test_numbered_related_papers_are_not_fuzzy_merged(first, second):
    papers = [Paper(title=first, authors=['Alice Smith']), Paper(title=second, authors=['Alice Smith'])]
    merged, _ = MergeService().merge([papers])
    assert len(merged) == 2


@pytest.mark.parametrize('kind', ['doi', 'arxiv_id', 'authors'])
def test_known_conflicts_block_title_merge_in_search_and_database(tmp_path, kind):
    title = 'Mechanisms of Adaptation in Complex Systems'
    values = {'doi': ('10.1000/one', '10.1000/two'),
              'arxiv_id': ('2401.12345', '2402.12345'),
              'authors': (['Alice Smith'], ['Brian Chen'])}
    a = Paper(title=title, **{kind: values[kind][0]})
    b = Paper(title=title, **{kind: values[kind][1]})
    merged, _ = MergeService().merge([[a], [b]])
    assert len(merged) == 2
    db = Database(tmp_path / 'papers.db')
    try:
        first, _, _ = db.upsert(a)
        second, created, _ = db.upsert(b)
        assert created and second != first
        assert db.count() == 2
    finally:
        db.close_all()


def test_crossref_singleton_does_not_send_collection_select(settings):
    calls = []
    def get_json(url, params, **kwargs):
        calls.append((url, params))
        return {'message': {'title': ['A Biology Study'], 'DOI': '10.1000/one'}}
    provider = CrossrefProvider(settings, SimpleNamespace(get_json=get_json))
    assert provider.search(SearchQuery(text='https://doi.org/10.1000/one', mode=SearchMode.DOI))
    assert calls[0][0].endswith('/works/10.1000/one')
    assert 'select' not in calls[0][1]


def test_crossref_outage_is_not_silently_reported_as_no_match(settings):
    def get_json(*args, **kwargs):
        raise ProviderError('Service unavailable', status=503)
    provider = CrossrefProvider(settings, SimpleNamespace(get_json=get_json))
    with pytest.raises(ProviderError):
        provider.search(SearchQuery(text='10.1000/one', mode=SearchMode.DOI))


def test_late_arxiv_id_becomes_download_candidate(settings):
    paper = Paper(title='Neural Control of Biological Networks', doi='10.1000/control')
    match = Paper(title=paper.title, doi=paper.doi, arxiv_id='2401.12345')
    provider = SimpleNamespace(available=lambda: True, get_by_doi=lambda doi: match)
    finding = OAFinder(settings, {'openalex': provider}).find(paper)
    assert 'https://arxiv.org/pdf/2401.12345' in [c.url for c in finding.candidates]


def test_stale_candidate_triggers_discovery_and_keeps_attempt_history():
    old = PdfCandidate('https://example.org/stale.pdf', 'old')
    new = PdfCandidate('https://example.org/current.pdf', 'new')
    paper = Paper(title='Open Data for Social Research', pdf_candidates=[old])
    app = object.__new__(AppService)
    calls, persisted = [], []
    app.db = SimpleNamespace(close_thread=lambda: None)
    app.oa_finder = SimpleNamespace(find=lambda p: p.add_candidates([new]))
    app._persist_download = lambda result, p: persisted.append(result)
    def download(p, candidates, **kwargs):
        urls = [c.url for c in candidates]
        calls.append(urls)
        success = new.url in urls
        return DownloadResult(title=p.title, paper=p,
            status=PdfStatus.VALIDATED if success else PdfStatus.DOWNLOAD_FAILED,
            attempts=[DownloadAttempt(url=c.url, provider=c.provider) for c in candidates])
    app.downloader = SimpleNamespace(download=download)
    result = app.download_one(paper)
    assert result.ok
    assert calls == [[old.url], [new.url]]
    assert [a.url for a in result.attempts] == [old.url, new.url]
    assert len(persisted) == 1


@pytest.mark.parametrize('status,pdf_status,skip', [
    ('FOUND', 'VALIDATED', True), ('FOUND', 'TITLE_WARNING', False),
    ('FOUND', 'DOWNLOAD_FAILED', False), ('ERROR', '', False),
    ('NO_MATCH', '', False), ('FOUND', 'PUBLIC_BUT_BLOCKED', False),
])
def test_resume_only_skips_verified_success(tmp_path, status, pdf_status, skip):
    path = tmp_path / 'audit.csv'
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['input_title', 'status', 'pdf_status'])
        writer.writeheader()
        writer.writerow({'input_title': 'A New Study', 'status': status, 'pdf_status': pdf_status})
    assert ('a new study' in _existing_audit_keys(path)) is skip


def test_resume_uses_latest_result_for_each_title(tmp_path):
    path = tmp_path / 'audit.csv'
    path.write_text('input_title,status,pdf_status\nA Study,FOUND,VALIDATED\nA Study,ERROR,\n', encoding='utf-8')
    assert 'a study' not in _existing_audit_keys(path)
