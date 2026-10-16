#!/usr/bin/env python3
"""CloudTrail Accelerator: centralised CloudTrail without an organization trail.

A one-time script that gives every account in an AWS Organization its own
CloudTrail trail, all delivering to one S3 bucket in the management account (or
another log account you choose). For organizations where an organization trail
can't be used. It:

  1. creates the central S3 bucket
  2. allows every account from organizations:ListAccounts in the bucket policy
  3. creates the trail in the management account: management events, read and
     write, no KMS, no CloudWatch Logs
  4. deploys a StackSet at maximum concurrency that creates a temporary role in
     every member account
  5. assumes that role in every member account and creates the same trail
  6. deletes the StackSet at maximum concurrency, which deletes the roles

Full documentation: README.md and docs/ next to this file.

Modes (all run with credentials for the organization's management account):
  (default)   set up
  --check     read-only pre-flight checks against the organization
  --dry-run   read-only: print the plan, bucket policy and role template
  --cleanup   delete StackSets left behind by an interrupted run

What it leaves behind does not depend on the organization: ordinary per-account
trails (not an organization trail) and a bucket policy that names account IDs
(`aws:SourceAccount`), not the organization ID. Accounts, including the log
account, can later leave or change organizations and keep logging.

By default, every resource it creates has "accelerator" in its name.

Only boto3 is required.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import re
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from queue import Empty, Queue

if sys.version_info < (3, 8):  # pragma: no cover
    sys.exit("ERROR: Python 3.8 or newer is required.")

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:  # pragma: no cover
    sys.exit("ERROR: boto3 is not installed. Run: pip install -r requirements.txt")

__version__ = "1.0.0"

# Every resource the accelerator creates is named with this prefix (StackSet,
# role, and the bucket and trail unless --bucket / --trail-name choose other
# names), so it can be recognised by name as well as by tag.
NAME_PREFIX = "cloudkeeper-cloudtrail-accelerator"
DEFAULT_REGION = "us-east-1"
DEFAULT_TRAIL_NAME = NAME_PREFIX
DEFAULT_WORKERS = 20
DEFAULT_TIMEOUT_MINUTES = 60
STACKSET_NAME_PREFIX = NAME_PREFIX  # + "-<run-id>"
ROLE_NAME_PREFIX = NAME_PREFIX
EXTERNAL_ID_SUFFIX = NAME_PREFIX
# Trails and buckets the accelerator creates carry this tag. It only ever
# updates a trail that has it, so it never touches someone else's trail.
MANAGED_TAG_KEY, MANAGED_TAG_VALUE = "CreatedBy", NAME_PREFIX
TAGS = {MANAGED_TAG_KEY: MANAGED_TAG_VALUE}
POLL_INTERVAL_SECONDS = 5
# A new role is not immediately assumable everywhere even after CFN reports it CURRENT.
ASSUME_ROLE_RETRY_SECONDS = 90
# CreateTrail and friends can fail briefly right after the bucket policy is
# written or the role appears. These codes are retried for this long.
TRANSIENT_RETRY_SECONDS = 60
TRANSIENT_ERROR_CODES = frozenset({
    "InsufficientS3BucketPolicyException",
    "S3BucketDoesNotExistException",
    "AccessDenied",
    "AccessDeniedException",
})
BUCKET_POLICY_MAX_BYTES = 20 * 1024
POLICY_SIDS = ("AWSCloudTrailAclCheck", "AWSCloudTrailWrite", "DenyInsecureTransport")
# A region that is always enabled in each partition. IAM's API also lives
# there, so SCP region conditions see IAM calls as made in it.
PARTITION_HOME_REGION = {"aws": "us-east-1", "aws-us-gov": "us-gov-west-1", "aws-cn": "cn-north-1"}

EXIT_OK, EXIT_FAILED, EXIT_INVALID, EXIT_ABORTED, EXIT_INTERRUPTED = 0, 1, 2, 3, 130

BOTO_CONFIG = Config(retries={"max_attempts": 10, "mode": "adaptive"})

_READY_STATUSES = frozenset({"CURRENT", "SUCCEEDED"})
_TERMINAL_OP_STATUSES = frozenset({"SUCCEEDED", "FAILED", "STOPPED"})
_ACTIVE_OP_STATUSES = frozenset({"RUNNING", "STOPPING", "QUEUED"})

# What to do about each per-account error code. Shown in the summary and the
# JSON report; docs/troubleshooting.md explains each one in full.
_SCP_HINT = ("An SCP, RCP or permissions boundary denies a CloudTrail action in this account. "
             "Run --check to see which policy.")
_HOME_REGION_HINT = ("A trail with this name already exists with a different home region. "
                     "Re-run with that --region, or choose another --trail-name.")
_KMS_HINT = ("The bucket's default encryption is SSE-KMS and CloudTrail may not use the key. The accelerator "
             "doesn't manage KMS: switch the bucket to SSE-S3, or grant cloudtrail.amazonaws.com the key.")
_THROTTLE_HINT = "AWS kept throttling even after retries. Re-run with fewer --workers."
_EXPIRED_HINT = "The credentials expired during the run. Refresh them and run again; running again is safe."
ERROR_HINTS = {
    "AccessDenied": _SCP_HINT,
    "AccessDeniedException": _SCP_HINT,
    "AssumeRoleFailed": ("Could not assume the temporary role. An SCP or RCP may deny sts:AssumeRole into this "
                         "account (run --check), or the caller lacks sts:AssumeRole permission."),
    "RoleNotReady": ("The StackSet could not create the temporary role in this account; the error shows "
                     "CloudFormation's reason. An SCP denying cloudformation:* or iam:* there is the usual cause."),
    "InsufficientS3BucketPolicyException": (
        "CloudTrail can't write to the bucket. Either the account is missing from the bucket policy (re-run the "
        "accelerator), or a statement in the bucket policy or an RCP on the log account denies CloudTrail."),
    "S3BucketDoesNotExistException": "The central bucket doesn't exist (was it deleted?). Re-run the accelerator.",
    "MaximumNumberOfTrailsExceededException": (
        "The account already has 5 trails in --region. Delete an unused trail, or use another --region."),
    "TrailNotManaged": (
        "A trail with this name exists but wasn't created by the accelerator, so it was left alone. Use another "
        f"--trail-name, or tag that trail {MANAGED_TAG_KEY}={MANAGED_TAG_VALUE} to hand it over."),
    "TrailHomeRegionMismatch": _HOME_REGION_HINT,
    "InvalidHomeRegionException": _HOME_REGION_HINT,
    "OptInRequired": ("--region isn't enabled in this account, or the account isn't fully activated yet. "
                      "Enable the region (or wait) and re-run."),
    "SubscriptionRequiredException": ("The account isn't fully activated yet (a recently created account can "
                                      "take a while). Run again later."),
    "InsufficientEncryptionPolicyException": _KMS_HINT,
    "KmsException": _KMS_HINT,
    "ThrottlingException": _THROTTLE_HINT,
    "Throttling": _THROTTLE_HINT,
    "TooManyRequestsException": _THROTTLE_HINT,
    "ExpiredToken": _EXPIRED_HINT,
    "ExpiredTokenException": _EXPIRED_HINT,
    "RequestExpired": _EXPIRED_HINT,
    "InvalidClientTokenId": ("STS rejected the credentials for this account, usually because STS is disabled in "
                             "--region there. Use a default-enabled region such as us-east-1."),
    "RegionDisabledException": "STS is disabled in --region for this account. Use a default-enabled region.",
    "BucketNotReady": "The central bucket was never created, so no trail could be. See the log account's error.",
}
DEFAULT_HINT = "Unexpected AWS error; see the message. Re-run with --debug --workers 1 for a full API log."

_print_lock = threading.Lock()
_start = time.monotonic()
_debug = False


class AcceleratorError(Exception):
    """Bad input or an unmet precondition, found before anything changed (exit 2)."""


class RunAborted(Exception):
    """The run stopped part-way after it had started making changes (exit 3)."""


class AccountError(Exception):
    """A per-account failure detected by the accelerator itself, with an error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def log(msg: str) -> None:
    with _print_lock:
        print(f"[{time.monotonic() - _start:7.1f}s] {msg}", flush=True)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def error_code(exc: ClientError) -> str:
    return exc.response.get("Error", {}).get("Code", "")


def describe_error(exc: BaseException) -> str:
    if isinstance(exc, ClientError):
        message = exc.response.get("Error", {}).get("Message", "")
        return f"{exc.operation_name} failed: {error_code(exc)}: {message}"
    return f"{type(exc).__name__}: {exc}"


def paginate(client, method: str, key: str, **kwargs) -> list:
    items = []
    for page in client.get_paginator(method).paginate(**kwargs):
        items.extend(page.get(key, []))
    return items


_client_lock = threading.Lock()


def make_client(session, service: str, **kwargs):
    """`session.client(...)`, one at a time. Worker threads share the
    management account's session, and boto3 sessions aren't thread-safe for
    creating clients; the clients themselves are."""
    with _client_lock:
        return session.client(service, **kwargs)


def new_session(profile: str | None = None, region: str | None = None, credentials: dict | None = None):
    """A boto3 session; with --debug, every API call it makes is logged."""
    if credentials:
        session = boto3.Session(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
            region_name=region,
        )
    else:
        session = boto3.Session(profile_name=profile, region_name=region)
    if _debug:
        session.events.register("provide-client-params", _debug_before_call)
        session.events.register("after-call", _debug_after_call)
    return session


def _debug_before_call(model=None, params=None, **_):
    # API parameters as passed to boto3, before serialisation and signing: no credentials or headers.
    text = json.dumps(params, default=str)
    log(f"[debug] -> {model.service_model.service_name}:{model.name} "
        f"{text[:400] + '...' if len(text) > 400 else text}")


def _debug_after_call(model=None, http_response=None, parsed=None, **_):
    status = getattr(http_response, "status_code", "?")
    err = (parsed or {}).get("Error", {}).get("Code", "")
    log(f"[debug] <- {model.service_model.service_name}:{model.name} HTTP {status} {err}".rstrip())


def configure_debug() -> None:
    """Log every AWS API call (operation and parameters, never credentials)
    plus SDK retries, to stdout/stderr."""
    global _debug
    _debug = True
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[debug] %(name)s: %(message)s"))
    for name in ("botocore.retryhandler", "botocore.retries"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)


def print_table(headers: list[str], rows: list[list]) -> None:
    cells = [[str(c) for c in row] for row in rows]
    widths = [max(len(r[i]) for r in [headers] + cells) for i in range(len(headers))]
    with _print_lock:
        for row in [headers, ["-" * w for w in widths]] + cells:
            print("  " + "  ".join(c.ljust(w) for c, w in zip(row[:-1], widths)) + "  " + row[-1], flush=True)


