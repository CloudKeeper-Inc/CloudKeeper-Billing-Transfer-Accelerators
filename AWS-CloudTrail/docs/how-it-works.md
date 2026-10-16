# How It Works

## Overview
This page explains in detail what the CloudTrail Accelerator does and the AWS resources and permissions it uses. The accelerator is a one-time setup: it sets up the accounts that are in the organization when it runs. For installation and usage, see the [README](../README.md).

- [Architecture](#architecture)
- [Step by Step](#step-by-step)
- [Running It Again](#running-it-again)
- [Concurrency and Runtime](#concurrency-and-runtime)
- [Least Privilege](#least-privilege)
- [SCPs and RCPs](#scps-and-rcps)
- [The Trail in Each Account](#the-trail-in-each-account)
- [The Central Bucket](#the-central-bucket)
- [Independence From the Organization](#independence-from-the-organization)
- [Protecting the Trails](#protecting-the-trails)
- [Cost](#cost)
- [FAQ](#faq)

## Architecture

```
  management account (your administrator profile)
  ┌───────────────────────────────────────────────────────────────┐
  │ 1. plan: Organizations ListAccounts, IAM simulator            │
  │ 2. S3: cloudkeeper-cloudtrail-accelerator-<id>-<region>       │◄──────────────┐
  │      bucket policy allows every account of the organization   │               │
  │ 3. trail ──────────────────────────────────► bucket           │               │
  │ 4. temporary StackSet (max concurrency) ── role ──┐           │               │
  └───────────────────────────────────────────────────┼───────────┘               │
                                                      ▼                           │
  every member account: assume role → create trail ───────────────────────────────┘
                                                      │
  5. StackSet deleted at max concurrency: roles and stacks gone, trails and bucket stay
```

With `--log-account-id`, the bucket is in that account instead of the management account. Nothing keeps running after the run: no Lambda, no long-lived cross-account role, no StackSet. What remains is one trail per account, and the bucket with its policy.

## Step by Step

### Planning (read-only, in every mode)
1. **Identifies the caller:**
   - Calls `sts:GetCallerIdentity`. The root user is refused.
2. **Reads the organization:**
   - Confirms that the caller is in the management account and that all features are enabled.
   - Lists every account with `ListAccounts`, with its state. Only `ACTIVE` accounts are used.
3. **Validates the region:**
   - It must be a CloudTrail region of the caller's partition, enabled in the management account.
4. **Checks the caller's permissions:**
   - Asks the IAM policy simulator whether the caller's role or user allows every API call the run makes.
5. **Looks at the bucket:**
   - The bucket name must be free, or the bucket must already be owned by the log account.
6. **Checks StackSets trusted access, leftover StackSets from interrupted runs, and the management account's trails:**
   - An existing trail with the same name must be one that the accelerator created, in the same home region.

Any blocking problem stops the run here with exit code 2. Nothing has been changed at that point.

### Setup
1. **Creates the bucket** in the management account and adds every active account to the bucket policy. CloudTrail checks the bucket policy when a trail is created, so every account must be in it first.
2. **Starts the management account's trail:** the script creates it with your own credentials while the next steps run.
3. **Creates the StackSet** and deploys it to the whole organization at maximum concurrency. It creates the temporary role in every member account.
4. **Creates the trail in each member account:** as soon as the stack instance of an account is `CURRENT`, the script assumes the role there and creates the trail. Every account gets the same trail, with the same name.
5. **Waits for every trail**, then takes accounts whose trail couldn't be created out of the bucket policy, so the policy only allows accounts that have the trail.
6. **Deletes the stack instances** at maximum concurrency with `RetainStacks` false, which deletes the stacks and the roles in them, then deletes the StackSet. The trails and the bucket aren't part of any stack, so they stay.

If the organization has no member accounts, only the bucket and the management account's trail are created: no StackSet at all.

### When a Member Account Is the Log Account
The bucket can only be created from inside the log account. So:
1. The StackSet is deployed to every member account at once, as above.
2. As soon as the role in the log account is ready, the script uses it to create the bucket and its policy. That role is the only one with S3 permissions, limited to the one bucket.
3. The management account's trail, and the trails of member accounts whose role was ready earlier, are then created together. The other member accounts get theirs as their role comes up.
4. Cleanup is the same as above. Accounts without a trail are removed from the bucket policy through the log account's role, before the StackSet is deleted.

If the role in the log account never becomes ready (usually because of an SCP), no trail is created anywhere and the run reports why.

## Running It Again
The accelerator is meant to be run once. Running it again is safe, for example after fixing an SCP that made some accounts fail:
- Every step is idempotent. The bucket is reused, and the accelerator's trail in every account is updated in place (`TRAIL updated` in the summary).
- The bucket policy keeps the accounts it already lists, so an account that has left the organization since keeps its permission to deliver.
- Accounts that fail again are taken back out of the bucket policy, unless the policy already listed them before that run.

## Concurrency and Runtime
- **StackSet operations:**
  - Use `MaxConcurrentPercentage: 100`, `FailureTolerancePercentage: 100` and `ConcurrencyMode: SOFT_FAILURE_TOLERANCE`, for both creation and deletion.
  - Under CloudFormation's default strict mode, concurrency is capped at *failure tolerance + 1* regardless of the percentage asked for, so the tolerance has to be 100%.
  - SOFT mode stops a few failing accounts from slowing the rest down.
  - Success is judged per account from the stack instance status, never from the overall result of the operation.
- **Parallel setup:**
  - The script checks the StackSet's progress every 5 seconds and starts on each account as soon as its role is ready, without waiting for the whole rollout. Up to `--workers` (default 20) accounts are set up in parallel.
- **Retries:**
  - AWS SDK calls use adaptive retries (10 attempts).
  - `AssumeRole` is retried for 90 seconds, because a new role isn't assumable everywhere at once.
  - `CreateTrail` and related calls are retried for 60 seconds on errors that clear once AWS catches up: `InsufficientS3BucketPolicyException`, `S3BucketDoesNotExistException`, `AccessDenied`, `AccessDeniedException`. A real SCP denial therefore takes about a minute to be reported for that account, without holding up the others.
  - Stack instances that fail to delete are retried once. Stacks are never retained (`RetainStacks` is always false), so deleting an instance always deletes its role. If an instance still can't be deleted, the StackSet is kept and reported, and `--cleanup` finishes the job once the cause is fixed.
- **Runtime:**
  - Mostly the StackSet rollout plus its deletion, which CloudFormation runs in parallel across accounts. Setting up the trail takes seconds per account and overlaps the rollout.
  - For example, in an organization of 29 accounts:

    | Milestone | Time from the start |
    |---|---|
    | Bucket created | 16 seconds |
    | Trails created and logging in every account | 1 minute 17 seconds |
    | Run finished, StackSet and roles deleted | 5 minutes 24 seconds |
    | Logs from every account in the bucket | 9 minutes 54 seconds |

    Each trail took 2 to 5 seconds to set up. Most of the run is spent waiting for CloudFormation to finish the StackSet rollout, which it must do before the StackSet can be deleted.
  - Every run prints its phase timings and records them in the report under `timings_seconds`. For very large organizations, raise `--timeout-minutes` (default 60 for the rollout and for the deletion).

## Least Privilege
Both scripts follow the principle of least privilege.

1. **Your profile (the runner):**
   - Must be an administrator in the management account, because it creates a StackSet that creates IAM roles in every member account. Before changing anything, the IAM policy simulator checks that it allows every API call the run makes.
   - The calls the accelerator makes with it:

     | Service | Actions | Used by |
     |---|---|---|
     | STS | `GetCallerIdentity`, `AssumeRole` (into `cloudkeeper-cloudtrail-accelerator-*`) | every run |
     | Organizations | `DescribeOrganization`, `ListRoots`, `ListAccounts`, `ListAWSServiceAccessForOrganization` | every mode |
     | Organizations | `ListAccountsForParent`, `ListOrganizationalUnitsForParent`, `ListPoliciesForTarget`, `DescribePolicy` | `--check` |
     | CloudFormation | `CreateStackSet`, `CreateStackInstances`, `DescribeStackSetOperation`, `ListStackSetOperations`, `ListStackInstances`, `DeleteStackInstances`, `DeleteStackSet`, `ListStackSets`, `TagResource` | run, `--cleanup` |
     | CloudTrail | `DescribeTrails`, `CreateTrail`, `UpdateTrail`, `PutEventSelectors`, `StartLogging`, `AddTags`, `ListTags`, `GetTrailStatus` | run (the management account's trail) |
     | S3 | `CreateBucket`, `ListBucket`, `GetBucketPolicy`, `PutBucketPolicy`, `GetEncryptionConfiguration`, `PutEncryptionConfiguration`, `PutBucketPublicAccessBlock`, `PutBucketOwnershipControls`, `PutBucketTagging` | run, when the management account is the log account |
     | EC2 | `DescribeRegions` | every mode (region check) |
     | IAM | `GetRole`, `SimulatePrincipalPolicy` | every mode (permission check) |

2. **The temporary role in member accounts:**
   - **Trust:** only the management account, only the identity running the script (an `aws:PrincipalArn` condition on your role or user), and only with the ExternalId `<member-account-id>-cloudkeeper-cloudtrail-accelerator`. Other principals in the management account can't assume it.
   - **Permissions:** only the calls the script makes, only on the accelerator's own trail (`arn:aws:cloudtrail:<region>:<account-id>:trail/cloudkeeper-cloudtrail-accelerator`): `CreateTrail`, `AddTags`, `ListTags`, `UpdateTrail`, `PutEventSelectors`, `StartLogging`, `GetTrailStatus`. `DescribeTrails` is allowed on `*` because AWS doesn't support resource-level permissions for it; it only reads trail settings. The role can't delete or stop any trail.
   - **Member log account only:** a second inline policy, added through a CloudFormation condition, with the S3 bucket setup actions on the one bucket ARN only.
   - **Lifetime:** created at the start of the run and deleted with the StackSet at the end of it.
3. **The bucket policy:**
   - CloudTrail may only write under `AWSLogs/`, only for the listed account IDs (`aws:SourceAccount`), only for the accelerator's trail (`aws:SourceArn`), and only with `bucket-owner-full-control`.
4. **The StackSets execution role:**
   - CloudFormation's own `stacksets-exec-*` role in member accounts is created and managed by AWS for service-managed StackSets; the accelerator doesn't change it.

## SCPs and RCPs
SCPs never apply to the management account. In **member accounts**, these actions must be allowed:

| Who | Actions | Region the call is made in |
|---|---|---|
| StackSets execution role (`stacksets-exec-*`) | `cloudformation:CreateStack`, `cloudformation:DeleteStack`, `iam:CreateRole`, `iam:PutRolePolicy`, `iam:TagRole`, `iam:DeleteRolePolicy`, `iam:DeleteRole` | `--region` (IAM calls: `us-east-1`, or the IAM region of the partition) |
| Temporary role `cloudkeeper-cloudtrail-accelerator-*` | `cloudtrail:DescribeTrails`, `CreateTrail`, `AddTags`, `ListTags`, `UpdateTrail`, `PutEventSelectors`, `StartLogging`, `GetTrailStatus` | `--region` |
| Temporary role in a **member log account** | `s3:CreateBucket`, `ListBucket`, `GetBucketPolicy`, `PutBucketPolicy`, `GetEncryptionConfiguration`, `PutEncryptionConfiguration`, `PutBucketPublicAccessBlock`, `PutBucketOwnershipControls`, `PutBucketTagging` | `--region` |

If an SCP blocks the delete actions (`cloudformation:DeleteStack`, `iam:DeleteRole*`), the temporary role can't be removed from that account; `--check` reports it.

RCPs on member accounts must not deny `sts:AssumeRole` into the temporary role from the management account. RCPs on the **log account** must not deny `s3:PutObject` or `s3:GetBucketAcl` by `cloudtrail.amazonaws.com`.

`--check` walks root → OUs → account for every member account and reports each one as:
- **OK:** every action is allowed.
- **will fail:** an SCP denies an action, or no SCP at some level allows it. For a member log account this blocks the whole run; any other account just fails on its own.
- **review:** a conditional or resource-scoped deny that can't be judged without the request. A deny conditioned only on `aws:RequestedRegion` is evaluated against the real region. RCP denies that touch the actions above are always listed for review.

## The Trail in Each Account
Every account ends up with the same trail. As shown by `aws cloudtrail get-trail --name cloudkeeper-cloudtrail-accelerator`:

| Setting | Value |
|---|---|
| Name | `--trail-name` (default `cloudkeeper-cloudtrail-accelerator`) |
| Home region | `--region` |
| S3 bucket | `--bucket` in the management account (or log account); no key prefix |
| Multi-region trail | yes |
| Include global service events | yes (IAM, STS, CloudFront, ...) |
| Log file validation | yes (hourly digest files) |
| Event selectors | management events, **Read and Write**; no data events |
| Insights, network activity events | off |
| KMS encryption | none (the bucket's SSE-S3 encryption applies) |
| CloudWatch Logs, SNS | none |
| Organization trail | no: an ordinary trail owned by the account |
| Tags | `CreatedBy=cloudkeeper-cloudtrail-accelerator` |
| Logging | started |

## The Central Bucket
When the accelerator creates the bucket, it sets:
- **Block Public Access:** all four settings on
- **Object Ownership:** `BucketOwnerEnforced` (ACLs disabled; the bucket's account owns every object)
- **Default encryption:** SSE-S3 (`AES256`); no KMS key to manage or share
- **Tags:** `CreatedBy=cloudkeeper-cloudtrail-accelerator`

An **existing** bucket with that name in the management account (or log account) is reused as it is: its public access, ownership, encryption, lifecycle and other settings are left alone. If its default encryption is SSE-KMS, you get a warning, because CloudTrail then needs permission to use that key, and the accelerator doesn't manage KMS.

The bucket policy gets these three statements, merged with any statements already there:

```json
[
  { "Sid": "AWSCloudTrailAclCheck", "Effect": "Allow",
    "Principal": { "Service": "cloudtrail.amazonaws.com" },
    "Action": "s3:GetBucketAcl", "Resource": "arn:aws:s3:::<bucket>",
    "Condition": { "ArnLike": { "aws:SourceArn": "arn:aws:cloudtrail:<region>:*:trail/<trail-name>" } } },
  { "Sid": "AWSCloudTrailWrite", "Effect": "Allow",
    "Principal": { "Service": "cloudtrail.amazonaws.com" },
    "Action": "s3:PutObject", "Resource": "arn:aws:s3:::<bucket>/AWSLogs/*",
    "Condition": {
      "StringEquals": { "s3:x-amz-acl": "bucket-owner-full-control",
                        "aws:SourceAccount": ["<every account ID from ListAccounts>"] },
      "ArnLike": { "aws:SourceArn": "arn:aws:cloudtrail:<region>:*:trail/<trail-name>" } } },
  { "Sid": "DenyInsecureTransport", "Effect": "Deny", "Principal": "*",
    "Action": "s3:*", "Resource": ["arn:aws:s3:::<bucket>", "arn:aws:s3:::<bucket>/*"],
    "Condition": { "Bool": { "aws:SecureTransport": "false" } } }
]
```

CloudTrail sets `aws:SourceArn` to the ARN of the trail that delivers, so only the accelerator's trail, in the listed accounts, can write. Your own statements are kept. `--dry-run` prints the exact policy a run would write.

## Independence From the Organization
After the run, nothing that makes logging work depends on AWS Organizations:

| Piece | Why it survives an account leaving or changing organizations |
|---|---|
| Trails | Ordinary per-account trails. An organization trail stops applying to an account as soon as it leaves. |
| Bucket policy | Allows accounts by ID (`aws:SourceAccount`). An `aws:SourceOrgID` or `aws:PrincipalOrgID` condition would stop matching after a move. |
| Temporary role and StackSet | Exist only during the run. |

This holds for the bucket's account too: if it leaves the organization, the bucket and its policy stay as they are, and every trail keeps delivering. The organization is only needed while the script runs.

## Protecting the Trails
Like any non-organization trail, an administrator of a member account can stop, change or delete their trail. Running the accelerator again restores its configuration. To prevent it, attach an SCP like this to the member OUs. It exempts the accelerator's own temporary roles:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "ProtectAcceleratorTrail",
    "Effect": "Deny",
    "Action": ["cloudtrail:StopLogging", "cloudtrail:DeleteTrail", "cloudtrail:UpdateTrail",
               "cloudtrail:PutEventSelectors", "cloudtrail:RemoveTags"],
    "Resource": "arn:aws:cloudtrail:*:*:trail/cloudkeeper-cloudtrail-accelerator",
    "Condition": { "ArnNotLike": { "aws:PrincipalArn": "arn:aws:iam::*:role/cloudkeeper-cloudtrail-accelerator-*" } }
  }]
}
```

If you used `--trail-name`, put your trail name in `Resource`. SCPs stop applying when an account leaves the organization. `--check` lists this SCP as *review* for the trail actions, because it is a conditional deny.

The accelerator doesn't configure the following; add them to the bucket yourself if you need them:
- KMS encryption
- Object Lock and retention, lifecycle rules
- replication, access logging

## Cost
- **CloudTrail:** the first copy of management events in each account is free. If an account already has another trail logging management events (including an organization trail or Control Tower's trail), this trail is an additional, paid copy. See [CloudTrail pricing](https://aws.amazon.com/cloudtrail/pricing/).
- **S3:** storage and PUT requests for the log and digest files. Add a lifecycle rule to expire or archive old logs.
- **CloudFormation StackSets, IAM roles and STS** are free.

## FAQ
- **What about accounts that join the organization later?** They aren't covered. The accelerator is a one-time setup for the accounts in the organization when it runs.
- **Can it run alongside an existing organization trail or Control Tower?** Yes. It adds one trail per account and doesn't touch other trails. Mind the cost of the second copy of management events.
- **Can I use my own names?** Yes. `--bucket` and `--trail-name` set the bucket name and the trail name (the same trail name in every account). Without them, the defaults `cloudkeeper-cloudtrail-accelerator-<account-id>-<region>` and `cloudkeeper-cloudtrail-accelerator` are used.
- **Can I use an existing bucket?** Yes. Pass `--bucket` with a bucket owned by the management account (or log account). Its settings are left alone; only the three policy statements are added. If it uses SSE-KMS, grant CloudTrail the key or switch it to SSE-S3.
- **What if a member account already has a trail with the same name?** If the accelerator didn't create it, that account fails with `TrailNotManaged` and the trail is left alone. Choose another `--trail-name`, or tag that trail `CreatedBy=cloudkeeper-cloudtrail-accelerator` to hand it over.
- **What happens to suspended or closed accounts?** They are skipped, and listed in the plan and the report.
- **Does it work in GovCloud or China?** Yes. The partition is taken from the caller's credentials, and every ARN and the IAM region follow it.
- **Is it safe to run it again after a failure or interruption?** Yes. See [Running It Again](#running-it-again). If a StackSet was left behind, `--cleanup` removes it.
- **How do I remove everything?** The accelerator doesn't remove what it created. Delete the trail (`aws cloudtrail delete-trail --name cloudkeeper-cloudtrail-accelerator`) in each account, then empty and delete the bucket in the management account (or log account).
