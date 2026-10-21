"""Batch resume preserves history and retries only unfinished acquisitions."""

import csv
from types import SimpleNamespace

from cli import AUDIT_FIELDS, cmd_audit
from models.paper import Paper, PdfStatus
from services.download_service import DownloadResult


def test_resume_retries_failure_and_records_attempt_time(tmp_path):
    titles=tmp_path/'titles.txt'
    titles.write_text('A Verified Study\nA Failed Study\n',encoding='utf-8')
    pdf=tmp_path/'verified.pdf';pdf.write_bytes(b'%PDF-test')
    output=tmp_path/'audit.csv'
    with output.open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=AUDIT_FIELDS)
        writer.writeheader()
        writer.writerow({'input_title':'A Verified Study','status':'FOUND','pdf_status':'VALIDATED','path':str(pdf),'attempt':1})
        writer.writerow({'input_title':'A Failed Study','status':'FOUND','pdf_status':'DOWNLOAD_FAILED','attempt':1})
    searches=[]
    def search(query):
        searches.append(query.text)
        return SimpleNamespace(papers=[Paper(title=query.text)])
    service=SimpleNamespace(run_search=search, download_one=lambda paper:
        DownloadResult(title=paper.title,status=PdfStatus.VALIDATED,path=pdf,paper=paper))
    args=SimpleNamespace(titles=str(titles),out=str(output),resume=True,year_from=None,year_to=None,limit=3)
    assert cmd_audit(service,args,None)==0
    with output.open(encoding='utf-8-sig',newline='') as handle:rows=list(csv.DictReader(handle))
    assert searches==['A Failed Study']
    assert len(rows)==3
    assert rows[-1]['attempt']=='2'
    assert rows[-1]['attempted_at'].endswith('+00:00')
    assert rows[-1]['pdf_status']=='VALIDATED'
    assert cmd_audit(service,args,None)==0
    assert searches==['A Failed Study']


def test_legacy_audit_header_is_not_corrupted_when_appending(tmp_path):
    titles=tmp_path/'titles.txt';titles.write_text('A Study\n',encoding='utf-8')
    output=tmp_path/'old-audit.csv'
    output.write_text('input_title,status,pdf_status,message\nA Study,ERROR,,Network failure\n',encoding='utf-8')
    args=SimpleNamespace(titles=str(titles),out=str(output),resume=True,year_from=None,year_to=None,limit=3)
    service=SimpleNamespace(run_search=lambda query:SimpleNamespace(papers=[]))
    assert cmd_audit(service,args,None)==0
    with output.open(encoding='utf-8-sig',newline='') as handle:rows=list(csv.DictReader(handle))
    assert len(rows)==2
    assert None not in rows[-1]
    assert rows[0]['message']=='Network failure'
