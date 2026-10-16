# Multi-Account AWS CloudTrail Accelerator

## Overview

This project automates a one-time, centralized AWS CloudTrail setup for an AWS Organization, without using an organization trail. It creates a trail in every account of the organization and delivers the logs of all accounts to a single S3 bucket in the management account (or another account of your choice, the **log account**). Because every account owns an ordinary trail and the bucket policy allows accounts by ID, logging keeps working after accounts leave the organization, for example during a billing transfer.

Use an [organization trail](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/creating-trail-organization.html) when you can. Use this accelerator when you can't, for example when:

- logs must keep flowing after accounts leave the organization
- the bucket must live in an account that an organization trail can't target
- the organization trail belongs to someone else and can't be changed

## Features

- Creates a central S3 bucket in the management account (or in the log account chosen with `--log-account-id`).
- Creates a trail with the same name in every account of the organization, including the management account, that logs management events (read and write). No KMS, no CloudWatch Logs.
- Creates the management account's trail with your own credentials, at the same time as the member accounts' trails.
- Allows every account of the organization in the bucket policy, using the AWS Organizations `ListAccounts` API.
- Creates a temporary IAM role in every member account with a CloudFormation StackSet, deployed at maximum concurrency.
- Assumes that role in every member account and creates the trail there, pointing at the central bucket.
- Deletes the StackSet at maximum concurrency at the end of the run, which deletes the temporary role in every account.
- Follows the principle of least privilege: the temporary role can only manage the accelerator's own trail and is trusted only by the identity running the script.
- Lets you choose the bucket name (`--bucket`) and the trail name (`--trail-name`). Without them, it uses default names that contain the keyword `accelerator`, so its resources are easy to tell apart. The temporary StackSet and role always have it in their names.
- Checks your organization before changing anything: permissions, StackSets trusted access, SCPs and RCPs of every account (`--check`).

## Prerequisites

Before you begin, ensure you have the following prerequisites in place:

