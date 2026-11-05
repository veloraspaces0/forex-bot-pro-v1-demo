# Experimental backup scheduler (NOT DEPLOYED)

Purpose: provide an independent Cloudflare Cron Trigger fallback when GitHub Actions `schedule` fails to run regularly. GitHub remains authoritative for trading-paper state; this Worker **only requests** a dispatch of `forex-v2.yml` on `main`. It never reads private trading data or sends real orders.

## Safety gates

- `BACKUP_SCHEDULER_MODE=observe` is the checked-in default: no outbound requests and no dispatches.
- Only the **fixed** `veloraspaces0/forex-bot-pro-v1-demo` repository, `forex-v2.yml` workflow and `main` ref can be dispatched.
- Needs an opt-in GitHub fine-grained token bound to this repository and permission **Actions: read and write**; store it as Cloudflare secret `GITHUB_DISPATCH_TOKEN`, never in git or chat.
- Requires a **dedicated** Cloudflare KV namespace bound as `BACKUP_DISPATCH_KV`; do not reuse `INCIDENT_KV` from the monitoring Worker.
- Does nothing while the last successful PAPER heartbeat is fresh (35-minute threshold), there is an active GitHub run, a recent run (28-minute threshold), or dispatch happened within the cooldown (40 minutes).
- If the GitHub API is returning 403 or the heartbeat is invalid, **fail closed** rather than dispatch blindly. This preserves safety but cannot guarantee uninterrupted operations.
- The existing GitHub Actions PAPER job has a concurrency group and fail-closed state semantics; keep both in place before any activation.
- Cloudflare Cron Trigger itself is best-effort; this is redundancy, not a guarantee of 15-minute execution.

## Offline validation

The isolated GitHub workflow `.github/workflows/forex-scheduler-backup-offline.yml` runs Node fault-injection tests and Wrangler `--dry-run`. It never dispatches any workflow.

## Manual activation procedure (NOT authorized in this PR)

1. Review the branch and require all offline CI checks to pass.
2. Provision a dedicated KV namespace and add the `BACKUP_DISPATCH_KV` binding to `experimentos/wrangler_backup.toml`.
3. Provision a fine-grained GitHub token for the single repository with only Actions read/write. Store it exclusively as a Cloudflare Worker secret; never log the token.
4. Deploy to a **new** Cloudflare Worker name, not `forex-watch-v23-paper-monitor`. First run it in `observe` mode, with the cron schedule present but without any dispatch.
5. Reverify the GitHub workflow has `PAPER_ONLY`, branch guard `refs/heads/main`, encryption, tests and `concurrency` protections intact.
6. After an explicit production change authorization, change the env variable to `active`. Monitor Cloudflare logs, Actions run IDs and rate-limit responses. Confirm duplicate and failure behavior.
7. Roll back simply by restoring `BACKUP_SCHEDULER_MODE=observe` or removing the backup Cron Trigger. Do not touch `v2/state.enc`, `v2/state.json`, or the Forex Watch monitor.

## Non-goals

This does not fix the GitHub API 403, Twelve Data latency, missed candles, or already-open virtual positions. All remain visible to the existing independent Forex Watch monitor. No automatic deployment or modification of production is included.