# --------------------------------------------------------------------------- #
# Input validation
# --------------------------------------------------------------------------- #

_ACCOUNT_ID_RE = re.compile(r"^\d{12}$")
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_TRAIL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|[._-](?![._-])){1,126}[A-Za-z0-9]$")
_IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def validate_args(args) -> None:
    problems = []
    if args.cleanup and (args.check or args.dry_run):
        problems.append("--cleanup can't be combined with --check or --dry-run")
    if args.log_account_id and not _ACCOUNT_ID_RE.match(args.log_account_id):
        problems.append(f"--log-account-id {args.log_account_id!r} is not a 12-digit AWS account ID")
    if args.bucket:
        b = args.bucket
        if (not _BUCKET_RE.match(b) or ".." in b or _IP_RE.match(b) or b.startswith(("xn--", "sthree-"))
                or b.endswith(("-s3alias", "--ol-s3"))):
            problems.append(f"--bucket {b!r} is not a valid S3 bucket name (3-63 lowercase letters, digits, "
                            "dots and hyphens, starting and ending with a letter or digit)")
    if not _TRAIL_RE.match(args.trail_name) or _IP_RE.match(args.trail_name):
        problems.append(f"--trail-name {args.trail_name!r} is not a valid trail name (3-128 letters, digits, "
                        "'.', '_' or '-', starting and ending with a letter or digit, no two symbols in a row)")
    if not 1 <= args.workers <= 100:
        problems.append("--workers must be between 1 and 100")
    if not 5 <= args.timeout_minutes <= 720:
        problems.append("--timeout-minutes must be between 5 and 720")
    if problems:
        raise AcceleratorError("Invalid arguments:\n  - " + "\n  - ".join(problems))


# --------------------------------------------------------------------------- #
# Organization
# --------------------------------------------------------------------------- #

def get_org_context(session) -> dict:
    """Caller, partition, management account, org root and all accounts.
    Refuses to run from anywhere but the management account."""
    caller = make_client(session, "sts", config=BOTO_CONFIG).get_caller_identity()
    account, arn = caller["Account"], caller["Arn"]
    partition = arn.split(":")[1]
    if arn.endswith(":root"):
        raise AcceleratorError(
            f"You are using the root user of account {account}. AWS doesn't let the root user assume roles, "
            "which the accelerator needs. Use an IAM role or user in the management account instead "
            "(for example an IAM Identity Center administrator role).")
    org = make_client(session, "organizations", config=BOTO_CONFIG)
    try:
        organization = org.describe_organization()["Organization"]
    except ClientError as exc:
        if error_code(exc) == "AWSOrganizationsNotInUseException":
            raise AcceleratorError(f"Account {account} is not part of an AWS Organization.") from exc
        if error_code(exc) in ("AccessDenied", "AccessDeniedException"):
            raise AcceleratorError(
                f"{arn} may not call organizations:DescribeOrganization. Use credentials for the "
                "organization's management account with Organizations read access.") from exc
        raise
    management = organization["MasterAccountId"]
    if account != management:
        raise AcceleratorError(
            f"The credentials are for account {account}, but this organization's management account is "
            f"{management}. Run the accelerator with an administrator profile for {management}.")
    root_id = org.list_roots()["Roots"][0]["Id"]
    accounts = paginate(org, "list_accounts", "Accounts")
    return {"caller_arn": arn, "partition": partition, "management": management,
            "org_id": organization["Id"], "feature_set": organization.get("FeatureSet", "ALL"),
            "root_id": root_id, "accounts": accounts}


def account_state(account: dict) -> str:
    # `State` replaced the deprecated `Status`; accept whichever the SDK returns.
    return account.get("State") or account.get("Status") or ""


def principal_arn_patterns(caller_arn: str) -> list[str]:
    """`aws:PrincipalArn` patterns that match only the identity running the
    script, so the temporary roles trust that one role or user rather than
    every principal in the management account. The caller ARN doesn't show an
    IAM path (IAM Identity Center roles have one), hence the `*/` pattern; role
    and user names are unique per account, so it can't match anything else."""
    _, partition, _, _, account, resource = caller_arn.split(":", 5)
    kind, _, rest = resource.partition("/")
    if kind == "assumed-role":
        name = rest.split("/")[0]
        return [f"arn:{partition}:iam::{account}:role/{name}", f"arn:{partition}:iam::{account}:role/*/{name}"]
    if kind == "user":
        name = rest.rsplit("/", 1)[-1]
        return [f"arn:{partition}:iam::{account}:user/{name}", f"arn:{partition}:iam::{account}:user/*/{name}"]
    return [caller_arn]  # a federated user is matched by its own ARN


def check_region(session, region: str, partition: str) -> str | None:
    """Fail on a region that doesn't exist or isn't enabled in the management
    account. Returns its opt-in status, or None if it couldn't be read."""
    known = session.get_available_regions("cloudtrail", partition_name=partition)
    if known and region not in known:
        raise AcceleratorError(
            f"--region {region} is not a CloudTrail region in partition {partition}. If it is a new region, "
            f"upgrade boto3 (pip install -U boto3). Known regions: {', '.join(sorted(known))}")
    try:
        ec2 = make_client(session, "ec2", region_name=PARTITION_HOME_REGION.get(partition, DEFAULT_REGION),
                             config=BOTO_CONFIG)
        regions = ec2.describe_regions(AllRegions=True, RegionNames=[region])["Regions"]
    except ClientError:
        return None  # no ec2:DescribeRegions; a disabled region still fails loudly later
    status = regions[0].get("OptInStatus") if regions else None
    if status == "not-opted-in":
        raise AcceleratorError(
            f"--region {region} is an opt-in region that is not enabled in the management account. "
            "Enable it in every account (Account settings > AWS Regions) or use a default-enabled region.")
    return status


def stacksets_trusted_access(session) -> bool | None:
    try:
        services = paginate(make_client(session, "organizations", config=BOTO_CONFIG),
                            "list_aws_service_access_for_organization", "EnabledServicePrincipals")
    except ClientError:
        return None
    return any(s["ServicePrincipal"] == "member.org.stacksets.cloudformation.amazonaws.com" for s in services)


# Every API the run itself calls with the management account's credentials.
# Checked with the IAM policy simulator before anything changes. An
# administrator identity (AdministratorAccess) has all of them.
RUNNER_ACTIONS = (
    "organizations:DescribeOrganization",
    "organizations:ListRoots",
    "organizations:ListAccounts",
    "organizations:ListAWSServiceAccessForOrganization",
    "cloudformation:CreateStackSet",
    "cloudformation:CreateStackInstances",
    "cloudformation:DescribeStackSetOperation",
    "cloudformation:ListStackSetOperations",
    "cloudformation:ListStackInstances",
    "cloudformation:DeleteStackInstances",
    "cloudformation:DeleteStackSet",
    "cloudformation:ListStackSets",
    "cloudformation:TagResource",
    "sts:AssumeRole",
    "cloudtrail:DescribeTrails",
    "cloudtrail:CreateTrail",
    "cloudtrail:UpdateTrail",
    "cloudtrail:PutEventSelectors",
    "cloudtrail:StartLogging",
    "cloudtrail:AddTags",
    "cloudtrail:ListTags",
    "cloudtrail:GetTrailStatus",
)
# Also needed when the management account is the log account.
RUNNER_BUCKET_ACTIONS = (
    "s3:CreateBucket",
    "s3:ListBucket",
    "s3:GetBucketPolicy",
    "s3:PutBucketPolicy",
    "s3:GetEncryptionConfiguration",
    "s3:PutEncryptionConfiguration",
    "s3:PutBucketPublicAccessBlock",
    "s3:PutBucketOwnershipControls",
    "s3:PutBucketTagging",
)


def caller_policy_source(session, caller_arn: str) -> str | None:
    """IAM ARN whose policies apply to the caller: the role behind an
    assumed-role session (path included, e.g. IAM Identity Center roles), or
    the IAM user. None for other principals."""
    resource = caller_arn.split(":", 5)[5]
    if resource.startswith("assumed-role/"):
        role_name = resource.split("/")[1]
        return make_client(session, "iam", config=BOTO_CONFIG).get_role(RoleName=role_name)["Role"]["Arn"]
    if resource.startswith("user/"):
        return caller_arn
    return None


def missing_permissions(session, caller_arn: str, actions) -> list[str] | None:
    """Actions the caller's identity policies don't allow, per the IAM policy
    simulator. None if that can't be determined (the simulator itself isn't
    allowed, or the principal type isn't supported)."""
    try:
        source = caller_policy_source(session, caller_arn)
        if not source:
            return None
        iam = make_client(session, "iam", config=BOTO_CONFIG)
        actions = list(actions)
        missing = []
        for i in range(0, len(actions), 25):
            results = paginate(iam, "simulate_principal_policy", "EvaluationResults",
                               PolicySourceArn=source, ActionNames=actions[i:i + 25])
            missing += [r["EvalActionName"] for r in results if r.get("EvalDecision") != "allowed"]
        return sorted(missing)
    except ClientError:
        return None


# --------------------------------------------------------------------------- #
# S3 bucket
# --------------------------------------------------------------------------- #

def read_bucket_policy(s3, bucket: str) -> dict | None:
    """The bucket's current policy, or None if the bucket or policy doesn't exist."""
    try:
        return json.loads(s3.get_bucket_policy(Bucket=bucket)["Policy"])
    except ClientError as exc:
        if error_code(exc) in ("NoSuchBucketPolicy", "NoSuchBucket"):
            return None
        raise


def bucket_encryption(s3, bucket: str) -> str | None:
    """Default encryption algorithm ('AES256', 'aws:kms', ...), None if unset, 'unknown' if unreadable."""
    try:
        rules = s3.get_bucket_encryption(Bucket=bucket)["ServerSideEncryptionConfiguration"]["Rules"]
    except ClientError as exc:
        if error_code(exc) == "ServerSideEncryptionConfigurationNotFoundError":
            return None
        return "unknown"
    return (rules[0].get("ApplyServerSideEncryptionByDefault") or {}).get("SSEAlgorithm") if rules else None


def inspect_bucket(s3, bucket: str) -> tuple[str, dict | None, str | None]:
    """('missing' | 'exists' | 'taken', policy, encryption) as seen by the bucket owner's credentials."""
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as exc:
        if error_code(exc) in ("404", "NoSuchBucket", "NotFound"):
            return "missing", None, None
        if error_code(exc) in ("403", "Forbidden", "AccessDenied"):
            return "taken", None, None
        raise
    return "exists", read_bucket_policy(s3, bucket), bucket_encryption(s3, bucket)


