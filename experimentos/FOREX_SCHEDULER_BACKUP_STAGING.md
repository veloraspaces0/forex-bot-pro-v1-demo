# Experimental backup scheduler (NOT DEPLOYED)

Purpose: provide an independent Cloudflare Cron Trigger fallback when GitHub Actions `schedule` fails to run regularly. GitHub remains authoritative for trading-paper state; this Worker **only requests** a dispatch of `forex-v2.yml` on `main`. It never reads private trading data or sends real orders.

## Safety gates

- `BACKUP_SCHEDULER_MODE=observe` is the checked-in default: no outbound requests and no dispatches.
- Only the **fixed** `veloraspaces0/forex-bot-pro-v1-demo` repository, `forex-v2.yml` workflow and `main` ref can be dispatched.
- Needs an opt-in GitHub fine-grained token bound to this repository and permission **Actions: read and write**; store it as Cloudflare secret `GITHUB_DISPATCH_TOKEN`, never in git or chat.
- Requires a **dedicated SQLite Durable Object**, configured in `wrangler_backup.toml` with binding `BACKUP_DISPATCH_GUARD` and migration `new_sqlite_classes = ["BackupDispatchGuard"]`. No Cloudflare KV is used for locking.
- Does nothing while the last successful PAPER heartbeat is fresh (35-minute threshold), there is an active GitHub run, a recent run (28-minute threshold), or the **atomic SQLite reservation** is within its 40-minute cooldown. The reservation is written **before** the GitHub POST and is deliberately retained after uncertain HTTP failures.
- If the GitHub API is returning 403 or the heartbeat is invalid, **fail closed** rather than dispatch blindly. This preserves safety but cannot guarantee uninterrupted operations.
- The existing GitHub Actions PAPER job has a concurrency group and fail-closed state semantics; keep both in place before any activation.
- Cloudflare Cron Trigger itself is best-effort; this is redundancy, not a guarantee of 15-minute execution.

## Offline validation

The isolated GitHub workflow `.github/workflows/forex-scheduler-backup-offline.yml` runs Node fault-injection tests, real SQLite 32-request contention tests, and Wrangler `--dry-run` with the Durable Object binding. It never dispatches any workflow.

## Manual activation procedure (NOT authorized in this PR)

1. Review the branch and require all offline CI checks to pass.
2. Keep the dedicated SQLite Durable Object binding and migration in `experimentos/wrangler_backup.toml`. Do not substitute KV: KV's eventual consistency cannot guard the dispatch.
3. Provision a fine-grained GitHub token for the single repository with only Actions read/write. Store it exclusively as a Cloudflare Worker secret; never log the token.
4. Deploy to a **new** Cloudflare Worker name, not `forex-watch-v23-paper-monitor`. First run it in `observe` mode, with the cron schedule present but without any dispatch.
5. Reverify the GitHub workflow has `PAPER_ONLY`, branch guard `refs/heads/main`, encryption, tests and `concurrency` protections intact.
6. After explicit production change authorization and separate staging tests, change the env variable to `active`. Monitor Durable Object state, Cloudflare logs, Actions run IDs and rate-limit responses. Confirm duplicate and failure behavior. The coordinator's cooldown prevents duplicate **backup** dispatches, not a race with the independently scheduled GitHub event.
7. Roll back simply by restoring `BACKUP_SCHEDULER_MODE=observe` or removing the backup Cron Trigger. Do not touch `v2/state.enc`, `v2/state.json`, or the Forex Watch monitor.

## Coordination limitations

- The Durable Object guards backup dispatches from multiple Worker invocations. It cannot atomically coordinate with GitHub's own scheduler, which never acquires this lock.
- Keep the existing fixed GitHub Actions `concurrency.group = forex-bot-pro-v2-main-state` and the PAPER motor's `last_bar` idempotency protections. Never substitute `run_id` into the concurrency group.
- The reservation is deliberately **at-most-once for 40 minutes**, not guaranteed delivery. A lost response, 403, timeout, or crash after reservation can suppress retries even when GitHub did not accept the POST. That is intentional fail-closed behavior to protect the paper state.
- Cloudflare Cron Triggers and GitHub workflow_dispatch both remain best effort. This does not guarantee exact 15-minute cadence.

## Non-goals

This does not fix the GitHub API 403, Twelve Data latency, missed candles, or already-open virtual positions. All remain visible to the existing independent Forex Watch monitor. No automatic deployment or modification of production is included.
