# claude-plugins

[Claude Code](https://code.claude.com) plugins by 31c0c3.

## zed-diff

Review every Claude Code edit in a [Zed](https://zed.dev) diff before it's applied:
left is the current file, right is Claude's proposal, and the right side is editable.
Your terminal comes back to the front when you close the diff.

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

Add a comment that starts or ends with `AI!` (change this), `AI?` (question) or `AI` (note),
the [Aider](https://aider.chat/docs/usage/watch.html) convention:

```python
return f"Bye, {name}."  # why not a template? AI?
```

The hook removes it from the file and sends it to Claude, on accept or reject.
In Markdown, a plain line starting or ending with `AI!` / `AI?` works too.

### On / off

`/plugin` → Installed → Space, or `claude plugin disable zed-diff@31c0c3`
(add `--scope local` for the current project only). Accept-edits mode skips the diff.
