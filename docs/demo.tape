# vhs script for the README demo GIF (https://github.com/charmbracelet/vhs).
# Rendering requires vhs installed AND one real (paid, ~$0.50) run — part of the
# live-validation checklist in the README; not run in CI.
#
#   vhs docs/demo.tape

Output docs/demo.gif

Set FontSize 15
Set Width 1100
Set Height 640
Set Theme "Catppuccin Mocha"
Set TypingSpeed 40ms

Type "uv run deepresearch 'What are the main approaches to LLM hallucination detection?' --depth quick"
Sleep 1s
Enter

# quick run: plan -> 3 researchers -> verification -> synthesis -> summary table
Sleep 150s

Type "ls runs/ && head -30 runs/*/report.md"
Sleep 1s
Enter
Sleep 8s
