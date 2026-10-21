"""Discipline-independent ranking, controls, and default-profile behavior."""

import threading
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from models.paper import Paper, SearchMode
from providers.base import SearchQuery
from services.app_service import AppService
from services.relevance_service import RelevanceService
from services.search_service import SearchService
from ui.search_panel import SearchPanel
from ui.result_table import ResultTable
from utils.config import load_settings


def test_new_install_does_not_preselect_any_discipline(tmp_path):
    settings = load_settings(home=tmp_path)
    assert settings.profile['keywords'] == []
    assert settings.profile['negative_terms'] == []
    assert settings.venues == []
    assert settings.directions == []


def test_custom_profile_survives_reload(tmp_path):
    settings = load_settings(home=tmp_path)
    settings.paths.profile_path.write_text('name: Economics\nkeywords:\n  - labor markets\n', encoding='utf-8')
    reloaded = load_settings(home=tmp_path)
    assert reloaded.profile['name'] == 'Economics'
    assert reloaded.profile['keywords'] == ['labor markets']


def test_current_medical_query_beats_saved_remote_sensing_profile(settings):
    medical = Paper(title='Medical Image Segmentation with Neural Networks', abstract='Medical image segmentation in clinical data.')
    remote = Paper(title='Optical SAR Change Detection for Building Damage Assessment', abstract='Robust fusion under fog and cloud degradation.', venue='CVPR')
    settings.config['search']['providers'] = ['test']
    provider = SimpleNamespace(name='test',display_name='Test',search=lambda query:[remote,medical])
    service = SearchService(settings,None,{'test':provider})
    result = service.search(SearchQuery(text='medical image segmentation'))
    assert result.papers[0] is medical
    assert medical.relevance_score > remote.relevance_score
    assert any('个人方向参考分' in item.get('note','') for item in medical.relevance_details)
    assert all('medical' not in item.get('terms',[]) or item.get('points',0)>=0 for item in medical.relevance_details)


def test_profile_sort_remains_available(settings):
    medical = Paper(title='Medical Image Segmentation')
    remote = Paper(title='Optical SAR Change Detection for Building Damage Assessment', abstract='fog cloud degradation',venue='CVPR')
    papers = [medical,remote]
    query = SearchQuery(text='medical image segmentation', extra={'sort_by':'profile'})
    RelevanceService(settings).score_papers(papers,query=query)
    assert papers[0] is remote


def test_recent_sort_is_explicit(settings):
    old = Paper(title='Graph Theory in Economics',year=2010)
    new = Paper(title='A Biology Study',year=2025)
    papers=[old,new]
    RelevanceService(settings).score_papers(papers,query=SearchQuery(text='graph theory',extra={'sort_by':'recent'}))
    assert papers[0] is new
    assert any(item.get('note')=='排序依据：发表年份' for item in new.relevance_details)


def test_missing_abstract_is_disclosed(settings):
    result=RelevanceService(settings).score_query(Paper(title='Labor Markets'),SearchQuery(text='labor markets'))
    assert '摘要缺失' in result.explain()
    assert '论文质量' in result.explain()


def test_identifier_query_uses_exact_identifier(settings):
    relevance=RelevanceService(settings)
    query=SearchQuery(text='10.1000/abc',mode=SearchMode.DOI)
    assert relevance.score_query(Paper(title='A Study',doi='10.1000/abc2'),query).score==0
    assert relevance.score_query(Paper(title='A Study',doi='10.1000/abc'),query).score==100


def test_search_panel_exposes_sort_selection(settings):
    app=QApplication.instance() or QApplication([])
    panel=SearchPanel(settings)
    assert panel.current_query()['sort_by']=='query'
    panel.sort_combo.setCurrentIndex(1)
    assert panel.current_query()['sort_by']=='profile'
    panel.venue_picker.combo.setEditText('Nature;经济研究')
    panel.direction_picker.combo.setEditText('clinical trials；labor markets')
    assert panel.current_query()['venues']==['Nature','经济研究']
    assert panel.current_query()['directions']==['clinical trials','labor markets']
    panel.reset()
    assert panel.current_query()['sort_by']=='query'
    assert panel.current_query()['directions']==[]
    panel.close()


def test_batch_uses_recovery_path_once_per_paper(settings):
    service=object.__new__(AppService)
    service.settings=settings
    papers=[Paper(title='A Biology Study'),Paper(title='A Physics Study')]
    calls=[]
    from services.download_service import DownloadResult
    from models.paper import PdfStatus
    service.download_one=lambda paper,*args: (calls.append(paper.title) or DownloadResult(title=paper.title,paper=paper,status=PdfStatus.VALIDATED))
    callbacks=[]
    results=service.download_many(papers,on_result=callbacks.append)
    assert len(calls)==len(callbacks)==len(results)==2
    assert [r.title for r in results]==[p.title for p in papers]


def test_result_table_respects_year_sort_and_can_switch_back():
    app=QApplication.instance() or QApplication([])
    table=ResultTable()
    table.set_papers([Paper(title='Older Relevant Study',year=2010,relevance_score=100),
                      Paper(title='Newer Study',year=2025,relevance_score=0)])
    table.set_sort_mode('recent')
    assert table.proxy.index(0,2).data()==2025
    table.set_sort_mode('query')
    assert table.proxy.index(0,2).data()==2010
    table.close()
