# UARB Regulatory Agent

An email-driven agent that fetches regulatory filings from the
[Nova Scotia Utility and Review Board public documents portal](https://uarb.novascotia.ca/fmi/webd/UARB15),
zips up to 10 of them, and replies with the ZIP plus a summary of the matter.

Send it an email like:

> Hi Agent, can you give me Other Documents files from M12205? Thanks!

and it replies with something like:

> Hi User,
>
> M12205 is about the Halifax Regional Water Commission - Windsor Street Exchange
> Redevelopment Project - $69,275,000. It relates to Capital Expenditure within the Water
> category. The matter is Closed with outcome "Directions given". It had an initial filing on
> April 7, 2025 and a final filing on October 23, 2025. I found 13 Exhibits, 6 Key Documents,
> 44 Other Documents, and no Transcripts or Recordings (63 files in total). I downloaded 10 out
> of the 44 Other Documents and am attaching them as a ZIP here.

Status: work in progress. See `docs/chat-history/` for the step-by-step build log.
