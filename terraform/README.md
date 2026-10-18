# Terraform

Infrastructure for the orders application, organised as shared modules plus one folder per environment.

## Structure

```
terraform/
├── modules/
│   ├── dynamodb/            # orders table (on-demand, SSE, PITR, deletion protection)
│   ├── eventbridge/         # custom event bus
│   ├── global_endpoint/     # EventBridge global endpoint, failover health check and alarm (multi-region)
│   ├── order_api/           # REST API: schema validation, SQS integration, throttling, IAM auth
│   ├── notification_topic/  # SNS topic for order notifications, EventBridge-only publish policy
│   ├── dashboard/           # CloudWatch dashboard (MatchedEvents, NumberOfMessagesSent, NumberOfMessagesPublished)
│   ├── alerts/              # SNS topic for operational alerts, optional email subscriber
│   ├── event_rule/          # EventBridge rule to a target
│   ├── event_queue/         # SQS queue + dead-letter queue, TLS-only and source-scoped policies
│   ├── fulfil_order/        # FulfilOrder Lambda, IAM role, SQS trigger
│   └── create_order/        # CreateOrder Lambda, IAM role, log group
└── environments/
    ├── dev/
    └── prod/
```

Each environment folder contains `main.tf`, `secondary.tf` (the optional second Region), `variables.tf`, `versions.tf`, `backend.tf` (S3 state) and `outputs.tf`. Each environment has its own state, so `dev` and `prod` never affect each other.

## Requirements

- Terraform >= 1.10 (S3-native state locking)
- AWS provider ~> 6.0
- AWS credentials from a scoped role, not admin keys

## Usage

```bash
cd environments/dev        # or environments/prod
terraform init
terraform plan
terraform apply
```

## Outputs

Each environment exposes: `orders_table_name`, `orders_table_arn`, `fulfilments_table_name`, `event_bus_name`, `event_bus_arn`, `create_order_function_name`, `fulfil_order_function_name`, `order_api_url`, `intake_queue_url`, `intake_dlq_url`, `fulfilment_queue_url`, `fulfilment_dlq_url`, `order_notifications_topic_arn`, `alerts_topic_arn` and `dashboard_url`. Read one with `terraform output -raw <name>`.

## Variables

| Variable | Description | Default |
|---|---|---|
| `environment` | Environment name; defaults to the folder's own name (`dev` or `prod`) and only accepts those two | `dev` / `prod` |
| `aws_region` | AWS region | `us-east-1` |
| `notification_email` | Email for order notifications; empty means no subscriber | `""` |
| `alert_email` | Email for alarm notifications; empty means no subscriber | `""` |

The provider applies `Project`, `Environment` and `ManagedBy` tags to all resources via `default_tags`.

