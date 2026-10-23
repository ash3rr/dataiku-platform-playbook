-- Vanity URL check, step 3: SQL scenario step on the Snowflake connection
-- that holds the audit table (<SNOWFLAKE_CONNECTION>).
-- Run condition: "Run always", so it executes even though step 2 fails the
-- scenario on purpose when duplicates exist.
-- Retention: six months.
--
-- A Python alternative with dataiku.sql.SQLExecutor2(...).exec_recipe_fragment()
-- was drafted first. The plain SQL step is simpler and is what runs.

DELETE FROM <DATABASE>.<SCHEMA>.PLATFORM_MAINTENANCE_VANITY_URL_AUDIT
WHERE CHECK_TIMESTAMP < DATEADD(MONTH, -6, CURRENT_TIMESTAMP())
