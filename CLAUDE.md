# Instructions for AI agents (ChatGPT/Codex, Claude, any other)

1. Work on branch **`discipline-bot`**. Run `git pull --rebase` first.
2. Read **`HANDOFF.md`** in full before doing anything. It is the single source of truth: current state, how to control the bot,
   owner decisions, open tasks and the change log.
3. Follow its §0 handoff protocol: every change updates `HANDOFF.md` (state, open tasks, a new change-log entry) **in the same commit**.
4. You run in the cloud, not on the owner's PC. Never ask him to stop `run_bot.bat`. Never switch live/paper or set `ALLOW_LIVE`.
5. **If you cannot push directly (403), open a pull request into `discipline-bot` instead** and give the owner its link.
   He merges it with one tap on his phone; the bot picks it up within a minute. Don't retry the push.
6. **Save the owner's tokens:** don't re-run checks the journal already answers (read `data/live/events.csv` first). Answer status
   questions from the journal files. Do the task, report in a few lines with links, stop.
