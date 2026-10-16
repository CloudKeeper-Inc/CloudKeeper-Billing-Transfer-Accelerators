# Output

## Overview

This page describes everything the CloudTrail Accelerator prints and writes: the console output of a run, the summary table, the JSON report, the state of the trail in every account, and the output of `--check` and `--dry-run`.

The examples use a sample organization: management account `100000000000`, member accounts `2000000000xx`. Your account IDs, names and run IDs will differ. Every console line starts with the seconds elapsed since the start, for example `[  42.7s]`; that prefix is left out below. Errors that stop the script go to stderr and start with `ERROR:`.

- [A Run](#a-run)
- [The Summary Table](#the-summary-table)
- [The JSON Report](#the-json-report)
- [The State of the Trail in Each Account](#the-state-of-the-trail-in-each-account)
- [Check Mode](#check-mode)
- [Dry-Run Mode](#dry-run-mode)
- [Where the Logs Land](#where-the-logs-land)

## A Run

A run in an organization with four member accounts: one suspended, and one whose SCP denies CloudTrail.

```text
CloudTrail Accelerator 1.0.0
Caller             : arn:aws:sts::100000000000:assumed-role/Admin/tester (profile org-management-admin)
Management account : 100000000000 management (org o-fake, root r-root, partition aws)
Log account        : 100000000000 management (management account)
Accounts           : 4 active (3 members), 1 skipped
  skipped          : 200000000003 member-03 (SUSPENDED)
Bucket             : s3://cloudkeeper-cloudtrail-accelerator-100000000000-us-east-1 (us-east-1)
Trail              : cloudkeeper-cloudtrail-accelerator (multi-region, management events read+write, log file validation, no KMS, no CloudWatch Logs)
Accounts to set up : management account + 3 member account(s)
StackSet / role    : cloudkeeper-cloudtrail-accelerator-20261013133041 / cloudkeeper-cloudtrail-accelerator-20261013133041 (temporary)
Created bucket s3://cloudkeeper-cloudtrail-accelerator-100000000000-us-east-1 in us-east-1 (SSE-S3, public access blocked, ACLs disabled)
Bucket policy allows CloudTrail delivery from 4 accounts
  100000000000: trail created, logging=yes (management account)
Deploying StackSet cloudkeeper-cloudtrail-accelerator-20261013133041 to 3 account(s) at max concurrency
  200000000000: trail created, logging=yes
  200000000001: trail created, logging=yes
StackSet rollout finished
  200000000002: FAILED - CreateTrail failed: AccessDeniedException: User: arn:aws:sts::200000000002:assumed-role/... is not authorized ...
Removed 1 account(s) without a trail from the bucket policy
Tearing down StackSet cloudkeeper-cloudtrail-accelerator-20261013133041 (stack instances first, at max concurrency)
Stack instance deletion finished: SUCCEEDED
Deleted StackSet cloudkeeper-cloudtrail-accelerator-20261013133041

  ACCOUNT       NAME        ROLE            RESULT  TRAIL    LOGGING  DETAIL
  ------------  ----------  --------------  ------  -------  -------  ---------------------
  100000000000  management  management+log  OK      created  yes      -
  200000000000  member-00   member          OK      created  yes      -
  200000000001  member-01   member          OK      created  yes      -
  200000000002  member-02   member          FAILED  -        -        AccessDeniedException

Trails ready: 3/4 account(s)
  FAILED 200000000002 member-02: AccessDeniedException: CreateTrail failed: AccessDeniedException: User: ... is not authorized ...
         fix: An SCP, RCP or permissions boundary denies a CloudTrail action in this account. Run --check to see which policy.
Timings: plan=<n>s, bucket_ready_at=<n>s, member_trails=<n>s, stackset_teardown=<n>s, total=<n>s
Report written to cloudtrail-accelerator-20261013133041.json
First log files reach the bucket within about 15 minutes.
```

The parts, from top to bottom:

1. **Header (`Caller` to `StackSet / role`):** the plan. Who is running it, which organization, which accounts are skipped and why, where the logs go, how many accounts get the trail, and the temporary StackSet and role names.
2. **`[warn]` lines:** non-blocking findings, for example an opt-in region or a bucket using SSE-KMS. See [troubleshooting.md](troubleshooting.md#warnings).
3. **Bucket lines:** whether the bucket was created or reused, and how many accounts the policy allows.
4. **One line per account:** `trail created` (or `trail updated` when the script is run again), and whether the trail is logging. Failures show the AWS error. The management account's trail is created at the same time as the member accounts', so lines appear in the order the trails finish.
5. **`Removed N account(s) without a trail ...`:** accounts whose trail couldn't be created were taken back out of the bucket policy, so the policy only allows accounts that have the trail.
6. **Teardown lines:** the stack instances, and with them the temporary roles, being deleted at maximum concurrency, then the StackSet. If some instances fail to delete, `retrying them once` follows.
7. **Summary table:** one row per account ([below](#the-summary-table)).
8. **`FAILED` and `fix:` lines:** for each failed account, the error code, the message and the fix. [troubleshooting.md](troubleshooting.md#per-account-errors) explains every code.
9. **`[error]` lines about stack instances:** only if a stack instance couldn't be deleted, even after a retry. The StackSet is then kept, so its roles can still be removed with `--cleanup` once the cause is fixed. Stacks are never retained.
10. **`Timings`:** seconds per phase: `plan`, `bucket_ready_at` (seconds since the start), `member_trails` (StackSet rollout plus trail creation), `stackset_teardown`, `total`. The total is the runtime of the setup for your organization.

## The Summary Table

| Column      | Values                                                                                                                                                          |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ACCOUNT` | Account ID. The management account comes first.                                                                                                                 |
| `NAME`    | Account name from AWS Organizations (first 32 characters).                                                                                                      |
| `ROLE`    | `management` or `member`, with `+log` for the account that owns the bucket.                                                                               |
| `RESULT`  | `OK` or `FAILED`.                                                                                                                                           |
| `TRAIL`   | `created` (new trail) or `updated` (the accelerator's trail already existed, because the script was run before, and was brought in line). `-` on failure. |
| `LOGGING` | `yes` if the trail reported `IsLogging` right after setup, `NO` if not, `-` on failure.                                                                 |
| `DETAIL`  | The error code on failure. On success, CloudTrail's `LatestDeliveryError` if there is one (only possible for a trail that already existed), otherwise `-`.  |

## The JSON Report

Every run writes `cloudtrail-accelerator-<run-id>.json` in the current directory, or at `--report`. It is written even when the run stops part-way; that version has an `aborted` field. It contains account IDs and names, so treat it as internal.

```json
{
  "accelerator_version": "1.0.0",
  "mode": "apply",
  "run_id": "20261013133041",
  "started_at": "2026-10-13T13:30:41.368621+00:00",
  "caller": "arn:aws:sts::100000000000:assumed-role/Admin/tester",
  "management_account": "100000000000",
  "organization_id": "o-fake",
  "log_account": "100000000000",
  "region": "us-east-1",
  "bucket": "cloudkeeper-cloudtrail-accelerator-100000000000-us-east-1",
  "trail_name": "cloudkeeper-cloudtrail-accelerator",
  "stackset": "cloudkeeper-cloudtrail-accelerator-20261013133041",
  "skipped_inactive": [{ "id": "200000000003", "name": "member-03", "state": "SUSPENDED" }],
  "accounts": {
    "100000000000": {
      "name": "management", "role": "management+log", "result": "OK",
      "trail_arn": "arn:aws:cloudtrail:us-east-1:100000000000:trail/cloudkeeper-cloudtrail-accelerator",
      "action": "created", "is_logging": true, "latest_delivery_error": null, "seconds": 2.1
    },
    "200000000002": {
      "name": "member-02", "role": "member", "result": "FAILED",
      "error_code": "AccessDeniedException",
      "error": "CreateTrail failed: AccessDeniedException: User: ... is not authorized ...",
      "hint": "An SCP, RCP or permissions boundary denies a CloudTrail action in this account. Run --check to see which policy.",
      "seconds": 63.4
    }
  },
  "removed_from_policy": ["200000000002"],
  "stackset_deleted": true,
  "stack_instances_left": {},
  "finished_at": "2026-10-13T13:34:53.004211+00:00",
  "timings_seconds": { "plan": 6.2, "bucket_ready_at": 9.8, "member_trails": 180.4, "stackset_teardown": 61.0, "total": 252.1 },
  "summary": { "accounts": 4, "succeeded": 3, "failed": 1 }
}
```

The `seconds` and `timings_seconds` values above are illustrative.

**Top-level fields**

| Field                                                   | Meaning                                                                                                                                                                                                                                                  |
| ------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `accelerator_version`                                 | Version of the script that wrote the report.                                                                                                                                                                                                             |
| `mode`                                                | `apply`.                                                                                                                                                                                                                                               |
| `run_id`                                              | UTC start time `YYYYMMDDHHMMSS`; also the suffix of the StackSet and role names.                                                                                                                                                                       |
| `started_at`, `finished_at`                         | ISO 8601 UTC timestamps.                                                                                                                                                                                                                                 |
| `caller`                                              | ARN the run used.                                                                                                                                                                                                                                        |
| `management_account`, `organization_id`             | The organization.                                                                                                                                                                                                                                        |
| `log_account`, `region`, `bucket`, `trail_name` | The settings used.                                                                                                                                                                                                                                       |
| `stackset`                                            | Temporary StackSet name, or `null` if the organization has no member accounts.                                                                                                                                                                         |
| `skipped_inactive`                                    | Accounts that aren't `ACTIVE` (`SUSPENDED`, `PENDING_CLOSURE`, ...), with their state.                                                                                                                                                             |
| `accounts`                                            | One entry per account (below).                                                                                                                                                                                                                           |
| `removed_from_policy`                                 | Present only if some accounts' trail couldn't be created: those accounts, taken back out of the bucket policy.                                                                                                                                           |
| `stackset_deleted`                                    | `true` if the StackSet is gone. `false` means: run `--cleanup`. Absent if no StackSet was needed.                                                                                                                                                  |
| `stack_instances_left`                                | `{account: reason}` for stack instances that couldn't be deleted, even after a retry. Their roles may still exist, so the StackSet was kept: fix the cause and run `--cleanup`. Empty when everything was deleted; absent if no StackSet was needed. |
| `timings_seconds`                                     | Phase timings, as in the console.                                                                                                                                                                                                                        |
| `summary`                                             | `accounts`, `succeeded`, `failed`.                                                                                                                                                                                                                 |
| `aborted`                                             | Present only if the run stopped part-way: the cause.                                                                                                                                                                                                     |

**Fields of each `accounts` entry**

| Field                     | When   | Meaning                                                                                                                                                            |
| ------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `name`                  | always | Account name.                                                                                                                                                      |
| `role`                  | always | `management` or `member`, with `+log`.                                                                                                                       |
| `result`                | always | `OK` or `FAILED`.                                                                                                                                              |
| `trail_arn`             | OK     | ARN of the trail in this account.                                                                                                                                  |
| `action`                | OK     | `created` or `updated`.                                                                                                                                        |
| `is_logging`            | OK     | `IsLogging` from `GetTrailStatus` right after setup.                                                                                                           |
| `latest_delivery_error` | OK     | `LatestDeliveryError` from `GetTrailStatus`, or `null`.                                                                                                      |
| `seconds`               | mostly | Time spent on this account, including retries.                                                                                                                     |
| `error_code`            | FAILED | An AWS error code, or one of the accelerator's own (`TrailNotManaged`, `TrailHomeRegionMismatch`, `RoleNotReady`, `AssumeRoleFailed`, `BucketNotReady`). |
| `error`                 | FAILED | The full message.                                                                                                                                                  |
| `hint`                  | FAILED | What to do about it.                                                                                                                                               |

## The State of the Trail in Each Account

`RESULT OK` means the account now has a trail with exactly the configuration in [how-it-works.md](how-it-works.md#the-trail-in-each-account), delivering to the central bucket, and that CloudTrail reported it as logging right after setup.

What to expect afterwards:

- **First log files** appear in the bucket within about 15 minutes, usually sooner. Quiet accounts and regions may take longer, since files are only written when there are events.
- **Digest files** (log file validation) appear hourly for every region.
- **`latest_delivery_error`** is empty for a new trail; delivery problems only show up after CloudTrail tries to write. Check with `aws cloudtrail get-trail-status` in the account, or by listing the bucket (see [troubleshooting.md](troubleshooting.md#checking-a-single-account-by-hand)).

## Check Mode

```text
Pre-flight checks (read-only)
[OK   ] The caller has every permission the run needs
[OK   ] Region us-east-1 is enabled in the management account
[OK   ] Bucket name cloudkeeper-cloudtrail-accelerator-100000000000-us-east-1 is free; it will be created
[OK   ] Bucket policy size 1132 bytes for 4 accounts (limit 20 KB)
[OK   ] StackSets trusted access is enabled
[OK   ] boto3 1.36.17 supports SOFT_FAILURE_TOLERANCE
[OK   ] Management account has 0 trail(s) in us-east-1 (limit 5)
[OK   ] 200000000000 member-00: SCPs allow every action needed
[OK   ] 200000000001 member-01: SCPs allow every action needed
[WARN ] 200000000002 member-02: will fail - cloudformation:CreateStack (denied by SCP DenyAllExceptIAM on 200000000002); cloudformation:DeleteStack (denied by SCP DenyAllExceptIAM on 200000000002); ...

Checks done: 0 blocking, 1 warning(s). Trails inside member accounts can only be seen from inside each account, so problems with them surface at run time.
Nothing was changed
```

Each line has a level:

- `OK`: fine.
- `WARN`: the run can go ahead, but something will fail or needs a look.
- `BLOCK`: a normal run would refuse to start.

A member account that will fail is only a `WARN`, because it fails on its own. A member log account failing is a `BLOCK`, because nothing works without the bucket. The exit code is `1` if there is any `BLOCK`, otherwise `0`. [troubleshooting.md](troubleshooting.md#check-findings) explains every finding. `--check` also prints the plan header with every member account that gets the trail.

## Dry-Run Mode

Prints, without changing anything:

- the plan header with every member account that gets the trail
- the full bucket policy as it will be after the run, including statements you already had
- the temporary role's CloudFormation template exactly as the StackSet will deploy it, with its trust condition and permissions
- any `WARN` and `BLOCK` findings

## Where the Logs Land

Standard CloudTrail layout, one folder per account:

```text
s3://<bucket>/AWSLogs/<account-id>/CloudTrail/<region>/YYYY/MM/DD/<account-id>_CloudTrail_<region>_<timestamp>_<id>.json.gz
s3://<bucket>/AWSLogs/<account-id>/CloudTrail-Digest/<region>/YYYY/MM/DD/<account-id>_CloudTrail-Digest_<region>_<trail-name>_<home-region>_<timestamp>.json.gz
```

Global service events (IAM, STS, CloudFront, ...) are logged in the `us-east-1` folder. To verify log integrity:

```sh
aws cloudtrail validate-logs --trail-arn <trail-arn-from-the-report> --start-time 2026-10-13T00:00:00Z
```

The credentials must be able to read the bucket. If they are for a different account than the one that owns the trail (for example the management account checking a member's trail), add `--s3-bucket <bucket> --account-id <member-account-id>`.
