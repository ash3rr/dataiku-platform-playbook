-- Network rules and policies for VPC-endpoint-based isolation.
--
-- Account A serves DEV and UAT, account B serves PROD. Each environment VPC has
-- its own PrivateLink endpoint; the endpoint IDs come from the Terraform output
-- `vpce_id` of terraform/modules/snowflake-privatelink.
--
-- When a request arrives over PrivateLink and the allowed list contains an
-- AWSVPCEID rule, Snowflake ignores IPv4 rules for that request. Blocking
-- 0.0.0.0/0 therefore stops public traffic without affecting the endpoints.
--
-- Run as ACCOUNTADMIN, or a role with CREATE NETWORK POLICY and ownership of the
-- schema holding the rules. Before activating, make sure your own session comes
-- through an allowed endpoint, or you will lock yourself out of the account.


-- ===========================================================================
-- Account A (DEV/UAT)
-- ===========================================================================

USE ROLE ACCOUNTADMIN;
CREATE DATABASE IF NOT EXISTS PLATFORM_ADMIN;
CREATE SCHEMA IF NOT EXISTS PLATFORM_ADMIN.NETWORK;
USE SCHEMA PLATFORM_ADMIN.NETWORK;

CREATE OR REPLACE NETWORK RULE allow_dev_vpce
  MODE = INGRESS
  TYPE = AWSVPCEID
  VALUE_LIST = ('vpce-0aaaaaaaaaaaaaaa1')
  COMMENT = 'PrivateLink endpoint in the DEV VPC';

CREATE OR REPLACE NETWORK RULE allow_uat_vpce
  MODE = INGRESS
  TYPE = AWSVPCEID
  VALUE_LIST = ('vpce-0bbbbbbbbbbbbbbb2')
  COMMENT = 'PrivateLink endpoint in the UAT VPC';

CREATE OR REPLACE NETWORK RULE block_public
  MODE = INGRESS
  TYPE = IPV4
  VALUE_LIST = ('0.0.0.0/0')
  COMMENT = 'No access from public networks';

CREATE OR REPLACE NETWORK POLICY account_dev_uat
  ALLOWED_NETWORK_RULE_LIST = ('allow_dev_vpce', 'allow_uat_vpce')
  BLOCKED_NETWORK_RULE_LIST = ('block_public');

ALTER ACCOUNT SET NETWORK_POLICY = account_dev_uat;

-- DEV and UAT share this account, so pin each service user to its own
-- endpoint. A user-level policy replaces the account policy for that user.

CREATE OR REPLACE NETWORK POLICY svc_dss_dev_only
  ALLOWED_NETWORK_RULE_LIST = ('allow_dev_vpce')
  BLOCKED_NETWORK_RULE_LIST = ('block_public');

CREATE OR REPLACE NETWORK POLICY svc_dss_uat_only
  ALLOWED_NETWORK_RULE_LIST = ('allow_uat_vpce')
  BLOCKED_NETWORK_RULE_LIST = ('block_public');

ALTER USER SVC_DSS_DEV SET NETWORK_POLICY = svc_dss_dev_only;
ALTER USER SVC_DSS_UAT SET NETWORK_POLICY = svc_dss_uat_only;


-- ===========================================================================
-- Account B (PROD)
-- ===========================================================================

USE ROLE ACCOUNTADMIN;
CREATE DATABASE IF NOT EXISTS PLATFORM_ADMIN;
CREATE SCHEMA IF NOT EXISTS PLATFORM_ADMIN.NETWORK;
USE SCHEMA PLATFORM_ADMIN.NETWORK;

CREATE OR REPLACE NETWORK RULE allow_prod_vpce
  MODE = INGRESS
  TYPE = AWSVPCEID
  VALUE_LIST = ('vpce-0ccccccccccccccc3')
  COMMENT = 'PrivateLink endpoint in the PROD VPC';

CREATE OR REPLACE NETWORK RULE block_public
  MODE = INGRESS
  TYPE = IPV4
  VALUE_LIST = ('0.0.0.0/0')
  COMMENT = 'No access from public networks';

CREATE OR REPLACE NETWORK POLICY account_prod
  ALLOWED_NETWORK_RULE_LIST = ('allow_prod_vpce')
  BLOCKED_NETWORK_RULE_LIST = ('block_public');

ALTER ACCOUNT SET NETWORK_POLICY = account_prod;


-- ===========================================================================
-- Checks
-- ===========================================================================

-- Which policy applies to the account and to the service users.
SHOW PARAMETERS LIKE 'NETWORK_POLICY' IN ACCOUNT;
SHOW PARAMETERS LIKE 'NETWORK_POLICY' IN USER SVC_DSS_DEV;

-- Recent failed logins, to spot connections arriving from the wrong place.
SELECT event_timestamp, user_name, client_ip, reported_client_type, error_message
FROM SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY
WHERE is_success = 'NO'
  AND event_timestamp > DATEADD(day, -7, CURRENT_TIMESTAMP())
ORDER BY event_timestamp DESC;
