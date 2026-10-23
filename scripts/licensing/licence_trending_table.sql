-- Snowflake table behind the platform_maintenance_license_trending dataset.
-- DSS named the table <PROJECT_KEY>_<dataset name>, hence the doubled prefix.
--
-- Column order MUST match the order of today_row in licence_count_snapshot.py.
-- With the first version of this DDL (trial_full_designer_count before
-- technical_account_count) the recipe failed with:
--   SQLException: Cannot write to table, table already exists but with an
--   incompatible schema: Name mismatch for column 3 : 'technical_account_count'
--   in dataset, 'trial_full_designer_count' in table ...
--
-- CREATE OR REPLACE wipes history. To keep history, use the ALTER variant
-- at the bottom instead (Snowflake will not mix ADD and DROP in one ALTER).

CREATE OR REPLACE TABLE "PLATFORM_MAINTENANCE_PLATFORM_MAINTENANCE_LICENSE_TRENDING" (
    "date"                               varchar,
    "full_designer_count"                bigint,
    "technical_account_count"            bigint,
    "trial_full_designer_count"          bigint,
    "trial_technical_account_count"      bigint,
    "total_non_trial_users"              bigint,
    "total_trial_users"                  bigint,
    "ai_consumer_and_reader_count"       bigint,
    "trial_ai_consumer_and_reader_count" bigint
);

-- Previous schema (before AI_CONSUMER and READER were merged):
--   "date" varchar(16777216), "full_designer_count", "trial_full_designer_count",
--   "trial_ai_consumer_count", "trial_reader_count", "total_non_trial_users",
--   "total_trial_users", "ai_consumer_count", "reader_count"   (all bigint)
--
-- History-preserving migration from that schema (new columns are NULL for
-- old rows). Note this appends the new columns at the end, so the column
-- ORDER will not match today_row. Reorder today_row or rebuild the table.
--
-- ALTER TABLE "PLATFORM_MAINTENANCE_PLATFORM_MAINTENANCE_LICENSE_TRENDING"
--     DROP COLUMN "trial_ai_consumer_count",
--     DROP COLUMN "trial_reader_count",
--     DROP COLUMN "ai_consumer_count",
--     DROP COLUMN "reader_count";
--
-- ALTER TABLE "PLATFORM_MAINTENANCE_PLATFORM_MAINTENANCE_LICENSE_TRENDING"
--     ADD COLUMN "technical_account_count" bigint,
--     ADD COLUMN "trial_technical_account_count" bigint,
--     ADD COLUMN "ai_consumer_and_reader_count" bigint,
--     ADD COLUMN "trial_ai_consumer_and_reader_count" bigint;
