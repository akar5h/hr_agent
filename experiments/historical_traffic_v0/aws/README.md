# AWS setup — unattended, self-terminating run

Laptop-off run. Everything lives on AWS; the box cleans itself up; artifacts
survive in S3. No SQS / ECS / Lambda / Redis — one EC2 + managed services.

## Architecture

```
 you (phone/console)                     AWS
        │  watch logs                ┌────────────────────────────────┐
        └───────────────────────────▶│ CloudWatch Logs /hr-traffic-v0 │
                                      └────────────────────────────────┘
   provisioner role (my CLI) ──launch/kill──▶  EC2 t3.small (self-terminating)
        │  stage code+secrets                    │ instance role:
        │                                         │  • Bedrock InvokeModel  ──▶ DeepSeek/Qwen/Kimi
        ├──▶ S3  hr-traffic-v0-*  ◀──artifacts────┤  • S3 pull code / push out
        │      (code in, traces/report out)       │  • SSM read secrets
        └──▶ SSM /hr-traffic-v0/*  ◀──read────────┤  • Postgres wire ──▶ RDS free-tier (you)
               (DATABASE_URL, LANGFUSE keys)       └──▶ Langfuse cloud (external SaaS)
```

## Services + why

| Service | Role | Cost guard |
|---|---|---|
| **EC2** t3.small | runs the orchestrator | self-terminates on finish + timed failsafe; on-demand ~$0.02/hr |
| **S3** `hr-traffic-v0-*` | durable artifacts (EBS dies with box) + code drop | pennies; lifecycle-expire after N days |
| **SSM Parameter Store** | secrets (DB URL, Langfuse keys) — never baked into user-data | free |
| **CloudWatch Logs** | live run logs so you watch from anywhere | pennies |
| **RDS Postgres** free-tier | agent state / checkpointer | **you** create; db.t3.micro/t4g.micro, Single-AZ, 20GB, no Multi-AZ |
| **Bedrock** | LLM invoke | in-app $45 hard cap |

## Two IAM roles

- **`iam-provisioner.json`** → attach to the principal YOU grant me. Launches/kills
  the tagged EC2, stages code+secrets, watches logs. EC2 lifecycle + SG ops scoped
  to `project=hr-traffic-v0` tag / `REGION`, so I can't touch your other resources.
- **`iam-ec2-instance-role.json`** → the instance profile EC2 assumes at runtime.
  Bedrock invoke + S3 + SSM read + Logs + self-terminate. Trust policy below.

### Instance role trust policy
```json
{ "Version": "2012-10-17",
  "Statement": [{ "Effect": "Allow",
    "Principal": { "Service": "ec2.amazonaws.com" },
    "Action": "sts:AssumeRole" }] }
```
Role name must be `hr-traffic-bedrock-ec2-role` (the provisioner's `PassRole` is
pinned to it). Create an instance profile of the same name wrapping it.

## Boot flow (user-data)

Launched with `--instance-initiated-shutdown-behavior terminate` and
`--tag-specifications 'ResourceType=instance,Tags=[{Key=project,Value=hr-traffic-v0}]'`:

1. install python + deps; `aws s3 cp s3://BUCKET/code.tar.gz` → unpack.
2. read secrets from SSM (`DATABASE_URL`, `LANGFUSE_*`) into env.
3. run the pipeline (CP6→CP8), streaming to CloudWatch, artifacts → `s3://BUCKET/out/`.
4. on exit (success OR failure) `aws s3 sync out/ s3://BUCKET/out/` then `shutdown -h now`
   → instance terminates (EBS gone, S3 keeps everything).
5. **Failsafe:** user-data also sets `shutdown -h +360` (6h hard cap) so a hung run
   still kills the box even if the pipeline never returns.

## Self-termination = two independent guards

1. shutdown-behavior=terminate + script `shutdown -h now` (normal path).
2. instance-role `ec2:TerminateInstances` on own tag (script can force-kill itself).
Plus the 6h `shutdown +360` failsafe. The $45 LLM cap is enforced in-app and stops
Bedrock spend regardless of instance state.

## Teardown / kill checklist (when you're back)

```
1. aws ec2 describe-instances --filters Name=tag:project,Values=hr-traffic-v0   # confirm terminated
2. aws s3 sync s3://BUCKET/out/ ./out/                                          # pull artifacts
3. aws ec2 delete-security-group  (the hr-traffic-v0 SG, if I created one)
4. aws ssm delete-parameter  /hr-traffic-v0/*                                   # wipe secrets
5. (optional) empty + delete the S3 bucket, or let lifecycle expire it
6. YOU: stop/delete the free-tier RDS if not reused
7. YOU: detach/delete the two IAM roles + instance profile
```

## What YOU do (console, one-time) vs what I do (CLI)

**You:**
- Create the **RDS** free-tier Postgres; note endpoint/user/pass; put its SG in a VPC.
- In **Bedrock console → Model access**, enable the chosen models (DeepSeek / Qwen /
  Kimi / GLM). Some open-weight models need a one-time access grant; keep this a
  console action, not an IAM grant to me.
- Create the two **IAM roles** from the JSONs (fill placeholders), attach provisioner
  to the principal you hand me, create the instance profile.
- Put the secrets in **SSM** (or let me, provisioner has `PutParameter`):
  `/hr-traffic-v0/DATABASE_URL`, `/hr-traffic-v0/LANGFUSE_SECRET_KEY`,
  `/hr-traffic-v0/LANGFUSE_PUBLIC_KEY` as SecureString.
- Set an **AWS Budgets** alarm (~$50) — needs `budgets:*`, do it in console.

**Me (CLI, asking before each resource):**
- CP0: verify Bedrock models + TPM quotas in region.
- Stage code → S3, secrets → SSM (if you delegate).
- Create the EC2 SG (EC2→RDS:5432), launch the tagged self-terminating instance.
- Watch CloudWatch; pull artifacts; run the teardown checklist.

## Placeholders to fill in all three JSONs
`ACCOUNT_ID`, `REGION`, `BUCKET` (e.g. `hr-traffic-v0-<account>`), `KMS_KEY_ID`
(the SSM SecureString key — `alias/aws/ssm` default key works; scope `kms:Decrypt`
to it).

## Region note
Pick `REGION` in CP0 by **model availability + TPM quota**, not price. Bedrock
cross-region inference profiles may route invokes to sibling regions — that's why
the instance role's Bedrock `Resource` keeps `arn:aws:bedrock:*::foundation-model/*`.
Tighten once the region set is fixed.
```