def ensure_bucket(s3, bucket: str, region: str) -> bool:
    """Create the bucket if it doesn't exist. Only a bucket created here gets
    its settings changed; an existing bucket keeps its own settings (only its
    policy is merged later). Returns True if the bucket was created."""
    state, _, encryption = inspect_bucket(s3, bucket)
    if state == "taken":
        raise AcceleratorError(f"Bucket name {bucket} is already owned by another AWS account. Pass another --bucket.")
    if state == "exists":
        log(f"Bucket s3://{bucket} already exists in the log account; reusing it (its settings are left as they are)")
        if encryption and encryption.startswith("aws:kms"):
            log(f"  [warn] s3://{bucket} uses {encryption} default encryption. {_KMS_HINT}")
        return False
    params = {"Bucket": bucket}
    if region != "us-east-1":
        params["CreateBucketConfiguration"] = {"LocationConstraint": region}
    s3.create_bucket(**params)
    s3.get_waiter("bucket_exists").wait(Bucket=bucket)
    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_ownership_controls(
        Bucket=bucket,
        OwnershipControls={"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]},
    )
    # SSE-S3 only: no KMS key to manage or grant across accounts.
    s3.put_bucket_encryption(
        Bucket=bucket,
        ServerSideEncryptionConfiguration={
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
        },
    )
    s3.put_bucket_tagging(
        Bucket=bucket,
        Tagging={"TagSet": [{"Key": k, "Value": v} for k, v in TAGS.items()]},
    )
    log(f"Created bucket s3://{bucket} in {region} (SSE-S3, public access blocked, ACLs disabled)")
    return True


def onboarded_accounts(policy: dict | None) -> set[str]:
    """Accounts allowed by our write statement: the accounts that have, or had,
    the accelerator's trail."""
    for stmt in (policy or {}).get("Statement", []):
        if stmt.get("Sid") == "AWSCloudTrailWrite":
            ids = stmt.get("Condition", {}).get("StringEquals", {}).get("aws:SourceAccount", [])
            return {ids} if isinstance(ids, str) else set(ids)
    return set()


def trail_arn_pattern(partition: str, region: str, trail_name: str) -> str:
    """The accelerator's trail in any account: what `aws:SourceArn` must match."""
    return f"arn:{partition}:cloudtrail:{region}:*:trail/{trail_name}"


def build_bucket_policy(bucket: str, account_ids, partition: str, region: str, trail_name: str,
                        existing: dict | None = None, exclude=()) -> dict:
    """CloudTrail delivery policy allowing every listed account.

    Least privilege: CloudTrail may only write for the listed accounts
    (`aws:SourceAccount`) and only for the accelerator's own trail
    (`aws:SourceArn`, which CloudTrail sets to the trail's ARN). The account
    list appears exactly once, in one `AWSLogs/*` write statement; that keeps
    the policy within S3's 20 KB limit for about 1,250 accounts, where one
    statement per account would hit it at a few hundred.

    If the bucket already has this policy (the script was run before),
    accounts it lists are kept: an account that has left the organization since
    still has its trail, and would silently stop delivering if it were dropped.
    Statements from an existing policy are kept unless they reuse one of our
    Sids. `exclude` removes accounts (see remove_from_policy); with no account
    left, the two CloudTrail statements go and only the TLS-only statement stays.
    """
    accounts = sorted((set(account_ids) | onboarded_accounts(existing)) - set(exclude))
    arn = f"arn:{partition}:s3:::{bucket}"
    source_arn = {"aws:SourceArn": trail_arn_pattern(partition, region, trail_name)}
    ours = [
        {
            "Sid": "AWSCloudTrailAclCheck",
            "Effect": "Allow",
            "Principal": {"Service": "cloudtrail.amazonaws.com"},
            "Action": "s3:GetBucketAcl",
            "Resource": arn,
            "Condition": {"ArnLike": source_arn},
        },
        {
            "Sid": "AWSCloudTrailWrite",
            "Effect": "Allow",
            "Principal": {"Service": "cloudtrail.amazonaws.com"},
            "Action": "s3:PutObject",
            "Resource": f"{arn}/AWSLogs/*",
            "Condition": {
                "StringEquals": {
                    "s3:x-amz-acl": "bucket-owner-full-control",
                    "aws:SourceAccount": accounts,
                },
                "ArnLike": source_arn,
            },
        },
    ] if accounts else []
    ours.append({
        "Sid": "DenyInsecureTransport",
        "Effect": "Deny",
        "Principal": "*",
        "Action": "s3:*",
        "Resource": [arn, f"{arn}/*"],
        "Condition": {"Bool": {"aws:SecureTransport": "false"}},
    })
    kept = [s for s in (existing or {}).get("Statement", []) if s.get("Sid") not in POLICY_SIDS]
    return {"Version": "2012-10-17", "Statement": kept + ours}


def policy_size(policy: dict) -> int:
    return len(json.dumps(policy, separators=(",", ":")).encode())


def setup_central_bucket(session, bucket: str, region: str, partition: str, trail_name: str,
                         account_ids: list[str]) -> set[str]:
    """Create the bucket if needed, then merge our statements into its policy.
    `session` must be for the log account. Returns the accounts the policy
    already listed before this run (none on a first run)."""
    s3 = make_client(session, "s3", region_name=region, config=BOTO_CONFIG)
    ensure_bucket(s3, bucket, region)
    existing = read_bucket_policy(s3, bucket)
    previous = onboarded_accounts(existing)
    document = build_bucket_policy(bucket, account_ids, partition, region, trail_name, existing)
    if policy_size(document) > BUCKET_POLICY_MAX_BYTES:
        raise AcceleratorError(
            f"The bucket policy would be {policy_size(document)} bytes, over S3's 20 KB limit "
            f"({len(onboarded_accounts(document))} accounts). See 'Limitations' in the README.")
    s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps(document, separators=(",", ":")))
    allowed = onboarded_accounts(document)
    retained = len(allowed - set(account_ids))
    log(f"Bucket policy allows CloudTrail delivery from {len(allowed)} accounts"
        + (f" ({retained} no longer in the organization, kept)" if retained else ""))
    return previous


def remove_from_policy(session, bucket: str, region: str, partition: str, trail_name: str,
                       accounts: list[str]) -> None:
    """Take accounts whose trail couldn't be created back out of the bucket policy.

    Every account has to be in the policy before its trail can be created.
    Removing the ones that failed afterwards keeps the policy to the accounts
    that actually have the trail (least privilege).
    """
    s3 = make_client(session, "s3", region_name=region, config=BOTO_CONFIG)
    existing = read_bucket_policy(s3, bucket)
    document = build_bucket_policy(bucket, [], partition, region, trail_name, existing, exclude=accounts)
    s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps(document, separators=(",", ":")))


# --------------------------------------------------------------------------- #
# CloudTrail
# --------------------------------------------------------------------------- #

def retry_transient(call, *args, **kwargs):
    """Retry `call` on errors caused by propagation delay, not by real
    misconfiguration: a just-written bucket policy that CloudTrail can't see
    yet, a just-created bucket, or a just-created role whose policy hasn't
    reached the service. Anything still failing after
    TRANSIENT_RETRY_SECONDS is a real error and is raised."""
    deadline = time.time() + TRANSIENT_RETRY_SECONDS
    while True:
        try:
            return call(*args, **kwargs)
        except ClientError as exc:
            if error_code(exc) not in TRANSIENT_ERROR_CODES or time.time() > deadline:
                raise
            _sleep(5)


def find_trail(cloudtrail, trail_name: str) -> dict | None:
    """The trail named `trail_name` visible in this client's region (including
    a multi-region trail whose home is elsewhere), or None."""
    trails = retry_transient(cloudtrail.describe_trails, includeShadowTrails=True)["trailList"]
    return next((t for t in trails if t.get("Name") == trail_name), None)


def assert_managed(cloudtrail, trail: dict, region: str) -> None:
    """Raise AccountError unless `trail` is one the accelerator created in `region`."""
    name, home = trail.get("Name"), trail.get("HomeRegion")
    if home and home != region:
        raise AccountError("TrailHomeRegionMismatch",
                           f"A trail named {name} already exists with home region {home}, not {region}.")
    if trail.get("IsOrganizationTrail"):
        raise AccountError("TrailNotManaged", f"{name} is an organization trail; the accelerator won't change it.")
    tag_lists = retry_transient(cloudtrail.list_tags, ResourceIdList=[trail["TrailARN"]])["ResourceTagList"]
    tags = tag_lists[0].get("TagsList", []) if tag_lists else []
    if not any(t.get("Key") == MANAGED_TAG_KEY and t.get("Value") == MANAGED_TAG_VALUE for t in tags):
        raise AccountError("TrailNotManaged",
                           f"A trail named {name} already exists and was not created by the accelerator.")


def ensure_trail(cloudtrail, trail_name: str, bucket: str, region: str) -> dict:
    """Create (or update, if the accelerator created it) a multi-region trail
    that logs management read+write events to `bucket`, start logging, and
    return its state. No KMS, no CloudWatch Logs."""
    settings = {
        "Name": trail_name,
        "S3BucketName": bucket,
        "IsMultiRegionTrail": True,
        "IncludeGlobalServiceEvents": True,
        "EnableLogFileValidation": True,
    }
    try:
        arn = retry_transient(
            cloudtrail.create_trail,
            **settings,
            TagsList=[{"Key": k, "Value": v} for k, v in TAGS.items()],
        )["TrailARN"]
        action = "created"
    except ClientError as exc:
        if error_code(exc) != "TrailAlreadyExistsException":
            raise
        existing = find_trail(cloudtrail, trail_name)
        if existing:
            assert_managed(cloudtrail, existing, region)
        arn = retry_transient(cloudtrail.update_trail, **settings)["TrailARN"]
        action = "updated"
    retry_transient(
        cloudtrail.put_event_selectors,
        TrailName=trail_name,
        EventSelectors=[
            {"ReadWriteType": "All", "IncludeManagementEvents": True, "DataResources": []}
        ],
    )
    retry_transient(cloudtrail.start_logging, Name=trail_name)
    status = retry_transient(cloudtrail.get_trail_status, Name=arn)
    return {"trail_arn": arn, "action": action, "is_logging": bool(status.get("IsLogging")),
            "latest_delivery_error": status.get("LatestDeliveryError") or None}


# --------------------------------------------------------------------------- #
# StackSet: temporary role in member accounts
# --------------------------------------------------------------------------- #

def inline_policy(name: str, statements: list[dict]) -> dict:
    return {"PolicyName": name, "PolicyDocument": {"Version": "2012-10-17", "Statement": statements}}


