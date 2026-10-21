"""Search controls must reach real query/filter behavior, not just the UI."""
from datetime import date
import sqlite3
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

from database.db import Database
from models.paper import Paper, SearchMode
from providers.base import SearchQuery
from providers.crossref import CrossrefProvider, parse_item
from providers.openalex import OpenAlexProvider, parse_work
from providers.arxiv import ArxivProvider
from providers.semantic_scholar import SemanticScholarProvider, parse_paper
from services.search_service import SearchService
from ui.search_panel import SearchPanel
from utils.venues import venue_matches


def test_calendar_bounds_preserve_days_and_normalize_reversed_input():
    query = SearchQuery(text='cancer', date_from='2024-03-31', date_to='2024-02-29')
    assert query.date_from == '2024-02-29'
    assert query.date_to == '2024-03-31'
    assert query.start_date == '2024-02-29'


@pytest.mark.parametrize('value', ['2024-02-30', '2024-13-01', '2024-2-1'])
def test_invalid_query_dates_are_rejected(value):
    with pytest.raises(ValueError):
        SearchQuery(date_from=value)


def test_publication_date_precision_is_not_fabricated():
    full = parse_item({'title':['A Study'], 'issued':{'date-parts':[[2024,2,29]]}})
    month = parse_item({'title':['A Study'], 'issued':{'date-parts':[[2024,2]]}})
    year = parse_item({'title':['A Study'], 'issued':{'date-parts':[[2024]]}})
    assert [p.publication_date for p in (full,month,year)] == ['2024-02-29','2024-02','2024']
    deposited = parse_item({'title':['A Study'], 'created':{'date-parts':[[2024,2,29]]}})
    assert deposited.publication_date is None


def test_provider_dates_are_retained():
    assert parse_work({'title':'A Study','publication_date':'2024-03-12'}).publication_date == '2024-03-12'
    assert parse_paper({'title':'A Study','publicationDate':'2024-03-13'}).publication_date == '2024-03-13'


def test_date_filter_is_inclusive_and_does_not_admit_uncertain_dates(settings):
    query = SearchQuery(text='cancer', date_from='2024-03-01', date_to='2024-03-31')
    papers = [Paper(title='Start',publication_date='2024-03-01'),
              Paper(title='End',publication_date='2024-03-31'),
              Paper(title='Outside',publication_date='2024-04-01'),
              Paper(title='Year Only',year=2024), Paper(title='Unknown')]
    service = SearchService(settings,None,{'test':SimpleNamespace()})
    assert [p.title for p in service._apply_filters(papers,query)] == ['Start','End']
    query.extra['include_uncertain_dates'] = True
    assert [p.title for p in service._apply_filters(papers,query)] == ['Start','End','Year Only','Unknown']


def test_month_precision_within_full_month_is_usable(settings):
    query=SearchQuery(date_from='2024-02-01',date_to='2024-02-29')
    paper=Paper(title='Monthly Record',publication_date='2024-02')
    service=SearchService(settings,None,{'test':SimpleNamespace()})
    assert service._apply_filters([paper],query)==[paper]
    query.date_from='2024-02-15'
    assert service._apply_filters([paper],query)==[]


def test_publication_date_survives_database_and_json_roundtrip(tmp_path):
    paper=Paper(title='Stored Date',publication_date='2024-02-29',date_kind='submitted')
    assert Paper.from_dict(paper.to_dict()).publication_date=='2024-02-29'
    db=Database(tmp_path/'papers.db')
    try:
        paper.id=db.insert(paper)
        stored=db.get(paper.id)
        assert (stored.publication_date,stored.date_kind)==('2024-02-29','submitted')
        stored.publication_date='2024-03'
        db.update(stored)
        assert db.get(paper.id).publication_date=='2024-03'
    finally:
        db.close_all()


def test_schema_upgrade_preserves_old_library_and_makes_backup(tmp_path):
    path=tmp_path/'old.db'
    db=Database(path)
    db.insert(Paper(title='Keep My Library',year=2024))
    db.connection.execute('ALTER TABLE papers DROP COLUMN publication_date')
    db.connection.execute('ALTER TABLE papers DROP COLUMN date_kind')
    db.connection.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
    db.connection.commit()
    db.close_all()
    upgraded=Database(path)
    try:
        assert upgraded.count()==1
        assert upgraded.schema_version==4
        assert upgraded.get(1).publication_date is None
        assert list(tmp_path.glob('old.db.pre-migration.*'))
    finally:
        upgraded.close_all()


def test_crossref_and_openalex_receive_day_bounds(settings):
    query=SearchQuery(text='cancer',date_from='2024-02-29',date_to='2024-03-03')
    provider=CrossrefProvider(settings,None)
    assert 'from-pub-date:2024-02-29' in provider._params(query)['filter']
    calls=[]
    http=SimpleNamespace(get_json=lambda url,params,**kwargs: (calls.append(params) or {'results':[]}))
    OpenAlexProvider(settings,http).search(query)
    assert 'to_publication_date:2024-03-03' in calls[0]['filter']
    assert 'from_publication_date:2024-02-29' in calls[0]['filter']


