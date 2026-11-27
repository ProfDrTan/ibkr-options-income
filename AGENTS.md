# Instructions for AI agents (ChatGPT/Codex, Claude, any other)


**This is the `main` branch. All trading-bot work is on `discipline-bot`: switch to it and read HANDOFF.md there.**
1. Work on branch **`discipline-bot`**. Run `git pull --rebase` first.
2. Read **`HANDOFF.md`** in full before doing anything. It is the single source of truth: current state, how to control the bot,
   owner decisions, open tasks and the change log.
3. Follow its §0 handoff protocol: every change updates `HANDOFF.md` (state, open tasks, a new change-log entry) **in the same commit**.
4. You run in the cloud, not on the owner's PC. Never ask him to stop `run_bot.bat`. Never switch live/paper or set `ALLOW_LIVE`.
