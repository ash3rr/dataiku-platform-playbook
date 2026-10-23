# Snowflake network isolation

## What we want Snowflake to enforce

Two Snowflake accounts: one shared by DEV and UAT, one for PROD. The PROD account should refuse any connection that doesn't come from the PROD VPC, whatever credentials it presents. The DEV/UAT account should refuse anything that isn't from the DEV or UAT VPC.

Credentials alone don't give you that. A DSS user with permission to read connection details, or with access to the node's environment, can copy a password or key pair into a notebook. The network check is what makes the copied credential useless from the wrong place.

## Option 1: allowlist outbound IPs

Each VPC sends traffic out through its own NAT gateway, and each NAT gateway has a fixed Elastic IP. On the PROD account, allow only the PROD NAT gateway's IP. On the DEV/UAT account, allow the DEV and UAT IPs.

This is cheap and easy. Its weaknesses: the traffic crosses the public internet (encrypted, but public), anything else that shares a NAT gateway shares the allowlist entry, and it only works if environments really do have separate NAT gateways. Two subnets in one VPC sharing one NAT gateway look identical to Snowflake.

## Option 2: PrivateLink and `AWSVPCEID` rules

Snowflake on AWS supports PrivateLink on Business Critical edition and above. You create an interface VPC endpoint in your VPC pointing at Snowflake's endpoint service. Every endpoint has an ID (`vpce-...`), and Snowflake network rules of type `AWSVPCEID` can allow or deny by that ID.

With one endpoint per environment VPC, the PROD account allows exactly one endpoint ID. A request that arrives through the DEV endpoint is rejected before authentication.

When a request arrives over private connectivity and the allowed list contains an `AWSVPCEID` rule, Snowflake ignores the IPv4 rules for that request. A blocked `0.0.0.0/0` rule therefore stops public traffic without affecting PrivateLink traffic. The SQL is in [`snowflake/network-policies.sql`](../../infra/snowflake/network-policies.sql) and the endpoint in [`terraform/modules/snowflake-privatelink`](../../infra/terraform/modules/snowflake-privatelink).

### Setting it up

1. On each Snowflake account, as `ACCOUNTADMIN`, run `SELECT SYSTEM$GET_PRIVATELINK_CONFIG();`. It returns the endpoint service name (`privatelink-vpce-id`) and the hostnames you need DNS records for.
2. In each environment VPC, create the interface endpoint, a security group that allows 443 and 80 from the DSS nodes and EKS workers, and a private hosted zone for `privatelink.snowflakecomputing.com` with CNAMEs for each hostname from step 1.
3. Note each endpoint ID and put it in the network rules on the right account.
4. Point the DSS connections at the PrivateLink hostname (`<account>.privatelink.snowflakecomputing.com` or the regionless variant).
5. If DSS uses fast-write (bulk load through an S3 stage), the stage traffic needs its own path. Give each environment its own staging bucket and check whether your account uses PrivateLink for internal stages.

### DEV and UAT in one account

The DEV/UAT account allows both endpoints at account level. To stop the DEV service user from connecting through the UAT endpoint (and the reverse), attach a user-level network policy to each service user that allows only its own endpoint. User-level policies take precedence over the account policy for that user.

## The catch: a central network account

Many organisations route all private connectivity through a dedicated networking account: workload VPCs attach to a Transit Gateway, and the PrivateLink endpoints live in the network account.

```
DSS node (DEV VPC) -> Transit Gateway -> network account -> one Snowflake endpoint -> Snowflake
```

The endpoint ID belongs to the endpoint, not to the VPC the traffic started in. If DEV, UAT and PROD all exit through the same endpoint in the network account, Snowflake sees one ID for all three, and `AWSVPCEID` rules can't tell them apart. The design above stops working without anything visibly failing.

This came up late, after the multi-VPC design had been drawn on the assumption that endpoints would live in the workload VPCs. Ask early. The question for the networking team is short: "Can we create Snowflake PrivateLink endpoints in our own VPCs, or must all PrivateLink go through the network account? If the latter, is there one endpoint or could there be one per environment?"

The three outcomes:

1. Endpoints in the workload VPCs. The design works as written.
2. One endpoint per environment in the network account, with Transit Gateway routing that sends each environment only to its own endpoint. Works, but the isolation now depends on routing someone else owns. Make sure the routing is in their change control, not a one-off.
3. One shared endpoint. Snowflake can't enforce the boundary by network. Keep the separate VPCs anyway (they still isolate S3, EKS and node-to-node traffic) and fall back to security groups, separate service users, key-pair auth and Snowflake RBAC for the Snowflake side. The design should state that Snowflake then has only this one layer of protection.

The vendor's field engineer proposed an alternative for the single-VPC case: separate security groups per node, with Snowflake-bound traffic filtered by security group. That gives you a network control on the AWS side, but Snowflake still sees one endpoint, so it is the same single layer as outcome 3.
