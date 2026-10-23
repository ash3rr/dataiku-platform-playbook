# Fleet Manager: "Provision" hangs for a new node

Worked through in the sandbox account (AWS profile `dev2`, SSO role `<SSO_ROLE>`), when provisioning a new design node from Fleet Manager made no progress.

## First question: did an EC2 instance launch?

List what is running. If no new instance is `pending`, FM failed before calling EC2 successfully and the answer is in FM's own log, not on any node.

```powershell
aws ec2 describe-instances --filters "Name=instance-state-name,Values=pending,running" --query "Reservations[].Instances[].[InstanceId,State.Name,LaunchTime,Tags[?Key=='Name'].Value]" --output table --profile dev2
```

What was there: the FM instance itself (t3.medium, tag `dataiku-fleet-manager`, SG `fleet-manager-ec2`, no key pair), the existing FM-managed nodes, and some EKS worker nodes. No new instance. The existing FM-managed nodes had all been launched together, much earlier, with key pair `fleet-manager` and SG `dataiku-dss-node`.

## Where FM's logs are

FM runs on its own EC2 instance as a Java process under supervisord, as the `dataiku` user. The guesses that do **not** exist: `/var/log/dataiku-fm/backend.log`, logs directly under `/data/dataiku/fmhome`, a systemd unit for the backend (`systemctl list-units | grep -i dataiku` only shows `fm-setup.service`, "Setup Dataiku Fleet Manager based on a configuration file", a one-shot).

Find it the long way if needed:

```bash
sudo find / -type d -name "*dataiku*" 2>/dev/null
ps aux | grep -iE "java|python" | grep -v grep
```

The supervisord config is `/data/dataiku/fmhome/install-support/supervisord.conf`. ssm-user gets `Permission denied`, so switch to dataiku:

```bash
sudo su - dataiku
grep -i "stdout\|stderr\|logfile" /data/dataiku/fmhome/install-support/supervisord.conf
# logfile = %(ENV_DIP_HOME)s/run/supervisord.log
# stdout_logfile = %(ENV_DIP_HOME)s/run/%(program_name)s.log
# redirect_stderr = true
# stdout_logfile_maxbytes = 100MB
ls /data/dataiku/fmhome/run/
# fmmain.log  fmmain.log.1 ... fmmain.log.10  install.log  nginx  nginx.log  supervisord.log  supervisord.pid  svd.sock
```

The application log is:

```
/data/dataiku/fmhome/run/fmmain.log
```

Tail it while clicking Provision again:

```bash
aws ssm start-session --target <FM_INSTANCE_ID> --profile dev2
sudo su - dataiku
tail -f /data/dataiku/fmhome/run/fmmain.log
grep -iE "error|exception|fail|provisi|launch" /data/dataiku/fmhome/run/fmmain.log | tail -50
```

Useful patterns to grep for when it is an AWS-side refusal: `AccessDenied`, `InvalidParameter`, `VcpuLimitExceeded`, `InsufficientInstanceCapacity`, `InvalidKeyPair`.

## The case we hit

On the retry:

```
InvalidKeyPair.NotFound: The key pair 'fleet-manager' does not exist
```

The log also gave the template ID FM was using (`ist-<ID>`).

FM's config file does not hold it (`config/settings.json` only has SSO claim keys and `spnegoMode: PREAUTH_KEYTAB` when grepped for "key"). Instance settings templates live in FM's own PostgreSQL database (local, database and user `dataiku`). Log in once rather than per command, since each call prompts for the password:

```bash
psql -U dataiku -h 127.0.0.1 -d dataiku
```

Tables in that database: `datavolumesnapshot`, `fmgroup`, `fmpersonalapikey`, `fmuser`, `fmusersession`, `instancesettingstemplate`, `logicalinstance`, `logicalinstanceevent`, `physicaldatavolume`, `physicalinstance`, `physicalinstancecreationstate`, `sublicense`, `tenant`, `user_to_group`, `virtualnetwork` (plus sequences). Guesses that failed: `instancesettingstemplates` (`relation does not exist`) and a `name` column (it is `label`).

`instancesettingstemplate` columns include `id`, `label`, `awskeypairname`, `description`, `forsnapshot`, `copiedfromid`, `license`, `setupactions`, `startupinstanceprofilearn`, `runtimeinstanceprofilearn`, `restrictawsmetadataserveraccess`, `dataikuawsapiaccessmode`, `dataikuawsaccesskeyid`, `dataikuawssecretaccesskey`, `tenant_id` and Azure/GCP equivalents. Some of those (`license`, `dataikuawssecretaccesskey`) are sensitive. Select only what you need. Key pair names are not secrets.

```sql
\d instancesettingstemplate
SELECT id, label, awskeypairname FROM instancesettingstemplate;
```

Result: `ist-default` has no key pair. The "Main IST for design/automation/deployer nodes" and every snapshot of it use `fleet-manager`. The "IST for admin node" and its snapshots have none.

Then:

```powershell
aws ec2 describe-key-pairs --region eu-west-1 --profile dev2 --query "KeyPairs[].KeyName" --output table
```

returned nothing: no key pairs at all in the region. The key pair existed when the other nodes were launched and was deleted later. The infrastructure repo's Terraform has no `aws_key_pair` resource, and its EC2 module's `key_pair` variable defaults to `""`, so it was created by hand and nothing recreated it.

## Fixes

A key pair is only used at launch, to put a public key on the new instance. Running instances are unaffected. We access nodes through SSM, not SSH, so the key material is irrelevant. FM only needs the name to resolve. Recreating a key pair with the same name gives a different key, and that is fine for the same reason.

1. Proper fix: create a key pair named `fleet-manager` and manage it in Terraform. Attempted:

   ```powershell
   aws ec2 create-key-pair --key-name "fleet-manager" --region eu-west-1 --profile dev2 --query "KeyMaterial" --output text | Out-File -FilePath fleet-manager.pem
   ```

   (The bash version with `\` continuations fails in PowerShell with `Missing expression after unary operator '--'`.) Result:

   ```
   An error occurred (UnauthorizedOperation) when calling the CreateKeyPair operation: You are not authorized to perform this operation.
   User: arn:aws:sts::<DEV_ACCOUNT_ID>:assumed-role/AWSReservedSSO_<SSO_ROLE>_<HASH>/<user>
   is not authorized to perform: ec2:CreateKeyPair on resource: arn:aws:ec2:eu-west-1:<DEV_ACCOUNT_ID>:key-pair/fleet-manager
   because no identity-based policy allows the ec2:CreateKeyPair action. Encoded authorization failure message: ...
   ```

   So this needs the owners of the infrastructure repo or the cloud team.

2. Unblock now: clear the key pair name in the template so FM launches without one (SSM still works):

   ```sql
   UPDATE instancesettingstemplate SET awskeypairname = NULL WHERE id = '<IST_ID>';
   ```

   Option: set it to an existing key pair name instead. There were none.

Which option was finally used was not recorded.

