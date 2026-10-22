"""Claude Code PreToolUse hook: review each Edit/Write in a Zed diff before it's applied.

Keys and AI comments: see the README. Zed side: zed/keymap.json and zed/tasks.json.
"""

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

# The Zed reject keys write "reject" or "ask" here (zed/tasks.json), but only while the
# file exists, so they do nothing outside a diff opened by this hook.
DECISION = os.path.expanduser("~/.claude/zed-diff/decision")
SKIP_MODES = ("acceptEdits", "bypassPermissions")  # edits are auto-approved anyway
# how long to wait for you to come back to this session before asking in its prompt instead;
# under the hook's 1800 s timeout, to leave time for the review
FOCUS_WAIT = 1200

# a comment that starts or ends with AI, AI! or AI? (Aider's convention); an HTML or
# Markdown one whole, its `<!--` before `--` and its `-->` dropped
AI_COMMENT = re.compile(
    r"\s*(?:<!--|#|//|--|;+)\s*((?<!\w)AI[!?]?(?!\w).*?|.*?(?<!\w)AI[!?]?)\s*(?:-->)?\s*$",
    re.IGNORECASE,
)
# or, for files without line comments (Markdown, text): a whole line starting or ending with AI! / AI?
AI_LINE = re.compile(r"^\s*((?<!\w)AI[!?](?!\w).*|.*(?<!\w)AI[!?])\s*$", re.IGNORECASE)


def extract_ai_comments(proposed, edited):
    """Strip AI comments from lines added in the diff; return (text, notes)."""
    ed_lines = edited.splitlines(True)
    kept, notes = [], []
    ops = difflib.SequenceMatcher(
        None, proposed.splitlines(True), ed_lines, autojunk=False
    )
    for tag, _, _, j1, j2 in ops.get_opcodes():
        for line in ed_lines[j1:j2]:
            m = (
                (AI_COMMENT.search(line) or AI_LINE.match(line))
                if tag != "equal"
                else None
            )
            if not m:
                kept.append(line)
                continue
            # the note points at the next kept line
            notes.append((len(kept), m.group(1).strip()))
            code = line[: m.start()].rstrip()
            if code:
                kept.append(code + "\n" if line.endswith("\n") else code)
    out = []
    for i, comment in notes:
        target = (
            (kept[i].strip() or "(blank line)") if i < len(kept) else "(end of file)"
        )
        out.append(f"line {i + 1} `{target}`: {comment}")
    return "".join(kept), out


def front_app():
    def run(*args):
        return subprocess.run(args, check=False, capture_output=True, text=True).stdout

    asn = run("lsappinfo", "front").strip()
    info = run("lsappinfo", "info", "-only", "bundleid", asn)
    return info.rpartition("=")[2].strip().strip('"') or None


def session_in_front():
    """Whether you're looking at this session: its terminal in front and, in Herdr, its tab."""
    terminal = os.environ.get("__CFBundleIdentifier")  # the app Claude Code runs in
    if terminal and front_app() != terminal:
        return False
    tab = os.environ.get("HERDR_TAB_ID")
    if not tab:
        return True
    try:
        out = subprocess.run(
            ["herdr", "tab", "get", tab], check=True, capture_output=True, text=True
        ).stdout
        return json.loads(out)["result"]["tab"]["focused"]
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError):
        return True  # can't tell: don't hold the edit back


def wait_for_session():
    """Wait until you're looking at this session, so Zed doesn't pop up over other work."""
    deadline = time.monotonic() + FOCUS_WAIT
    while not session_in_front():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.5)
    return True