def test_semantic_scholar_uses_date_filter_instead_of_year(settings):
    params=SemanticScholarProvider(settings,None)._params(SearchQuery(text='cancer',date_from='2024-02-29',date_to='2024-03-03'))
    assert params['publicationDateOrYear']=='2024-02-29:2024-03-03'
    assert 'year' not in params


def test_arxiv_query_preserves_terms_and_uses_submitted_date(settings):
    provider=ArxivProvider(settings,None)
    query=SearchQuery(text='robust neural network graph learning',date_from='2024-02-29',date_to='2024-03-03',extra={'match_mode':'fuzzy'})
    built=provider._build_query(query)
    assert 'submittedDate:[202402290000 TO 202403032359]' in built
    assert 'graph' in built and 'learning' in built
    assert 'all:"robust neural network graph learning"' not in built


def test_fuzzy_title_handles_partial_phrase_and_small_typo(settings):
    service=SearchService(settings,None,{'test':SimpleNamespace()})
    papers=[Paper(title='A Neural Network Approach to Protein Design'),Paper(title='Taxation and Labor Markets')]
    query=SearchQuery(text='neurl network',mode='title',extra={'match_mode':'fuzzy'})
    assert service._apply_filters(papers,query)==papers[:1]
    query.extra['match_mode']='exact'
    assert service._apply_filters(papers,query)==[]


def test_nature_selection_does_not_include_other_nature_journals():
    assert venue_matches('Nature',['Nature'])
    assert not venue_matches('Nature Communications',['Nature'])
    assert venue_matches('Nat. Commun.',['Nature Communications'])


def test_search_panel_has_suggestions_calendar_and_no_default_restriction(settings):
    app=QApplication.instance() or QApplication([])
    panel=SearchPanel(settings)
    try:
        assert panel.area_combo.count()>=9
        assert panel.query_combo.count()>20
        assert panel.venue_picker.combo.count()>20
        assert panel.direction_picker.combo.count()>20
        assert panel.date_from.calendarPopup() and panel.date_to.calendarPopup()
        assert panel.current_query()['venues']==[]
        assert panel.current_query()['directions']==[]
        panel.date_check.setChecked(True)
        panel.date_from.setDate(QDate(2024,2,29))
        panel.date_to.setDate(QDate(2024,3,3))
        payload=panel.current_query()
        assert payload['date_from']=='2024-02-29' and payload['date_to']=='2024-03-03'
        assert payload['match_mode']=='fuzzy'
        panel.reset()
        assert panel.current_query()['date_from'] is None
    finally:
        panel.close()


def test_area_switch_preserves_custom_query_and_selected_venues(settings):
    app=QApplication.instance() or QApplication([])
    panel=SearchPanel(settings)
    try:
        panel.query_edit.setText('my original custom query')
        panel.venue_picker.add_value('Nature')
        panel.area_combo.setCurrentIndex(1)
        assert panel.query_edit.text()=='my original custom query'
        assert panel.current_query()['venues']==['Nature']
        panel.query_edit.clear()
        panel.direction_picker.add_value('large language models')
        assert panel.current_query()['directions']==['large language models']
    finally:
        panel.close()


def test_direction_only_search_is_sent_and_keeps_all_selected_topics(settings):
    calls=[]
    def search(query):
        calls.append(query.text)
        return [Paper(title=query.text,doi='10.1000/'+str(len(calls)))]
    settings.config['search']['providers']=['test']
    service=SearchService(settings,None,{'test':SimpleNamespace(name='test',display_name='Test',search=search)})
    outcome=service.search(SearchQuery(extra={'directions':['cancer','climate change'],'match_mode':'fuzzy'}))
    assert calls==['cancer','climate change']
    assert len(outcome.papers)==2
    assert all(p.relevance_score>0 for p in outcome.papers)


def test_completer_finds_acronyms_and_partial_venue_names(settings):
    app=QApplication.instance() or QApplication([])
    panel=SearchPanel(settings)
    try:
        completion=panel.query_combo.completer()
        completion.setCompletionPrefix('llm')
        assert completion.completionCount()>0
        completion=panel.venue_picker.combo.completer()
        completion.setCompletionPrefix('commun')
        assert completion.completionCount()>0
        panel.query_combo.setEditText('大模型')
        assert panel.current_query()['text']=='large language models'
    finally:
        panel.close()


