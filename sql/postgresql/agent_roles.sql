-- OPTIONAL ROLE/RESTORE PREREQUISITES. No passwords or existing-role adoption.
-- Run once as administrator in the chosen database; grants/views follow separately.
\set ON_ERROR_STOP on
BEGIN;
CREATE ROLE jobwatch_agent_readonly LOGIN PASSWORD NULL NOSUPERUSER NOCREATEDB
  NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 3;
CREATE ROLE jobwatch_agent_owner LOGIN PASSWORD NULL NOSUPERUSER NOCREATEDB
  NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2;
CREATE ROLE jobwatch_calendar_reader LOGIN PASSWORD NULL NOSUPERUSER NOCREATEDB
  NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2;
GRANT CONNECT ON DATABASE :"DBNAME" TO jobwatch_agent_readonly,jobwatch_agent_owner,jobwatch_calendar_reader;
ALTER ROLE jobwatch_agent_readonly SET default_transaction_read_only=on;
ALTER ROLE jobwatch_agent_owner SET default_transaction_read_only=on;
ALTER ROLE jobwatch_calendar_reader SET default_transaction_read_only=on;
ALTER ROLE jobwatch_agent_readonly SET search_path=jobwatch,pg_catalog;
ALTER ROLE jobwatch_agent_owner SET search_path=jobwatch,pg_catalog;
ALTER ROLE jobwatch_calendar_reader SET search_path=jobwatch,pg_catalog;
ALTER ROLE jobwatch_agent_readonly SET statement_timeout='5s';
ALTER ROLE jobwatch_agent_owner SET statement_timeout='5s';
ALTER ROLE jobwatch_calendar_reader SET statement_timeout='15s';
ALTER ROLE jobwatch_agent_readonly SET lock_timeout='1s';
ALTER ROLE jobwatch_agent_owner SET lock_timeout='1s';
ALTER ROLE jobwatch_calendar_reader SET lock_timeout='1s';
ALTER ROLE jobwatch_agent_readonly SET idle_in_transaction_session_timeout='10s';
ALTER ROLE jobwatch_agent_owner SET idle_in_transaction_session_timeout='10s';
ALTER ROLE jobwatch_calendar_reader SET idle_in_transaction_session_timeout='15s';
ALTER ROLE jobwatch_agent_readonly SET transaction_timeout='15s';
ALTER ROLE jobwatch_agent_owner SET transaction_timeout='15s';
ALTER ROLE jobwatch_calendar_reader SET transaction_timeout='30s';
ALTER ROLE jobwatch_agent_readonly SET work_mem='4MB';
ALTER ROLE jobwatch_agent_owner SET work_mem='4MB';
ALTER ROLE jobwatch_calendar_reader SET work_mem='4MB';
COMMIT;
