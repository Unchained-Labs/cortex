# Examples

Copy these into a brain to see each plugin surface working:

- `plugins/dice.py` → `<brain>/plugins/` — a drop-in tool plugin
- `skills/weekly-review/` → `<brain>/skills/` — an agentskills.io skill

Connectors follow the same drop-in shape (`<brain>/connectors/*.py` with
`sync(out_dir, settings)`); `src/cortex/connectors/calendar_ics.py` is the
built-in reference implementation.
