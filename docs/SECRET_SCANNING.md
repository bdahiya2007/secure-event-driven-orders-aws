# Secret scanning

Nothing secret, confidential or personal may be committed, because this repository is public. One scanner,
[scripts/secret-scan.py](../scripts/secret-scan.py), enforces that in three places so the rules cannot drift apart. It is the same scanner
used in `secure-serverless-api-aws`.

| Where | When | Scope |
|---|---|---|
| **Pre-commit hook** (`.githooks/pre-commit`) | Every `git commit` on your machine | What is staged |
| **CI job** "Scan for secrets and sensitive data" (`validate.yml`) | Every pull request | Every tracked file, and every line added by every commit in the PR |
| **By hand** | Any time | `--staged`, `--all-tracked` or `--range BASE..HEAD` |
| **GitHub secret scanning with push protection** (repository setting) | Every push | Known provider token formats; refuses the push before the secret is stored |

Only content that is committed (or about to be) is scanned. Git-ignored local files, such as `emails.auto.tfvars`, `backend.hcl` or Terraform state, are not.

## What it blocks

| Category | Examples |
|---|---|
| Credentials | AWS access keys and secret keys, private key blocks, GitHub and Slack tokens, JWTs, a password or token assigned a literal value, Google OAuth secrets and API keys |
| AWS identifiers | An account ID inside an ARN or a resource name, deployed API, SSO and CloudFront hostnames, Cognito pool IDs and hosted domains |
| Personal data | Email addresses (except `example.com`, `example.org`, `example.net` and the `users.noreply.github.com` address), personal file paths such as `/home/<user>/`, private IP addresses |
| Files that must never be committed | `*.tfstate`, `*.tfvars`, saved plans, `.env`, `*.pem`, `*.key`, `*credentials*`, `.terraform/`, `.aws/` (`.terraform.lock.hcl` and `*.tfvars.example` are allowed) |
| Your own block list | Exact values you list, such as your account ID and email |

Use placeholders in docs and code: `<ACCOUNT_ID>`, `you@example.com`. That is why `environment` has a default in each environment's `variables.tf`
instead of a committed `terraform.tfvars`, and why real values live in the git-ignored `emails.auto.tfvars` and `backend.hcl`.

## Enable the hook (once per clone)

```bash
./scripts/install-hooks.sh        # sets git config core.hooksPath .githooks and runs the self-test
```

A blocked commit prints the rule, file and line with a **redacted** snippet, never the value itself. Add `secret-scan: allow` to a line to accept a reviewed false positive. **The marker is honoured everywhere, including in CI**, so treat every new marker in a pull request as a review item (`git diff main... | grep "secret-scan: allow"`).

## Block your own values (kept out of the repo)

The built-in rules cannot know your account ID or email, and writing them in the repo would publish them. List them, one per line, in
`~/.config/secret-scan/literals` on your machine, and in the optional repository secret `SECRET_SCAN_LITERALS` for CI.

## If a secret was ever committed

Rotate it. Deleting it from history is not enough, because it may already be cached or cloned. Then remove it in a pull request, check the GitHub Security tab for an alert, and add a rule or a block-list entry so the same kind of value is caught next time.

## Known limits

- The hook is local and can be skipped (`git commit --no-verify`); the required CI check is the real backstop.
- `secret-scan: allow` is honoured in CI.
- Only private IP addresses are blocked. A real public IP address, domain name or hosted zone ID is not caught by a rule; list them in the private block list above.
- Commit messages, branch names, pull request titles and descriptions and issue text are not scanned (only file contents and added lines).
- The history has not been scanned in one pass with a second tool, and earlier commits carry a personal email address (see T27 in the threat model).
- `.gitignore` is the first barrier and the file-name rule is the second; keep them in step when a new file type is added.
