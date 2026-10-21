"""Regressions from the reported Chinese-query + direction + venues search."""
from types import SimpleNamespace
import time

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow
from models.paper import Paper
from providers.base import SearchQuery
from services.app_service import AppService
from services.search_service import ProviderReport, SearchOutcome, SearchService
from ui.result_table import ResultTable
from utils.search_catalog import resolve_term, topic_aliases
from utils.search_matching import text_matches


def test_image_degradation_has_explicit_bilingual_mapping():
    assert resolve_term('图像退化') == 'image degradation'


def test_multimodal_direction_matches_common_metadata_wording():
    assert any(text_matches(alias,'Multimodal fusion for degraded images')
               for alias in topic_aliases('multimodal learning'))


def test_zero_results_include_filter_counts_and_actual_query(settings):
    rows=[Paper(title='Image degradation and multimodal fusion',venue='CVPR',doi='10.1000/one'),
          Paper(title='Image degradation and multimodal fusion',venue='Another Journal',doi='10.1000/two'),
          Paper(title='Image degradation in single image restoration',venue='Nature',doi='10.1000/three')]
    settings.config['search']['providers']=['test']
    provider=SimpleNamespace(name='test',display_name='Test',search=lambda query:rows)
    service=SearchService(settings,None,{'test':provider})
    query=SearchQuery(text='图像退化',venues=['Science'],extra={'directions':['multimodal learning'],'match_mode':'fuzzy'})
    result=service.search(query)
    assert not result.papers
    assert result.query.text=='image degradation'
    assert result.filter_counts['venue']==3
    assert '筛选' in result.feedback_text()
    assert 'image degradation' in result.feedback_text()


def test_blank_table_is_replaced_with_visible_completed_feedback():
    app=QApplication.instance() or QApplication([])
    table=ResultTable()
    result=SearchOutcome(SearchQuery(text='something'))
    result.reports=[ProviderReport(provider='test',display_name='Test',count=0)]
    table.show_outcome(result)
    assert '未找到' in table.feedback_title.text()
    assert 'Test' in table.feedback_detail.text()
    assert not table.feedback_box.isHidden()
    table.close()


def test_all_sources_failed_are_not_described_as_no_papers():
    result=SearchOutcome(SearchQuery(text='test'))
    result.reports=[ProviderReport(provider='test',display_name='Test',error='429 Too Many Requests')]
    assert '失败' in result.feedback_heading()
    assert '429' in result.feedback_text()


def test_no_enabled_source_is_visible(settings):
    settings.config['search']['providers']=[]
    result=SearchService(settings,None).search(SearchQuery(text='test'))
    assert '未启用' in result.feedback_heading()


def test_actual_search_click_returns_english_candidates_and_relax_button(settings):
    app=QApplication.instance() or QApplication([])
    settings.config['search']['providers']=[]
    service=AppService(settings)
    seen=[]
    def search(query):
        seen.append(query.text)
        return [Paper(title='Multimodal Fusion for Image Degradation',venue='CVPR',doi='10.1000/feedback')]
    service.search_service.providers={'test':SimpleNamespace(name='test',display_name='Test',search=search)}
    settings.config['search']['providers']=['test']
    window=MainWindow(service)
    window.show()
    try:
        panel=window.search_panel
        panel.query_edit.setText('图像退化')
        panel.direction_picker.add_value('multimodal learning')
        panel.venue_picker.add_value('Nature')
        QTest.mouseClick(panel.search_button,Qt.LeftButton)
        deadline=time.monotonic()+3
        while window._search_busy and time.monotonic()<deadline:
            app.processEvents()
            QTest.qWait(10)
        assert not window._search_busy
        assert seen and seen[0]=='image degradation'
        assert '筛选' in window.result_table.feedback_title.text()
        assert not window.result_table.relax_venue_button.isHidden()
        QTest.mouseClick(window.result_table.relax_venue_button,Qt.LeftButton)
        deadline=time.monotonic()+3
        while window._search_busy and time.monotonic()<deadline:
            app.processEvents()
            QTest.qWait(10)
        assert panel.current_query()['venues']==[]
        assert panel.current_query()['directions']==['multimodal learning']
        assert len(window.result_table.papers())==1
        QTest.mouseClick(window.result_table.logs_button,Qt.LeftButton)
        assert window.tabs.currentWidget() is window.log_panel
    finally:
        window.close()
        service.close()