def test_multi_select_dropdown_click_reaches_payload(settings):
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt
    app=QApplication.instance() or QApplication([])
    panel=SearchPanel(settings)
    panel.resize(420,1100)
    panel.show()
    try:
        picker=panel.venue_picker
        picker.combo.showPopup()
        app.processEvents()
        index=picker.combo.model().index(0,0)
        QTest.mouseClick(picker.combo.view().viewport(),Qt.LeftButton,pos=picker.combo.view().visualRect(index).center())
        app.processEvents()
        assert panel.current_query()['venues']==['Nature']
        assert picker.combo.currentText()==''
    finally:
        panel.close()


def test_rolling_date_preset_and_custom_calendar_switch(settings):
    app=QApplication.instance() or QApplication([])
    panel=SearchPanel(settings)
    try:
        panel.date_preset.setCurrentIndex(panel.date_preset.findData('7d'))
        assert panel.current_query()['date_from']==QDate.currentDate().addDays(-6).toString('yyyy-MM-dd')
        assert panel.date_preset.currentData()=='7d'
        panel.date_from.setDate(QDate(2024,2,29))
        assert panel.date_preset.currentData()=='custom'
    finally:
        panel.close()


def test_recent_sort_orders_days_inside_same_year(settings):
    from ui.result_table import ResultTable
    app=QApplication.instance() or QApplication([])
    table=ResultTable()
    table.set_papers([Paper(title='Earlier',publication_date='2024-01-01'),Paper(title='Later',publication_date='2024-12-31')])
    table.set_sort_mode('recent')
    assert table.proxy.index(0,1).data()=='Later'
    table.close()


def test_cli_day_filters_and_invalid_calendar_date():
    from cli import build_parser
    parser=build_parser()
    args=parser.parse_args(['search','-q','cancer','--date-from','2024-02-29','--date-to','2024-03-03','--match-mode','exact'])
    assert args.date_from=='2024-02-29' and args.match_mode=='exact'
    with pytest.raises(SystemExit):
        parser.parse_args(['search','-q','cancer','--date-from','2024-02-30'])


def test_profile_editor_suggestion_does_not_overwrite_custom_terms(settings):
    from ui.dialogs import ProfileDialog
    app=QApplication.instance() or QApplication([])
    dialog=ProfileDialog(settings)
    try:
        assert dialog.keyword_suggestion.count()>20
        assert dialog.venue_suggestion.count()>20
        original=dialog.profile_data()['keywords']
        dialog.suggestion_area.setCurrentIndex(2)
        assert dialog.profile_data()['keywords']==original
    finally:
        dialog.close()


def test_journal_only_search_does_not_send_empty_fulltext_query(settings):
    calls=[]
    http=SimpleNamespace(get_json=lambda url,params,**kwargs: (calls.append(params) or {'message':{'items':[]}}))
    CrossrefProvider(settings,http).search(SearchQuery(venues=['Nature']))
    assert 'query.bibliographic' not in calls[0]
    assert calls[0]['sort']=='published'


def test_fuzzy_search_accepts_single_character_transposition(settings):
    service=SearchService(settings,None,{'test':SimpleNamespace()})
    query=SearchQuery(text='neural netwrok',mode='title',extra={'match_mode':'fuzzy'})
    paper=Paper(title='A Neural Network for Medical Imaging')
    assert service._apply_filters([paper],query)==[paper]


def test_failed_upgrade_backup_leaves_legacy_schema_unchanged(tmp_path,monkeypatch):
    path=tmp_path/'old.db'
    db=Database(path)
    db.insert(Paper(title='Keep This Record',year=2024))
    db.connection.execute('ALTER TABLE papers DROP COLUMN publication_date')
    db.connection.execute('ALTER TABLE papers DROP COLUMN date_kind')
    db.connection.commit()
    db.close_all()
    monkeypatch.setattr(Database,'backup',lambda *args,**kwargs:None)
    with pytest.raises(RuntimeError,match='backup'):
        Database(path)
    with sqlite3.connect(path) as connection:
        columns={row[1] for row in connection.execute('PRAGMA table_info(papers)')}
        assert 'publication_date' not in columns
        assert connection.execute('SELECT title FROM papers').fetchone()[0]=='Keep This Record'


def test_main_window_passes_calendar_and_match_selection_to_service(settings,monkeypatch):
    from app.main_window import MainWindow
    from services.app_service import AppService
    app=QApplication.instance() or QApplication([])
    settings.config['search']['providers']=[]
    service=AppService(settings)
    window=MainWindow(service)
    captured=[]
    monkeypatch.setattr(window.runner,'submit',lambda function,query,**kwargs:captured.append(query))
    try:
        panel=window.search_panel
        panel.query_edit.setText('neural network')
        panel.date_check.setChecked(True)
        panel.date_from.setDate(QDate(2024,2,29))
        panel.date_to.setDate(QDate(2024,3,3))
        panel._emit_search()
        assert len(captured)==1
        assert captured[0].start_date=='2024-02-29'
        assert captured[0].end_date=='2024-03-03'
        assert captured[0].extra['match_mode']=='fuzzy'
    finally:
        window._search_busy=False
        window.close()
        service.close()
