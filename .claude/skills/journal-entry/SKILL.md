---
name: journal-entry
description: Append a Builder Journal entry to JOURNAL.md before pushing. Use whenever a work session is about to push commits, or when the check-journal-before-push hook blocks a push.
---

# Writing a JOURNAL.md entry

The journal is a first-class deliverable: a running, honest log of building this system on
the Pydantic stack. Every push carries one new entry.

## Format

- Insert the new entry directly under the `---` separator at the top of the file — **newest
  entries on top**. Entry 0 (the origin story) stays at the bottom.
- Heading: `## Entry N — YYYY-MM-DD — <one-line summary>` where N continues the sequence.
- Never rewrite or edit old entries. Corrections and follow-ups go in NEW entries.

## Content checklist (cover all that apply)

1. **What was built** — the change, and the reasoning that shaped it.
2. **Stack learnings** — insights about Pydantic AI / Logfire / Pydantic Evals / Gateway;
   API surprises, patterns that worked, patterns that fought back.
3. **Issues hit** — what broke, the diagnosis, the workaround. Failed approaches are content,
   not embarrassment.
4. **Limitations knowingly accepted** — with the reason they're acceptable.
5. **Dev-ex impressions** — good and bad, specific.
6. **Cost** — what the session spent (state `$0` explicitly for offline work).

## Style

Dense prose over bullets-of-nothing; name files/functions concretely; write for a future
maintainer deciding whether to trust a design decision. Bold the load-bearing findings.
