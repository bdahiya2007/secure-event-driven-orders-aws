# FAQ

Questions about the design, the behaviour and the operation of this project, grouped by topic, with short answers and where to read more. Everything described here was built and checked in `dev` (one account, one region, one user) unless an answer says otherwise; "not tested" means exactly that.

**Topics:** [Design and flow](#design-and-flow) | [The order API](#the-order-api) | [Idempotency](#idempotency) | [EventBridge](#eventbridge) | [SQS and fulfilment](#sqs-and-fulfilment) | [SNS and notifications](#sns-and-notifications) | [DynamoDB](#dynamodb) | [Lambda and Python](#lambda-and-python) | [Security and permissions](#security-and-permissions) | [Cost](#cost) | [Terraform and state](#terraform-and-state) | [CI/CD](#cicd) | [Operating and testing](#operating-and-testing)

---

## Design and flow

### What happens when an order is created?
A signed `POST /orders` reaches API Gateway, which validates the body against a JSON schema, writes one message to the intake SQS queue and answers `202 Accepted` with the new `orderId` straight away. `CreateOrder` then consumes the queue: it validates the message again, saves one DynamoDB item per order line in a single transaction, and only then publishes an `OrderCreated` event to the `orders-<env>` bus. Two rules on that bus work independently: one feeds the fulfilment queue, the other publishes a notification. See the diagrams in the [README](../README.md#architecture).

### Why Terraform and not a SAM template?
Terraform was the requirement from the start, so the whole project, including the Lambdas, is in Terraform. There is no `template.yaml`. The same resources could be written as SAM; nothing in the design depends on the tool.

### Why save the order first and publish the event second?
So a failed save never produces an event for an order that does not exist. A save error returns `400` before any publish is attempted.

### What if the save works but the publish fails?
The message goes back to the intake queue. On the retry `CreateOrder` finds the order already saved, confirms the stored order is the same one (same customer, lines and quantities), and publishes again, so a saved order is not left unannounced. Only if it fails three times does the message land in the intake dead-letter queue, where the alarm reports it; the order is then saved but unannounced until the message is redriven (redrive is untried). Before the API and queue were added, this case returned a `500` and was never retried.

### Is anything exactly-once?
No. EventBridge, SQS and SNS all deliver **at least once**. Fulfilment is safe against repeats because its write is conditional on the `orderId`. The notification path does not deduplicate, so a rare repeat could send two identical emails. `CreateOrder` is idempotent per `orderId` (see [Idempotency](#idempotency)), so a redelivered or concurrent copy of one message creates one order and publishes one event, except in one narrow window described there. The caller cannot supply its own idempotency key, so a client that retries the whole HTTP request still creates a second order.

### Why EventBridge, then SQS, rather than calling the fulfilment Lambda directly?
The bus lets more consumers subscribe later without changing `CreateOrder` (the notification rule was added that way). The queue in front of `FulfilOrder` absorbs bursts, retries failures and keeps unprocessable messages in a dead-letter queue instead of losing them.

### Why two rules instead of one?
Fulfilment and notification are independent: a failure in one does not affect the other or the caller. Each rule has its own target, retry policy and permissions.

### What does the event look like?
Source `orders.create-order`, detail-type `OrderCreated`, and a detail of `orderId`, `customerId`, `items` (each `itemId` and `quantity`) and `createdAt`. It carries only validated fields, never the raw request.

---

## The order API

### How do I call the API?
`POST` JSON to the `order_api_url` output. The request must be signed with AWS credentials (SigV4) for a principal allowed `execute-api:Invoke`; curl 7.75 or newer can sign it with `--aws-sigv4`. The exact command is under "Try it" in the [README](../README.md#try-it).

### Why does it answer `202` and not `201`?
Because the order is accepted, not created. API Gateway has only queued it; saving it and publishing the event happen afterwards, so the caller never waits for DynamoDB or EventBridge. `201` would claim a result that does not exist yet.

### How does a caller find out that an order failed?
Today only through the alarm: a message that fails three times ends in the intake dead-letter queue and the alarm emails the alerts topic. The API gives no way to read an order's status, which is a known limitation. A status endpoint would be the next step.

### My order returned `202` but never appeared. Why?
`202` only means accepted. The order is validated again by `CreateOrder`, and an order it rejects is retried three times and then moved to the intake dead-letter queue without being saved. A repeated `itemId` used to be the known case; the API now rejects it with `400`, so what remains is anything `CreateOrder` refuses that the API's schema allowed. To find out, look at the intake dead-letter queue (`order-intake-<env>-dlq`) and the alarm email, and read the `CreateOrder` log for `Failed to process message`, which gives the message ID and the reason but never the order contents. An order status endpoint is on the to-do list.

### Why is the order ID the request ID?
So the caller has it in the `202` response without waiting, and so every retry of one queue message refers to the same order. A client that retries the whole HTTP request still creates a second order, because there is no idempotency key yet.

### Why AWS IAM signing and not an API key or Cognito?
It needs no extra service and keeps the endpoint closed to anyone without credentials, which suits service-to-service callers. An API key is not authentication. A Cognito or JWT authorizer would suit end users and is listed as an open gap (T1).

### What does the API reject before anything is queued?
An unsigned request (`403`), a body that does not match the schema or repeats an `itemId` (`400`: only `customerId`, `items`, `itemId` and `quantity`, with length and range limits, nothing else, and each `itemId` at most once), a content type other than JSON (`415`), and requests over the throttle (`429`). The `400` and `500` messages are fixed and never echo what the caller sent. All four were checked live on 2026-10-17, along with the happy path: a signed order returned `202`, was saved, fulfilled and announced within seconds, and the dead-letter queue and alarms stayed clean.

### Why can I not send a test message to the intake queue myself?
The queue policy denies `sqs:SendMessage` to everyone except the API's role, administrators included, for the same reason as the bus policy: the queue is a trust boundary. To test, call the API. `CreateOrder` also re-validates every message, so a forged one could not store extra data.

## Idempotency

### How does `CreateOrder` avoid creating an order twice?
Each `orderId` has one record in the `Orders-processing-<env>` table. Before doing anything, `CreateOrder` **claims** the order with a single conditional write: it succeeds only if there is no record yet, or the previous holder's lease has run out for the same order. The claim is the check and the lock in one step, so two copies of a message cannot both pass it. After the claim it saves the order lines (also a conditional write), publishes the `OrderCreated` event once, and records the result. The next copy of that message finds the record `COMPLETED` and returns the stored result without saving or publishing anything.

### What if two copies of one message arrive at the same time?
One wins the claim. The other finds the record `IN_PROGRESS` with a valid lease, does nothing, and reports the message as failed so SQS delivers it again after the visibility timeout (180 seconds). By then the first copy has finished, so the retry finds `COMPLETED` and returns the stored result. This is covered by a race test that delivers a second copy in the middle of the first. Live, twelve simultaneous deliveries of one message created the order exactly once, but the deliveries were not close enough to hit the "lease held" branch, so that branch is verified by the test rather than in AWS.

### What happens if `CreateOrder` crashes halfway?
The lease (60 seconds) simply runs out. The next delivery takes it over, finds the order lines already saved and checks that they match the message, publishes the event if it was not published, and records the result. Nothing is lost and a saved order is never left unannounced.

### Can the event still be published twice?
In one narrow window, yes: the event goes out and then writing the result fails, for example because DynamoDB throttles. The retry cannot know the event went out, so it publishes again. This is the usual limit of at-least-once delivery; an exactly-once publish would need the event bus to deduplicate, and it cannot. The consumers tolerate it: `FulfilOrder` is idempotent, and the notification email could repeat in that window. Outside that window, redeliveries repeat nothing.

### What does "the existing result" mean?
The record stores the outcome of the first run, `{orderId, status: CREATED, createdAt, lines}`. A duplicate returns that same result and logs `Order already processed, nothing repeated`. SQS does not pass a Lambda's return value back to the producer, so the result is visible in the record and in the log rather than in an HTTP response.

### What if an `orderId` is reused for a different order?
The record keeps a fingerprint (a hash) of the customer and the lines. A message with the same `orderId` but different content is rejected, saved nowhere and published nowhere, and it ends in the dead-letter queue with an alarm. A `COMPLETED` record is never overwritten.

### How long are the records kept, and why a separate table?
Seven days, then DynamoDB's time-to-live removes them. That is longer than the four days SQS keeps a message, so a late redelivery is still recognised. The state sits in its own table so the order table keeps one clean item per order line, and so the claim and the result can be rewritten without touching order data. The table is named `Orders-processing-<env>` on purpose: the existing least-privilege patterns (`Orders-*`) already cover it, so the permissions boundary and the deploy role did not need to change.

## EventBridge

### What happens to an event if no rule matches it?
It is dropped. The bus has no archive, so there is no replay. Replay is on the to-do list.

### How do I know the rules are matching?
Open the dashboard (the `dashboard_url` output) and look at **MatchedEvents**, or read the `MatchedEvents` and `Invocations` metrics for the rule. `FailedInvocations` should stay empty. See [Dashboard](../terraform/README.md#dashboard).

### Why is the notification message plain text, not JSON?
The rule has an **input transformer** that sends `Order <orderId> was created at <createdAt>.` instead of the full event. The fulfilment rule sends the whole event, because `FulfilOrder` needs it.

---

## SQS and fulfilment

### What does FulfilOrder do?
It reads the queue in batches of up to 10, re-validates every message, and writes one record per order to `Fulfilments-<env>` with `status` `FULFILLED`, `itemCount`, `totalQuantity`, `eventId`, `orderCreatedAt` and `fulfilledAt`. That is all it does today: there is no inventory or shipping logic yet.

### What happens when processing fails?
On both queues (the intake queue feeding `CreateOrder` and the fulfilment queue feeding `FulfilOrder`) only the failed messages in a batch go back to the queue (`ReportBatchItemFailures`). A message gets **three processing attempts**, 180 seconds apart (the visibility timeout). When SQS would deliver it a fourth time it moves it to the queue's dead-letter queue instead (`maxReceiveCount = 3`), where it is kept for 14 days. A CloudWatch alarm per dead-letter queue fires when it is not empty. Nothing is retried forever and nothing is lost.

Both paths were tested live on 2026-10-17: on the fulfilment queue with an unparseable message and a well-formed forged one, and on the intake queue with an order that repeats an `itemId` (accepted by the API, rejected three times by `CreateOrder`, then dead-lettered with a receive count of 4). The alarm went to `ALARM` about 11 minutes after each message was sent. The test is in [terraform/README.md](../terraform/README.md#asynchronous-fulfilment).

### How can I get messages out of the dead-letter queue?
Read them with the CLI, fix the cause, then redrive them to the source queue (SQS offers a redrive task in the console and CLI). Redrive has **not been tried** in this project.

### Why a visibility timeout of 180 seconds and a concurrency cap of 5?
The function timeout is 30 seconds and the queue's visibility timeout is 6 times that, as AWS recommends, so a message is not redelivered while still being processed. Capping concurrency at 5 stops a burst of orders from running many Lambdas and flooding DynamoDB.

### Why can I not put a test event on the bus myself?
The bus policy denies `events:PutEvents` to every principal except the `CreateOrder` role, administrators included. Rules match only on `source` and `detail-type`, which any caller of `PutEvents` can set, so without the policy anyone with that permission could forge an `OrderCreated` event. To test, invoke `CreateOrder`. As a second layer, `FulfilOrder` rejects any event that does not match a stored order, so even a message sent straight to the queue cannot create a fulfilment. Both were checked live: a hand-made `PutEvents` from an administrator identity was refused, and a well-formed forged message sent straight to the queue created no fulfilment record, was dead-lettered and fired the alarm.

### Why is a repeated message not a problem?
The `Fulfilments` write has `attribute_not_exists(orderId)`. A second delivery fails that condition, is logged as already fulfilled, and counts as success, so no duplicate record and no retry loop.

---

## SNS and notifications

### Why did I not get an email after applying?
SNS email subscriptions stay `PendingConfirmation` until you click the link AWS emails. Check spam, and note there are **two topics, so two confirmation emails**: one for `order-notifications-<env>` and one for `orders-alerts-<env>`. Confirming the alerts one does not confirm the other.

### Why did I get two emails for one test?
Check how many orders you created: each order sends one email. When this came up, two orders had been created 20 seconds apart, and the rule matched 2 events and the topic published 2 messages to one subscription. See [Operating and testing](#operating-and-testing).

### What is the difference between the two topics?
`order-notifications-<env>` carries order messages. `orders-alerts-<env>` carries only CloudWatch alarm state changes (dead-letter queue not empty). You can subscribe different people to each.

### Does the notification contain customer data?
No: only the `orderId` and the creation time.

### Why are the topics not encrypted with KMS?
EventBridge and CloudWatch alarms can only publish to an encrypted topic that uses a **customer-managed** key, not the AWS-managed `aws/sns` key. The topics hold no sensitive data, so they are left unencrypted for now. A customer-managed key is on the to-do list.

---

## DynamoDB

### Why `orderId` and `itemId` as the keys?
One item per order line: `orderId` is the partition key and `itemId` the sort key, so all lines of an order are read with one query, and a duplicate `itemId` within an order is rejected. The transaction holds up to 100 lines. `Fulfilments` has only `orderId`, because there is one record per order.

### Why on-demand capacity?
There is nothing to size, nothing is billed when idle, and the traffic is small and unpredictable. At steady millions of requests a day, provisioned capacity with autoscaling could be cheaper.

### Which encryption key do the tables use?
The AWS-managed `aws/dynamodb` key. A customer-managed key can be set through the module's `kms_key_arn` input; it is not used yet.

### Why is deletion protection on in prod and off in dev?
So prod cannot be dropped by a stray `terraform destroy`, while dev can be torn down freely. Prod's tables cannot be destroyed until protection is turned off first.

### Is the data kept if I destroy the environment?
No. Destroying a table deletes its data, and point-in-time recovery backups are deleted with it. Take an on-demand backup first if you need the data.

---

## Lambda and Python

### Why Python 3.14, and why AWS provider 6.x?
3.14 is a current Lambda runtime. AWS provider 5.x rejects the `python3.14` runtime name, so the project requires provider `~> 6.0`.

### Why are there no packaged libraries?
The functions use only the standard library and `boto3`, which the Lambda runtime provides and AWS keeps patched. There is nothing to package or pin. If packages are added later, pin them and run `pip-audit`.

### What does the input allow-list do?
`CreateOrder` accepts only `customerId`, `itemId` and `quantity`, with type and length checks (IDs up to 64 characters, quantity 1 to 1000, up to 100 lines, body up to 256 KB). Unknown fields are dropped, so a caller cannot store arbitrary data or override the server-set `orderId` and `createdAt`. There is deliberately no price field: prices should come from a catalog, not the client.

### Why does the response show `statusCode` 201 inside a `StatusCode` 200?
The CLI's `StatusCode 200` says the Lambda invocation ran. The `statusCode 201` in the payload is the function's own answer, shaped like an API Gateway response so API Gateway can sit in front of it later.

### What is out.json?
The file `aws lambda invoke` writes the function's response to. It is git-ignored and can be deleted.

---

## Security and permissions

### Why did the first apply fail with AccessDenied on `iam:CreateRole`?
The **PowerUserAccess** permission set deliberately excludes IAM, so it cannot create the Lambda execution roles. The fix is an inline policy on the permission set that allows role actions only on `CreateOrder-*` and `FulfilOrder-*`, plus `iam:PassRole` to Lambda only. The ready-made JSON is in [terraform/iam/deploy-iam-policy.json](../terraform/iam/deploy-iam-policy.json). After changing it, log in again with `aws sso login`.

### What can each function do?
- `CreateOrder`: write its own logs, `dynamodb:PutItem` on the `Orders` table, `events:PutEvents` on the one bus.
- `FulfilOrder`: write its own logs, read and delete from the one queue, `dynamodb:PutItem` on the `Fulfilments` table.

Nothing uses `*` as a resource or a wildcard action.

### Who can send to the queue and the notification topic?
Only the matching EventBridge rule, enforced with an `aws:SourceArn` condition. Both queues and both topics also deny requests that do not use TLS.

### What is not secured yet?
No customer-managed keys, no API in front of `CreateOrder` (so no authentication), state kept in a shared bucket, and no static scanning in CI. See the known gaps in the [README](../README.md#security-design).

---

## Cost

### What does it cost?
Almost nothing at this scale: roughly $0 when idle and under $1 a month for demo traffic, because everything is pay-per-use. About 100,000 orders a month is roughly $1 to $3, mostly DynamoDB writes, EventBridge events and email delivery. Prices are us-east-1 list prices from memory, **not checked against the pricing pages**.

### What is the biggest cost per order?
Email delivery: after the first 1,000 a month it is about $2 per 100,000. At large volumes use a batched or non-email subscriber.

### What bills while nothing is happening?
The Lambda's queue polling uses some SQS requests (inside the free million), a dashboard past the third free one costs $3 a month, and storage and point-in-time recovery are well under a cent. If you do not need the project, `terraform destroy` in the environment removes it.

---

## Terraform and state

### Where is the state?
In S3, with native locking (`use_lockfile`, Terraform 1.10 or newer), one key per environment, in the bucket created by the `secure-serverless-api-aws` project. The bucket name comes from the git-ignored `backend.hcl`, so every `init` needs `-backend-config=../../backend.hcl`. See [Remote state](../terraform/README.md#remote-state).

### Could a destroy in the other project delete the state bucket?
No. The bucket is in that project's `bootstrap` configuration with `prevent_destroy = true` and no `force_destroy`, and S3 will not delete a non-empty bucket. State could still be lost by deleting its contents by hand. See [Destroy safety](../terraform/README.md#destroy-safety).

### Why did a plain plan want to delete my email subscriptions?
The emails were passed with `-var` on one run, so the next plain `plan` saw them as empty and proposed removing the subscriptions (you would have had to confirm them again). Keep them in a git-ignored `emails.auto.tfvars` in the environment folder. See [Email variables and plans](../terraform/README.md#email-variables-and-plans).

### What does `-migrate-state` do?
It copies the local state file into the new S3 backend the first time you switch. Afterwards `terraform plan` should say **No changes**, and the leftover local `terraform.tfstate*` files can be deleted. Prod has no local state, so it does not need the flag.

---

## CI/CD

### What do the workflows do?
`validate.yml` runs offline on pull requests (format, validate, handler compile check, unit tests, `bandit`, `checkov` and the secret scan). `deploy.yml` runs on pushes to `main`: checks, `plan`, a guard against deletions, then `apply` of that saved plan. Details in [terraform/README.md](../terraform/README.md#cicd-github-actions). Both run on GitHub Actions, and the five pull-request checks are required before anything can merge into `main`.

### Where is the SAM `package` and `update-function-code` step?
There is none, because the project is Terraform. The `archive_file` data sources zip each handler during `plan`, and `apply` updates a Lambda when its code hash changes.

### How does the workflow get AWS access, and are there stored keys?
Through GitHub OIDC: the job asks GitHub for a short-lived token and exchanges it for temporary credentials for `orders-pipeline-deploy-role`. No AWS access keys are stored anywhere. The role trusts only this repository (pinned by its immutable owner and repository IDs) and only jobs running in the `dev` or `prod` environment, so pull requests and forks cannot assume it. The setup is a one-time manual bootstrap; see [terraform/README.md](../terraform/README.md#cicd-github-actions).

### What is the permissions boundary for?
The deploy role must be able to create the two Lambda execution roles, which in principle lets it grant them any permission. The boundary `orders-pipeline-app-role-boundary` is a ceiling: the deploy role can only create or change those roles while the boundary is attached, cannot remove it, and cannot modify itself. Even an over-broad policy on a Lambda role then yields no more than logs, `PutEvents` on the bus, `PutItem` on the two tables and consuming the fulfilment queue.

### Why is the bootstrap applied by hand?
So the pipeline cannot change its own permissions. A compromised workflow can deploy the stack but cannot widen the role that deploys it. It also has to exist before the first pipeline run.

### What do the unit tests cover, and are they trustworthy?
Both Lambdas have standard-library tests (`python3 -m unittest discover -s tests`) that replace `boto3` with stand-ins, so they need no AWS access or installs. They cover validation limits, the save-before-publish order, hostile input being dropped, the failure paths and the batch handling in `FulfilOrder`. They were checked by seeding seven deliberate bugs (for example ignoring a failed publish, allowing a boolean quantity, dropping the duplicate-order condition); every one made a test fail.

### Why are some checkov findings skipped?
The first run reported 19 findings across nine rules. Each skip in `.checkov.yaml` has a reason: for example unencrypted SNS topics (EventBridge and CloudWatch need a customer-managed key, which is on the to-do list), no VPC (a NAT gateway would cost money for no benefit), and no code signing. Any new finding fails the pull request check.

### Why is there no `pip-audit` step?
The Lambdas use only the standard library and the `boto3` that the runtime provides, so there is no pinned dependency list to audit. Add it when a `requirements.txt` exists.

### Why can I not push to `main`?
Branch protection requires every change to go through a pull request with five passing checks (unit tests, `bandit`, `checkov`, the secret scan and the Terraform validation), with the branch up to date, and it includes administrators, so it binds you too. A direct push is refused with `GH006`. Work on a branch, push it, open a pull request and merge once the checks pass. If a pull request cannot be merged, check that the required check names match the jobs that actually ran (`gh pr checks <number>`) and that the PR is not still a draft.

### What protects the repository now that it is public?
`main` is protected as above. The `dev` and `prod` environments accept only protected branches, and `prod` needs the owner's approval. Secret scanning with push protection, Dependabot alerts and private vulnerability reporting are on, the Actions token is read-only, workflows from first-time contributors need approval, actions must be pinned to a full commit SHA, and merged branches are deleted automatically. Vulnerabilities are reported privately, as described in [SECURITY.md](../SECURITY.md).

### Why did Dependabot open a pull request?
Every action in the workflows is pinned to a full commit SHA, so it does not update by itself. Once a week Dependabot opens one grouped pull request that bumps the pinned SHAs and their version comments. The five checks run on it like on any other change; read what changed in each action, then merge it.

### Why did a workflow fail because of an unpinned action?
The repository requires actions to be pinned to a full-length commit SHA. A `uses:` line with a tag or branch (for example `@v4`) is refused. Find the commit with `gh api repos/<owner>/<action>/commits/<tag> --jq .sha` and write it as `owner/action@<sha> # <tag>`.

### Why did a deploy stop with "the plan would delete or replace resources"?
The guard refuses automatic deletions, because a replaced DynamoDB table loses its data. The most common cause is a missing repository secret: an empty `NOTIFICATION_EMAIL` or `ALERT_EMAIL` would delete that subscription. Set the secret and re-run, or run manually with `allow_destroy` if the deletion is intended. The one exception built into the guard is an API Gateway *deployment*: it is a stateless snapshot of the API definition that is replaced whenever the API changes, so its replacement is allowed.

### Can a pull request deploy or read my secrets?
No. `validate.yml` uses no credentials, and `deploy.yml` never runs on pull requests. A fork's pull request cannot reach the secrets.

---

## Operating and testing

### How do I test the whole flow?
Run the `CreateOrder` invoke command from the [README quick start](../README.md#try-it), then read the new `orderId` from `Fulfilments-<env>` and check your inbox. A normal order shows `FULFILLED` within about a second.

### The dashboard widgets are empty. Is something broken?
Usually not. Metrics appear only after events flow and can take a minute or two to show, and a narrow time range at the top right can miss them. Create a test order and widen the range.

### Which dimensions do the dashboard metrics use?
`MatchedEvents` is published under `EventBusName` plus `RuleName`, SQS under `QueueName` and SNS under `TopicName`; the dashboard uses those. If you add a metric, check its dimensions with `aws cloudwatch list-metrics` first.

### Why did `terraform validate` fail with "no space on device"?
Each scratch copy of an environment downloads a large provider (hundreds of MB) into `/tmp`, which is a small in-memory filesystem. Delete scratch copies, or validate in place.

### How do I remove everything?
`terraform destroy` in `terraform/environments/dev`. Data in the tables is lost, the state key stays in the shared bucket as an empty state, and re-applying later sends fresh subscription confirmation emails. Prod needs deletion protection turned off first.
