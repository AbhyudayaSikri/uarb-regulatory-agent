# Chat history 01: planning

This folder is a faithful record of the conversation between the project owner and the
Cursor cloud agent that built this repository. It is kept so the step-by-step process is
visible, not just the end result. Nothing here is reconstructed after the fact; each file
was written while the work described in it was happening. Timestamps are America/Toronto.

## Session 1: 2026-10-28, 9:12 PM

### User

> Co-op Technical Assignment - Winter 2027.pdf
>
> Here are your instructions, create a plan to make this on my personal github which you
> have SSH access to. This is a non textnow private project. I want you to make a plan to
> implement this on Github and publish it for any user to use as per the PDF instructions.
> Make sure to generate chat histories between us in the github repo to show that this was
> a step by step process.

The PDF (Senpilot's co-op technical assignment, not reproduced here because it is marked
confidential) asks for an AI agent that:

1. Receives an email containing a matter number (`M12205`, `M12383`, ...) and a document
   type (Exhibits, Key Documents, Other Documents, Transcripts, Recordings).
2. Opens https://uarb.novascotia.ca/fmi/webd/UARB15, enters the matter number in the
   "Go Directly to Matter" box, presses Search, opens the relevant tab, downloads up to 10
   documents with the "Go Get It" button and zips them.
3. Replies by email with the ZIP attached, the total count of files in the matter, and a
   summary of the matter's key metadata (title, type/category, initial and final filing
   dates, per-tab counts, how many were downloaded).

### Agent: environment and research

Checks performed before planning (all read-only):

- `ssh -T git@github.com` -> `Permission denied (publickey)`; `gh auth status` -> not logged
  in. No GitHub credential is present in the VM, so repo creation/push is blocked on the
  user adding one.
- Python 3.12, Node 22, Google Chrome 156 are installed; Playwright is not.
- The portal is reachable from the VM. It is a FileMaker WebDirect app (Vaadin 8), fully
  JavaScript rendered, no login, no deep links.
- A browser-use subagent explored the portal with matter M12205 and produced a research
  report. Key findings:
  - The "Go Directly to Matter" box is a `div.text[tabindex=0]` with a sibling
    `div.placeholder` reading `eg M01234`. Typing the matter and pressing Enter navigates
    straight to the matter page.
  - Header fields: Matter No, Status, Title - Description, Type, Category, Date Received,
    Date Final Submissions, Outcome. For M12205: "Halifax Regional Water Commission -
    Windsor Street Exchange Redevelopment Project - $69,275,000", Water / Capital
    Expenditure, received 04/07/2025, final submissions 10/23/2025, Closed, "Directions
    given".
  - Tabs carry counts in their labels: `Exhibits - 13`, `Key Documents - 6`,
    `Other Documents - 44`, `Transcripts - 0`, `Recordings - 0`, plus Hearings and Related
    Matters. (The PDF example says 5 Key Documents and 21 Other Documents; the matter has
    grown since the assignment was written.)
  - Empty tabs pop a "No Matching Records" dialog with an OK button.
  - Rows: Doc No / Exhibit No, Security (Public or Confidential), Title, file type, Date,
    `GO GET IT`, `Preview`. The grid is virtualized, so only visible rows are in the DOM.
  - `GO GET IT` opens a "Download Files" modal with a filename link; clicking it downloads
    from `/fmi/webd/UARB15/container/data/...` with
    `Content-Disposition: attachment; filename="<docno>.pdf"`. A Close button dismisses it.

### Agent: plan (approved by the user at 10:22 PM)

- Stack: Python 3.12, Playwright (Chromium), stdlib `imaplib`/`smtplib`/`email`/`zipfile`,
  `pydantic-settings`, `typer`, `pytest`, `ruff`. Optional OpenAI/Anthropic adapter behind
  `LLM_PROVIDER`; the agent is fully functional without an LLM key.
- "Up to 10 documents" = the first 10 rows in the tab's default order; confidential or
  undownloadable rows are skipped and the next row is tried; the email says exactly what
  was attached.
- ZIP size guard (`MAX_ATTACHMENT_MB`, default 20) drops the largest files until the
  attachment fits, and the email says so.
- Deliverables: public GitHub repo with README quick start, Docker image, GitHub Actions
  scheduled runner (zero-infra deployment), unit tests with fake mail/scraper, an opt-in
  live test against M12205, and this chat history.
- Commits land directly on `main` in small steps so `git log` shows the progression.

### Session 2: 2026-10-28, 10:22 PM

User: "Implement the plan as specified."

User (10:24 PM) asked whether an existing SSH key on their GitHub account ("Skibidi",
`SHA256:uyJjZkCdYuxehUxk3O0WzUNf/Kg5/Izds2HWwoNvEHw`) is usable. Agent checked the VM:
no private key under `~/.ssh`, no ssh-agent, no GitHub secret injected. Answer: the
fingerprint only identifies the public key registered on GitHub; the private half lives on
the user's machine, so it cannot be used from the VM unless added as a Cloud Agent secret.
A fine-grained PAT (`GITHUB_TOKEN`) was recommended instead. The build continues locally in
the meantime and will be pushed once a credential is available.
