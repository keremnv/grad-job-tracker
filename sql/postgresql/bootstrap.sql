-- ADMINISTRATIVE PROVISIONING, not an application migration.
-- Run once with psql as a role administrator + owner of a dedicated application database.
-- Roles are cluster-wide. Existing names cause failure: never silently reset their privileges/passwords.
-- Managed providers may provision equivalent roles/grants through their own administrative interface.
-- No passwords: configure authentication separately (e.g. psql \password or provider secret management).
\set ON_ERROR_STOP on
BEGIN;
CREATE ROLE jobwatch_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE jobwatch_collector LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE jobwatch_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

-- DBNAME is psql's connected-database variable; identifier quoting is intentional.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON DATABASE :"DBNAME" FROM PUBLIC;
GRANT CONNECT, CREATE ON DATABASE :"DBNAME" TO jobwatch_migrator;
GRANT CONNECT ON DATABASE :"DBNAME" TO jobwatch_collector, jobwatch_owner;
ALTER ROLE jobwatch_migrator IN DATABASE :"DBNAME" SET search_path = jobwatch, pg_catalog;
ALTER ROLE jobwatch_collector IN DATABASE :"DBNAME" SET search_path = jobwatch, pg_catalog;
ALTER ROLE jobwatch_owner IN DATABASE :"DBNAME" SET search_path = jobwatch, pg_catalog;
-- Deliberately no membership between runtime and migration roles, and no database ownership for runtime.
COMMIT;
