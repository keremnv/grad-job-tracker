"""Read-only Git inventory + CURRENT-posting schema round-trip in the disposable DB.

Not the production importer: owner/evidence/history migration is inventoried, not
implemented here. This distinction is intentional and recorded in results.
"""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from uuid import uuid5, NAMESPACE_URL

from psycopg.types.json import Jsonb
from reference import actor, connect, encoded
from jobwatch.filtering import screen

COMMIT='90434ea6d9a3457f9c8ba40f01ad25fa2ceffca3'


def blob(path):
    return subprocess.check_output(['git','show',COMMIT+':'+path],stderr=subprocess.DEVNULL)


def load(path):
    return json.loads(blob(path))


def main():
    state=load('data/jobs.json')
    health=load('data/jobs-health.json')
    config=load('config/employers.json')
    leads=load('data/leads.json')
    verifications=load('data/verifications.json')
    files=subprocess.check_output(['git','ls-tree','-r','--name-only',COMMIT,'data/jobs-text']).decode().splitlines()
    text={}
    for path in files:
        b=blob(path)
        assert sha256(b).hexdigest()==Path(path).stem
        text[Path(path).stem]=b.decode('utf8')
    with connect() as c,c.transaction():
        actor(c,'import:review-current-only')
        names=c.execute("SELECT tablename FROM pg_tables WHERE schemaname='jobwatch'").fetchall()
        c.execute('TRUNCATE '+','.join('jobwatch.'+r['tablename'] for r in names)+' RESTART IDENTITY CASCADE')
        sources={}
        for key in sorted(health['boards']):
            provider,board=key.split(':',1)
            sid=c.execute('INSERT INTO jobwatch.sources(provider,board) VALUES (%s,%s) RETURNING id',
                          (provider,board)).fetchone()['id']
            sources[key]=sid
        for key,row in sorted(state['postings'].items()):
            sid=sources[row['provider']+':'+row['board'].lower()]
            vid=uuid5(NAMESPACE_URL,'jobwatch-review:'+COMMIT+':'+key)
            s=row['screening']
            c.execute('INSERT INTO jobwatch.postings(posting_key,source_id,provider,board,native_id,status,first_seen_at,'
                'first_seen_basis,last_seen_at,missing_since,current_version_id,verdict,family,codes,rules_version,'
                'screening_input_sha256,screening_basis,screened_at) VALUES ('+','.join(['%s']*18)+')',
                (key,sid,row['provider'],row['board'],row['external_id'],row['feed_status'],row['first_seen'],
                 row['first_seen_basis'],row.get('last_seen'),row.get('missing_since'),vid,s['verdict'],s['family'],
                 s['codes'],s['rules_version'],s.get('input_sha256'),s['basis'],s['screened_at']))
            description=text.get(row.get('description_sha256'))
            assert description is not None, 'missing CURRENT text'
            n={'company':row['company'],'title':row['title'],'posting_url':row['url'],
               'location_text':row.get('location',''),'country':row.get('country',''),
               'countries':row.get('countries',[]),'department':row.get('department',''),
               'team':row.get('team',''),'employment_type':row.get('employment_type',''),
               'salary':row.get('salary',''),'published_at':row.get('published_at',''),
               'source_dates':row.get('source_dates',{}),'deadlines':row.get('deadlines',[]),
               'description_text':description}
            payload={'kind':'legacy_snapshot','row':row,'description_text':description}
            columns=['id','posting_key','source_id','origin','evidence','observed_at','normalizer_version',
                     'normalized_sha256','provenance']+list(n)
            values=[vid,key,sid,'legacy_index',encoded(payload),row['first_seen'],'v1-legacy',
                    sha256(encoded(n)).hexdigest(),Jsonb({'commit':COMMIT,'path':'data/jobs.json',
                    'time_basis':'import uses original first_seen; not provider capture time'})]
            values += [Jsonb(v) if isinstance(v,dict) or k=='deadlines' else v for k,v in n.items()]
            c.execute('INSERT INTO jobwatch.posting_versions('+','.join(columns)+') VALUES ('+
                      ','.join(['%s']*len(columns))+')',values)
        restored=c.execute('SELECT p.*,v.* FROM jobwatch.postings p JOIN jobwatch.posting_versions v '
                           'ON v.id=p.current_version_id').fetchall()
        assert len(restored)==len(state['postings'])
        for r in restored:
            original=state['postings'][r['posting_key']]
            assert json.loads(bytes(r['evidence']))['row']==original
            assert sha256(r['description_text'].encode()).hexdigest()==original['description_sha256']
            for source,dest in [('title','title'),('url','posting_url'),('location','location_text'),
                                ('country','country'),('countries','countries'),('department','department'),
                                ('team','team'),('employment_type','employment_type'),('salary','salary'),
                                ('published_at','published_at'),('source_dates','source_dates'),('deadlines','deadlines')]:
                assert r[dest]==original.get(source,[] if dest in ('countries','deadlines') else {} if dest=='source_dates' else '')
            for source,dest in [('verdict','verdict'),('codes','codes'),('family','family'),('rules_version','rules_version'),
                                ('input_sha256','screening_input_sha256'),('basis','screening_basis')]:
                assert r[dest]==original['screening'].get(source)
            assert r['first_seen_at'].isoformat().replace('+00:00','Z')==original['first_seen']
            assert r['first_seen_basis']==original['first_seen_basis']
            inputs={'title':r['title'],'location':r['location_text'],'country':r['country'],'countries':r['countries'],
                    'department':r['department'],'team':r['team'],'employment_type':r['employment_type'],
                    'description':r['description_text']}
            result=screen(inputs)
            assert result['verdict']==r['verdict'] and result['codes']==r['codes']
    # Owner records are NEVER copied into output. Count them and hash source files for later full reconciliation.
    import csv,io
    apps=list(csv.DictReader(io.StringIO(blob('data/applications.csv').decode())))
    try:
        triage=load('data/triage.json')['decisions']
    except subprocess.CalledProcessError:
        triage={}
    rows=list(state['postings'].values())
    report={'source_commit':COMMIT,'current_postings_roundtripped':len(rows),
        'verdicts':dict(Counter(r['screening']['verdict'] for r in rows)),
        'statuses':dict(Counter(r['feed_status'] for r in rows)),
        'first_seen_bases':dict(Counter(r['first_seen_basis'] for r in rows)),
        'retained_text_files_verified':len(files),'current_missing_texts':0,
        'current_nul_descriptions':sum('\0' in text[r['description_sha256']] for r in rows),
        'employers':len(config['employers']),'health_sources':len(health['boards']),
        'vacancy_leads':len(leads['vacancies']),'programme_leads':len(leads['programmes']),
        'verified_postings':len(verifications['postings']),'applications':len(apps),'triage':len(triage),
        'retained_events':{k:sum(len(r.get(k,[])) for r in rows) for k in ('source_events','screening_events','legacy_history')},
        'by_source':dict(sorted(Counter(r['provider']+':'+r['board'].lower() for r in rows).items())),
        'source_file_sha256':{p:sha256(blob(p)).hexdigest() for p in ('data/jobs.json','data/jobs-health.json',
            'config/employers.json','data/leads.json','data/verifications.json','data/applications.csv')},
        'not_executed':['full employer/health/lead/verification/application/triage/history import',
                        'Git-history text/native recovery','production importer determinism','production backup/restore']}
    print(json.dumps(report,indent=2,sort_keys=True))


if __name__=='__main__':
    main()
