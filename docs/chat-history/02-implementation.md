# Chat history 02: implementation

Session 2, 2026-10-28 from 10:22 PM (America/Toronto). The user approved the plan with
"Implement the plan as specified" and the agent worked through the to-do list. This log was
appended at each milestone; each heading corresponds to one commit on `main`.

## Credentials check (10:24 PM)

The user pasted an SSH key entry from their GitHub settings ("Skibidi",
`SHA256:uyJjZkCd...`, read/write) and asked "Is this usable". Checks on the VM:

- `~/.ssh/` contains only `known_hosts`; no ssh-agent; `ssh -v -T git@github.com` tries every
  default identity path and ends with `Permission denied (publickey)`.
- The only secrets injected into the run are unrelated TextNow ones.

Answer given: a fingerprint only identifies the public key registered on GitHub; the private
half lives on the user's machine and cannot be used from the VM unless added as a Cloud Agent
secret (`GITHUB_SSH_PRIVATE_KEY`), and a fine-grained PAT (`GITHUB_TOKEN`) with Contents,
Administration, Secrets, Workflows and Actions permissions is the safer option. Secrets are
injected only into new VMs, so the push happens in a later run. Work continued locally in a
git repository under `/agent/uarb-regulatory-agent`.

## Commit 1: Bootstrap project

- `python3 -m venv` failed with "ensurepip is not available"; worked around with
  `python3 -m venv --without-pip` plus `get-pip.py`.
- `pyproject.toml` (playwright, pydantic-settings, typer, rich; dev: pytest, ruff),
  `.gitignore`, MIT `LICENSE`, `.env.example`, `config.py` (`Settings` from env/.env),
  `models.py` (dataclasses shared by all layers), README skeleton, and
  `docs/chat-history/01-planning.md`.
- Playwright 1.64.1 and its Chromium headless shell were installed in the background.

## Commit 2: Deterministic request parser

`intent.py`: `M\d{5}` plus five document type patterns with common variants ("other docs",
"key docs", "exhibit", "audio"). Quoted replies (`>` lines, "On ... wrote:") are stripped so a
reply to the agent's own email does not re-parse the counts sentence. Ambiguous requests (two
matters, two types) are rejected with a reason, except when the second type is negated
("other documents, not exhibits"). 17 tests.

## Commit 3: Playwright scraper

Before writing selectors the agent ran two throwaway exploration scripts (kept under `/tmp`,
not committed) against the live portal and dumped the DOM at each step. Findings that shaped
the code:

- The matter box is a `div.text[tabindex=0]` that becomes `contenteditable="true"` once
  clicked; `keyboard.type` + Enter works. A first attempt that typed too early lost the text,
  so the scraper now waits for `contenteditable` and verifies the box content before Enter.
- Element ids encode layout object and row (`b0p1o299i3i0r1`), so nothing uses ids.
- Rows are Vaadin grid rows `tr.v-grid-row-has-data`; a 2400px viewport renders ~33 of 44.
  Row `innerText` for Other Documents: `104160 / title / 10/28/2026 / Public / Preview /
  GO GET IT / .pdf`; for Exhibits the exhibit number (`H-1`) comes after the security value.
- The selected tab segment gets class `fm-selected`; clicking a tab navigates to a different
  FileMaker layout (object ids change) but the header stays visible.
- `GO GET IT` opens a `Download Files` dialog whose file button is `.v-button.fm-download-button`
  (not an anchor). Playwright's `expect_download` captured
  `/fmi/webd/APP/connector/0/2264/dl/102266.pdf` (284,667 bytes).
- Unknown matter: dialog `No Records Found`. Empty tab: dialog `No Matching Records`.
- Re-using a browser context to `goto` the portal again never showed the landing page (the
  WebDirect session resumed on the matter page), so every attempt uses a fresh context.

Header parsing: first version classified labels by element type and failed on the live page
because labels and values are both plain text blocks. Rewritten to classify by content (a block
whose lines are all known field names is a label) and match values to the nearest label above
them by geometry. The live test then passed: title, Water / Capital Expenditure Approvals,
04/07/2025, 10/23/2025, Closed, "Directions given", counts 13/6/44/0/0, two PDFs downloaded.
Also fixed: `normalise_label` left double spaces ("title   description").

Live tests (`UARB_LIVE=1 pytest tests/live`): happy path, unknown matter, empty tab. 26 s.

