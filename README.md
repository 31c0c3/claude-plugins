# claude-plugins

[Claude Code](https://code.claude.com) plugins by 31c0c3.

## zed-diff

Review every Claude Code edit in a [Zed](https://zed.dev) diff before it's applied:
left is the current file, right is Claude's proposal, and the right side is editable.
It shows only the changed hunks, with a few lines of context, not the whole file.
Your terminal comes back to the front when you close the diff.

The diff waits until you're looking at the session: its terminal app in front and, in
[Herdr](https://herdr.dev), its tab focused. After 20 minutes away, Claude Code asks in its prompt instead.

### Install

```bash
claude plugin marketplace add 31c0c3/claude-plugins
claude plugin install zed-diff@31c0c3
```

In an open session, run `/reload-plugins`. Then append the entries from [`zed/keymap.json`](plugins/zed-diff/zed/keymap.json) and
[`zed/tasks.json`](plugins/zed-diff/zed/tasks.json) to the same files in `~/.config/zed/`.

Needs macOS, Zed's `zed` CLI on `PATH`, and `python3`. Without them the hook steps aside
and Claude Code asks as usual.

### Keys

| Key | Result |
|---|---|
| `cmd-enter` / `cmd-w` | Accept, including your edits |
| `cmd-alt-z` | Reject the block under the cursor (all blocks = reject) |
| `cmd-escape` | Reject |
| `cmd-shift-escape` | Decide in Claude Code's prompt: Yes or No, with an optional note |

### Comments for Claude

Add a comment on a line of its own: a request or a question for Claude.

```python
# why not a template?
return f"Bye, {name}."
```

The hook removes it from the file and sends it to Claude, on accept or reject.
Comment lines in a row form one note. In Markdown and HTML, write `<!-- note -->`.

These stay in the file as code:
- a comment after code on the same line (`x = 1  # note`)
- an edit to one of Claude's comments
- comments in a file type the hook doesn't know (`COMMENT_SYNTAX` in
  [`zed_diff.py`](plugins/zed-diff/scripts/zed_diff.py)), such as plain text

### On / off

`/plugin` → Installed → Space, or `claude plugin disable zed-diff@31c0c3`
(add `--scope local` for the current project only). Accept-edits mode skips the diff.
