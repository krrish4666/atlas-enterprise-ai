-- infra/postgres/init.sql
-- Runs once on first container start (via docker-entrypoint-initdb.d).
-- Creates the langfuse role and database.

CREATE USER langfuse WITH PASSWORD 'langfusepassword';
CREATE DATABASE langfuse OWNER langfuse;
GRANT ALL PRIVILEGES ON DATABASE langfuse TO langfuse;


