# Chat history 03: testing and release

## Verification summary (2026-10-28, late evening, America/Toronto)

| Check | Result |
|---|---|
| `ruff check .` | clean |
| `pytest` (unit, no network) | 50 passed |
| `UARB_LIVE=1 pytest tests/live` | 3 passed in 26 s (metadata + counts + 2 downloads, unknown matter, empty tab) |
| `uarb-agent run --matter M12205 --type "Other Documents" --dry-run` | 10 PDFs (all start with `%PDF-`), 6.37 MB ZIP with `MANIFEST.csv`, well-formed `.eml` (text + HTML + `application/zip`), 22 s |
| `uarb-agent run --matter M12205 --type exhibits --dry-run --limit 3` | size guard dropped the 46 MB and 26 MB exhibits and said so |
| `uarb-agent run --matter M12383 --type "key docs" --dry-run --limit 3` | second matter from the assignment parsed correctly end to end |
| SMTP transport | real send to a local `aiosmtpd` server verified in tests |
| IMAP transport | protocol stub verifies UNSEEN search, `BODY.PEEK[]`, `\Seen` after handling |
| Docker image / Actions | validated by CI on first push (Docker is not installed on the build VM) |

Not performed in this session, because the VM had no mailbox credentials:

- A real round trip through Gmail (send a request, let `poll-once` answer it). The transports
  are covered by tests and the pipeline by live dry runs, so the remaining risk is provider
  configuration (App Password, IMAP enabled), which `uarb-agent check-config` and the first
  `poll-once` log will surface.

## Release

Pending a GitHub credential in the Cloud Agent VM (see 02-implementation.md, "Credentials
check"). Once available the steps are:

1. `gh repo create <user>/uarb-regulatory-agent --public --source . --push` (or add the SSH
   remote and `git push -u origin main`).
2. Replace `OWNER` in `README.md` and `pyproject.toml` URLs with the real account.
3. Confirm the `CI` workflow is green (lint, tests, Docker build + fake dry run).
4. Optionally add mailbox secrets and enable the `Agent` workflow so anyone can email the
   agent.

This file is updated when those steps happen.