def review_in_zed(path, orig, new):
    """Open the diff and wait for the tab to close.

    Returns (edited, decision) with decision "", "reject" or "ask"; None when Zed failed.
    """
    tmp = tempfile.mkdtemp(prefix="claude-diff-")
    name = os.path.basename(path)  # keep the extension for syntax highlighting
    a, b = (os.path.join(tmp, side, name) for side in ("current", "proposed"))
    for p, text in ((a, orig), (b, new)):
        os.makedirs(os.path.dirname(p))
        with open(p, "w") as f:
            f.write(text)
    os.makedirs(os.path.dirname(DECISION), exist_ok=True)
    open(DECISION, "w").close()

    prev_app = front_app()  # e.g. your terminal, to return to after the diff
    try:
        # directories, not files: Zed then opens its multi-diff view, which shows only
        # the changed hunks instead of the whole file
        dirs = (os.path.dirname(a), os.path.dirname(b))
        zed = subprocess.run(["zed", "--wait", "--diff", *dirs], check=False)
        # switch back right away, without waiting: Zed stays in front until then
        if prev_app:
            subprocess.Popen(["open", "-b", prev_app])
        if zed.returncode != 0:
            return None
        # the reject task may land just after the tab closes, so give it a moment
        decision = ""
        for _ in range(10):
            with open(DECISION) as f:
                decision = f.read().strip()
            if decision:
                break
            time.sleep(0.05)
        with open(b) as f:
            return f.read(), decision
    finally:
        if os.path.exists(DECISION):
            os.remove(DECISION)
        shutil.rmtree(tmp, ignore_errors=True)


def decide(tool_name, tool_input, orig, new, edited, decision=""):
    """Turn the review result into the hook's hookSpecificOutput."""
    edited, notes = extract_ai_comments(new, edited)
    out = {"hookEventName": "PreToolUse", "permissionDecision": "allow"}
    messages = []
    if decision == "reject" or (not decision and edited == orig and new != orig):
        out["permissionDecision"] = "deny"
        messages.append("The user rejected this change in the Zed diff view.")
    else:
        if decision == "ask":  # Yes/No in Claude Code's prompt, with an optional note
            out["permissionDecision"] = "ask"
            out["permissionDecisionReason"] = (
                "Decide here: Yes or No, and add a note if you like"
            )
        if edited != new:
            user_diff = "".join(
                difflib.unified_diff(
                    new.splitlines(True),
                    edited.splitlines(True),
                    "your proposal",
                    "applied",
                )
            )
            messages.append(
                f"The user edited your change to {tool_input['file_path']} in Zed. "
                f"The file holds their version; build on it, not on your proposal. "
                f"Their edits:\n{user_diff}"
            )
            # updatedInput replaces the whole tool input, so start from the original
            out["updatedInput"] = {
                **tool_input,
                **(
                    {"content": edited}
                    if tool_name == "Write"
                    else {
                        "old_string": orig,
                        "new_string": edited,
                        "replace_all": False,
                    }
                ),
            }
    if notes:
        messages.append(
            "The user left review comments in the diff; the hook removed them "
            "from the file. Act on each AI! comment and answer each AI? question:\n"
            + "\n".join(f"- {n}" for n in notes)
        )
    if messages:
        key = (
            "permissionDecisionReason"
            if out["permissionDecision"] == "deny"
            else "additionalContext"
        )
        out[key] = "\n\n".join(messages)
    return out


def main():
    data = json.load(sys.stdin)
    if (
        data.get("permission_mode") in SKIP_MODES
        or sys.platform != "darwin"
        or not shutil.which("zed")
    ):
        return  # no output: Claude Code's normal permission flow applies

    tool_name, tool_input = data["tool_name"], data["tool_input"]
    path = tool_input["file_path"]
    orig = ""
    if os.path.exists(path):
        with open(path) as f:
            orig = f.read()
    if tool_name == "Write":
        new = tool_input["content"]
    else:
        count = -1 if tool_input.get("replace_all") else 1
        new = orig.replace(tool_input["old_string"], tool_input["new_string"], count)
    if new == orig:
        return  # nothing to review, e.g. old_string not found; the tool reports it

    if not wait_for_session():
        out = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": "You were away, so no Zed diff: review this change here",
        }
        print(json.dumps({"hookSpecificOutput": out}))
        return
    review = review_in_zed(path, orig, new)
    if review is None:
        out = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": "Zed couldn't open the diff: review this change here",
        }
    else:
        out = decide(tool_name, tool_input, orig, new, *review)
    print(json.dumps({"hookSpecificOutput": out}))


if __name__ == "__main__":
    main()  # an uncaught error exits 1: non-blocking, so the normal permission flow applies