1. **Python**:

   - Python 3.8 or newer. [Install Python](https://www.python.org/downloads/)
   - `boto3` 1.29 or newer (installed in [Installation](#installation)).
2. **AWS CLI**:

   - Install AWS CLI if not already installed. Follow the [AWS CLI installation guide](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html).
3. **Configuring AWS CLI**:

   - Ensure that you have an AWS CLI profile on your machine for an **administrator identity in the organization's management account**, for example the `AdministratorAccess` permission set of IAM Identity Center. The script uses this one profile for everything; you don't need a profile per account.
   - Do not use the root user: AWS doesn't let the root user assume roles, which the script needs.
   - With IAM Identity Center, your `~/.aws/config` should look like this:
     ```ini
     [profile org-management-admin]
     sso_session = my-sso
     sso_account_id = 111111111111          # the management account
     sso_role_name = AdministratorAccess
     region = us-east-1

     [sso-session my-sso]
     sso_start_url = https://my-org.awsapps.com/start
     sso_region = us-east-1
     sso_registration_scopes = sso:account:access
     ```
   - Log in and confirm that the profile belongs to the management account:
     ```sh
     aws sso login --profile org-management-admin
     aws sts get-caller-identity --profile org-management-admin --query Account
     aws organizations describe-organization --profile org-management-admin --query Organization.MasterAccountId
     ```

     The two account IDs must match.
4. **AWS Organizations settings**:

   - All features must be enabled (not only consolidated billing).
   - StackSets trusted access must be enabled. Enable it once per organization:
     ```sh
     aws cloudformation activate-organizations-access --profile org-management-admin
     ```
5. **Region**:

   - `--region` (default `us-east-1`) must be enabled in every account. Every trail is multi-region, so all enabled regions are logged whatever you choose.

`--check` verifies all of these. The SCPs and RCPs that must allow the accelerator are listed in [docs/how-it-works.md](docs/how-it-works.md#scps-and-rcps).

## Installation

1. Clone the repository:
   ```sh
   git clone https://github.com/CloudKeeper-Inc/CloudKeeper-Billing-Transfer-Accelerators.git
   cd CloudKeeper-Billing-Transfer-Accelerators/AWS-CloudTrail
   ```
2. Install dependencies:
   ```sh
   pip install -r requirements.txt
   ```

## Usage

1. **Run the pre-flight checks (read-only):**

   ```sh
   python3 cloudtrail_accelerator.py --profile org-management-admin --check
   ```

   Fix every `BLOCK` line. `WARN` lines show accounts that will fail on their own, for example because of an SCP.
2. **Review the plan (read-only):**

   ```sh
   python3 cloudtrail_accelerator.py --profile org-management-admin --dry-run
   ```

   Prints every account that gets a trail, the bucket policy and the temporary role exactly as they will be created.
3. **Run the accelerator:**

   ```sh
   python3 cloudtrail_accelerator.py --profile org-management-admin
   ```

   To put the bucket in a member account instead of the management account, add `--log-account-id <account-id>` to every command.

   A run takes a few minutes. In an organization of 29 accounts, it took about 5.5 minutes, and logs from every account were in the bucket within 10 minutes of the start.

Keep the JSON report the run writes. It records the result of every account, the bucket and trail names, and the run's timings.

### Modes

| Command            | Changes anything?              | What it does                                                                                         |
| ------------------ | ------------------------------ | ---------------------------------------------------------------------------------------------------- |
| *(no mode flag)* | **yes**                  | Sets up the bucket and a trail in every active account                                               |
| `--check`        | no                             | Pre-flight checks, including an SCP/RCP verdict for every account. Exit 1 if anything blocks the run |
| `--dry-run`      | no                             | Prints the plan, the bucket policy after the run, and the temporary role's CloudFormation template   |
| `--cleanup`      | **yes** (StackSets only) | Deletes accelerator StackSets left behind by an interrupted run                                      |

### Parameters

| Option                          | Default                                                          | Description                                                                                                                                                                                                                                     |
| ------------------------------- | ---------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--profile PROFILE`           | standard AWS credential chain                                    | AWS CLI profile of an administrator identity in the management account. Without it, boto3's usual chain applies: environment variables, `AWS_PROFILE`, the `default` profile, then an instance or task role.                                |
| `--log-account-id ACCOUNT_ID` | management account                                               | Account that owns the central bucket: the management account or any `ACTIVE` member account.                                                                                                                                                  |
| `--region REGION`             | `us-east-1`                                                    | Home region of the bucket, every trail and the temporary StackSet. Use a region that is enabled in every account.                                                                                                                               |
| `--bucket NAME`               | `cloudkeeper-cloudtrail-accelerator-<log-account-id>-<region>` | Name of the central bucket. Must be a valid S3 bucket name not owned by another AWS account. An existing bucket with this name in the management account (or log account) is reused as it is.                                                   |
| `--trail-name NAME`           | `cloudkeeper-cloudtrail-accelerator`                           | Name of the trail, the same in every account: 3 to 128 letters, digits, `.`, `_` or `-`, starting and ending with a letter or digit, no two symbols in a row. A trail with this name that the accelerator didn't create is never changed. |
| `--workers N`                 | `20`                                                           | Accounts configured in parallel (1 to 100). Lower it if you see throttling.                                                                                                                                                                     |
| `--timeout-minutes N`         | `60`                                                           | Maximum wait for the StackSet rollout and for its deletion (5 to 720). Raise it for very large organizations.                                                                                                                                   |
| `--report PATH`               | `cloudtrail-accelerator-<run-id>.json`                         | Where to write the JSON report.                                                                                                                                                                                                                 |
| `--debug`                     | off                                                              | Logs every AWS API call (operation and parameters, never credentials) and SDK retry, and prints full tracebacks.                                                                                                                                |
| `--check`                     | off                                                              | Read-only pre-flight checks.                                                                                                                                                                                                                    |
| `--dry-run`                   | off                                                              | Read-only plan.                                                                                                                                                                                                                                 |
| `--cleanup`                   | off                                                              | Deletes StackSets that an interrupted run left behind in `--region`. Can't be combined with `--check` or `--dry-run`.                                                                                                                     |
| `--version`                   |                                                                  | Prints the version.                                                                                                                                                                                                                             |
| `-h`, `--help`              |                                                                  | Prints usage.                                                                                                                                                                                                                                   |

### Exit Codes

| Code    | Meaning                                                                                                                                                         |
| ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `0`   | Success: every active account has the trail, and the StackSet and every temporary role were removed. For `--check`: nothing blocks the run.                   |
| `1`   | Finished, but not everything worked: some accounts failed, or the StackSet or a temporary role could not be removed. For `--check`: something blocks the run. |
| `2`   | Invalid arguments, a credentials problem, or an unmet precondition. **Nothing was changed.**                                                              |
| `3`   | The run stopped part-way after it had started making changes. The StackSet was still deleted and a partial report was written. Running it again is safe.        |
| `130` | Interrupted (Ctrl-C). The StackSet is deleted first if possible; if one is left, run `--cleanup`.                                                             |

## How It Works

1. **Checks the organization (read-only):**
   - Confirms that the profile is an administrator of the management account, and that all features and StackSets trusted access are enabled.
   - Lists every account with `ListAccounts`. Only `ACTIVE` accounts are used; suspended or closed accounts are skipped and listed.
   - Stops here with exit code 2 if anything blocks the run. Nothing has been changed at that point.
2. **Creates the central bucket in the management account (or the log account):**
   - SSE-S3 encryption (no KMS), all public access blocked, ACLs disabled, tagged `CreatedBy=cloudkeeper-cloudtrail-accelerator`.
   - Allows every active account of the organization in the bucket policy. CloudTrail may only write for those accounts, and only for the accelerator's trail.
3. **Deploys a temporary StackSet:**
   - Service-managed, to every account in the organization, at maximum concurrency (`MaxConcurrentPercentage` 100, `FailureTolerancePercentage` 100, `SOFT_FAILURE_TOLERANCE`).
   - Creates a temporary IAM role in every member account. The role is trusted only by the identity running the script, and may only create and configure the accelerator's trail.
4. **Creates the trail in every account at the same time:**
   - **Management account:** with your own credentials, as soon as the bucket exists, while the StackSet rolls out.
   - **Member accounts:** assumes the temporary role in each account as soon as it is ready, and creates the trail there.
   - The same trail everywhere: same name, multi-region, global service events, log file validation, management events read and write, pointing at the central bucket. No data events, no KMS, no CloudWatch Logs, no SNS.
   - Up to `--workers` accounts in parallel.
5. **Tidies up the bucket policy:**
   - Takes accounts whose trail couldn't be created back out of the bucket policy, so the policy only allows accounts that have the trail.
6. **Deletes the StackSet:**
   - Deletes all stack instances at maximum concurrency with `RetainStacks` false, which deletes the temporary role in every account, then deletes the StackSet. The trails and the bucket stay.
   - Retries stack instances that failed to delete once. Stacks are never retained: if one still can't be deleted, the StackSet is kept and reported so `--cleanup` can remove it and its role once the cause is fixed.
   - This step always runs, even after an error or Ctrl-C.
7. **Reports:**
   - Prints a table with the result of every account and a fix for every failure, and writes a JSON report with the phase timings.

When the bucket is in a member account, it is created through that account's temporary role as soon as the role is ready, and every trail, the management account's included, is created after it. Only that account's role gets S3 permissions, limited to the one bucket. [docs/how-it-works.md](docs/how-it-works.md) explains every step in detail.

## Resources Created

By default, every resource the accelerator creates has `accelerator` in its name. The bucket and trail names can be changed with `--bucket` and `--trail-name`; the StackSet and role names can't.

| Resource                                                                            | Name                                                                         | Where                               | Lifetime                                |
| ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- | ----------------------------------- | --------------------------------------- |
| S3 bucket                                                                           | `cloudkeeper-cloudtrail-accelerator-<log-account-id>-<region>`             | Management account (or log account) | Permanent                               |
| Bucket policy statements                                                            | `AWSCloudTrailAclCheck`, `AWSCloudTrailWrite`, `DenyInsecureTransport` | Management account (or log account) | Permanent                               |
| CloudTrail trail                                                                    | `cloudkeeper-cloudtrail-accelerator`                                       | Every active account                | Permanent                               |
| CloudFormation StackSet                                                             | `cloudkeeper-cloudtrail-accelerator-<run-id>`                              | Management account                  | **Deleted at the end of the run** |
| IAM role (in a stack `StackSet-cloudkeeper-cloudtrail-accelerator-<run-id>-<id>`) | `cloudkeeper-cloudtrail-accelerator-<run-id>`                              | Every active member account         | **Deleted at the end of the run** |
| JSON report                                                                         | `cloudtrail-accelerator-<run-id>.json`                                     | Your machine                        | Yours to keep                           |

`<run-id>` is the UTC start time, `YYYYMMDDHHMMSS`. Trails and buckets are also tagged `CreatedBy=cloudkeeper-cloudtrail-accelerator`.

## Limitations

- **One-time setup:** the script sets up the accounts that are in the organization when it runs. Accounts that join the organization later don't get a trail.
- **Management account only:** everything runs with an administrator profile of the management account.
- **About 1,250 accounts:** the bucket policy must stay under S3's 20 KB limit, and the list of account IDs is the largest part of it. A run that would exceed it is refused before anything changes.
- **One region, one bucket and one trail name:** every trail is multi-region, so all enabled regions are logged.
- **Management events only:** data events, Insights, CloudWatch Logs and SNS aren't configured.
- **No automatic removal:** the trails and the bucket stay after the run. To remove them, delete the trail in each account and then the bucket.

## Troubleshooting

- **Profile Not Found Error:** Ensure the AWS profile is correctly set up:
  ```sh
  aws configure list-profiles
  ```
- **Token has expired:** Log in again with `aws sso login --profile <profile>`.
- **The credentials are for account ..., but this organization's management account is ...:** The profile belongs to a member account. Use an administrator profile of the management account.
- **An account fails with `AccessDeniedException` or `RoleNotReady`:** An SCP blocks CloudFormation, IAM or CloudTrail in that account. Run `--check` to see which one. After fixing it, run the accelerator again: it is safe to run again, and existing accelerator trails are updated in place.
- **`TrailNotManaged`:** The account already has a trail with the same name that the accelerator didn't create. It is left alone; choose another `--trail-name`.
- **A StackSet was left behind after an interrupted run:** Run the accelerator with `--cleanup`.

[docs/troubleshooting.md](docs/troubleshooting.md) lists every error and warning with its fix, and explains debugging.

## Documentation

- [docs/how-it-works.md](docs/how-it-works.md): every step, SCPs and RCPs, least privilege, the trail and bucket settings, cost, FAQ
- [docs/output.md](docs/output.md): console output, JSON report, the state of the trail in every account
- [docs/troubleshooting.md](docs/troubleshooting.md): every error and warning, debugging, checking by hand, recovery

## License

MIT License

## Author

[**Viransh Bhardwaj**](https://github.com/CloudSpyV)