def trail_statements(trail_name: str, actions: list[str]) -> list[dict]:
    """`actions` on the accelerator's trail in this account and region only
    (`${AWS::Region}` is --region, where the StackSet deploys the stack).
    DescribeTrails supports no resource-level permissions, so it gets `*`; it
    only reads trail settings."""
    return [
        {"Sid": "FindTrails", "Effect": "Allow", "Action": "cloudtrail:DescribeTrails", "Resource": "*"},
        {"Sid": "AcceleratorTrailOnly", "Effect": "Allow", "Action": actions,
         "Resource": {"Fn::Sub": "arn:${AWS::Partition}:cloudtrail:${AWS::Region}:${AWS::AccountId}:trail/"
                                 + trail_name}},
    ]


def bucket_statements(bucket: str, partition: str, bucket_actions: list[str],
                      object_actions: list[str] = ()) -> list[dict]:
    """S3 `bucket_actions` on the central bucket and `object_actions` on its
    objects, nothing else in S3."""
    arn = f"arn:{partition}:s3:::{bucket}"
    statements = [{"Sid": "CentralBucketOnly", "Effect": "Allow", "Action": bucket_actions, "Resource": arn}]
    if object_actions:
        statements.append({"Sid": "CentralBucketObjectsOnly", "Effect": "Allow", "Action": list(object_actions),
                           "Resource": f"{arn}/*"})
    return statements


def temporary_role_template(description: str, management_account_id: str, role_name: str,
                            principal_arns: list[str], partition: str, policies: list,
                            log_account_policy: dict | None = None, log_account_id: str | None = None) -> str:
    """CloudFormation template for a temporary role in each member account.

    Trust is as narrow as AssumeRole allows: only the identity running the
    script (`aws:PrincipalArn`), from the management account, and only with
    this account's ExternalId. `log_account_policy` is attached only in the
    member log account; a CloudFormation condition keeps it off every other
    account."""
    policies = list(policies)
    conditions = {}
    if log_account_policy and log_account_id and log_account_id != management_account_id:
        conditions["IsLogAccount"] = {"Fn::Equals": [{"Ref": "AWS::AccountId"}, log_account_id]}
        policies.append({"Fn::If": ["IsLogAccount", log_account_policy, {"Ref": "AWS::NoValue"}]})
    template = {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": description,
        **({"Conditions": conditions} if conditions else {}),
        "Resources": {
            "AcceleratorRole": {
                "Type": "AWS::IAM::Role",
                "Properties": {
                    "RoleName": role_name,
                    "AssumeRolePolicyDocument": {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Principal": {"AWS": f"arn:{partition}:iam::{management_account_id}:root"},
                                "Action": "sts:AssumeRole",
                                "Condition": {
                                    "StringEquals": {
                                        "sts:ExternalId": {
                                            "Fn::Sub": "${AWS::AccountId}-" + EXTERNAL_ID_SUFFIX
                                        }
                                    },
                                    "ArnLike": {"aws:PrincipalArn": principal_arns},
                                },
                            }
                        ],
                    },
                    "Policies": policies,
                    "Tags": [{"Key": k, "Value": v} for k, v in TAGS.items()],
                },
            }
        },
    }
    return json.dumps(template)


# What the setup role may do: exactly the calls ensure_trail() makes, on the
# accelerator's trail only, plus (member log account only) the calls
# ensure_bucket() / setup_central_bucket() make, on the central bucket only.
SETUP_TRAIL_ACTIONS = [
    "cloudtrail:CreateTrail",
    "cloudtrail:AddTags",
    "cloudtrail:ListTags",
    "cloudtrail:UpdateTrail",
    "cloudtrail:PutEventSelectors",
    "cloudtrail:StartLogging",
    "cloudtrail:GetTrailStatus",
]
SETUP_BUCKET_ACTIONS = [
    "s3:CreateBucket",
    "s3:ListBucket",
    "s3:GetBucketPolicy",
    "s3:PutBucketPolicy",
    "s3:GetEncryptionConfiguration",
    "s3:PutEncryptionConfiguration",
    "s3:PutBucketPublicAccessBlock",
    "s3:PutBucketOwnershipControls",
    "s3:PutBucketTagging",
]


def role_template(management_account_id: str, role_name: str, log_account_id: str, bucket: str,
                  partition: str, trail_name: str, principal_arns: list[str]) -> str:
    """The setup run's temporary role: create and configure the accelerator's
    trail, and in a member log account also set up the central bucket."""
    return temporary_role_template(
        f"{NAME_PREFIX}: temporary trail setup role, deleted at the end of the run",
        management_account_id, role_name, principal_arns, partition,
        [inline_policy(f"{NAME_PREFIX}-trail-setup", trail_statements(trail_name, SETUP_TRAIL_ACTIONS))],
        inline_policy(f"{NAME_PREFIX}-bucket-setup", bucket_statements(bucket, partition, SETUP_BUCKET_ACTIONS)),
        log_account_id,
    )


def _supports_concurrency_mode(cfn) -> bool:
    """`ConcurrencyMode` needs botocore >= 1.32. An older SDK rejects it
    client-side with ParamValidationError, which would escape the ClientError
    handling in teardown and strand the role, so check the model first."""
    try:
        prefs = (
            cfn.meta.service_model.operation_model("DeleteStackInstances")
            .input_shape.members["OperationPreferences"]
        )
        return "ConcurrencyMode" in prefs.members
    except Exception:
        return False


def operation_preferences(cfn) -> dict:
    """Maximum concurrency for both create and delete, same as Preflight.

    Under STRICT_FAILURE_TOLERANCE, CloudFormation caps concurrency at
    FailureToleranceCount + 1 whatever MaxConcurrentPercentage says, so
    tolerance has to be 100% too. SOFT mode stops a few failing accounts from
    slowing the rest of the batch. We never rely on the operation's own status:
    success is judged per account.
    """
    prefs = {"MaxConcurrentPercentage": 100, "FailureTolerancePercentage": 100}
    if _supports_concurrency_mode(cfn):
        prefs["ConcurrencyMode"] = "SOFT_FAILURE_TOLERANCE"
    return prefs


def deployment_targets(root_id: str, accounts: list[str] | None) -> dict:
    """The whole organization, or only `accounts` within it."""
    if accounts is None:
        return {"OrganizationalUnitIds": [root_id]}
    return {"OrganizationalUnitIds": [root_id], "Accounts": sorted(accounts),
            "AccountFilterType": "INTERSECTION"}


def create_stackset(cfn, stackset_name: str, template: str,
                    description: str = f"{NAME_PREFIX}: temporary role, deleted when the run ends") -> None:
    cfn.create_stack_set(
        StackSetName=stackset_name,
        Description=description,
        TemplateBody=template,
        PermissionModel="SERVICE_MANAGED",
        AutoDeployment={"Enabled": False},
        Capabilities=["CAPABILITY_NAMED_IAM"],
        Tags=[{"Key": k, "Value": v} for k, v in TAGS.items()],
        CallAs="SELF",
    )


def add_stack_instances(cfn, stackset_name: str, root_id: str, accounts: list[str] | None,
                        region: str) -> str:
    return cfn.create_stack_instances(
        StackSetName=stackset_name,
        DeploymentTargets=deployment_targets(root_id, accounts),
        Regions=[region],
        OperationPreferences=operation_preferences(cfn),
        CallAs="SELF",
    )["OperationId"]


def wait_for_operation(cfn, stackset_name: str, operation_id: str, timeout: float) -> str:
    deadline = time.time() + timeout
    status = ""
    while time.time() < deadline:
        try:
            status = cfn.describe_stack_set_operation(
                StackSetName=stackset_name, OperationId=operation_id, CallAs="SELF"
            )["StackSetOperation"]["Status"]
        except ClientError as exc:
            log(f"  [warn] {describe_error(exc)}")
        if status in _TERMINAL_OP_STATUSES:
            return status
        _sleep(POLL_INTERVAL_SECONDS)
    return status or "TIMED_OUT"


