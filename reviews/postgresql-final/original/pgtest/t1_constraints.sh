#!/usr/bin/env bash
# Each statement must FAIL (expect error) or PASS. Runs as superuser unless noted.
q() { docker exec -i jw-review-pg psql -U "${2:-postgres}" -d postgres -v ON_ERROR_STOP=1 -qAt -c "SET search_path=jobwatch,public; $1" 2>&1; }
expect_fail() { out=$(q "$2" "$3"); if [[ $? -ne 0 ]]; then echo "OK   (rejected) $1 :: $(echo "$out" | grep -m1 ERROR | cut -c1-110)"; else echo "BAD  (accepted) $1"; fi; }
expect_ok()   { out=$(q "$2" "$3"); if [[ $? -eq 0 ]]; then echo "OK   (accepted) $1 ${out:+:: $out}"; else echo "BAD  (rejected) $1 :: $out"; fi; }

expect_ok   "seed employer+source+run+posting" "
  INSERT INTO employers (slug,name) VALUES ('monzo','Monzo');
  INSERT INTO sources (provider,board,employer_id,status,validation_url,mapping_verified_at) VALUES ('greenhouse','monzo',1,'monitored','https://monzo.com/careers',now());
  INSERT INTO collection_runs (id,source_id,mode,request_version,adapter_version,status,vacancies,finished_at) VALUES ('00000000-0000-0000-0000-000000000001',1,'publish','gh:v1','a1','published',1,now());
  INSERT INTO postings (posting_key,source_id,native_id,status,first_seen_at,first_seen_basis,normalizer_version,title,posting_url,description_text)
    VALUES ('greenhouse:monzo:1',1,'1','listed',now(),'observed','n1','Graduate Software Engineer','https://job-boards.greenhouse.io/monzo/jobs/1','Build backend services in Go and Python.');
  INSERT INTO posting_versions (posting_key,raw_sha256,raw,first_run_id,first_seen_at,normalizer_version) VALUES ('greenhouse:monzo:1',repeat('a',64),'{\"id\":1}','00000000-0000-0000-0000-000000000001',now(),'n1');
  UPDATE postings SET current_version_id=1 WHERE posting_key='greenhouse:monzo:1';
  INSERT INTO postings (posting_key,source_id,native_id,status,first_seen_at,first_seen_basis,normalizer_version,title,posting_url)
    VALUES ('greenhouse:monzo:2',1,'2','listed',now(),'observed','n1','Staff Engineer','https://job-boards.greenhouse.io/monzo/jobs/2');
  INSERT INTO posting_events (posting_key,run_id,at,category,kind,after_version_id) VALUES ('greenhouse:monzo:1','00000000-0000-0000-0000-000000000001',now(),'observation','first_seen',1);"
