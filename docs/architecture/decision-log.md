# Decision log

Short records of what was decided and what was turned down. Most recent last.

---

### Separate VPCs per environment, not separate subnets

Decided: MGMT, DEV, UAT and PROD each get a VPC, joined by a Transit Gateway with blackhole routes between the environment VPCs.

Turned down: subnets within the existing VPC. They share a NAT gateway, so Snowflake can't tell environments apart by IP, and they'd share any interface endpoint created in the VPC.

Cost: more NAT gateways and endpoints, a Transit Gateway, and a dependency on the cloud team, who own VPC creation.

---

### PrivateLink with `AWSVPCEID` rules for Snowflake

Decided: one Snowflake interface endpoint per environment VPC. Each Snowflake account allows only the endpoint IDs of its environments and blocks public IPv4.

Turned down: IP allowlisting alone. It works, but traffic crosses the internet and the control is weaker. Kept as a fallback for DEV/UAT if PrivateLink is refused.

Open: depends on being allowed to place endpoints in workload VPCs rather than routing through the central network account.

---

### Isolation per DSS group, not per project

Decided: one bucket and one IAM role per group per environment.

Turned down: per-project buckets (thousands of buckets, connections and remapping rules, with project counts growing faster than group counts), and a single bucket per environment with prefix-scoped IAM roles (a prefix condition per group in one bucket policy hits the bucket policy size limit quickly and makes lifecycle rules harder).

Why the group: it is how DSS already scopes access. Members of a group see all its projects, so there's nothing to protect between projects of the same group.

---

### Per-group connections with fixed role ARNs

Decided: each group gets its own S3 connection, using STS AssumeRole with that group's role, usable only by that group.

Turned down: one shared connection with the role ARN taken from a per-user admin property. A user in two groups can hold only one value, so they'd lose access to one group's data.

---

### Intelligent-Tiering instead of age-based lifecycle rules

Decided: objects move to Intelligent-Tiering on write, with archive tiers after a configurable period of no access.

Turned down: lifecycle transitions by object age. They archive data that's read daily but rarely rewritten, which is most of what a data science platform stores.

Accepted risk: archived objects need a restore before DSS can read them.

---

### Git access from the Design node and Deployer only

Decided: only DEV and MGMT get a route to GitHub. Automation and API nodes receive bundles from the Deployer.

Note: the Deployer was initially left out of this list. It needs Git because it builds bundles from the project's backing repository.

---

### Strip VPC peering permissions from Fleet Manager

Decided: remove `ec2:CreateVpcPeeringConnection`, `ec2:AcceptVpcPeeringConnection` and `ec2:DeleteVpcPeeringConnection` from the FM role after the Transit Gateway exists.

Why: FM can pair virtual networks on its own, which would create a path between VPCs that bypasses the blackhole routes.

---

### Don't build ahead of the architecture

Decided: no new DSS nodes or buckets in the target account for individual projects until the network design is approved by the vendor and the cloud team.

Why: a node built before the network exists ends up in the wrong VPC with the wrong role, and moving it later means a migration instead of a fresh install.