def wait_for_active_operations(cfn, stackset_name: str, timeout: float) -> None:
    """Wait until no operation on the StackSet is running or queued."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        ops = paginate(cfn, "list_stack_set_operations", "Summaries", StackSetName=stackset_name, CallAs="SELF")
        if not any(o.get("Status") in _ACTIVE_OP_STATUSES for o in ops):
            return
        _sleep(POLL_INTERVAL_SECONDS)


def list_instances(cfn, stackset_name: str) -> list[dict]:
    return paginate(cfn, "list_stack_instances", "Summaries", StackSetName=stackset_name, CallAs="SELF")


def instance_reason(inst: dict) -> str:
    detailed = (inst.get("StackInstanceStatus") or {}).get("DetailedStatus")
    status = detailed or inst.get("Status") or ""
    return f"{status}: {inst.get('StatusReason', '')}".strip(": ")


class InstancePoller:
    """Streams accounts onto a queue as soon as their stack instance is
    CURRENT, so trails get created while CloudFormation is still rolling out.

    It follows every create operation added with `add_operation` and stops once
    `close()` has been called and all of them have finished (or on timeout),
    always ending the queue with a None sentinel. `idle` is True when every
    operation so far has finished and its accounts are already queued.
    `reasons` keeps the latest CloudFormation status reason for accounts that
    never became ready.
    """

    def __init__(self, cfn, stackset_name: str, timeout: float):
        self.cfn = cfn
        self.stackset_name = stackset_name
        self.deadline = time.time() + timeout
        self.queue: "Queue[str | None]" = Queue()
        self.reasons: dict[str, str] = {}
        self.idle = False
        self.timed_out = False
        self._ops: list[str] = []
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._enqueued: set[str] = set()
        self._stopped = False
        self._thread = threading.Thread(target=self._run, name="stackset-poller", daemon=True)

    def add_operation(self, operation_id: str) -> None:
        with self._lock:
            self._ops.append(operation_id)
            self.idle = False

    def close(self) -> None:
        """No more operations will be added."""
        self._closed.set()

    def stop(self) -> None:
        """Stop polling now (the run is ending early)."""
        self._stopped = True
        self._closed.set()

    def start(self) -> None:
        self._thread.start()

    def join(self) -> None:
        self._thread.join()

    def _sweep(self) -> None:
        try:
            instances = list_instances(self.cfn, self.stackset_name)
        except ClientError as exc:
            log(f"  [warn] {describe_error(exc)}")
            return
        for inst in instances:
            acct = inst.get("Account")
            if not acct or acct in self._enqueued:
                continue
            detailed = (inst.get("StackInstanceStatus") or {}).get("DetailedStatus")
            if (detailed or inst.get("Status")) in _READY_STATUSES:
                self.queue.put(acct)
                self._enqueued.add(acct)
            else:
                self.reasons[acct] = instance_reason(inst)

    def _all_finished(self) -> bool:
        with self._lock:
            ops = list(self._ops)
        for op in ops:
            try:
                status = self.cfn.describe_stack_set_operation(
                    StackSetName=self.stackset_name, OperationId=op, CallAs="SELF"
                )["StackSetOperation"]["Status"]
            except ClientError as exc:
                log(f"  [warn] {describe_error(exc)}")
                return False
            if status not in _TERMINAL_OP_STATUSES:
                return False
        return True

    def _run(self) -> None:
        try:
            while time.time() < self.deadline:
                if self._stopped:
                    return
                # Order matters: read `closed`, then operation status, then
                # sweep. An operation added before close() is then always
                # checked, and a finished operation's ready accounts are always
                # queued before `idle` says so.
                closed = self._closed.is_set()
                finished = self._all_finished()
                self._sweep()
                with self._lock:
                    self.idle = finished
                if closed and finished:
                    log("StackSet rollout finished")
                    return
                _sleep(POLL_INTERVAL_SECONDS)
            self.timed_out = True
            log("[warn] Timed out waiting for the StackSet rollout; raise --timeout-minutes for large organizations")
        finally:
            self.queue.put(None)


def delete_stackset(cfn, stackset_name: str, root_id: str, region: str, timeout: float) -> dict:
    """Delete every stack instance at maximum concurrency, then the StackSet.

    RetainStacks is always False: deleting a stack instance deletes its stack,
    and with it the temporary role. Trails and the bucket aren't part of any
    stack, so they stay. Instances that fail to delete get one more attempt,
    since most such failures are transient. If some still can't be deleted, the
    StackSet is kept rather than detaching them (which would leave their roles
    behind): they are returned in `instances_left`, and --cleanup finishes the
    job once the cause is fixed.
    """
    result = {"deleted": False, "instances_left": {}}
    log(f"Tearing down StackSet {stackset_name} (stack instances first, at max concurrency)")
    try:
        # Instances can't be deleted while an operation is still running
        # (e.g. after Ctrl-C), so let it finish first.
        wait_for_active_operations(cfn, stackset_name, timeout)
        remaining = list_instances(cfn, stackset_name)
        for attempt in (1, 2):
            if not remaining:
                break
            if attempt == 2:
                log(f"  {len(remaining)} stack instance(s) failed to delete; retrying them once")
            accounts = sorted({i["Account"] for i in remaining})
            op_id = cfn.delete_stack_instances(
                StackSetName=stackset_name,
                DeploymentTargets=deployment_targets(root_id, accounts),
                Regions=[region],
                RetainStacks=False,
                OperationPreferences=operation_preferences(cfn),
                CallAs="SELF",
            )["OperationId"]
            status = wait_for_operation(cfn, stackset_name, op_id, timeout)
            log(f"Stack instance deletion finished: {status}")
            remaining = list_instances(cfn, stackset_name)
        if remaining:
            result["instances_left"] = {i["Account"]: instance_reason(i) for i in remaining}
            log(f"  [error] {len(remaining)} stack instance(s) could not be deleted; StackSet {stackset_name} is "
                "kept so their roles can still be removed with --cleanup")
            return result
    except ClientError as exc:
        if error_code(exc) == "StackSetNotFoundException":
            result["deleted"] = True
            return result
        log(f"  [warn] {describe_error(exc)}")

    try:
        cfn.delete_stack_set(StackSetName=stackset_name, CallAs="SELF")
        log(f"Deleted StackSet {stackset_name}")
        result["deleted"] = True
    except ClientError as exc:
        if error_code(exc) == "StackSetNotFoundException":
            result["deleted"] = True
        else:
            log(f"  [error] {describe_error(exc)}")
    return result


def leftover_stacksets(cfn) -> list[str]:
    return sorted(s["StackSetName"] for s in paginate(cfn, "list_stack_sets", "Summaries",
                                                       Status="ACTIVE", CallAs="SELF")
                  if s["StackSetName"].startswith(STACKSET_NAME_PREFIX))


# --------------------------------------------------------------------------- #
# Member accounts
# --------------------------------------------------------------------------- #

def assume_member_role(session, account_id: str, role_name: str, partition: str, region: str):
    sts = make_client(session, "sts", config=BOTO_CONFIG)
    deadline = time.time() + ASSUME_ROLE_RETRY_SECONDS
    while True:
        try:
            creds = sts.assume_role(
                RoleArn=f"arn:{partition}:iam::{account_id}:role/{role_name}",
                RoleSessionName=NAME_PREFIX,
                ExternalId=f"{account_id}-{EXTERNAL_ID_SUFFIX}",
            )["Credentials"]
            return new_session(region=region, credentials=creds)
        except ClientError as exc:
            if error_code(exc) != "AccessDenied":
                raise
            if time.time() > deadline:
                raise AccountError("AssumeRoleFailed", describe_error(exc)) from exc
            _sleep(5)


def failure(code: str, message: str, started: float | None = None) -> dict:
    entry = {"result": "FAILED", "error_code": code, "error": message,
             "hint": ERROR_HINTS.get(code, DEFAULT_HINT)}
    if started is not None:
        entry["seconds"] = round(time.monotonic() - started, 1)
    return entry


def configure_account(session, account_id: str, trail_name: str, bucket: str, region: str,
                      role_name: str | None = None, partition: str | None = None) -> dict:
    """Create or update the trail in one account. Member accounts go through
    the temporary role (`role_name`); the management account uses `session`,
    the caller's own credentials. Never raises: any failure is returned."""
    started = time.monotonic()
    try:
        if role_name:
            session = assume_member_role(session, account_id, role_name, partition, region)
        cloudtrail = make_client(session, "cloudtrail", region_name=region, config=BOTO_CONFIG)
        state = ensure_trail(cloudtrail, trail_name, bucket, region)
    except AccountError as exc:
        log(f"  {account_id}: FAILED - {exc.code}: {exc}")
        return failure(exc.code, str(exc), started)
    except ClientError as exc:
        log(f"  {account_id}: FAILED - {describe_error(exc)}")
        return failure(error_code(exc), describe_error(exc), started)
    except BotoCoreError as exc:  # e.g. the credentials expired
        log(f"  {account_id}: FAILED - {describe_error(exc)}")
        return failure(type(exc).__name__, describe_error(exc), started)
    log(f"  {account_id}: trail {state['action']}, logging={'yes' if state['is_logging'] else 'NO'}"
        + ("" if role_name else " (management account)"))
    return {"result": "OK", **state, "seconds": round(time.monotonic() - started, 1)}


# --------------------------------------------------------------------------- #
# Plan (read-only): everything the run, --check and --dry-run need to know
# --------------------------------------------------------------------------- #

def default_bucket_name(log_account: str, region: str) -> str:
    name = f"{NAME_PREFIX}-{log_account}-{region}"
    if len(name) > 63:  # S3's limit; only reachable with a very long region name
        raise AcceleratorError(f"The default bucket name {name} is longer than 63 characters. Pass a shorter "
                               "name with --bucket.")
    return name


def build_plan(session, args) -> dict:
    ctx = get_org_context(session)
    partition, management, region = ctx["partition"], ctx["management"], args.region
    region_status = check_region(session, region, partition)
    accounts = ctx["accounts"]
    names = {a["Id"]: a["Name"] for a in accounts}
    states = {a["Id"]: account_state(a) for a in accounts}
    all_ids = sorted(a for a, s in states.items() if s == "ACTIVE")
    member_ids = [a for a in all_ids if a != management]
    log_account = args.log_account_id or management
    if log_account not in names:
        raise AcceleratorError(f"--log-account-id {log_account} is not an account in this organization.")
    if states[log_account] != "ACTIVE":
        raise AcceleratorError(f"--log-account-id {log_account} ({names[log_account]}) is "
                               f"{states[log_account]}; the log account must be ACTIVE.")
    bucket = args.bucket or default_bucket_name(log_account, region)
    run_id = utcnow().strftime("%Y%m%d%H%M%S")

    findings: list[tuple[str, str]] = []  # (OK | WARN | BLOCK, message)

    if ctx["feature_set"] != "ALL":
        findings.append(("BLOCK", f"The organization has only the {ctx['feature_set']} feature set. StackSets with "
                                  "service-managed permissions need all features enabled (AWS Organizations console > "
                                  "Settings > Enable all features)."))

    # The caller must be an administrator of the management account.
    needed = RUNNER_ACTIONS + (RUNNER_BUCKET_ACTIONS if log_account == management else ())
    missing = missing_permissions(session, ctx["caller_arn"], needed)
    if missing is None:
        findings.append(("WARN", f"Could not verify {ctx['caller_arn']}'s permissions with the IAM policy "
                                 "simulator (iam:GetRole / iam:SimulatePrincipalPolicy not allowed). The accelerator "
                                 "needs an administrator identity in the management account."))
    elif missing:
        findings.append(("BLOCK", f"{ctx['caller_arn']} is missing permissions the run needs: {', '.join(missing)}. "
                                  "Use an administrator identity in the management account (for example the "
                                  "AdministratorAccess policy or permission set)."))
    else:
        findings.append(("OK", "The caller has every permission the run needs"))

    # Region
    if region_status == "opted-in":
        findings.append(("WARN", f"{region} is an opt-in region. Accounts that haven't enabled it will fail; "
                                 "a default-enabled region such as us-east-1 avoids this."))
    elif region_status is None:
        findings.append(("WARN", f"Could not confirm {region} is enabled (no ec2:DescribeRegions permission)."))
    else:
        findings.append(("OK", f"Region {region} is enabled in the management account"))

    # Bucket: its contents are only visible from here when the management
    # account is the log account.
    s3 = make_client(session, "s3", region_name=region, config=BOTO_CONFIG)
    existing_policy, bucket_state = None, "unknown"
    if log_account == management:
        bucket_state, existing_policy, encryption = inspect_bucket(s3, bucket)
        if bucket_state == "taken":
            findings.append(("BLOCK", f"Bucket name {bucket} is owned by another AWS account (or these credentials "
                                      "can't access it). Pass another --bucket."))
        elif bucket_state == "exists":
            findings.append(("OK", f"Bucket {bucket} exists in the management account and will be reused"))
            if encryption and encryption.startswith("aws:kms"):
                findings.append(("WARN", f"Bucket {bucket} uses {encryption} default encryption. {_KMS_HINT}"))
        else:
            findings.append(("OK", f"Bucket name {bucket} is free; it will be created"))
    else:
        try:
            s3.head_bucket(Bucket=bucket)
            bucket_state = "management"
            findings.append(("BLOCK", f"Bucket {bucket} exists in the management account, but the log account is "
                                      f"{log_account}. Pass another --bucket."))
        except ClientError as exc:
            if error_code(exc) in ("404", "NoSuchBucket", "NotFound"):
                bucket_state = "missing"
                findings.append(("OK", f"Bucket name {bucket} is free; it will be created in {log_account}"))
            elif error_code(exc) in ("403", "Forbidden", "AccessDenied"):
                bucket_state = "elsewhere"
                findings.append(("WARN", f"Bucket {bucket} already exists in another account. That is fine if it is "
                                         f"log account {log_account}'s bucket from an earlier run; otherwise the run "
                                         "will stop. Pass another --bucket to be sure."))
            else:
                raise

    needs_stackset = bool(member_ids)

    future_policy = build_bucket_policy(bucket, all_ids, partition, region, args.trail_name, existing_policy)
    size = policy_size(future_policy)
    count = len(onboarded_accounts(future_policy))
    if size > BUCKET_POLICY_MAX_BYTES:
        findings.append(("BLOCK", f"The bucket policy would be {size} bytes for {count} accounts, over S3's 20 KB "
                                  "limit. See 'Limitations' in the README."))
    else:
        findings.append(("OK", f"Bucket policy size {size} bytes for {count} accounts (limit 20 KB)"))

    # StackSets prerequisites
    trusted = stacksets_trusted_access(session)
    if trusted is False:
        findings.append(("BLOCK" if needs_stackset else "WARN",
                         "StackSets trusted access is not enabled in AWS Organizations. Enable it with "
                         "'aws cloudformation activate-organizations-access' (or CloudFormation console > "
                         "StackSets > Activate trusted access)."))
    elif trusted is None:
        findings.append(("WARN", "Could not read StackSets trusted access "
                                 "(no organizations:ListAWSServiceAccessForOrganization permission)."))
    else:
        findings.append(("OK", "StackSets trusted access is enabled"))
    cfn = make_client(session, "cloudformation", region_name=region, config=BOTO_CONFIG)
    if _supports_concurrency_mode(cfn):
        findings.append(("OK", f"boto3 {boto3.__version__} supports SOFT_FAILURE_TOLERANCE"))
    else:
        findings.append(("WARN", f"boto3 {boto3.__version__} is older than 1.29: StackSet concurrency drops when "
                                 "accounts fail. Upgrade with: pip install -U boto3"))
    try:
        leftovers = leftover_stacksets(cfn)
    except ClientError as exc:
        leftovers = []
        findings.append(("WARN", f"Could not list StackSets: {describe_error(exc)}"))
    for name in leftovers:
        findings.append(("WARN", f"StackSet {name} was left by an interrupted run. Remove it with --cleanup."))

    # Management account trail
    cloudtrail = make_client(session, "cloudtrail", region_name=region, config=BOTO_CONFIG)
    trails = cloudtrail.describe_trails(includeShadowTrails=True)["trailList"]
    mine = next((t for t in trails if t.get("Name") == args.trail_name), None)
    if mine:
        try:
            assert_managed(cloudtrail, mine, region)
            findings.append(("OK", f"Management account already has the accelerator's trail {args.trail_name}; "
                                   "it will be updated in place"))
        except AccountError as exc:
            findings.append(("BLOCK", f"Management account: {exc} {ERROR_HINTS[exc.code]}"))
    elif len(trails) >= 5:
        findings.append(("WARN", f"Management account already sees {len(trails)} trails in {region}; the limit is "
                                 "5 per region, so creating another may fail."))
    else:
        findings.append(("OK", f"Management account has {len(trails)} trail(s) in {region} (limit 5)"))

    return {
        "run_id": run_id, "caller_arn": ctx["caller_arn"], "partition": partition, "management": management,
        "profile": args.profile,
        "org_id": ctx["org_id"], "root_id": ctx["root_id"], "region": region, "log_account": log_account,
        "bucket": bucket, "bucket_state": bucket_state, "trail_name": args.trail_name, "names": names,
        "states": states, "all_ids": all_ids, "member_ids": member_ids,
        "inactive": sorted(a for a, s in states.items() if s != "ACTIVE"),
        "existing_policy": existing_policy,
        "needs_stackset": needs_stackset, "stackset_name": f"{STACKSET_NAME_PREFIX}-{run_id}",
        "role_name": f"{ROLE_NAME_PREFIX}-{run_id}", "findings": findings, "leftovers": leftovers,
        "principal_arns": principal_arn_patterns(ctx["caller_arn"]),
    }


