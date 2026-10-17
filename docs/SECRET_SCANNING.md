# Secret scanning

Nothing secret, confidential or personal may be committed, because this repository may be public. One scanner,
[scripts/secret-scan.py](../scripts/secret-scan.py), enforces that in three places so the rules cannot drift apart. It is the same scanner
used in `secure-serverless-api-aws`.

| Where | When | Scope |
|---|---|---|
| **Pre-commit hook** (`.githooks/pre-commit`) | Every `git commit` on your machine | What is staged |
| **CI job** "Scan for secrets and sensitive data" (`validate.yml`) | Every pull request | Every tracked file, and every line added by every commit in the PR |
| **By hand** | Any time | `--staged`, `--all-tracked` or `--range BASE..HEAD` |

Only content that is committed (or about to be) is scanned. Git-ignored local files, such as `emails.auto.tfvars`, `backend.hcl` or Terraform state, are not.

## What it blocks

| Category | Examples |
|---|---|
| Credentials | AWS access keys and secret keys, private key blocks, GitHub and Slack tokens, JWTs, a password or token assigned a literal value, Google OAuth secrets and API keys |
| AWS identifiers | An account ID inside an ARN or a resource name, deployed API, SSO and CloudFront hostnames, Cognito pool IDs and hosted domains |
| Personal data | Email addresses (except `example.com`, `.org`, `.net`), personal file paths such as `/home/<user>/`, private IP addresses |
| Files that must never be committed | `*.tfstate`, `*.tfvars`, saved plans, `.env`, `*.pem`, `*.key`, `*credentials*`, `.terraform/`, `.aws/` (`.terraform.lock.hcl` and `*.tfvars.example` are allowed) |
| Your own block list | Exact values you list, such as your account ID and email |

Use placeholders in docs and code: `<ACCOUNT_ID>`, `you@example.com`. That is why `environment` has a default in each environment's `variables.tf`
instead of a committed `terraform.tfvars`, and why real values live in the git-ignored `emails.auto.tfvars` and `backend.hcl`.

## Enable the hook (once per clone)

```bash
./scripts/install-hooks.sh        # sets git config core.hooksPath .githooks and runs the self-test
```

A blocked commit prints the rule, file and line with a **redacted** snippet, never the value itself. Add `secret-scan: allow` to a line to accept a reviewed false positive.

## Block your own values (kept out of the repo)

The built-in rules cannot know your account ID or email, and writing them in the repo would publish them. List them, one per line, in
`~/.config/secret-scan/literals` on your machine, and in the optional repository secret `SECRET_SCAN_LITERALS` for CI.

## If a secret was ever committed

Rotate it. Deleting it from history is not enough, because it may already be cached or cloned.
