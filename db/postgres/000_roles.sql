-- PostgreSQL bootstrap. Run only as the local cluster superuser.
-- Login remains disabled until the operator provisions EACH service password
-- via a secret-bearing channel (for example psql's interactive \password).
DO $roles$
DECLARE role_name text;
BEGIN
  FOREACH role_name IN ARRAY ARRAY[
    'mcp_user', 'webhook_user', 'ci_user', 'controller_user', 'db_migrator'
  ] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION', role_name);
    END IF;
  END LOOP;
END
$roles$;

REVOKE ALL ON DATABASE agent_workflow FROM PUBLIC;
GRANT CONNECT ON DATABASE agent_workflow TO mcp_user, webhook_user, ci_user, controller_user, db_migrator;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS workflow AUTHORIZATION db_migrator;
REVOKE ALL ON SCHEMA workflow FROM PUBLIC;
GRANT USAGE ON SCHEMA workflow TO mcp_user, webhook_user, ci_user, controller_user;

ALTER ROLE mcp_user IN DATABASE agent_workflow SET search_path = workflow, pg_catalog;
ALTER ROLE webhook_user IN DATABASE agent_workflow SET search_path = workflow, pg_catalog;
ALTER ROLE ci_user IN DATABASE agent_workflow SET search_path = workflow, pg_catalog;
ALTER ROLE controller_user IN DATABASE agent_workflow SET search_path = workflow, pg_catalog;