def print_plan(plan: dict, list_accounts: bool) -> None:
    names, management, log_account = plan["names"], plan["management"], plan["log_account"]
    log(f"CloudTrail Accelerator {__version__}")
    log(f"Caller             : {plan['caller_arn']} "
        f"({'profile ' + plan['profile'] if plan['profile'] else 'default credentials'})")
    log(f"Management account : {management} {names[management]} (org {plan['org_id']}, root {plan['root_id']}, "
        f"partition {plan['partition']})")
    log(f"Log account        : {log_account} {names[log_account]}"
        + (" (management account)" if log_account == management else " (member)"))
    log(f"Accounts           : {len(plan['all_ids'])} active ({len(plan['member_ids'])} members), "
        f"{len(plan['inactive'])} skipped")
    for acct in plan["inactive"]:
        log(f"  skipped          : {acct} {names[acct]} ({plan['states'][acct]})")
    log(f"Bucket             : s3://{plan['bucket']} ({plan['region']})")
    log(f"Trail              : {plan['trail_name']} (multi-region, management events read+write, "
        "log file validation, no KMS, no CloudWatch Logs)")
    log(f"Accounts to set up : management account + {len(plan['member_ids'])} member account(s)")
    if list_accounts:
        for acct in plan["member_ids"]:
            log(f"                     {acct} {names[acct]}")
    log(f"StackSet / role    : " + (f"{plan['stackset_name']} / {plan['role_name']} (temporary)"
                                    if plan["needs_stackset"] else "not needed (no member accounts)"))


# --------------------------------------------------------------------------- #
# --check: read-only pre-flight checks
# --------------------------------------------------------------------------- #

# Performed inside every member account by the StackSet execution role: create
# the stack and its role, and at the end delete them again. An SCP blocking the
# delete half would leave the temporary role behind.
STACK_ACTIONS = (
    "cloudformation:CreateStack",
    "iam:CreateRole",
    "iam:PutRolePolicy",
    "iam:TagRole",
    "cloudformation:DeleteStack",
    "iam:DeleteRolePolicy",
    "iam:DeleteRole",
)
# Plus what the temporary role does there (the trail).
MEMBER_ACTIONS = STACK_ACTIONS + ("cloudtrail:DescribeTrails",) + tuple(SETUP_TRAIL_ACTIONS)
# Additionally performed in a member log account by its temporary role.
LOG_ACCOUNT_ACTIONS = tuple(SETUP_BUCKET_ACTIONS)
# CloudTrail's own writes into the bucket, which an RCP on the log account could block.
DELIVERY_ACTIONS = ("s3:PutObject", "s3:GetBucketAcl")
DEFAULT_RCP_ID = "p-RCPFullAWSAccess"
_REGION_NEGATIONS = ("StringNotEquals", "StringNotEqualsIfExists", "StringNotLike", "StringNotLikeIfExists")


def _listify(value) -> list:
    return [value] if isinstance(value, str) else list(value or [])


def _action_matches(action: str, patterns) -> bool:
    return any(fnmatch.fnmatchcase(action.lower(), p.lower()) for p in _listify(patterns))


def _region_condition_applies(condition: dict, region: str) -> bool | None:
    """For the common region-lockdown SCP, whose only condition is a negated
    match on aws:RequestedRegion: whether that deny applies in `region`.
    Returns None for any other condition, which can't be judged statically."""
    if len(condition) != 1:
        return None
    operator, clause = next(iter(condition.items()))
    if operator not in _REGION_NEGATIONS or set(clause) != {"aws:RequestedRegion"}:
        return None
    allowed = _listify(clause["aws:RequestedRegion"])
    return not any(fnmatch.fnmatchcase(region, r) for r in allowed)


def _statement_covers(stmt: dict, action: str) -> bool:
    if "Action" in stmt:
        return _action_matches(action, stmt["Action"])
    return not _action_matches(action, stmt.get("NotAction", []))


def _statements(document: dict) -> list[dict]:
    stmts = document.get("Statement", [])
    return [stmts] if isinstance(stmts, dict) else stmts


class OrgPolicies:
    """Caches the org tree and the SCPs/RCPs attached at each level."""

    def __init__(self, org, root_id: str):
        self.org = org
        self.path: dict[str, list[str]] = {}  # account -> [root, ou..., account]
        self._attached: dict[tuple[str, str], list[str] | None] = {}
        self._content: dict[str, dict] = {}
        self._names: dict[str, str] = {}
        self._walk(root_id, [root_id])

    def _walk(self, parent: str, trail: list[str]) -> None:
        for acct in paginate(self.org, "list_accounts_for_parent", "Accounts", ParentId=parent):
            self.path[acct["Id"]] = trail + [acct["Id"]]
        for ou in paginate(self.org, "list_organizational_units_for_parent",
                           "OrganizationalUnits", ParentId=parent):
            self._names[ou["Id"]] = ou["Name"]
            self._walk(ou["Id"], trail + [ou["Id"]])

    def attached(self, target: str, policy_type: str) -> list[str] | None:
        """Policy IDs attached to `target`, or None if this policy type is not
        enabled in the org (so it restricts nothing)."""
        key = (target, policy_type)
        if key not in self._attached:
            try:
                policies = paginate(self.org, "list_policies_for_target", "Policies",
                                    TargetId=target, Filter=policy_type)
            except ClientError as exc:
                if error_code(exc) != "PolicyTypeNotEnabledException":
                    raise
                policies = None
            for p in policies or []:
                self._names[p["Id"]] = p["Name"]
            self._attached[key] = None if policies is None else [p["Id"] for p in policies]
        return self._attached[key]

    def statements(self, policy_id: str) -> list[dict]:
        if policy_id not in self._content:
            content = self.org.describe_policy(PolicyId=policy_id)["Policy"]["Content"]
            self._content[policy_id] = json.loads(content)
        return _statements(self._content[policy_id])

    def name(self, ident: str) -> str:
        return self._names.get(ident, ident)

    def scp_verdict(self, account_id: str, action: str, region: str) -> tuple[str, str]:
        """('ok' | 'blocked' | 'review', detail) for `action` in `account_id`.

        SCP semantics: every level from the root down to the account must have
        an attached SCP that allows the action, and no attached SCP may deny it.
        A deny whose only condition is on aws:RequestedRegion is judged against
        `region`, the region the call is made in. Any other condition, or a
        deny scoped to specific resources, can't be judged statically and is
        reported for review rather than as a block.
        """
        review = ""
        for target in self.path[account_id]:
            policies = self.attached(target, "SERVICE_CONTROL_POLICY")
            if policies is None:
                return "ok", ""  # SCPs are not enabled in this org
            allowed = False
            for pid in policies:
                for stmt in self.statements(pid):
                    if not _statement_covers(stmt, action):
                        continue
                    if stmt.get("Effect") == "Deny":
                        scoped = "NotResource" in stmt or _listify(stmt.get("Resource", "*")) != ["*"]
                        condition = stmt.get("Condition")
                        if condition and not scoped:
                            applies = _region_condition_applies(condition, region)
                            if applies is False:
                                continue
                            if applies:
                                return "blocked", (f"region {region} denied by SCP {self.name(pid)} "
                                                   f"on {self.name(target)}")
                        if condition or scoped:
                            review = review or f"conditional deny in SCP {self.name(pid)} on {self.name(target)}"
                        else:
                            return "blocked", f"denied by SCP {self.name(pid)} on {self.name(target)}"
                    elif stmt.get("Effect") == "Allow":
                        allowed = True
            if not allowed:
                return "blocked", f"no SCP on {self.name(target)} allows it"
        return ("review", review) if review else ("ok", "")

    def relevant_rcps(self, account_id: str, actions) -> list[str]:
        """Non-default RCPs on the account's path with a deny touching any of
        `actions`. RCPs can block AssumeRole into the temporary role, or
        CloudTrail delivery into the bucket; their conditions need a human look."""
        found = []
        for target in self.path[account_id]:
            for pid in self.attached(target, "RESOURCE_CONTROL_POLICY") or []:
                if pid == DEFAULT_RCP_ID:
                    continue
                hits = sorted({a for stmt in self.statements(pid) if stmt.get("Effect") == "Deny"
                               for a in actions if _statement_covers(stmt, a)})
                if hits:
                    found.append(f"RCP {self.name(pid)} on {self.name(target)} denies {', '.join(hits)} "
                                 "under conditions")
        return found


