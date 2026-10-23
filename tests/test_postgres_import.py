"""Read-only frozen Git planning tests; no psycopg, database or operational files."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jobwatch.postgres.git_snapshot import ImportProblem,GitSnapshot
from jobwatch.postgres.import_plan import build_plan
from postgres.import_fixture import repository,git,write,KEY,WORDS


class ImportPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.repo,self.commit=repository(self.temp.name)

    def commit_change(self):
        git(self.repo,'add','.');git(self.repo,'commit','-qm','fixture modification',day=6)
        return git(self.repo,'rev-parse','HEAD')

    def test_explicit_commit_and_bounded_history(self):
        for invalid in ('HEAD','main',self.commit[:8],self.commit+'^{tree}'):
            with self.assertRaises(ImportProblem):
                build_plan(self.repo,invalid)
        with self.assertRaisesRegex(ImportProblem,'bound exceeded'):
            build_plan(self.repo,self.commit,max_commits=1)
        with self.assertRaisesRegex(ImportProblem,'bound exceeded'):
            build_plan(self.repo,self.commit,max_bytes=10)

    def test_frozen_commit_ignores_working_tree_and_plan_is_deterministic(self):
        a=build_plan(self.repo,self.commit)
        (self.repo/'data/jobs.json').write_text('uncommitted invalid local data')
        (self.repo/'untracked').write_text('preserve me')
        before=git(self.repo,'status','--porcelain')
        b=build_plan(self.repo,self.commit)
        self.assertEqual(a.sha256,b.sha256)
        self.assertEqual(git(self.repo,'status','--porcelain'),before)
        self.assertEqual((self.repo/'untracked').read_text(),'preserve me')
        self.assertEqual(a.manifest['unprojected_posting_fields'],['legacy_extra'])

    def test_promisor_input_is_refused_before_it_can_lazy_fetch(self):
        git(self.repo,'config','remote.origin.promisor','true')
        with self.assertRaisesRegex(ImportProblem,'Partial/promisor'):
            build_plan(self.repo,self.commit)

    def test_recovery_traverses_all_hashes_and_proves_native_origin(self):
        plan=build_plan(self.repo,self.commit)
        self.assertEqual(plan.manifest['description_recovery']['distinct_texts'],4)
        self.assertEqual(plan.manifest['description_recovery']['native_postings'],3)
        current=next(r for r in plan.rows['postings'] if r['posting_key']==KEY)
        version=next(r for r in plan.rows['posting_versions'] if r['id']==current['current_version_id'])
        evidence=json.loads(version['evidence'])
        self.assertEqual({r['text'] for r in evidence['descriptions'].values()},set(WORDS))
        self.assertEqual(version['origin'],'legacy_index')
        self.assertTrue(all(r['normalizer_version']=='legacy-schema1-retained' for r in plan.rows['posting_versions'] if r['origin']=='git_history'))

    def test_hash_mismatch_is_failure_even_when_correct_old_blob_survives(self):
        file=next((self.repo/'data/jobs-text').rglob('*.txt'))
        file.write_text('incorrect current content')
        with self.assertRaisesRegex(ImportProblem,'hash mismatch'):
            build_plan(self.repo,self.commit_change())

    def test_invalid_json_and_expected_file_absence_are_distinct(self):
        (self.repo/'data/leads.json').write_text('{"schema":1,"schema":1}')
        with self.assertRaisesRegex(ImportProblem,'Invalid JSON'):
            build_plan(self.repo,self.commit_change())
        (self.repo/'data/jobs.json').unlink()
        with self.assertRaisesRegex(ImportProblem,'Required frozen file is absent'):
            build_plan(self.repo,self.commit_change())

    def test_disabled_source_and_programme_collisions_are_not_dropped(self):
        config=json.loads((self.repo/'config/employers.json').read_text())
        row=deepcopy(config['employers'][0]);row.update(company='Duplicate mapping',enabled=False,board='TEST')
        config['employers'].append(row);write(self.repo,'config/employers.json',config)
        with self.assertRaisesRegex(ImportProblem,'Duplicate source namespace'):
            build_plan(self.repo,self.commit_change())

    def test_programme_identity_collision_is_explicit(self):
        data=json.loads((self.repo/'data/leads.json').read_text())
        data['programmes'].append(deepcopy(data['programmes'][0]));write(self.repo,'data/leads.json',data)
        with self.assertRaisesRegex(ImportProblem,'identity/cycle collision'):
            build_plan(self.repo,self.commit_change())

    def test_native_payload_cannot_be_bound_to_another_posting(self):
        data=json.loads((self.repo/'data/jobs.json').read_text())
        data['postings'][KEY]['raw']={'id':'different-native-id'}
        write(self.repo,'data/jobs.json',data)
        with self.assertRaisesRegex(ImportProblem,'Native evidence ID disagrees'):
            build_plan(self.repo,self.commit_change())

    def test_unavailable_text_retains_hash_and_provenance(self):
        with TemporaryDirectory() as folder:
            repo,commit=repository(folder,missing=True);plan=build_plan(repo,commit)
        missing=plan.manifest['missing_evidence']
        self.assertEqual([(r['posting_key'],r['sha256']) for r in missing],[(KEY,'f'*64)])
        self.assertTrue(missing[0]['references'])
        self.assertEqual(plan.report()['counts']['posting_events'],16)

    def test_empty_unknown_and_nul_descriptions_remain_distinct(self):
        data=json.loads((self.repo/'data/jobs.json').read_text())
        empty=sha256(b'').hexdigest();nul_text='Native\0wording';nul=sha256(nul_text.encode()).hexdigest()
        for key,sha,chars in ((KEY,empty,0),('greenhouse:test:2',None,None),('greenhouse:test:3',nul,len(nul_text))):
            data['postings'][key]['description_sha256']=sha
            if chars is None:
                data['postings'][key].pop('description_chars',None)
            else:
                data['postings'][key]['description_chars']=chars
        write(self.repo,'data/jobs.json',data)
        for h,text in ((empty,''),(nul,nul_text)):
            file=self.repo/'data/jobs-text'/h[:2]/(h+'.txt');file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(text.encode())
        plan=build_plan(self.repo,self.commit_change())
        versions={r['id']:r for r in plan.rows['posting_versions']}
        current={r['native_id']:versions[r['current_version_id']] for r in plan.rows['postings']}
        self.assertEqual(current['1']['description_text'],'')
        self.assertIsNone(current['2']['description_text'])
        self.assertEqual(current['3']['description_text'],'Native\ufffdwording')
        self.assertTrue(current['3']['nul_replaced'])
        self.assertEqual(json.loads(current['3']['evidence'])['descriptions'][nul]['text'],nul_text)
