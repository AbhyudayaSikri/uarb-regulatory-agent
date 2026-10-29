"""Command line entry point: ``uarb-agent serve | poll-once | run | check-config``."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler

from . import __version__
from .agent import Agent
from .config import Settings, get_settings
from .mail import FakeOutbox, ImapInbox, SmtpOutbox, write_eml
from .models import DocumentType, MatterRequest, OutboundMessage
from .scraper.fake import FakeScraper
from .scraper.uarb import UarbScraper

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Email-driven agent that fetches UARB filings and replies with a ZIP.",
)
console = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="%H:%M:%S",
        handlers=[RichHandler(console=console, show_path=False, rich_tracebacks=True)],
    )
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def _doc_type(value: str) -> DocumentType:
    cleaned = " ".join(value.replace("-", " ").replace("_", " ").split()).title()
    aliases = {"Other Docs": "Other Documents", "Key Docs": "Key Documents",
               "Exhibit": "Exhibits", "Transcript": "Transcripts", "Recording": "Recordings"}
    cleaned = aliases.get(cleaned, cleaned)
    try:
        return DocumentType(cleaned)
    except ValueError as exc:
        raise typer.BadParameter(
            f"{value!r} is not one of {', '.join(d.value for d in DocumentType)}"
        ) from exc


def _build_agent(settings: Settings, *, fake: bool, dry_run: bool, with_inbox: bool) -> Agent:
    scraper = FakeScraper() if fake else UarbScraper(settings)
    inbox = ImapInbox(settings) if with_inbox else None
    outbox = FakeOutbox() if dry_run else SmtpOutbox(settings)
    return Agent(settings, scraper, inbox=inbox, outbox=outbox, keep_files=dry_run)


def _print_reply(reply: OutboundMessage) -> None:
    console.rule("[bold]Reply")
    console.print(f"[bold]To:[/bold] {reply.to}")
    console.print(f"[bold]Subject:[/bold] {reply.subject}")
    if reply.attachments:
        sizes = ", ".join(f"{p.name} ({p.stat().st_size / 1024 / 1024:.2f} MB)"
                          for p in reply.attachments)
        console.print(f"[bold]Attachments:[/bold] {sizes}")
    console.print()
    console.print(reply.text_body)
    console.rule()


def _dump_eml(reply: OutboundMessage, settings: Settings) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = Path(settings.work_dir) / f"reply-{stamp}.eml"
    write_eml(reply, settings.smtp_from or "agent@example.com", path)
    return path


@app.callback()
def _main(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    _setup_logging(verbose)


@app.command()
def version() -> None:
    """Print the version."""
    console.print(__version__)


@app.command("check-config")
def check_config() -> None:
    """Show the effective configuration (secrets masked)."""
    s = get_settings()
    imap = f"{s.imap_user} on {s.imap_host}:{s.imap_port}/{s.imap_folder}"
    rows = {
        "portal_url": s.portal_url,
        "imap": imap if s.imap_host else "(not set)",
        "smtp": f"{s.smtp_from} via {s.smtp_host}:{s.smtp_port}" if s.smtp_host else "(not set)",
        "max_documents": s.max_documents,
        "max_attachment_mb": s.max_attachment_mb,
        "poll_interval_seconds": s.poll_interval_seconds,
        "browser": f"{s.browser_channel} headless={s.headless}",
        "allowed_senders": ", ".join(s.allowed_senders) or "(anyone)",
        "rate_limit_per_sender_per_hour": s.rate_limit_per_sender_per_hour,
        "llm_provider": s.llm_provider,
        "work_dir": str(s.work_dir),
        "mail_configured": s.mail_configured,
    }
    for key, value in rows.items():
        console.print(f"[bold]{key:32}[/bold] {value}")


@app.command()
def run(
    matter: Annotated[str, typer.Option("--matter", "-m", help="Matter number, e.g. M12205")],
    doc_type: Annotated[str, typer.Option("--type", "-t", help="Exhibits | Key Documents | "
                                          "Other Documents | Transcripts | Recordings")],
    to: Annotated[str, typer.Option("--to", help="Recipient of the reply")] = "",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Do everything except send; write "
                                          "the .eml and ZIP under WORK_DIR")] = False,
    fake: Annotated[bool, typer.Option("--fake", help="Use canned data instead of the live "
                                       "portal")] = False,
    limit: Annotated[int | None, typer.Option("--limit", help="Override MAX_DOCUMENTS")] = None,
) -> None:
    """Handle one request from the command line instead of from an email."""
    settings = get_settings()
    if limit is not None:
        settings = settings.model_copy(update={"max_documents": limit})
    if not dry_run and not to:
        raise typer.BadParameter("--to is required unless --dry-run is given")
    if not dry_run and not settings.smtp_host:
        raise typer.BadParameter("SMTP is not configured; use --dry-run or set SMTP_* variables")

    request = MatterRequest(matter, _doc_type(doc_type))
    agent = _build_agent(settings, fake=fake, dry_run=dry_run, with_inbox=False)
    result = agent.process_request(request, to=to or "you@example.com", send=not dry_run)

    if result.reply is None:
        console.print(f"[red]failed:[/red] {result.error}")
        raise typer.Exit(code=1)
    _print_reply(result.reply)
    if dry_run:
        path = _dump_eml(result.reply, settings)
        console.print(f"[green]dry run:[/green] wrote {path}")
    elif result.ok:
        console.print(f"[green]sent[/green] to {to}")
    raise typer.Exit(code=0 if result.ok else 1)


@app.command("poll-once")
def poll_once(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Fetch and process requests but "
                                          "print replies instead of sending them")] = False,
    fake: Annotated[bool, typer.Option("--fake", help="Use canned data instead of the live "
                                       "portal")] = False,
) -> None:
    """Process every unread request in the mailbox once, then exit (for cron / Actions)."""
    settings = get_settings()
    if not settings.imap_host:
        raise typer.BadParameter("IMAP is not configured; set IMAP_* variables")
    agent = _build_agent(settings, fake=fake, dry_run=dry_run, with_inbox=True)
    results = agent.poll_once()
    handled = sum(1 for r in results if r.ok)
    for r in results:
        if r.reply is not None and dry_run:
            _print_reply(r.reply)
            _dump_eml(r.reply, settings)
    console.print(f"processed {len(results)} message(s), {handled} fulfilled")


@app.command()
def serve(
    fake: Annotated[bool, typer.Option("--fake", help="Use canned data instead of the live "
                                       "portal")] = False,
) -> None:
    """Poll the mailbox forever (for Docker / a long-lived host)."""
    settings = get_settings()
    if not settings.mail_configured:
        raise typer.BadParameter("IMAP and SMTP must both be configured for serve mode")
    agent = _build_agent(settings, fake=fake, dry_run=False, with_inbox=True)
    try:
        agent.serve()
    except KeyboardInterrupt:
        console.print("stopped")


if __name__ == "__main__":  # pragma: no cover
    app()