def run_checks(session, plan: dict, targets: list[str] | None = None, member_actions=MEMBER_ACTIONS,
               log_account_actions=LOG_ACCOUNT_ACTIONS, log_account_rcp_actions=DELIVERY_ACTIONS,
               log_account_blocks: bool = True, nothing_to_do: str = "No member accounts") -> int:
    """Print every plan finding plus an SCP/RCP verdict for each member account
    the run would touch (`targets`; by default every active member account).
    Returns 0 when nothing blocks the run, else 1."""
    findings = list(plan["findings"])
    print()
    log("Pre-flight checks (read-only)")
    for level, msg in plan["findings"]:
        log(f"[{level:5}] {msg}")

    management, log_account, region = plan["management"], plan["log_account"], plan["region"]
    if targets is None:
        targets = plan["member_ids"]
    if log_account != management and log_account in plan["member_ids"]:
        targets = sorted(set(targets) | {log_account})
    if not targets:
        log(f"[OK   ] {nothing_to_do}")
    policies = OrgPolicies(make_client(session, "organizations", config=BOTO_CONFIG), plan["root_id"])
    iam_region = PARTITION_HOME_REGION.get(plan["partition"], DEFAULT_REGION)
    for acct in targets:
        label = f"{acct} {plan['names'][acct]}" + (" [log account]" if acct == log_account else "")
        if acct not in policies.path:
            findings.append(("WARN", f"{label}: not found in the OU tree"))
            log(f"[WARN ] {findings[-1][1]}")
            continue
        actions = tuple(member_actions) + (tuple(log_account_actions) if acct == log_account else ())
        blocked, review = [], []
        for action in actions:
            call_region = iam_region if action.startswith("iam:") else region
            verdict, detail = policies.scp_verdict(acct, action, call_region)
            if verdict == "blocked":
                blocked.append(f"{action} ({detail})")
            elif verdict == "review":
                review.append(f"{action} ({detail})")
        rcp_actions = ("sts:AssumeRole",) + (tuple(log_account_rcp_actions) + tuple(log_account_actions)
                                             if acct == log_account else ())
        review += policies.relevant_rcps(acct, rcp_actions)
        if blocked:
            # Without the log account nothing works; any other account just fails alone.
            level = "BLOCK" if acct == log_account and log_account_blocks else "WARN"
            msg = f"{label}: will fail - {'; '.join(blocked)}"
        elif review:
            level, msg = "WARN", f"{label}: review - {'; '.join(review)}"
        else:
            level, msg = "OK", f"{label}: SCPs allow every action needed"
        findings.append((level, msg))
        log(f"[{level:5}] {msg}")

    blocks = sum(1 for level, _ in findings if level == "BLOCK")
    warns = sum(1 for level, _ in findings if level == "WARN")
    print()
    log(f"Checks done: {blocks} blocking, {warns} warning(s). Trails inside member accounts can only be seen "
        "from inside each account, so problems with them surface at run time.")
    return EXIT_FAILED if blocks else EXIT_OK


# --------------------------------------------------------------------------- #
# --cleanup: remove StackSets left by an interrupted run
# --------------------------------------------------------------------------- #

def run_cleanup(session, plan: dict, args) -> int:
    cfn = make_client(session, "cloudformation", region_name=plan["region"], config=BOTO_CONFIG)
    leftovers = leftover_stacksets(cfn)
    if not leftovers:
        log(f"No accelerator StackSets left in {plan['region']}; nothing to clean up")
        return EXIT_OK
    ok = True
    for name in leftovers:
        result = delete_stackset(cfn, name, plan["root_id"], plan["region"], args.timeout_minutes * 60)
        ok = ok and result["deleted"] and not result["instances_left"]
        for acct, reason in result["instances_left"].items():
            log(f"  [error] {acct}: stack instance could not be deleted ({reason}); StackSet {name} and its role "
                "there are kept. Fix the cause, then run --cleanup again.")
    return EXIT_OK if ok else EXIT_FAILED


# --------------------------------------------------------------------------- #
# Apply
# --------------------------------------------------------------------------- #

def write_report(report: dict, path: str) -> None:
    with open(path, "w") as fh:
        json.dump(report, fh, indent=2, default=str)


