"""Synthetic, nonempty private-data fixture; creates only a caller-owned temporary Git repository."""
from copy import deepcopy
import csv
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import subprocess

from jobwatch.applications import COLUMNS

KEY = 'greenhouse:test:1'
WORDS = ('First source wording', 'Second source wording', 'Third source wording', 'Fourth source wording')


def git(repo,*args,day=1):
    env = {**os.environ,'GIT_AUTHOR_DATE':f'2026-01-{day:02}T12:00:00Z','GIT_COMMITTER_DATE':f'2026-01-{day:02}T12:00:00Z'}
    return subprocess.check_output(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid',*args],env=env,stderr=subprocess.DEVNULL).decode().strip()


def write(repo,path,value):
    target = Path(repo,path); target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n',encoding='utf-8')


def native(i='1',text=WORDS[0]):
    return {'id':i,'internal_job_id':int(i),'title':'Graduate Software Engineer','content':text,
            'absolute_url':f'https://job-boards.greenhouse.io/test/jobs/{i}', 'location':{'name':'London, UK'},
            'metadata':[{'name':'Job Summary','value':'fixture retained verbatim'}]}


def repository(folder, *, missing=False):
    repo = Path(folder)/'source'; repo.mkdir()
    subprocess.run(['git','init','-q','-b','fixture',str(repo)],check=True)
    employer = {'company':'Test Company','provider':'greenhouse','board':'test','region':'global','enabled':True,
        'careers_url':'https://example.org/careers','validation_url':'https://example.org/jobs',
        'mapping_verified_at':'2026-01-01T00:00:00Z','verification_notes':'Official page maps test board',
        'watch_status':'listed_in_feed','watch_checked_at':'2026-01-01T00:00:00Z','relevance_notes':'Fixture watch note'}
    paused = {**employer,'company':'Paused Company','provider':'lever','board':'paused','enabled':False}
    write(repo,'config/employers.json',{'notes':'fixture root metadata','employers':[employer,paused]})
    rows = {}
    for i in ('1','2','3'):
        raw = native(i)
        rows['greenhouse:test:'+i] = {'provider':'greenhouse','board':'test','external_id':i,'company':'Test Company',
            'title':raw['title'],'url':raw['absolute_url'],'location':'London, UK','country':'','countries':[],
            'salary':'','published_at':'','source_dates':{},'deadlines':[], 'description':WORDS[0], 'raw':raw,
            'first_seen':'2026-01-01T00:00:00Z','last_seen':'2026-01-01T00:00:00Z','feed_status':'listed',
            'priority':'review','role_family':'software','reason':'old interpretation',
            'history':[{'type':'changed','at':'2026-01-01T00:00:00Z','changes':{'reason':{'before':'old','after':'new'}}}]}
    write(repo,'data/jobs.json',{'schema':1,'postings':rows,'boards':{'greenhouse:test':{
        'status':'success','last_attempt':'2026-01-01T00:00:00Z','last_success':'2026-01-01T00:00:00Z','count':3}},
        'last_scan':{'at':'2026-01-01T01:00:00Z'}})
    git(repo,'add','.');git(repo,'commit','-qm','schema one native observation')
    hashes = [sha256(t.encode()).hexdigest() for t in WORDS]
    from jobwatch.state import upgrade
    decoded = upgrade({'schema':1,'postings':deepcopy(rows)})['postings']
    for step in range(1,4):
        at = f'2026-01-0{step+1}T00:00:00Z'
        for key,row in decoded.items():
            row.update(description_sha256=hashes[step],description_chars=len(WORDS[step]),legacy_extra={'preserved':True})
            row['screening']={'verdict':'excluded' if key!=KEY else 'review','family':'software','codes':['x.legacy','x.order'],
                'rules_version':'fixture-old-rules','basis':'full','input_sha256':'a'*64,'screened_at':'2026-01-02T00:00:00Z'}
            row['source_events'].append({'type':'changed','at':at,'changes':{'description_sha256':{'before':hashes[step-1],'after':hashes[step]}}})
            row['screening_events']=[{'type':'rescreened','at':'2026-01-02T00:00:00Z',
                'before':{'verdict':'review','codes':[],'rules_version':'legacy-schema-1'},
                'after':{'verdict':row['screening']['verdict'],'codes':['x.legacy','x.order'],'rules_version':'fixture-old-rules'},'cause':'legacy_migration'}]
        write(repo,'data/jobs.json',{'schema':2,'migration':{'from_schema':1,'note':'fixture original metadata'},'postings':decoded})
        write(repo,'data/jobs-health.json',{'schema':1,'boards':{'greenhouse:test':{'status':'success','last_attempt':at,
            'last_success':at,'fetched_at':at,'count':3,'source_records':3,'screening':{'review':1,'excluded':2}}},
            'last_scan':{'at':at,'rules_version':'fixture-old-rules','events':[]}})
        directory = repo/'data/jobs-text'
        if directory.exists():
            for file in directory.rglob('*.txt'):
                file.unlink()
        # Current and latest predecessor only; older texts must be recovered from Git/inline history.
        for index in (step-1,step):
            file=directory/hashes[index][:2]/(hashes[index]+'.txt');file.parent.mkdir(parents=True,exist_ok=True)
            file.write_text(WORDS[index],encoding='utf-8')
        git(repo,'add','.');git(repo,'commit','-qm',f'normalized observation {step}',day=step+1)
    final=json.loads((repo/'data/jobs.json').read_text())
    final['postings']['greenhouse:test:2'].update(feed_status='not_seen',missing_since='2026-01-05T00:00:00Z',last_seen='2026-01-04T00:00:00Z')
    if missing:
        final['postings'][KEY]['source_events'].append({'type':'changed','at':'2026-01-05T00:00:00Z',
            'changes':{'description_sha256':{'before':'f'*64,'after':hashes[-1]}}})
    write(repo,'data/jobs.json',final)
    write(repo,'data/jobs-health.json',{'schema':1,'unknown_root':{'kept':True},'boards':{'greenhouse:test':{
        'status':'failure','last_attempt':'2026-01-05T00:00:00Z','last_success':'2026-01-04T00:00:00Z',
        'count':3,'error':'private synthetic transport detail'}},'last_scan':{'at':'2026-01-05T00:00:00Z','failures':['fixture']}})
    write(repo,'data/verifications.json',{'schema':1,'extra_root':'retained','postings':{KEY:{'status':'open_verified',
        'checked_at':'2026-01-03T00:00:00Z','url':native()['absolute_url'],'method':'read-only fixture page',
        'description_verified_at':'2026-01-03T00:00:00Z','description_url':native()['absolute_url'],'description_sha256':hashes[0],
        'deadlines':[{'wording':'Apply by 1 December 2026','value':'2026-12-01','url':'https://example.org/deadline',
            'verified_at':'2026-01-03T00:00:00Z','verified_by':'agent','timezone':'unknown'}]},
        'greenhouse:test:3':{'status':'unknown'}}})
    write(repo,'data/triage.json',{'schema':1,'decisions':{KEY:{'decision':'later','reason_code':'other',
        'note':'private triage fixture note','revisit_on':'2026-11-01','decided_at':'2026-01-05T00:00:00Z','recorded_by':'owner'}}})
    owner_rows=[]
    for i,key,url in [('1',KEY,native()['absolute_url']),('2','manual:stable',native('2')['absolute_url']),
                      ('3','manual:fragment',native('3')['absolute_url']+'#different')]:
        row={k:'' for k in COLUMNS}
        row.update(job_key=key,company='Test Company',role='Graduate Software Engineer',url=url,
                   stage='preparing' if i=='1' else 'applied',date_applied='' if i=='1' else '2026-01-04',
                   cv_version='fixture-cv-label',next_action='Synthetic follow-up',next_action_date='2026-11-02',notes='private application fixture note '+i)
        owner_rows.append(row)
    output=io.StringIO(newline='');writer=csv.DictWriter(output,fieldnames=COLUMNS);writer.writeheader();writer.writerows(owner_rows)
    (repo/'data/applications.csv').write_bytes(output.getvalue().encode())
    programmes=[{'company':'Test Company','programme':'Graduate Programme '+year,'cycle':year,
        'state':'previous_cycle' if year=='2026' else 'open_2027','wording':'Graduate Programme '+year+' opens in autumn',
        'url':'https://example.org/programme','source':'official fixture page','official':True,'checked_at':'2026-01-05T00:00:00Z'} for year in ('2026','2027')]
    write(repo,'data/leads.json',{'schema':1,'notes':'preserved lead root','vacancies':[{'company':'Test Company','title':'Graduate Software Engineer',
        'url':native()['absolute_url'],'source':'official fixture vacancy','official':True,'checked_at':'2026-01-05T00:00:00Z',
        'intake':'unknown','intake_wording':'not specified','status':'listed','posting_key':KEY,'notes':'Synthetic research note',
        'deadline':{'wording':'Apply by December','timezone':'unknown'}}], 'programmes':programmes})
    git(repo,'add','.');git(repo,'commit','-qm','owner records and failed latest collection',day=5)
    return repo,git(repo,'rev-parse','HEAD')
