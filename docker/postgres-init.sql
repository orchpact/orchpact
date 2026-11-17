-- Runs only on a new PostgreSQL volume; never reconfigures an existing role.
-- psql reads the password from the container environment, never command arguments.
\getenv app_password ORCHPACT_POSTGRES_PASSWORD
CREATE ROLE orchpact LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'app_password';
ALTER DATABASE orchpact OWNER TO orchpact;