def apply(session, plan: dict, args) -> int:
    management, root_id, partition = plan["management"], plan["root_id"], plan["partition"]
    log_account, bucket, region, names = plan["log_account"], plan["bucket"], plan["region"], plan["names"]
    all_ids, member_ids = plan["all_ids"], plan["member_ids"]
    stackset_name, role_name = plan["stackset_name"], plan["role_name"]
    template = role_template(management, role_name, log_account, bucket, partition, args.trail_name,
                             plan["principal_arns"])
    timeout = args.timeout_minutes * 60
    timings: dict[str, float] = {"plan": time.monotonic() - _start}
    report_path = args.report or f"cloudtrail-accelerator-{plan['run_id']}.json"
    report = {
        "accelerator_version": __version__,
        "mode": "apply",
        "run_id": plan["run_id"],
        "started_at": utcnow().isoformat(),
        "caller": plan["caller_arn"],
        "management_account": management,
        "organization_id": plan["org_id"],
        "log_account": log_account,
        "region": region,
        "bucket": bucket,
        "trail_name": args.trail_name,
        "stackset": stackset_name if plan["needs_stackset"] else None,
        "skipped_inactive": [{"id": a, "name": names[a], "state": plan["states"][a]} for a in plan["inactive"]],
        "accounts": {},
    }
    bucket_ready = False
    previously: set[str] = set()  # accounts the bucket policy listed before this run (none on a first run)
    futures: dict = {}
    results: dict[str, dict] = {}
    reasons: dict[str, str] = {}
    timed_out = False

    def collect_results() -> None:
        """Results of every finished account so far (all of them once the pool has shut down)."""
        for acct, future in futures.items():
            if future.done() and not future.cancelled() and future.exception() is None:
                results[acct] = future.result()

    def drop_accounts_without_trail() -> None:
        """Least privilege: take member accounts whose trail couldn't be created
        back out of the bucket policy. Accounts the policy already listed before
        this run are never removed; they may have a working trail."""
        if not bucket_ready:
            return
        collect_results()
        succeeded = {a for a, r in results.items() if r.get("result") == "OK"}
        drop = sorted(set(member_ids) - previously - succeeded)
        if not drop:
            return
        try:
            if log_account == management:
                target = session
            else:
                # Fresh credentials: the log account's first session may have expired during a long run.
                target = assume_member_role(session, log_account, role_name, partition, region)
            remove_from_policy(target, bucket, region, partition, args.trail_name, drop)
            report["removed_from_policy"] = drop
            log(f"Removed {len(drop)} account(s) without a trail from the bucket policy")
        except (ClientError, BotoCoreError, AccountError) as exc:
            log(f"  [warn] Could not remove accounts without a trail from the bucket policy "
                f"({describe_error(exc)}): {', '.join(drop)}")

    def roles(acct: str) -> str:
        parts = ["management" if acct == management else "member"]
        if acct == log_account:
            parts.append("log")
        return "+".join(parts)

    def submit(acct: str) -> None:
        """Create the trail in `acct` on a worker thread: the management
        account with the caller's own credentials, members through the role."""
        if acct == management:
            futures[acct] = pool.submit(configure_account, session, acct, args.trail_name, bucket, region)
        else:
            futures[acct] = pool.submit(configure_account, session, acct, args.trail_name, bucket, region,
                                        role_name, partition)

    def on_bucket_ready() -> None:
        """Bucket exists and its policy is in place: every trail can be created.
        The management account's starts right away, alongside the members'."""
        nonlocal bucket_ready
        bucket_ready = True
        timings["bucket_ready_at"] = time.monotonic() - _start
        submit(management)

    cfn = make_client(session, "cloudformation", region_name=region, config=BOTO_CONFIG)
    stackset_created, poller = False, None
    pool = ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="account")
    try:
        try:
            # 1-2. The management account owns the bucket: set it up and allow
            # every account. Its trail then starts while the StackSet rolls out.
            if log_account == management:
                previously = setup_central_bucket(session, bucket, region, partition, args.trail_name, all_ids)
                on_bucket_ready()

            # 3-5. One StackSet to every member account, at maximum concurrency;
            # each account's trail is created as soon as its role is ready.
            if member_ids:
                phase = time.monotonic()
                log(f"Deploying StackSet {stackset_name} to {len(member_ids)} account(s) at max concurrency")
                create_stackset(cfn, stackset_name, template)
                stackset_created = True
                poller = InstancePoller(cfn, stackset_name, timeout)
                poller.add_operation(add_stack_instances(cfn, stackset_name, root_id, None, region))
                poller.close()
                poller.start()

                waiting: list[str] = []  # roles that came up before a member log account's bucket
                while True:
                    try:
                        acct = poller.queue.get(timeout=POLL_INTERVAL_SECONDS)
                    except Empty:
                        continue
                    if acct is None:
                        break
                    if acct not in member_ids:
                        continue
                    if acct == log_account and not bucket_ready:
                        # A member log account: its role creates the bucket. A failure
                        # here aborts the run, since no trail works without the bucket.
                        # Teardown still runs.
                        log(f"  {log_account}: role ready, setting up the central bucket")
                        log_session = assume_member_role(session, log_account, role_name, partition, region)
                        previously = setup_central_bucket(log_session, bucket, region, partition,
                                                          args.trail_name, all_ids)
                        on_bucket_ready()
                        for queued in waiting:
                            submit(queued)
                        waiting.clear()
                    if bucket_ready:
                        submit(acct)
                    else:
                        waiting.append(acct)
                poller.join()
                reasons, timed_out = poller.reasons, poller.timed_out
            else:
                log("The organization has no member accounts; StackSet not needed")
        except BaseException:
            # Stopping: don't start trails that haven't started yet.
            for future in futures.values():
                future.cancel()
            raise
        finally:
            # Every trail in progress must finish before the roles are deleted.
            pool.shutdown(wait=True)
            if member_ids and stackset_created:
                timings["member_trails"] = time.monotonic() - phase
            if poller is not None:
                poller.stop()
            # Before teardown: a member log account is only reachable through its temporary role.
            drop_accounts_without_trail()
            if stackset_created:
                # 6. Delete the StackSet, and with it every temporary role.
                phase = time.monotonic()
                teardown = delete_stackset(cfn, stackset_name, root_id, region, timeout)
                report["stackset_deleted"] = teardown["deleted"]
                report["stack_instances_left"] = teardown["instances_left"]
                timings["stackset_teardown"] = time.monotonic() - phase
    except BaseException as exc:
        # Leave a record of what was done before the abort, then stop.
        report["aborted"] = describe_error(exc) if not isinstance(exc, KeyboardInterrupt) else "Interrupted (Ctrl-C)"
        collect_results()
        report["accounts"].update({a: r for a, r in results.items()})
        report["finished_at"] = utcnow().isoformat()
        report["timings_seconds"] = {k: round(v, 1) for k, v in timings.items()}
        write_report(report, report_path)
        log(f"[error] Run stopped: {report['aborted']}. Partial report written to {report_path}")
        if report.get("stackset_deleted") is False:
            log("[error] The StackSet was not deleted. Run again with --cleanup to remove it.")
        if isinstance(exc, KeyboardInterrupt):
            raise
        if isinstance(exc, (AcceleratorError, ClientError, AccountError, BotoCoreError)):
            raise RunAborted(report["aborted"]) from exc
        raise

    # ---- 7. Report -----------------------------------------------------------
    collect_results()
    if not bucket_ready:
        reason = reasons.get(log_account, "no stack instance")
        log(f"[error] The temporary role never became ready in log account {log_account} ({reason}), so the "
            "central bucket was not created and no trails were set up.")
    for acct in [management] + member_ids:
        if acct in results:
            entry = results[acct]
        elif not bucket_ready and acct != log_account:
            entry = failure("BucketNotReady", "The central bucket was never created.")
        else:
            why = reasons.get(acct, "no stack instance was created for this account")
            if timed_out:
                why += "; the StackSet rollout timed out (raise --timeout-minutes)"
            entry = failure("RoleNotReady", f"The temporary role never became ready: {why}")
        report["accounts"][acct] = entry
    for acct in list(report["accounts"]):
        report["accounts"][acct] = {"name": names.get(acct, ""), "role": roles(acct), **report["accounts"][acct]}

    timings["total"] = time.monotonic() - _start
    report["finished_at"] = utcnow().isoformat()
    report["timings_seconds"] = {k: round(v, 1) for k, v in timings.items()}
    ordered = sorted(report["accounts"], key=lambda a: (a != management, a))
    failed = [a for a in ordered if report["accounts"][a]["result"] != "OK"]
    report["summary"] = {"accounts": len(ordered), "succeeded": len(ordered) - len(failed), "failed": len(failed)}
    write_report(report, report_path)

    print()
    rows = []
    for acct in ordered:
        e = report["accounts"][acct]
        logging_state = "-" if e["result"] != "OK" else ("yes" if e.get("is_logging") else "NO")
        detail = e.get("error_code") or (e.get("latest_delivery_error") or "")
        rows.append([acct, e["name"][:32], e["role"], e["result"], e.get("action", "-"), logging_state,
                     detail or "-"])
    print_table(["ACCOUNT", "NAME", "ROLE", "RESULT", "TRAIL", "LOGGING", "DETAIL"], rows)
    print()
    log(f"Trails ready: {len(ordered) - len(failed)}/{len(ordered)} account(s)")
    for acct in failed:
        e = report["accounts"][acct]
        log(f"  FAILED {acct} {e['name']}: {e['error_code']}: {e['error']}")
        log(f"         fix: {e['hint']}")
    left = report.get("stack_instances_left") or {}
    for acct, reason in left.items():
        log(f"[error] {acct}: its stack instance could not be deleted ({reason}), so role {role_name} may still "
            "exist there.")
    if report.get("stackset_deleted") is False:
        log(f"[error] StackSet {stackset_name} was NOT deleted. Fix the cause above (often an SCP denying "
            "cloudformation:DeleteStack or iam:DeleteRole), then run with --cleanup to delete it and its roles.")
    log("Timings: " + ", ".join(f"{k}={v:.0f}s" for k, v in timings.items()))
    log(f"Report written to {report_path}")
    log("First log files reach the bucket within about 15 minutes.")
    clean = not failed and report.get("stackset_deleted", True) is not False and not left
    return EXIT_OK if clean else EXIT_FAILED

# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="cloudtrail_accelerator.py",
        description="One-time setup: give every account in an AWS Organization its own CloudTrail trail "
                    "(management events, read and write), delivering to one S3 bucket in the management account "
                    "or a log account you choose. Reaches member accounts through a temporary StackSet role, "
                    "deleted at the end. For organizations where an organization trail can't be used. Run with "
                    "an administrator profile for the management account.",
        epilog="Exit codes: 0 success, 1 some accounts failed / --check found blockers, 2 invalid input or unmet "
               "precondition (nothing changed), 3 run stopped part-way (partial report written), 130 "
               "interrupted. Docs: README.md and docs/.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    target = p.add_argument_group("where the logs go")
    target.add_argument("--profile", help="AWS profile for the management account (default: the standard AWS "
                                          "credential chain: env vars, default profile, SSO, instance role)")
    target.add_argument("--log-account-id", metavar="ACCOUNT_ID",
                        help="account that owns the central bucket: the management account or any active "
                             "member (default: the management account)")
    target.add_argument("--region", default=DEFAULT_REGION,
                        help=f"home region of the bucket, every trail and the temporary StackSet "
                             f"(default: {DEFAULT_REGION})")
    target.add_argument("--bucket", help=f"name of the central bucket (default: "
                                         f"{NAME_PREFIX}-<log-account-id>-<region>)")
    target.add_argument("--trail-name", default=DEFAULT_TRAIL_NAME,
                        help=f"name of the trail, the same in every account (default: {DEFAULT_TRAIL_NAME})")
    run = p.add_argument_group("run behaviour")
    run.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                     help=f"accounts configured in parallel, 1-100 (default: {DEFAULT_WORKERS})")
    run.add_argument("--timeout-minutes", type=int, default=DEFAULT_TIMEOUT_MINUTES,
                     help=f"max wait for each StackSet rollout and teardown, 5-720 (default: {DEFAULT_TIMEOUT_MINUTES})")
    run.add_argument("--report", metavar="PATH",
                     help="where to write the JSON report (default: cloudtrail-accelerator-<run-id>.json)")
    run.add_argument("--debug", action="store_true",
                     help="log every AWS API call and SDK retry, and show full tracebacks on errors")
    modes = p.add_argument_group("read-only and maintenance modes (default: set up)")
    modes.add_argument("--check", action="store_true",
                       help="read-only pre-flight checks: trusted access, region, bucket, trail limits, "
                            "SCPs/RCPs per account. Exit 1 if anything blocks the run")
    modes.add_argument("--dry-run", action="store_true",
                       help="read-only: print the plan, the bucket policy and the role template")
    modes.add_argument("--cleanup", action="store_true",
                       help="delete accelerator StackSets left behind by an interrupted run in --region")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.debug:
        configure_debug()
    validate_args(args)
    session = new_session(args.profile, args.region)
    try:
        plan = build_plan(session, args)
    except ClientError as exc:
        raise AcceleratorError(f"Could not read the organization: {describe_error(exc)}") from exc

    if args.cleanup:
        return run_cleanup(session, plan, args)

    print_plan(plan, list_accounts=args.dry_run or args.check)
    if args.dry_run:
        print("\n--- Bucket policy after this run ---")
        print(json.dumps(build_bucket_policy(plan["bucket"], plan["all_ids"], plan["partition"], plan["region"],
                                             plan["trail_name"], plan["existing_policy"]), indent=2))
        print("\n--- Temporary role template (deployed by the StackSet) ---")
        print(json.dumps(json.loads(role_template(plan["management"], plan["role_name"], plan["log_account"],
                                                  plan["bucket"], plan["partition"], plan["trail_name"],
                                                  plan["principal_arns"])), indent=2))
    if args.check:
        status = run_checks(session, plan)
    elif args.dry_run:
        for level, msg in plan["findings"]:
            if level != "OK":
                log(f"[{level:5}] {msg}")
    if args.dry_run or args.check:
        log("Nothing was changed")
        return status if args.check else EXIT_OK

    blockers = [msg for level, msg in plan["findings"] if level == "BLOCK"]
    if blockers:
        raise AcceleratorError("Not starting, nothing was changed:\n  - " + "\n  - ".join(blockers)
                               + "\nRun with --check for the full pre-flight report.")
    for level, msg in plan["findings"]:
        if level == "WARN":
            log(f"[warn] {msg}")
    return apply(session, plan, args)


def cli(argv=None) -> int:
    try:
        return main(argv)
    except AcceleratorError as exc:
        _print_error(exc, f"ERROR: {exc}")
        return EXIT_INVALID
    except RunAborted as exc:
        _print_error(exc, f"ERROR: the run stopped part-way: {exc}\nSee the partial report; running it again "
                          "is safe (existing accelerator trails are updated in place).")
        return EXIT_ABORTED
    except KeyboardInterrupt:
        print("\nInterrupted. If a StackSet was left behind, remove it with --cleanup.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except BotoCoreError as exc:
        _print_error(exc, f"ERROR: AWS credentials/configuration problem: {exc}\n"
                          "Check --profile, run 'aws sso login' if you use IAM Identity Center, and confirm "
                          "'aws sts get-caller-identity' works with the same profile.")
        return EXIT_INVALID
    except ClientError as exc:
        _print_error(exc, f"ERROR: {describe_error(exc)}")
        return EXIT_INVALID


def _print_error(exc: BaseException, message: str) -> None:
    if _debug:
        traceback.print_exception(type(exc), exc, exc.__traceback__)
    print(message, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(cli())