expect_fail "monitored source without mapping evidence" "INSERT INTO sources (provider,board,status) VALUES ('lever','acme','monitored')"
expect_fail "same board, different case (key namespace)" "INSERT INTO sources (provider,board) VALUES ('greenhouse','Monzo')"
expect_fail "same board, other region (key namespace)" "INSERT INTO sources (provider,board,region) VALUES ('greenhouse','monzo','eu')"
expect_fail "arbitrary URL as a board slug" "INSERT INTO sources (provider,board) VALUES ('lever','evil.example/x')"
expect_fail "jsonb with \\u0000 (provider payload containing NUL)" "SELECT '{\"d\":\"a\\u0000b\"}'::jsonb"
expect_fail "current version pointing at another posting's version" "UPDATE postings SET current_version_id=1 WHERE posting_key='greenhouse:monzo:2'"
expect_fail "deleting evidence referenced by an event" "DELETE FROM posting_versions WHERE id=1"
expect_fail "not_seen without missing_since" "UPDATE postings SET status='not_seen' WHERE posting_key='greenhouse:monzo:2'"
expect_fail "programme cycle year not quoted in wording" "INSERT INTO leads (lead_key,kind,company,title,cycle_year,state,url,official,source_note,wording,checked_at,checked_by) VALUES ('programme:acme:grad:2027','programme','Acme','Grad',2027,'open','https://acme.com/x',true,'page','Applications for our 2026 programme are open',now(),'agent')"
expect_ok   "programme cycle year quoted in wording" "INSERT INTO leads (lead_key,kind,company,title,cycle_year,state,url,official,source_note,wording,checked_at,checked_by) VALUES ('programme:acme:grad:2027','programme','Acme','Grad',2027,'open','https://acme.com/x',true,'page','Applications for our 2027 programme are open',now(),'agent')"
expect_fail "verification checked in the future" "INSERT INTO verifications (posting_key,kind,checked_at,checked_by,url,wording) VALUES ('greenhouse:monzo:1','deadline',now()+interval '1 day','agent','https://x.com','Apply by 1 Nov')"
expect_fail "plain agent claiming application status open" "INSERT INTO verifications (posting_key,kind,checked_at,checked_by,url,method,status) VALUES ('greenhouse:monzo:1','application_status',now(),'agent','https://x.com','looked','open_verified')"
expect_fail "revisit_on without decision later" "INSERT INTO triage (posting_key,decision,revisit_on,recorded_by) VALUES ('greenhouse:monzo:1','interested','2026-11-01','owner')"
expect_ok   "manual application (no posting)" "INSERT INTO applications (job_key,company,role,url) VALUES ('manual:abc','BBC','Graduate SE','https://CAREERS.bbc.co.uk/job/1?x=1#frag') RETURNING url_loose"
expect_fail "second application differing only by fragment/host case" "INSERT INTO applications (job_key,company,role,url) VALUES ('manual:def','BBC','Graduate SE','https://careers.bbc.co.uk/job/1?x=1#other')"
echo "--- as jobwatch_agent"
expect_fail "agent rewrites collector evidence" "UPDATE postings SET title='x'" jobwatch_agent
expect_fail "agent deletes a posting" "DELETE FROM postings WHERE posting_key='greenhouse:monzo:2'" jobwatch_agent
expect_fail "agent forges a collection run" "INSERT INTO collection_runs (id,source_id,mode,request_version,adapter_version) VALUES (gen_random_uuid(),1,'publish','x','x')" jobwatch_agent
expect_ok   "agent records triage" "INSERT INTO triage (posting_key,decision,reason_code,recorded_by) VALUES ('greenhouse:monzo:1','interested','role_fit','agent_on_owner_instruction')" jobwatch_agent
expect_ok   "agent changes triage" "UPDATE triage SET decision='dismissed' WHERE posting_key='greenhouse:monzo:1'" jobwatch_agent
expect_fail "agent erases the audit trail" "DELETE FROM owner_audit" jobwatch_agent
expect_ok   "audit captured both writes" "SELECT string_agg(op || ':' || coalesce(old_row->>'decision','-') || '->' || (new_row->>'decision'), ', ' ORDER BY id) FROM owner_audit WHERE table_name='triage'" jobwatch_agent
expect_fail "agent activates a candidate without evidence" "INSERT INTO sources (provider,board) VALUES ('ashby','acme'); UPDATE sources SET status='monitored' WHERE board='acme'" jobwatch_agent
expect_fail "agent moves the collector's publication pointer" "UPDATE sources SET last_published_run_id=NULL" jobwatch_agent
expect_ok   "agent reads everything incl. views" "SELECT count(*) FROM opportunities" jobwatch_agent
echo "--- as jobwatch_collector"
expect_fail "collector writes owner triage" "INSERT INTO triage (posting_key,decision,recorded_by) VALUES ('greenhouse:monzo:2','later','owner')" jobwatch_collector
expect_fail "collector reads private application notes" "SELECT notes FROM applications" jobwatch_collector
