# Disaster recovery

How the optional multi-region deployment behaves when a Region is impaired, what it can and cannot promise, and how to rehearse it. It describes the `enable_multi_region` switch (off by default) with `us-east-1` as the primary Region and `us-west-2` as the secondary.

**Status:** built and rehearsed in `dev` on 2026-10-18 (UTC). It has never faced a real outage, and `prod` has not been applied. "Observed" below means measured in that rehearsal; "expected" means it follows from the design or the code but was not exercised.

**Topics:** [What is built](#what-is-built) | [Objectives](#objectives-what-to-expect) | [Failure scenarios](#failure-scenarios) | [Manual failover and failback](#manual-failover-and-failback) | [Testing it](#testing-it) | [Known gaps](#known-gaps-and-what-to-do-before-relying-on-this)

---

## What is built

| In each Region | In both, shared |
|---|---|
| An API Gateway (`POST /orders`, signed requests, schema validation, throttling) | Three DynamoDB **global tables**: `Orders`, `Fulfilments`, `Orders-processing` |
| An intake queue and its dead-letter queue, with an alarm | An EventBridge **global endpoint**, with replication **disabled** |
| `CreateOrder` (consumes the intake queue, publishes the event) | A Route 53 **health check** that follows a CloudWatch alarm on EventBridge's `IngestionToInvocationStartLatency` |
| An event bus named `orders-<env>` (the same name in both Regions) with its rules | |
| A fulfilment queue, its dead-letter queue and alarm, and `FulfilOrder` | |
| Notification and alerts SNS topics | |

**Normal operation.** Clients can use either API. Each Region's `CreateOrder` saves to its local replica and publishes through the global endpoint, which routes every event to the **primary** bus while the health check is healthy. So the rules, fulfilment queue, `FulfilOrder` and notifications run in the primary Region. Intake is active-active; event processing is active in one Region at a time.

**After a failover.** The endpoint routes new events to the **secondary** bus, and the secondary rules, queue, `FulfilOrder` and SNS topic process them.

**Not built:** DNS routing for the API (clients choose a URL), replication of events, a dashboard for the secondary Region, a pipeline role for the second Region, and any automation that fails clients over. See the [README](../README.md#multi-region-optional) for the diagram and [terraform/README.md](../terraform/README.md#multi-region-optional) for setup.

## Objectives (what to expect)

These are what was observed or follows from the design, not guarantees.

| Objective | Expectation |
|---|---|
| **Detecting a primary failure** | The alarm needs five one-minute readings of average ingestion latency above 30 seconds before the health check turns unhealthy, so **at least five minutes** of a real problem. Rehearsals skip this by inverting the health check. |
| **Routing events to the secondary** | **Observed:** after the health check was made unhealthy, new events were being processed in the secondary within **7 minutes 40 seconds** (an upper bound; probes were sparse). A real failover is therefore about the detection time plus this. |
| **Routing back (fail-back)** | **Observed:** within **97 seconds** of restoring the health check. |
| **Orders (data)** | Writes are saved in the Region that took the request and replicate to the other, typically in about a second (informal observation, not a measured guarantee). A Region lost with unreplicated writes would lose those seconds. |
| **Events** | Events already in the primary bus when it fails are **not** copied to the secondary (replication is off). Those orders exist in the tables but have no fulfilment or notification until something reprocesses them; there is no replay tool. |
| **Accepted but unsaved orders** | An order the primary API accepted (`202`) but `CreateOrder` has not yet saved sits in the primary intake queue. If the Region is down, it stays there until the Region returns. |
| **Client intake** | The secondary API is always live, but nothing moves clients to it automatically. How fast intake recovers depends on how fast callers switch URL. |

## Failure scenarios

| Scenario | What happens | What you do |
|---|---|---|
| **EventBridge in the primary is slow or impaired** (the signal the health check watches) | After the detection time, the health check turns unhealthy and events go to the secondary. Both Regions' `CreateOrder` keep publishing through the endpoint without any change. | Nothing. Check both Regions' dead-letter queues and alarms afterwards. |
| **The whole primary Region is down** | The primary API is unreachable, so clients must use the secondary API. Until the endpoint fails over, `PutEvents` fails and `CreateOrder` returns the message to its queue (expected, from the code) and retries. Messages stuck in the primary intake queue wait for the Region. | Point callers at `order_api_url_secondary`. When the Region returns, queued messages process once: the processing records stop duplicates. |
| **Only Lambda, API Gateway or DynamoDB in the primary fail** (EventBridge healthy) | **No automatic failover.** The health check does not see these failures, so events keep going to the primary bus. | Use the secondary API, and force the failover by hand (below) if the primary's fulfilment is affected. |
| **The secondary Region is down, the primary is healthy** | Nothing changes for the primary. The secondary API is unavailable. Table replication to it resumes when it returns. | Nothing. |
| **A replica lags** | `FulfilOrder` checks each event against the order table. An event for an order that has not yet replicated fails the check, is retried after 180 seconds, and succeeds. If the lag is longer than three attempts, the message reaches the dead-letter queue and the alarm fires. | Redrive the message once the table is consistent (redrive is untried). |
| **A false alarm or flapping health check** | Events alternate between the two buses. Each is processed in the Region that received it; the global tables keep the data consistent, and each Region's idempotency records stop duplicates within that Region. | Investigate the latency alarm. |
| **The same order reaches both Regions** | Conditional writes are atomic within one Region only; across Regions, last writer wins. The API makes this unlikely (an order ID is a unique request ID). | None expected. |

## Manual failover and failback

A health check can be forced by inverting it. This is also how to rehearse, or to fail over deliberately (for example, before planned maintenance in the primary).

```bash
cd terraform/environments/dev
HC=$(terraform output -raw global_endpoint_health_check_id)

# Fail over: Route 53 now reports the primary unhealthy
aws route53 update-health-check --health-check-id "$HC" --inverted

# Check where events are processed: FulfilOrder-dev (us-east-1) or FulfilOrder-uswest2-dev (us-west-2)
aws logs filter-log-events --region us-west-2 --log-group-name /aws/lambda/FulfilOrder-uswest2-dev \
  --start-time $(( $(date -u +%s) - 300 ))000 --filter-pattern '"Order fulfilled"'

# Fail back
aws route53 update-health-check --health-check-id "$HC" --no-inverted
```

- **Always set it back.** While it is inverted, Terraform shows the health check as changed.
- **Do not try to trigger a failover by setting the alarm to `ALARM`.** Route 53 reads the metric, not the alarm state, so nothing happens (tried and confirmed).
- **`get-health-check-status` is not a reliable indicator.** It keeps reporting the raw metric check and ignores the inversion. Watch where events land.
- **Expect minutes, not seconds, in both directions.** Orders placed in between may be processed in either Region.
- **After fail-back**, check both Regions' dead-letter queues and alarms, and confirm new orders are processed in the primary.

## Testing it

**Rehearsal on 2026-10-18 UTC (`dev`):**

| Check | Result |
|---|---|
| Both APIs, signed for their own Region | `202`, saved locally, replicated to the other Region, fulfilled |
| `CreateOrder` in both Regions publishing through the global endpoint | No errors; signing (SigV4A) works with the vendored `awscrt` |
| Health check inverted at 06:37:41 UTC | Events processed in `us-west-2` by 06:45:21 (within 7 min 40 s). That Region's rule matched the event, its SNS topic published, its `FulfilOrder` wrote the record, and the record was readable from `us-east-1` |
| Health check restored at 06:45:18 | An order 3 seconds later still went to `us-west-2`; one 97 seconds later went to `us-east-1` |
| Afterwards | Dead-letter queues empty, all alarms `OK`, no Terraform drift |
| Switching the whole option off (`enable_multi_region=false`) | First attempt failed: DynamoDB refused to disable the table streams while a replica existed. After keeping the streams on, the apply removed the secondary stack, the replicas and the health check; `terraform plan` showed no changes and the next pipeline deploy passed |

**Mistakes made, so you do not repeat them:** the first rehearsal forced the alarm to `ALARM` and placed 14 orders that all stayed in the primary, because the health check follows the metric. The second script waited for the status text to say "Failure", which never appears under inversion, so it placed no orders during the window; the failover was seen only in the order placed after restoring.

**Costs of testing:** every probe order sends a notification email, so keep probes sparse. A more precise measurement would probe about once a minute and accept the emails.

**Automated tests:** the endpoint publishing path (with and without an endpoint, and a clear failure when the signing library is missing) and the script that vendors `awscrt` (hash check, unsafe paths, reproducible output) are unit-tested. Not tested at all: a real Region failure.

**Suggested drill:** repeat the rehearsal before relying on this, in `prod` as well, and after any change to the endpoint, the tables or the secondary stack.

## Known gaps and what to do before relying on this

| Gap | Effect | Next step |
|---|---|---|
| **The health signal is narrow** | Only EventBridge ingestion latency triggers a failover. A failure in API Gateway, Lambda or DynamoDB alone does not. | Add health signals for those, or fail over by hand |
| **No DNS routing for the API** | Clients pick a URL and must switch themselves | A custom domain with Route 53 latency or failover routing |
| **Events in flight are lost to the secondary** | Orders saved but not yet announced when the primary fails get no fulfilment or notification | A replay tool, or replication with the secondary rules paused |
| **Messages stuck in a failed Region's queues** | Accepted orders wait until the Region returns | Accept it, or add a cross-Region intake path |
| **No real outage has been tested** | The observed times come from an inverted health check, not a failure | A game day |
| **Replication is last-writer-wins** | Conditional writes are atomic per Region only | Keep order IDs unique per request, as now |
| **The pipeline cannot run while the secondary exists** | Terraform changes need the switch turned off first (verified: switching off works and the pipeline passes afterwards) | A pipeline role and job for the second Region |
| **A pinned third-party library** | `awscrt` is the one third-party package in a Lambda | Upgrade it deliberately; Dependabot does not watch it |
| **`prod` not applied** | The same code, never run there | Apply and rehearse before depending on it |

**Before relying on this:** rehearse in `prod`, decide how clients will switch, decide what to do with events in flight, add or accept the narrow health signal, and write down who is allowed to invert the health check.