**Why there is no `terraform.tfvars`:** earlier versions kept `environment = "dev"` (or `"prod"`) in a committed `terraform.tfvars`. The repository's secret scanner ([docs/SECRET_SCANNING.md](../docs/SECRET_SCANNING.md)) blocks every `*.tfvars` file from being committed, because such files often hold secrets, so those two files were removed. `environment` now has a default in each environment's `variables.tf` (the folder's own name) and only accepts `dev` or `prod`. Behaviour is unchanged: resource names, tags and plans are identical. Real values (email addresses, the state bucket name) stay in git-ignored local files, `emails.auto.tfvars` and `backend.hcl`.

## Remote state

State is stored in S3 with native locking (`use_lockfile`, Terraform >= 1.10), so no DynamoDB lock table is needed. Both environments use the existing bucket `secure-serverless-api-tfstate-<ACCOUNT_ID>`, which is versioned, encrypted (SSE-S3), blocks public access and denies non-TLS requests. This project's keys do not collide with the other project's:

| Environment | State key |
|---|---|
| dev | `secure-event-driven-orders-aws/dev/terraform.tfstate` |
| prod | `secure-event-driven-orders-aws/prod/terraform.tfstate` |

The bucket name is not hard-coded. Copy `backend.hcl.example` to `backend.hcl` (git-ignored) and set the bucket, then initialise from an environment folder:

```bash
cd environments/dev
terraform init -backend-config=../../backend.hcl -migrate-state   # answer "yes" to copy existing local state
terraform plan                                                      # should show no changes
```

After confirming the state object exists (`aws s3 ls s3://<bucket>/secure-event-driven-orders-aws/`), delete the leftover local `terraform.tfstate*` files. Use plain `terraform init -backend-config=../../backend.hcl` for `prod`, which has no local state to migrate.

Status: `dev` has been migrated. The state object is in S3 and `terraform plan` reports no changes. Every `init` on a fresh clone needs the `-backend-config` flag, because the bucket name is not stored in the repository.

The bucket is managed by the `secure-serverless-api-aws` project, so change its settings there. The deploying identity needs `s3:GetObject`, `PutObject`, `DeleteObject` and `ListBucket` on it, which PowerUserAccess already provides.

### Destroy safety

The bucket lives in the other project's `bootstrap` configuration, which has its own state, so `terraform destroy` in this repo or in that project's `environments/` and `frontend/` folders never touches it. In `bootstrap` it has `prevent_destroy = true` and no `force_destroy`, so a destroy there fails, and S3 refuses to delete a bucket that still holds objects or versions.

State can still be lost by deleting the bucket's contents by hand (console or CLI) or by purging every object version. Bucket versioning protects against accidental overwrites and deletes. If you destroy `dev` in this repo, its state object stays in the bucket as an empty state; delete that key manually if you want it gone.

## CI/CD (GitHub Actions)

Two workflows live in `.github/workflows/` (they take effect once this folder is pushed to a GitHub repository as its root):

| Workflow | Trigger | What it does |
|---|---|---|
| `validate.yml` | Pull requests to `main` | Offline checks only: `terraform fmt -check`, `init -backend=false` plus `validate` for `dev` and `prod`, a compile check of both Lambda handlers, the Lambda unit tests (`tests/`, standard library only), a `bandit` scan of the Lambda code, a `checkov` scan of the Terraform (reviewed exceptions, each with a reason, are in `.checkov.yaml`) and the secret scan ([docs/SECRET_SCANNING.md](../docs/SECRET_SCANNING.md)). No AWS credentials are used, so a pull request can never touch AWS. |
| `deploy.yml` | Push to `main` (documentation-only changes are skipped), or run manually | Format and compile checks, `init`, `validate`, `plan`, a **no-delete guard**, then `apply` of exactly that saved plan. |

**Lambda packaging:** Terraform does it. The `archive_file` data sources zip each function's `src/handler.py` during the plan, and the apply updates a function whenever its code hash changes. There is no separate `update-function-code` step.

**Environments:** a push deploys `dev`. Run the workflow manually from the Actions tab and choose `prod` to deploy it. Create GitHub environments named `dev` and `prod` to add required reviewers.

**No-delete guard:** the run stops if the plan would delete or replace anything (except an API Gateway deployment, a stateless snapshot that is replaced whenever the API changes), because a replaced table loses its data and a missing email secret would delete a subscription. Re-run manually with `allow_destroy` only when that is intended. The run summary lists each change by action and resource address, never ARNs or IDs.

**Authentication is GitHub OIDC: no AWS keys are stored in GitHub.** The workflow assumes the role `orders-pipeline-deploy-role` with a short-lived token. The role's trust policy pins this exact repository by its immutable owner and repository IDs and allows only jobs running in the `dev` or `prod` GitHub environment, so pull requests, other branches and other repositories cannot assume it. The role and its permissions boundary are created by `terraform/bootstrap`, which is applied **manually** and never by the pipeline, so the pipeline cannot widen its own permissions.

**Repository secrets** (Settings, Secrets and variables, Actions):

| Secret | Value |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | The `deploy_role_arn` output of `terraform/bootstrap` |
| `TF_STATE_BUCKET` | The state bucket name (the one in `backend.hcl`) |
| `NOTIFICATION_EMAIL` | Address for order notifications |
| `ALERT_EMAIL` | Address for alarm notifications |

Optionally `SECRET_SCAN_LITERALS` (values the secret scan must block). Set them with the GitHub CLI so they never appear in a file, for example `gh secret set NOTIFICATION_EMAIL`.

### One-time setup of the deploy role

1. Create the GitHub repository (empty) and, in it, turn on immutable subject claims, then read the subject prefix:
   ```bash
   gh api -X PUT repos/<owner>/<repo>/actions/oidc/customization/sub -F use_default=true -F use_immutable_subject=true
   gh api repos/<owner>/<repo>/actions/oidc/customization/sub --jq .sub_claim_prefix
   ```
   The prefix looks like `repo:<owner>@<owner-id>/<repo>@<repo-id>`.
2. Make sure your own AWS identity may create the pipeline roles and policy: [iam/deploy-iam-policy.json](iam/deploy-iam-policy.json) covers `orders-pipeline-*`, `CreateOrder-*` and `FulfilOrder-*`. A permission set has a single inline policy, so if you already have one for another project, do not replace it with this file: add these statements to the existing policy instead (keep a local copy of your full policy, outside the repository), replacing `<ACCOUNT_ID>` with your account ID.
3. Apply the bootstrap (local state, git-ignored):
   ```bash
   cd bootstrap
   terraform init
   terraform apply -var github_sub_prefix='<the prefix from step 1>'
   ```
   It references the account's existing GitHub OIDC provider (`token.actions.githubusercontent.com`) and creates the deploy role, its inline policy and the permissions boundary `orders-pipeline-app-role-boundary`.
4. Set `AWS_DEPLOY_ROLE_ARN` to the `deploy_role_arn` output, and the other secrets.
5. Create the GitHub environments `dev` and `prod` (add a required reviewer on `prod`). The trust policy only accepts jobs that run in them.

The environments attach the boundary to both Lambda execution roles, so the bootstrap must be applied before any environment apply. The deploy role can only create or modify roles named `CreateOrder-*` and `FulfilOrder-*`, only with that boundary attached, and cannot remove the boundary or change itself.

**Repository rules (public repository).** The repository is public, and these settings are applied through GitHub, not through Terraform:
- `main` accepts changes only through a pull request with five passing checks, up to date with `main`, administrators included, no force pushes or deletions.
- `dev` and `prod` accept only protected branches, and `prod` needs the owner's approval. Without that, a workflow on any branch could assume the role.
- Secret scanning with push protection, Dependabot alerts and private vulnerability reporting are on; the Actions token is read-only and cannot approve pull requests; first-time contributors need approval to run workflows; actions must be pinned to a full commit SHA; merged branches are deleted automatically.
- Dependabot opens a weekly grouped pull request to update the pinned actions.

**Security notes**
- The role's permissions are limited to the project's own resources: tables `Orders-*` and `Fulfilments-*`, the `orders-*` bus and rules, the `order-fulfilment-*` queues, the two SNS topics, the two functions and their log groups, `order-fulfilment-*` alarms, `orders-*` dashboards and this project's state keys. If a new resource type is added, the policy in `bootstrap/main.tf` needs a matching statement, applied manually.
- The email addresses are marked `sensitive`, so Terraform hides them in plan output. The account ID is masked in the log (a workflow step registers it as a mask), but resource names, queue names and API IDs are visible. They are not secrets and were reviewed before the repository became public.
- Actions are pinned to full commit SHAs. `deploy.yml` runs only on pushes to `main` and manual runs, never on pull requests, and `validate.yml` uses no AWS credentials.

## DynamoDB module

| Input | Description | Default |
|---|---|---|
| `table_name`, `hash_key`, `range_key` | Table name and string key attributes | required |
| `deletion_protection` | Block deletion | `true` |
| `enable_point_in_time_recovery` | PITR | `true` |
| `range_key` | Optional; leave unset for a partition-key-only table | `null` |
| `kms_key_arn` | Customer-managed key; `null` uses the AWS-managed `aws/dynamodb` key | `null` |

`dev` sets `deletion_protection = false` so it can be destroyed; `prod` keeps it on.

## Asynchronous fulfilment

The `order-created-to-fulfilment-<env>` rule matches `source = orders.create-order` and `detail-type = OrderCreated` and sends the event to the `order-fulfilment-<env>` SQS queue. `FulfilOrder-<env>` reads the queue in batches of 10 (max 5 concurrent invocations) and writes one record per order to `Fulfilments-<env>`, keyed by `orderId`.

- Writes are conditional (`attribute_not_exists(orderId)`), so redelivered messages do not create duplicates.
- Only failed messages in a batch are retried. After 3 failed receives a message moves to `order-fulfilment-<env>-dlq`, where it is kept for 14 days.
- Both queues use SQS-managed encryption and reject non-TLS requests. Only the rule can send to the main queue.

A CloudWatch alarm (`order-fulfilment-<env>-dlq-not-empty`) fires when the dead-letter queue has any message and notifies the `orders-alerts-<env>` SNS topic. To receive email, set `alert_email` in `emails.auto.tfvars` (or pass `-var alert_email=you@example.com`) and click the confirmation link AWS sends. The topic is not KMS-encrypted, because CloudWatch alarms cannot publish to a topic using the AWS-managed `aws/sns` key.

To test the failure path, send a message the function cannot process; it is retried 3 times, lands in the dead-letter queue and triggers the alarm after a few minutes:

```bash
aws sqs send-message --queue-url "$(terraform output -raw fulfilment_queue_url)" --message-body 'bad'
```

Inspect the dead-letter queue with:

```bash
aws sqs get-queue-attributes --queue-url "$(terraform output -raw fulfilment_dlq_url)" \
  --attribute-names ApproximateNumberOfMessages
```

## Order intake (API Gateway and SQS)

`modules/order_api` creates a REST API with one route, `POST /orders`, wired straight to the `order-intake-<env>` SQS queue with no Lambda in the request path:

- **Authorisation:** `AWS_IAM`. The request must be SigV4-signed by a principal allowed `execute-api:Invoke`.
- **Validation:** the body must match a JSON schema (`customerId`, and `items` of `itemId` with an optional `quantity`, no other fields) or repeats an `itemId` (checked in the request template, because a JSON schema cannot express it), or API Gateway answers `400` with a fixed message. Other content types get `415`.
- **Message:** a mapping template builds the queue message from the validated fields, with `$context.requestId` as the `orderId` and the request time as `receivedAt`. The caller gets `202` with the `orderId` at once.
- **Throttling:** 10 requests per second, burst 20, on the stage.
- **Access:** the API assumes a role that can only `SendMessage` on that queue. The queue policy denies sending to everyone else, so nothing can write to it behind the API's back.

`CreateOrder` consumes the queue in batches of 10 (at most 5 concurrent), with partial-batch failure reporting. A message that fails three times moves to `order-intake-<env>-dlq`, which has its own alarm. This path was tested live with an order that repeats an `itemId`: the API answered `202`, `CreateOrder` rejected it on three attempts 180 seconds apart, SQS moved it to the dead-letter queue on the fourth receive, nothing was saved or published, and the alarm fired. Message processing is retry-safe: a redelivery finds the order already stored, confirms it is the same one, and publishes again.

Call it with a signed request (curl 7.75 or newer):

```bash
curl -s -X POST "$(terraform output -raw order_api_url)" \
  --aws-sigv4 "aws:amz:us-east-1:execute-api" --user "$AWS_ACCESS_KEY_ID:$AWS_SECRET_ACCESS_KEY" \
  -H "x-amz-security-token: $AWS_SESSION_TOKEN" -H "Content-Type: application/json" \
  -d '{"customerId":"c1","items":[{"itemId":"i1","quantity":2}]}'
```

**Rolling it out.** The permissions boundary and the deploy role changed, and so did the permission set the owner applies locally:

1. Add `apigateway.amazonaws.com` to the `iam:PassedToService` condition of the pass-role statement in your permission set (see [iam/deploy-iam-policy.json](iam/deploy-iam-policy.json)).
2. Apply `terraform/bootstrap` (the boundary gains the intake queue and the API role's `SendMessage`, and the deploy role gains REST API, intake queue and alarm access).
3. Merge; the pipeline creates the API, the queues and the new `CreateOrder` trigger.

Until step 3 completes there is a short window where `CreateOrder` is triggered by the queue instead of being invoked directly, so a direct `aws lambda invoke` no longer works afterwards.

**Not included.** API access logging and execution logging are off because they need an account-wide API Gateway CloudWatch role, which this project does not change. There is no WAF, cache or client certificate.

## Idempotent processing

`CreateOrder` processes each `orderId` once in effect. The state lives in a second table, `Orders-processing-<env>` (key `orderId` only, point-in-time recovery on, deletion protection in prod, time-to-live on `expiresAt`). One record per order holds:

| Attribute | Meaning |
|---|---|
| `status` | `IN_PROGRESS` while an invocation holds the lease, `COMPLETED` once the order is saved and its event published |
| `lockOwner`, `lockedUntil` | The lease: the holder and when it ends (60 seconds after the claim) |
| `payloadHash` | A fingerprint of the customer and the order lines, so an `orderId` reused for different content is detected |
| `result` | The stored outcome, returned to every later copy of the message |
| `expiresAt` | Seven days after the last write; DynamoDB then deletes the record |

The claim is one `PutItem` with a condition (no record, or an expired lease for the same fingerprint), so concurrent copies cannot both win. The result is recorded with a condition too: only the holder of the lease may write it, and a `COMPLETED` record is never overwritten. The Lambda role may only `PutItem` and `Query` this table. No bootstrap change was needed, because the name starts with `Orders-` and the boundary and the deploy role already cover `Orders-*`.

## Multi-region (optional)

The switch `enable_multi_region` (default `false`, in both environments) adds a secondary Region (`secondary_region`, default `us-west-2`). With it on:

- **A second copy** of the application in the secondary Region (`secondary.tf`): API Gateway, intake and fulfilment queues with dead-letter queues and alarms, `CreateOrder-<region>-<env>` and `FulfilOrder-<region>-<env>` Lambda functions, the `orders-<env>` event bus (it must have the same name as the primary), the rules, and the notification and alerts SNS topics. IAM role names are global, so the secondary roles carry the Region in their names.
- **Global tables:** `Orders`, `Fulfilments` and `Orders-processing` get a replica in the secondary Region (`replica_regions` in the DynamoDB module). Every table keeps a DynamoDB stream (`NEW_AND_OLD_IMAGES`) on whether or not the switch is on, because DynamoDB refuses to disable a stream in the same update that removes a replica; an unread stream has no charge.
- **A global endpoint** (`modules/global_endpoint`): an EventBridge endpoint over the two buses with failover routing driven by a Route 53 health check on a CloudWatch alarm (`IngestionToInvocationStartLatency` above 30 seconds for five minutes). Event replication is disabled. `CreateOrder` in both Regions publishes with `PutEvents(EndpointId=...)`.
- **The signing library:** SigV4A needs `awscrt`. `scripts/vendor_awscrt.py` (standard library only) downloads one pinned, hash-checked wheel and assembles the `CreateOrder` package; Terraform runs it through an `external` data source, so it needs `python3` and internet access when planning.

**Rolling it out**

1. Apply `terraform/bootstrap`: the permissions boundary gains the secondary Region (one in-place change). The deploy role is deliberately not extended.
2. From the environment folder: `terraform apply -var enable_multi_region=true`. Expect about 54 resources to add and 6 to change, and no deletes. DynamoDB global tables use a service-linked role; if the apply complains about it, create `AWSServiceRoleForDynamoDBReplication` once with an identity that can manage IAM.
3. Confirm the extra SNS subscription emails (notifications and alerts in the secondary Region).
4. Test: `terraform output order_api_url_secondary` gives the secondary API. Sign requests for the secondary Region (`aws:amz:us-west-2:execute-api`).
5. Rehearse a failover by inverting the Route 53 health check from the `global_endpoint_health_check_id` output (`aws route53 update-health-check --health-check-id <id> --inverted`, and `--no-inverted` to fail back). Setting the alarm's state does not work, because Route 53 reads the metric. See the [FAQ](../docs/FAQ.md#multi-region) and [DISASTER_RECOVERY.md](../docs/DISASTER_RECOVERY.md).
6. **Before any change that triggers a pipeline deploy, switch it off:** `terraform apply -var enable_multi_region=false` (or run without the variable). While the secondary stack exists, the pipeline cannot work: its role has no permissions in the secondary Region (or on the replicated tables), so the plan fails while reading those resources, before anything is changed. Switching off removes about 54 resources (the secondary stack, the replicas and the Route 53 health check). It was verified in `dev` on 2026-10-18: the first attempt failed with `Disabling Stream is not allowed for a Global Table replica` on the three tables, which is why the streams now stay on; after the fix the apply completed, `terraform plan` showed no changes, and no health check or secondary API was left. After switching off, a deploy run triggered by a later merge plans against a single-Region state and passes; re-run an earlier failed deploy (`gh run rerun <run id> --failed`) only if no newer deploy has superseded it.

**Costs:** the second Region is idle until used. Added: replicated DynamoDB writes (each write is billed in both Regions) and one Route 53 health check, about $0.50 a month.

**Not included:** DNS routing for the API, event replication, a secondary dashboard, and a pipeline role for the second Region.

## Event authenticity

Two controls stop a forged `OrderCreated` event from creating a fulfilment (threats T5 and T6 in the [threat model](../docs/THREAT_MODEL.md)):

- **Bus policy** (`modules/eventbridge`, input `publisher_role_arns`): an explicit `Deny` on `events:PutEvents` for every principal except the `CreateOrder` role. It binds administrators too, so test events must go through the application.
- **Order check** (`FulfilOrder`): each event is compared with the stored order, read with a consistent `Query` on the Orders table. The customer, the item IDs and the quantities must all match, otherwise the message fails, is retried and dead-lettered.

The check needs `dynamodb:Query` on the Orders table. That permission has to be in the Lambda role **and** in the permissions boundary, which lives in `bootstrap`. When rolling this out, apply `terraform/bootstrap` **before** the pipeline deploys the role change; otherwise the boundary blocks the new permission and every message fails and ends up in the dead-letter queue until the boundary is updated.

## Order notifications

The `order-created-to-notification-<env>` rule matches the same `OrderCreated` events and publishes a short message (`Order <orderId> was created at <createdAt>.`) to the `order-notifications-<env>` SNS topic. It carries no customer data. Only that rule can publish to the topic, and only over TLS. Set `notification_email` to subscribe an address, then click the confirmation link AWS sends. Additional subscribers (SQS, Lambda, HTTPS) can be attached to the topic later without changing the rule.

The topic is not KMS-encrypted, because EventBridge can only publish to an encrypted topic that uses a customer-managed key.

## Dashboard

The `orders-<env>` CloudWatch dashboard has three widgets, one per metric, all `Sum` over 1-minute periods:

| Widget | Metric | Series |
|---|---|---|
| EventBridge MatchedEvents | `AWS/Events` `MatchedEvents` | fulfilment rule, notification rule |
| SQS NumberOfMessagesSent | `AWS/SQS` `NumberOfMessagesSent` | main queue, dead-letter queue |
| SNS NumberOfMessagesPublished | `AWS/SNS` `NumberOfMessagesPublished` | notifications topic, alerts topic |

The `dashboard_url` output links straight to it. Metrics appear only after the first events, so widgets are empty until you create an order.

## Email variables and plans

Passing `-var notification_email=...` only affects that one run. A later plain `terraform plan` would see the variable as empty and propose deleting the email subscriptions. To keep them, put the values in a git-ignored `emails.auto.tfvars` in the environment folder (`*.auto.tfvars` is in `.gitignore`):

```hcl
notification_email = "you@example.com"
alert_email        = "you@example.com"
```

## OrderCreated event

`CreateOrder` saves the order to DynamoDB (one item per order line, written in a transaction) and then publishes:

```json
{
  "source": "orders.create-order",
  "detail-type": "OrderCreated",
  "detail": { "orderId": "...", "customerId": "...", "items": [], "createdAt": "..." }
}
```

Subscribe with an EventBridge rule on the bus matching `detail-type = OrderCreated`. Test with:

```bash
aws lambda invoke --function-name CreateOrder-dev \
  --cli-binary-format raw-in-base64-out \
  --payload '{"customerId":"c1","items":[{"itemId":"i1","qty":1}]}' out.json
```
