# Troubleshooting

## Overview
This page lists every error and warning the CloudTrail Accelerator can print, what it means and how to fix it, followed by debugging and recovery steps. Messages are quoted as printed, with `<placeholders>` for the variable parts.

- [First Steps](#first-steps)
- [How Problems Are Reported](#how-problems-are-reported)
- [Before Anything Changes (Exit 2)](#before-anything-changes-exit-2)
- [Check Findings](#check-findings)
- [The Run Stopped Part-Way (Exit 3)](#the-run-stopped-part-way-exit-3)
- [Per-Account Errors](#per-account-errors)
- [Teardown Problems](#teardown-problems)
- [Warnings](#warnings)
- [Cleanup Mode](#cleanup-mode)
- [Debug Mode](#debug-mode)
- [Reading the Report](#reading-the-report)
- [Watching a Run in AWS](#watching-a-run-in-aws)
- [Checking a Single Account by Hand](#checking-a-single-account-by-hand)
- [Checking Delivery in the Bucket](#checking-delivery-in-the-bucket)
- [Recovering From an Interrupted Run](#recovering-from-an-interrupted-run)
- [Reporting a Problem](#reporting-a-problem)

## First Steps
1. **Confirm the identity:** it must be an administrator in the management account.
   ```sh
   aws sts get-caller-identity --profile <profile>
   aws organizations describe-organization --profile <profile> --query 'Organization.MasterAccountId'
   ```
   The two account IDs must match.
2. **Run the pre-flight checks:** every `BLOCK` and `WARN` line explains itself; [Check Findings](#check-findings) has the details.
   ```sh
   python3 cloudtrail_accelerator.py --profile <profile> [--log-account-id <id>] --check
   ```
3. **Look up the error code:** for a failed account, find its `error_code` in [Per-Account Errors](#per-account-errors). The `fix:` line under each failure says the same in short.
4. **Run it again:** after fixing the cause, run the accelerator again. It's safe: the bucket is reused and existing accelerator trails are updated in place.

## How Problems Are Reported

| Situation | What you see | Exit code | Anything changed? |
|---|---|---|---|
| Invalid input, credentials problem, unmet precondition | `ERROR: ...` on stderr | `2` | No |
| One or more accounts failed, others fine | `FAILED <account>` lines with a `fix:` hint; `result: FAILED` in the report | `1` | Yes, for the accounts that succeeded |
| A stack instance (and its role) couldn't be deleted | `[error] <account>: its stack instance could not be deleted ...` and `StackSet ... was NOT deleted` | `1` | Yes; the StackSet is kept for `--cleanup` |
| The run stopped part-way | `ERROR: the run stopped part-way: ...`; partial report with `aborted` | `3` | Partly; the StackSet was still deleted |
| Ctrl-C | `Interrupted. ...` | `130` | Partly; the StackSet is deleted first if possible |
| `--check` found a blocker | `[BLOCK]` lines | `1` | No |

Add `--debug` to any command to see full tracebacks and every AWS API call; see [Debug Mode](#debug-mode).

## Before Anything Changes (Exit 2)
These stop the script during planning. **Nothing has been created or changed.** Fix the cause and run again.

### Input
`ERROR: Invalid arguments:` followed by one or more of:
- **`--log-account-id '<x>' is not a 12-digit AWS account ID`:** Typo. Pass the 12-digit ID, including leading zeros.
- **`--bucket '<x>' is not a valid S3 bucket name (...)`:** Uppercase letters, underscores, wrong length, IP-address format, or a reserved prefix or suffix (`xn--`, `sthree-`, `-s3alias`, `--ol-s3`). Choose a valid name.
- **`--trail-name '<x>' is not a valid trail name (...)`:** Fewer than 3 or more than 128 characters, starts or ends with a symbol, or two symbols in a row (`--`, `._`). Choose a valid name.
- **`--workers must be between 1 and 100`**, **`--timeout-minutes must be between 5 and 720`:** Use a value in range.
- **`--cleanup can't be combined with --check or --dry-run`:** Run one mode at a time.
- **`usage: cloudtrail_accelerator.py ... error: ...`:** Unknown option or non-numeric value. See `--help`.

Also:
- **`ERROR: The default bucket name <name> is longer than 63 characters. ...`:** Only possible with a very long region name. Pass a shorter name with `--bucket`.

### Credentials and Identity
- **`ERROR: AWS credentials/configuration problem: The config profile (<p>) could not be found`:** `--profile` isn't in `~/.aws/config`. Check the name with `aws configure list-profiles`.
- **`ERROR: AWS credentials/configuration problem: Unable to locate credentials`:** No credentials in the default chain. Pass `--profile`, or set up credentials.
- **`ERROR: AWS credentials/configuration problem: Error when retrieving token from sso: Token has expired and refresh failed`** (or similar): The IAM Identity Center session expired. Run `aws sso login --profile <profile>`.
- **`ERROR: AWS credentials/configuration problem: Error when retrieving credentials from custom-process: ...`:** The profile's `credential_process` failed (login needed, network). Run that tool's login, then `aws sts get-caller-identity --profile <profile>`.
- **`ERROR: You are using the root user of account <id>. AWS doesn't let the root user assume roles, ...`:** Use an IAM Identity Center or IAM administrator identity instead.
- **`ERROR: Account <id> is not part of an AWS Organization.`:** Run it against an organization's management account.
- **`ERROR: <arn> may not call organizations:DescribeOrganization. ...`:** The identity lacks Organizations permissions, or isn't in the organization's management account. Use an administrator profile of the management account.
- **`ERROR: The credentials are for account <a>, but this organization's management account is <m>. ...`:** The profile belongs to a member account. Use an administrator profile of `<m>`.
- **`ERROR: Could not read the organization: <Operation> failed: <Code>: <message>`:** An AWS call during planning failed, for example `AccessDenied` on `ListAccounts`, `DescribeTrails` or `GetBucketPolicy`, or throttling. Read `<Operation>` and `<Code>`; usually the identity isn't an administrator. `--debug` shows the exact call.

### Region and Organization
- **`ERROR: --region <r> is not a CloudTrail region in partition <p>. ...`:** Typo, a region from another partition, or a region too new for the installed boto3. Fix the region, or run `pip install -U boto3`.
- **`ERROR: --region <r> is an opt-in region that is not enabled in the management account. ...`:** Enable it in every account, or use a default-enabled region.
- **`ERROR: --log-account-id <id> is not an account in this organization.`:** Wrong ID, or an account outside the organization. Use an account in the organization.
- **`ERROR: --log-account-id <id> (<name>) is <STATE>; the log account must be ACTIVE.`:** The chosen log account is suspended or closing. Choose an active account.

### Blockers Found by Planning
The run prints `ERROR: Not starting, nothing was changed:` and lists each blocker. These are the `BLOCK` findings of [Check Findings](#check-findings):
- **`The organization has only the CONSOLIDATED_BILLING feature set. ...`:** Enable all features in AWS Organizations.
- **`<arn> is missing permissions the run needs: <actions>. ...`:** Use an administrator identity in the management account.
- **`Bucket name <b> is owned by another AWS account (or these credentials can't access it). ...`:** Pass another `--bucket`. If the bucket is really yours, check that your identity can call `s3:ListBucket` on it.
- **`Bucket <b> exists in the management account, but the log account is <id>. ...`:** Pass another `--bucket`, or drop `--log-account-id` to use the management account's bucket.
- **`The bucket policy would be <n> bytes for <k> accounts, over S3's 20 KB limit. ...`:** See [Limitations](../README.md#limitations).
- **`StackSets trusted access is not enabled in AWS Organizations. ...`:** Run `aws cloudformation activate-organizations-access --profile <profile>`.
- **`Management account: A trail named <t> already exists and was not created by the accelerator. ...`:** Choose another `--trail-name`, or tag that trail `CreatedBy=cloudkeeper-cloudtrail-accelerator` to hand it over.
- **`Management account: A trail named <t> already exists with home region <r1>, not <r2>. ...`:** Use `--region <r1>`, or another `--trail-name`.
- **`Management account: <t> is an organization trail; the accelerator won't change it. ...`:** Choose another `--trail-name`.

## Check Findings
`--check` prints every planning finding plus one line per account it would touch. Levels: `OK`, `WARN` (the run can go ahead), `BLOCK` (a run would refuse to start).
- **`The caller has every permission the run needs`** (OK): Verified with the IAM policy simulator.
- **`Could not verify <arn>'s permissions with the IAM policy simulator ...`** (WARN): The identity can't call `iam:GetRole` or `iam:SimulatePrincipalPolicy`, which is typical of a non-administrator. The run may fail later; use an administrator identity.
- **`<arn> is missing permissions the run needs: ...`** (BLOCK): See above.
- **`Region <r> is enabled in the management account`** (OK).
- **`<r> is an opt-in region. Accounts that haven't enabled it will fail; ...`** (WARN): Member accounts without the region fail with `OptInRequired`. Prefer a default-enabled region.
- **`Could not confirm <r> is enabled (no ec2:DescribeRegions permission).`** (WARN): Informational; a disabled region still fails clearly later.
- **`Bucket name <b> is free; it will be created`** / **`... created in <id>`** (OK).
- **`Bucket <b> exists in the management account and will be reused`** (OK): Its settings are left as they are.
- **`Bucket <b> uses aws:kms default encryption. ...`** (WARN): CloudTrail needs permission on the key, which the accelerator doesn't manage. Switch to SSE-S3 or grant `cloudtrail.amazonaws.com` the key.
- **`Bucket <b> already exists in another account. That is fine if it is log account <id>'s bucket from an earlier run; ...`** (WARN): Seen with a member log account, because from the management account any foreign bucket looks the same. If it isn't the log account's bucket, the run stops part-way (exit 3); pass another `--bucket`.
- **`Bucket policy size <n> bytes for <k> accounts (limit 20 KB)`** (OK).
- **`StackSets trusted access is enabled`** (OK).
- **`StackSets trusted access is not enabled ...`** (BLOCK, or WARN if no StackSet is needed): Enable it.
- **`Could not read StackSets trusted access ...`** (WARN): Informational.
- **`boto3 <v> supports SOFT_FAILURE_TOLERANCE`** (OK).
- **`boto3 <v> is older than 1.29: ...`** (WARN): Run `pip install -U boto3`.
- **`StackSet <name> was left by an interrupted run. Remove it with --cleanup.`** (WARN): Run `--cleanup`.
- **`Could not list StackSets: ...`** (WARN): Informational.
- **`Management account has <n> trail(s) in <r> (limit 5)`** (OK).
- **`Management account already has the accelerator's trail <t>; it will be updated in place`** (OK): The script was run before.
- **`Management account already sees <n> trails in <r>; the limit is 5 per region, ...`** (WARN): Creating the trail may fail with `MaximumNumberOfTrailsExceededException`. The count includes trails homed in other regions and organization trails.
- **`<account> <name>: SCPs allow every action needed`** (OK).
- **`<account> <name>: will fail - <action> (denied by SCP <p> on <target>); ...`** (WARN, or BLOCK for the log account): An SCP denies an action the run needs in that account. Exempt the accelerator (see [how-it-works.md](how-it-works.md#scps-and-rcps)), or accept that the account fails. If the denied actions include `cloudformation:DeleteStack` or `iam:DeleteRole*` but not the create actions, the temporary role would be created but couldn't be removed; fix that before running.
- **`... (no SCP on <target> allows it)`**: Some level between the root and the account has no SCP allowing the action (for example `FullAWSAccess` was detached).
- **`... (region <r> denied by SCP <p> on <target>)`**: A region-lockdown SCP doesn't allow `--region`. Use an allowed region, or add it to the SCP.
- **`<account> <name>: review - <action> (conditional deny in SCP <p> on <target>)`** (WARN): A deny with conditions (tags, principals, IPs, ...) that can't be judged statically. Read the SCP. It applies to the StackSets execution role (`stacksets-exec-*`) and to the role `cloudkeeper-cloudtrail-accelerator-*`.
- **`<account> <name>: review - RCP <p> on <target> denies <actions> under conditions`** (WARN): An RCP may block `sts:AssumeRole` into the temporary role, or CloudTrail writing to the bucket. Check that it allows the management account and `cloudtrail.amazonaws.com`.
- **`<account> <name>: not found in the OU tree`** (WARN): The account moved during the check. Run it again.
- **`No member accounts`** (OK): The organization has only the management account.

Trails inside member accounts (for example the limit of 5 per region) can't be seen from the management account; problems with them show up at run time as `MaximumNumberOfTrailsExceededException` or `TrailNotManaged`.

## The Run Stopped Part-Way (Exit 3)
`ERROR: the run stopped part-way: <cause>` means that something failed that affects every account, after the run had started changing things. Before exiting, the accelerator:
1. removes accounts whose trail wasn't created from the bucket policy (when it still can)
2. deletes the StackSet, and with it the temporary roles
3. writes the report with an `aborted` field

**Running it again is safe:** the bucket is reused and the trails that were already created are updated in place.
- **`AcceleratorError: Bucket name <b> is already owned by another AWS account. ...`:** Member log account, and the bucket name is taken by a third account. Pass another `--bucket`.
- **`AcceleratorError: The bucket policy would be <n> bytes, ...`:** Member log account whose existing policy pushes it over 20 KB. See [Limitations](../README.md#limitations).
- **`AccountError: <AssumeRole failed ...>`:** The temporary role in the member log account couldn't be assumed (an RCP or SCP on `sts:AssumeRole`). Run `--check --log-account-id <id>`.
- **`CreateStackSet failed: ...`:** `LimitExceededException` (too many StackSets in the account), or a `ValidationError` about trusted access. Delete unused StackSets, or enable trusted access.
- **`CreateStackInstances failed: OperationInProgressException`:** Another operation is running on the same StackSet. This is unexpected, because every run creates its own StackSet. Run `--cleanup`, then run again.
- **`<Operation> failed: ExpiredToken`** / **`RequestExpired`:** The credentials expired mid-run (long runs with short SSO sessions). Log in again and run again; use longer sessions for large organizations.
- **`<Operation> failed: Throttling...`:** API throttling beyond the SDK's retries. Run again with fewer `--workers`.
- **`PutBucketPolicy failed: MalformedPolicy`:** A statement of your own on a reused bucket is invalid. Fix the bucket's own statements.

If the message also says `The StackSet was not deleted. Run again with --cleanup to remove it.`, do that.

## Per-Account Errors
These affect one account. The run continues with the others and exits `1`. Each failure shows an `error_code`, the full `error` and a `hint`, in the console and in the report.

Accounts whose trail couldn't be created are taken back out of the bucket policy. Fix the cause and run the accelerator again to finish them; accounts that already have the trail are updated in place.
- **`AccessDenied`, `AccessDeniedException`:** After 60 seconds of retries, an SCP, RCP or permissions boundary still denies a CloudTrail action in that account. `--check` names the policy. Exempt `arn:aws:iam::*:role/cloudkeeper-cloudtrail-accelerator-*`, or accept the gap.
- **`RoleNotReady`:** The StackSet couldn't create the temporary role there. `error` includes CloudFormation's reason, for example `... is not authorized to perform: iam:CreateRole ... with an explicit deny in a service control policy`, or `Account ... is suspended`. With `the StackSet rollout timed out`, the rollout took longer than `--timeout-minutes`. Fix the SCP, or raise `--timeout-minutes`.
- **`AssumeRoleFailed`:** The role exists but couldn't be assumed for 90 seconds: an RCP or SCP on `sts:AssumeRole`, or the caller lacks `sts:AssumeRole`. Run `--check`; review the RCPs on that account. The role only trusts the identity that started the run, so the same profile must be used for the whole run.
- **`BucketNotReady`:** The central bucket was never created (the log account failed), so no trail could be. Fix the log account's error, shown on its own row.
- **`InsufficientS3BucketPolicyException`:** After retries, CloudTrail still can't confirm that it may write to the bucket. The account isn't in the bucket policy (it joined mid-run), or a statement in the bucket policy or an RCP on the log account denies CloudTrail. Run again; otherwise review the bucket policy and the log account's RCPs.
- **`S3BucketDoesNotExistException`:** The bucket was deleted. Run again; the bucket is created again.
- **`MaximumNumberOfTrailsExceededException`:** The account already has 5 trails in `--region` (trails homed elsewhere count too). Delete an unused trail, then run again.
- **`TrailNotManaged`:** A trail named `--trail-name` exists but wasn't created by the accelerator (no `CreatedBy=cloudkeeper-cloudtrail-accelerator` tag), or it is an organization trail. It was left untouched. Choose another `--trail-name`, or add the tag to that trail to let the accelerator manage it.
- **`TrailHomeRegionMismatch`, `InvalidHomeRegionException`:** A trail with this name exists with a different home region. Use that region, delete that trail, or choose another `--trail-name`.
- **`OptInRequired`:** `--region` isn't enabled in that account, or the account isn't fully activated yet. Enable the region or wait, then run again.
- **`SubscriptionRequiredException`:** The account isn't fully activated yet (common in the minutes after an account is created). Run the accelerator again later.
- **`InvalidClientTokenId`, `RegionDisabledException`:** STS is disabled for `--region` in that account. Use a default-enabled region.
- **`InsufficientEncryptionPolicyException`, `KmsException`:** The bucket's default encryption is SSE-KMS and CloudTrail may not use the key. Switch the bucket to SSE-S3, or grant `cloudtrail.amazonaws.com` the key.
- **`ThrottlingException`, `Throttling`, `TooManyRequestsException`:** Throttled beyond retries. Run again with fewer `--workers`.
- **`ExpiredToken`, `ExpiredTokenException`, `RequestExpired`:** The credentials expired during the run. Log in again and run again.
- **Anything else:** An unexpected AWS error; `error` has the full message. Run again with `--debug --workers 1`.

The `LOGGING` column shows `NO` when CloudTrail reported the trail as not logging right after `StartLogging`. That is unusual; check the account with `aws cloudtrail get-trail-status`.

## Teardown Problems
The StackSet is always deleted at the end of a run, at maximum concurrency. Deleting a stack instance deletes the temporary role in that account.
- **`<n> stack instance(s) failed to delete; retrying them once`:** Some deletions failed (often a transient problem); they are tried once more.
- **`[error] <n> stack instance(s) could not be deleted; StackSet <name> is kept so their roles can still be removed with --cleanup`**, followed by **`[error] <account>: its stack instance could not be deleted (<reason>), so role <name> may still exist there.`** and **`[error] StackSet <name> was NOT deleted. ...`:** After the retry, CloudFormation still couldn't delete the stack in some accounts, typically because an SCP denies `cloudformation:DeleteStack` or `iam:DeleteRole*` there. The accelerator never uses `RetainStacks`, because that would leave the role behind, so the StackSet is kept. The accounts are listed under `stack_instances_left` in the report, and the run exits `1`. The trails and the bucket are not affected. Fix the cause (the `<reason>` names it), then run `--cleanup`.

An account whose SCP blocks CloudFormation entirely never gets a stack, so it has nothing to delete. Its stack instance deletes normally.
- **`[error] StackSet <name> was NOT deleted. Run again with --cleanup to remove it.`:** `DeleteStackSet` failed. Run `python3 cloudtrail_accelerator.py --profile <p> --region <r> --cleanup`.
- **`[warn] Timed out waiting for the StackSet rollout; raise --timeout-minutes for large organizations`:** The rollout took longer than `--timeout-minutes`. Accounts not ready yet fail with `RoleNotReady`. Run again with a higher `--timeout-minutes`.
- **`[warn] Could not remove accounts without a trail from the bucket policy (...): <accounts>`:** Writing the bucket policy failed during cleanup, so those accounts stay allowed to deliver although they have no trail. That is harmless. Running the accelerator again fixes it, or remove them from the `AWSCloudTrailWrite` statement by hand.

## Warnings
`[warn]` lines don't stop the run:
- **Any `WARN` finding from planning, repeated at the start of a run:** See [Check Findings](#check-findings).
- **`[warn] s3://<b> uses aws:kms default encryption. ...`:** A reused bucket with SSE-KMS; accounts may fail with `InsufficientEncryptionPolicyException`.
- **`[warn] <Operation> failed: ...` while polling:** A transient error reading the StackSet's status. It is retried on the next poll; only a concern if it repeats until the timeout.

## Cleanup Mode
- **`No accelerator StackSets left in <r>; nothing to clean up`:** `--cleanup` found nothing. Check `--region`: StackSets live in the run's region.
- **`[error] <account>: stack instance could not be deleted (...); StackSet <name> and its role there are kept. Fix the cause, then run --cleanup again.`:** As in [Teardown Problems](#teardown-problems).

## Debug Mode
`--debug` adds:
- a line for every AWS API call: service, operation, parameters, HTTP status and error code
- the SDK's retry decisions
- full Python tracebacks for errors

Credentials are never logged.

```sh
python3 cloudtrail_accelerator.py --profile <profile> --debug --workers 1 > run.log 2>&1
```

`--workers 1` keeps the calls of different accounts from interleaving. A slice of the output:

```text
[   0.4s] [debug] -> sts:GetCallerIdentity {}
[   0.9s] [debug] <- sts:GetCallerIdentity HTTP 200
[   0.9s] [debug] -> organizations:DescribeOrganization {}
[debug] botocore.retries.standard: Retry needed, retrying request after delay of: 0.39
[   1.6s] [debug] <- organizations:DescribeOrganization HTTP 200
...
[  48.2s] [debug] -> cloudtrail:CreateTrail {"Name": "cloudkeeper-cloudtrail-accelerator", "S3BucketName": "...", ...}
[  48.5s] [debug] <- cloudtrail:CreateTrail HTTP 400 AccessDeniedException
```

The log contains account IDs, bucket names and the bucket policy. Redact them before sharing. Use `--debug` together with `--check` or `--dry-run` to debug those modes without changing anything.

## Reading the Report
The JSON report (`cloudtrail-accelerator-<run-id>.json`) is the most complete record of a run. [output.md](output.md#the-json-report) describes every field. Useful queries:

```sh
R=cloudtrail-accelerator-<run-id>.json
jq '.summary' $R                                                     # counts
jq -r '.accounts | to_entries[] | select(.value.result=="FAILED")
       | "\(.key) \(.value.error_code): \(.value.error)"' $R            # failures
jq '.removed_from_policy, .stackset_deleted, .stack_instances_left' $R   # policy tidy-up and teardown
jq '.timings_seconds' $R                                             # where the time went
jq -r '.aborted // "completed"' $R                                   # did it stop part-way?
```

## Watching a Run in AWS
While a run is in progress, its StackSet is visible in the management account:
- **Console:** CloudFormation > StackSets > `cloudkeeper-cloudtrail-accelerator-<run-id>` > Stack instances, in the `--region` region.
- **CLI:**
  ```sh
  aws cloudformation list-stack-instances --profile <profile> --region <region> \
    --stack-set-name cloudkeeper-cloudtrail-accelerator-<run-id> \
    --query 'Summaries[].[Account,StackInstanceStatus.DetailedStatus,StatusReason]' --output table
  ```

The StackSet is deleted at the end of the run. The reason any account's instance failed is kept in the report (`error` for `RoleNotReady`).

## Checking a Single Account by Hand
From inside the member account (for example through IAM Identity Center), in the home region:

```sh
T=cloudkeeper-cloudtrail-accelerator; R=us-east-1
aws cloudtrail get-trail --name $T --region $R            # configuration: bucket, multi-region, validation
aws cloudtrail get-trail-status --name $T --region $R     # IsLogging, LatestDeliveryTime, LatestDeliveryError
aws cloudtrail get-event-selectors --trail-name $T --region $R
aws cloudtrail list-tags --resource-id-list $(aws cloudtrail get-trail --name $T --region $R --query Trail.TrailARN --output text) --region $R
```

What to look for:
- `IsLogging: true`
- `S3BucketName` is the central bucket
- `IsMultiRegionTrail: true` and `LogFileValidationEnabled: true`
- event selectors: `ReadWriteType: All`, `IncludeManagementEvents: true`
- the tag `CreatedBy=cloudkeeper-cloudtrail-accelerator`

`LatestDeliveryError` values you may see:
- **`AccessDenied`:** The bucket policy doesn't allow this account or this trail, or an RCP or a bucket policy statement denies CloudTrail. Run the accelerator again; check the RCPs of the bucket's account and the bucket policy. A trail with a different name than the one in the bucket policy's `aws:SourceArn` is denied by design.
- **`NoSuchBucket`:** The bucket was deleted. Run the accelerator again; the bucket is created again.
- **`InsufficientEncryptionPolicyException` or KMS errors:** The bucket uses SSE-KMS that CloudTrail can't use. Switch the bucket to SSE-S3, or grant CloudTrail the key.

## Checking Delivery in the Bucket
From the account that owns the bucket (the management account, or the log account):

```sh
B=cloudkeeper-cloudtrail-accelerator-<log-account-id>-<region>; A=<account-id>; R=<region>
aws s3 ls s3://$B/AWSLogs/$A/CloudTrail-Digest/$R/$(date -u +%Y/%m/%d)/ | tail -3   # hourly digests
aws s3 ls s3://$B/AWSLogs/$A/CloudTrail/$R/$(date -u +%Y/%m/%d)/ | tail -3          # log files
aws s3api get-bucket-policy --bucket $B --query Policy --output text | jq '.Statement[] | select(.Sid=="AWSCloudTrailWrite") | .Condition.StringEquals["aws:SourceAccount"]'
```

The last command prints the accounts allowed to deliver.

## Recovering From an Interrupted Run
If a run was killed (Ctrl-C twice, a closed terminal, a lost network), its StackSet and roles may still exist:

```sh
python3 cloudtrail_accelerator.py --profile <profile> --region <region> --cleanup
```

`--cleanup` finds every `ACTIVE` StackSet whose name starts with `cloudkeeper-cloudtrail-accelerator` in `--region` and deletes it at maximum concurrency, stack instances first (never retaining stacks), so their roles go too. If it reports accounts whose stack instance couldn't be deleted, fix the cause it shows and run `--cleanup` again.

Then run the accelerator again to finish the setup. It reuses the bucket and updates the trails that were already created.

## Reporting a Problem
Include:
- the accelerator version (`--version`), `python3 --version` and `pip show boto3`
- the exact command, with account IDs and profile names redacted if needed
- the console output and the JSON report, redacted
- for unexpected errors, a `--debug --workers 1` log, redacted
- the `--check` output
