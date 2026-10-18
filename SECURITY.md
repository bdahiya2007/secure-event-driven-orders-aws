# Security policy

This is a personal portfolio project, not a supported product. Security reports are still welcome, and are handled on a best-effort basis.

## Reporting a vulnerability

Please report it **privately**, not in a public issue or pull request:

1. Open the repository's **Security** tab.
2. Choose **Report a vulnerability** (GitHub private vulnerability reporting).
3. Describe what you found, where, and how to reproduce it. Do not include real credentials or other people's data.

You can expect an acknowledgement within about a week and a fix or a decision as time allows. Please give me a reasonable chance to fix the
issue before sharing details publicly.

## What is in scope

- The Terraform in `terraform/`, the Lambda code in `terraform/modules/*/src/`, the GitHub Actions workflows, the helper scripts and the
  documentation: for example a way to bypass the IAM, queue or event bus policies, injection through the order message, a weakness in the
  deploy role's trust policy or permissions boundary, or a secret committed by mistake.

## What is out of scope

- Findings that need access to the owner's AWS or GitHub account.
- Weaknesses in AWS or GitHub themselves.
- The gaps already listed in [the threat model](docs/THREAT_MODEL.md) and the "Open security gaps" table in the [README](README.md), which are
  known and tracked.
- Denial-of-service testing, load testing or any other testing against the deployed environment. The deployed stack belongs to the owner and
  is not offered for testing; please review the code instead.

## Supported versions

Only the latest commit on `main` is maintained.

## If you find a secret

If you find what looks like a real credential in the repository or its history, report it privately as above. It will be rotated first, then removed.
