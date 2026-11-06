# Known issues

## P13 — Markdown tables render as a run-on line in Hermes chat

**Status:** open; upstream Hermes Webapp limitation.
**Scope:** chat rendering only. No EOH code change in this task; no router or
tool change.

### Verdict

The run-on table is not produced by an EOH tool. EOH chat results are JSON
payloads, not Markdown tables. The chat transcript is rendered by the Hermes
Webapp, so table support (GFM pipe tables) belongs to the upstream Webapp
Markdown renderer. If a table arrives as one line there, the Webapp is either
not enabling GFM tables or its sanitizer is flattening the pipe rows.

### Evidence in this tree

- `gev/detail.py` turns chat-facing tool results into JSON strings
  (`render_obj`, `render_mcp`). It does not emit pipe tables.
- `gev/router.py` dispatches the eleven composable tools; their results are
  JSON objects. There is no table formatter on the chat path.
- EOH does emit a GFM table in one place: `gev/investigations.py`
  `render_markdown` writes the investigation report's pin table as
  `| # | Label | Lat | Lon | Drawer |` plus a `|---|...|` separator.
  `godseye_investigation_report` returns the saved report's file paths in
  JSON (see `gev/composer.py`), not the table itself. That file renders
  correctly in GitHub, a browser, or any GFM-aware viewer.
- `skills/godseye-analyst/SKILL.md` contains static GFM tables used as model
  guidance. Those are instructions, not chat output.
- Separate and narrower: the EOH pin drawer's own Markdown subset in
  `gev/panel_pins.js` (`renderMarkdown`) supports headings, lists, rules, and
  inline styling but has no table branch. A table placed in a pin's
  `description_md` would collapse inside the drawer. This is not the Hermes
  chat issue and is not changed by P13.

### Why there is no EOH code fix

Changing EOH output would not fix a table the model types directly into chat,
and the Webapp owns the chat renderer. Rewriting EOH tool payloads as prose
would hide useful structured data without guaranteeing anything about the
Webapp's Markdown support. The correct fix is in the upstream Hermes Webapp
Markdown renderer (enable GFM tables, or preserve table line breaks). Report
it there rather than patching the plugin.

### Safer list format (recommendation only)

For multi-column summaries that must survive chat rendering, prefer one
bullet per row and `Label: value` pairs. Lists, bold, and line breaks are
widely supported; pipe tables are not.

```markdown
**Pin 1 — Louvre**
- Lat: 48.8606
- Lon: 2.3376
- Drawer: yes

**Pin 2 — Pavillon de l'Horloge**
- Lat: 48.8590
- Lon: 2.3450
- Drawer: no
```

A compact single-line form also works:

```markdown
- **Louvre** — 48.8606, 2.3376 — drawer: yes
- **Pavillon de l'Horloge** — 48.8590, 2.3450 — drawer: no
```

For saved report files, keeping the real GFM table is fine because file
viewers render it. A future optional list mirror in the report writer would
be a separate enhancement, not part of P13.

### Workaround for users

In chat, ask for "one bullet per place" or "a list, not a table" until the
Webapp renderer supports GFM tables. EOH's `/gev` command output already uses
plain `key: value` lines, so it is unaffected.
