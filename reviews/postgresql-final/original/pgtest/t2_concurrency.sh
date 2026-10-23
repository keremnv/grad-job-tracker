#!/usr/bin/env bash
P() { docker exec -i jw-review-pg psql -U "${2:-postgres}" -d postgres -qAt -v ON_ERROR_STOP=1 -c "SET search_path=jobwatch,public; $1" 2>&1; }
P "INSERT INTO collection_runs (id,source_id,mode,request_version,adapter_version) VALUES
   ('00000000-0000-0000-0000-0000000000a1',1,'publish','gh:v1','a1'),('00000000-0000-0000-0000-0000000000a2',1,'publish','gh:v1','a1')" >/dev/null

echo "== A. READ COMMITTED: second publisher waits on the source lock, then sees the first one's committed pointer"
P "BEGIN; SELECT id FROM sources WHERE id=1 FOR UPDATE; UPDATE sources SET last_published_run_id='00000000-0000-0000-0000-0000000000a2' WHERE id=1; SELECT pg_sleep(3); COMMIT;" jobwatch_collector >/dev/null &
sleep 1
start=$(date +%s.%N)
out=$(P "BEGIN; SELECT last_published_run_id FROM sources WHERE id=1 FOR UPDATE; COMMIT;" jobwatch_collector)
echo "   waited $(printf '%.1f' $(echo "$(date +%s.%N) - $start" | bc))s; saw last_published_run_id=$out  (stale-guard input is current)"
wait

echo "== B. REPEATABLE READ: the same pattern fails with a serialization error (needs retry logic)"
P "BEGIN; SELECT id FROM sources WHERE id=1 FOR UPDATE; UPDATE sources SET last_published_run_id='00000000-0000-0000-0000-0000000000a1' WHERE id=1; SELECT pg_sleep(3); COMMIT;" jobwatch_collector >/dev/null &
sleep 1
out=$(P "BEGIN ISOLATION LEVEL REPEATABLE READ; SELECT last_published_run_id FROM sources WHERE id=1 FOR UPDATE; COMMIT;" jobwatch_collector)
echo "   $(echo "$out" | grep -m1 ERROR)"
wait

echo "== C. Owner FK insert while the collector holds an UPDATE on the same posting (non-key columns)"
P "BEGIN; UPDATE postings SET title='Graduate Software Engineer (2027)' WHERE posting_key='greenhouse:monzo:2'; SELECT pg_sleep(4); COMMIT;" jobwatch_collector >/dev/null &
sleep 1
start=$(date +%s.%N)
out=$(P "SET statement_timeout='2s'; INSERT INTO triage (posting_key,decision,recorded_by) VALUES ('greenhouse:monzo:2','later','owner'); SELECT 'inserted'" jobwatch_agent)
echo "   $out after $(printf '%.2f' $(echo "$(date +%s.%N) - $start" | bc))s (FOR KEY SHARE does not conflict with FOR NO KEY UPDATE)"
wait

echo "== D. Agent pauses a source while a collector is waiting to publish it"
P "BEGIN; UPDATE sources SET status='paused' WHERE id=1; SELECT pg_sleep(3); COMMIT;" jobwatch_agent >/dev/null &
sleep 1
out=$(P "BEGIN; SELECT status FROM sources WHERE id=1 FOR UPDATE; COMMIT;" jobwatch_collector)
echo "   collector's locked read returned status=$out  (it must skip publication)"
wait
P "UPDATE sources SET status='monitored' WHERE id=1" >/dev/null

echo "== E. Opposite-order row updates (cross-source rescreen vs per-source publish) deadlock"
P "INSERT INTO screenings VALUES ('greenhouse:monzo:1','review','software','{}','r1',null,'full',now()),('greenhouse:monzo:2','excluded','software','{}','r1',null,'full',now()) ON CONFLICT DO NOTHING" >/dev/null
P "BEGIN; UPDATE screenings SET rules_version='r2' WHERE posting_key='greenhouse:monzo:1'; SELECT pg_sleep(2); UPDATE screenings SET rules_version='r2' WHERE posting_key='greenhouse:monzo:2'; COMMIT;" jobwatch_collector > /tmp/claude-1000/-home-kerem-Desktop-Job-grad-job-tracker-starter-updated/57806037-cfdf-4bc3-8442-31cda8b03bb7/scratchpad/pgtest/e1.out &
sleep 0.5
o2=$(P "BEGIN; UPDATE screenings SET rules_version='r3' WHERE posting_key='greenhouse:monzo:2'; SELECT pg_sleep(2); UPDATE screenings SET rules_version='r3' WHERE posting_key='greenhouse:monzo:1'; COMMIT;" jobwatch_collector)
wait
echo "   $( (cat /tmp/claude-1000/-home-kerem-Desktop-Job-grad-job-tracker-starter-updated/57806037-cfdf-4bc3-8442-31cda8b03bb7/scratchpad/pgtest/e1.out; echo "$o2") | grep -m1 -E 'ERROR' )"

echo "== F. ON CONFLICT DO NOTHING RETURNING returns no id for an existing version (common upsert trap)"
P "INSERT INTO posting_versions (posting_key,raw_sha256,raw,first_run_id,first_seen_at,normalizer_version) VALUES ('greenhouse:monzo:1',repeat('a',64),'{}','00000000-0000-0000-0000-000000000001',now(),'n1') ON CONFLICT (posting_key, raw_sha256) DO NOTHING RETURNING id" | sed 's/^/   returned: [/; s/$/]/'