## Commit 4: Optional LLM adapter

`llm.py`: OpenAI/Anthropic clients behind `LLM_PROVIDER`, JSON extraction fallback, and reply
polish guarded by `facts_preserved` (every number, dollar amount, matter id and file name must
survive). SDKs are optional extras; nothing imports them unless configured.

## Commit 5: ZIP builder and reply composer

- `zipper.py`: `<matter>-<type>.zip` + `MANIFEST.csv`; drops the largest file repeatedly until
  under `MAX_ATTACHMENT_MB`. A test for the size guard initially used repetitive bytes that
  compressed to nothing; switched to `os.urandom`.
- `summary.py`: reply modelled on the assignment example. `counts_sentence(TabCounts(13, 6,
  44, 0, 0))` renders exactly "I found 13 Exhibits, 6 Key Documents, 44 Other Documents, and no
  Transcripts or Recordings (63 files in total)." Dates render as "April 7, 2025". Error and
  help replies for parse failures, unknown matters, rate limiting and portal errors.

## Commit 6: Mail layer, orchestrator and CLI

- `mail/`: `ImapInbox` (UNSEEN + `BODY.PEEK[]`, `\Seen` only after handling), `SmtpOutbox`
  (STARTTLS or implicit TLS, threaded reply, `Auto-Submitted: auto-replied`), RFC 822 parsing
  with HTML-to-text and auto-reply/bounce detection, `write_eml` for dry runs, fakes.
- `agent.py`: allow-list, per-sender rate limit, parse (LLM fallback), scrape, zip, summary,
  send with three attempts, work-dir cleanup.
- `cli.py`: `run` (one request, `--dry-run` writes the `.eml` and ZIP), `poll-once`, `serve`,
  `check-config`, `version`.

First full live dry run, `uarb-agent run --matter M12205 --type "Other Documents" --dry-run`:
10 PDFs, 6.37 MB ZIP, reply text as shown in the README, 22 seconds.

Two of the ten files (102329.pdf and 102330.pdf) were byte-identical. Re-downloading each row
independently by its title in a fresh session produced the same bytes with different server
supplied filenames, so the portal itself hosts one PDF under two document numbers; the scraper
is correct.

## Commit 7: Docker and GitHub Actions

- `Dockerfile` on `mcr.microsoft.com/playwright/python:v1.64.1-noble`, pinned to the same
  Playwright version as `pyproject.toml` (browser builds are version-locked); non-root; `serve`
  by default. `docker-compose.yml` with `.env`, a data volume and 1 GB `/dev/shm`.
- `ci.yml`: ruff + pytest on push/PR, optional live tests via `workflow_dispatch`, Docker build
  plus a fake dry-run smoke test.
- `agent.yml`: cron every 10 minutes with repository secrets, `workflow_dispatch` inputs to
  answer one request directly, a concurrency group so pollers never overlap, and a clean skip
  when secrets are absent (forks do not fail).
- Docker is not installed on the build VM, so the image build is validated by CI after push.

## Commit 8: Transport verification and wording

- Added `tests/test_mail_transport.py`: `SmtpOutbox` against a local `aiosmtpd` server (checks
  envelope, threading headers, text + HTML + ZIP parts) and `ImapInbox` against a protocol stub
  (asserts `BODY.PEEK[]` is used and `\Seen` is set only on handled messages).
- Live dry runs on other paths: `M12205` Exhibits with `--limit 3` and `M12383` Key Documents
  (title "Municipal Boundary - Town of Amherst - 2025 Application for Mutual Boundary Change",
  Other / Municipal Boundaries, Closed, Allowed/Approved, July 10 2025 to November 28 2025,
  counts 6/4/18/0/0).
- The Exhibits run showed H-1.pdf (46 MB) and H-3.pdf (26 MB) being dropped by the size guard,
  which is correct, but the sentence read "attaching it as a ZIP ... I left out H-1.pdf,
  H-3.pdf". Rewritten to: "I downloaded 3 out of the 13 Exhibits. 2 of them (H-1.pdf at
  46.1 MB and H-3.pdf at 26.4 MB) exceed the 20 MB email attachment limit, so the attached ZIP
  (M12205-exhibits.zip) contains the remaining 1 file."

Final local state: `ruff check .` clean, 50 unit tests passing, 3 live tests passing.
