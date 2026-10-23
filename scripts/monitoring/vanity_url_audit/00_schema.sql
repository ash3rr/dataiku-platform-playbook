-- Prerequisite for the Vanity URL check scenario: the Snowflake audit table.
--
-- TIMESTAMP_NTZ on purpose. TIMESTAMP_TZ was the first draft and was
-- rejected: everything is UTC, so the zone adds nothing. Python writes
-- naive pandas Timestamps (pd.Timestamp(ms, unit='ms')), never ISO strings.

CREATE TABLE IF NOT EXISTS <DATABASE>.<SCHEMA>.PLATFORM_MAINTENANCE_VANITY_URL_AUDIT (
    CHECK_TIMESTAMP     TIMESTAMP_NTZ   NOT NULL,
    VANITY_SLUG         VARCHAR(255)    NOT NULL,
    WEBAPP_KEY          VARCHAR(255)    NOT NULL,
    PROJECT_KEY         VARCHAR(255)    NOT NULL,
    WEBAPP_ID           VARCHAR(50)     NOT NULL,
    WEBAPP_NAME         VARCHAR(500),
    CREATED_AT          TIMESTAMP_NTZ,
    LAST_MODIFIED_AT    TIMESTAMP_NTZ,
    CREATOR             VARCHAR(255),
    LAST_MODIFIED_BY    VARCHAR(255),
    IS_RUNNING          BOOLEAN,
    IS_DUPLICATE        BOOLEAN         NOT NULL
);

-- The table was created by hand, so it was owned by a personal role and the
-- first recipe run failed with:
--   SnowflakeSQLException: SQL access control error: Insufficient privileges
--   to operate on table 'PLATFORM_MAINTENANCE_VANITY_URL_AUDIT'. Your primary
--   role <DSS_SNOWFLAKE_ROLE> must have OWNERSHIP granted on TABLE ...
-- Hand ownership to the role the DSS connection uses:
GRANT OWNERSHIP ON TABLE <DATABASE>.<SCHEMA>.PLATFORM_MAINTENANCE_VANITY_URL_AUDIT
TO ROLE <DSS_SNOWFLAKE_ROLE>
COPY CURRENT GRANTS;

-- In a managed access schema that GRANT fails with
--   SQL execution error: Not authorized in a managed access schema. Use the
--   schema owner role or a role with the MANAGE GRANTS privilege.
-- Options we looked at: run the GRANT as the schema owner role (SYSADMIN /
-- ACCOUNTADMIN depending on who owns the schema), or create the table while
-- using the DSS role so it owns it from the start:
--   USE ROLE <DSS_SNOWFLAKE_ROLE>;
--   CREATE TABLE <DATABASE>.<SCHEMA>.PLATFORM_MAINTENANCE_VANITY_URL_AUDIT (...);
-- Dropping the table and expecting write_with_schema to recreate it did NOT
-- work for us (DSS did not recreate it), possibly because the DSS role lacks
-- CREATE TABLE on the schema:
--   GRANT CREATE TABLE ON SCHEMA <DATABASE>.<SCHEMA> TO ROLE <DSS_SNOWFLAKE_ROLE>;
-- Which of these finally fixed it was not written down.
