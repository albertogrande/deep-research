#!/usr/bin/env bash
# PreToolUse hook (matcher: Bash) — the journal mandate, enforced deterministically instead
# of hoped-for: block `git push` unless the outgoing commits touch JOURNAL.md.
# Exit 0 = allow; exit 2 = block and surface the message below to the agent.
set -euo pipefail

command=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input", {}).get("command", ""))')

case "$command" in
  *"git push"*) ;;
  *) exit 0 ;;
esac

# Outgoing commits: upstream..HEAD when an upstream exists; otherwise (first push of a new
# branch) fall back to the last commit — imperfect but errs on the annoying-not-silent side.
range="@{upstream}..HEAD"
if ! git rev-parse --abbrev-ref '@{upstream}' >/dev/null 2>&1; then
  range="HEAD~1..HEAD"
fi

if git diff --name-only "$range" 2>/dev/null | grep -qx "JOURNAL.md"; then
  exit 0
fi

echo "Blocked: pushing without a JOURNAL.md entry. Append a dated entry (newest on top) \
covering what was built, learnings, issues, accepted limitations, dev-ex notes, and cost — \
commit it, then push. Format: .claude/skills/journal-entry/SKILL.md" >&2
exit 2
