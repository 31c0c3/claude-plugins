"""Claude Code PreToolUse hook: review each Edit/Write in a Zed diff before it's applied.

Keys and comment notes: see the README. Zed side: zed/keymap.json and zed/tasks.json.
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

# Whole-line comment syntax by file extension (or name, for dotfiles and Makefile).
# A file type missing here gets no notes: its comment lines stay in the file.
COMMENT_SYNTAX = {
    "#": ".py .pyi .sh .bash .zsh .fish .rb .pl .r .yaml .yml .toml .conf .cfg .nix .ex "
    ".exs .jl .ps1 .tf .cmake .mk makefile dockerfile brewfile gemfile justfile .env "
    ".gitignore .dockerignore .zshrc .zshenv .zprofile .bashrc .bash_profile",
    "//": ".js .jsx .ts .tsx .mjs .cjs .mts .cts .c .h .cc .cpp .hpp .m .mm .java .kt "
    ".kts .swift .go .rs .cs .scala .dart .php .zig .jsonc .json5 .scss .less .sol "
    ".proto .groovy .gradle .glsl",
    "/*": ".c .h .cc .cpp .hpp .m .mm .java .kt .swift .go .rs .cs .scala .dart .php "
    ".css .scss .less .js .jsx .ts .tsx .mjs .cjs",
    "--": ".sql .lua .hs .elm",
    ";": ".lisp .clj .cljs .el .scm .asm .s .ini",
    "%": ".tex .sty .erl",
    "<!--": ".md .markdown .mdx .html .htm .xml .svg .vue .svelte",
}
COMMENT_LINE = {  # group 1: the comment's text
    "#": r"\s*#+\s*(.*?)\s*",
    "//": r"\s*//+\s*(.*?)\s*",
    "/*": r"\s*/\*+\s*(.*?)\s*\*+/\s*",
    "--": r"\s*--+\s*(.*?)\s*",
    ";": r"\s*;+\s*(.*?)\s*",
    "%": r"\s*%+\s*(.*?)\s*",
    "<!--": r"\s*<!--\s*(.*?)\s*-->\s*",
}


def comment_text(path):
    """A function: the text of a line that is only a comment in path's language, else None."""
    name = os.path.basename(path).lower()
    key = os.path.splitext(name)[1] or name
    patterns = [
        re.compile(COMMENT_LINE[syntax])
        for syntax, keys in COMMENT_SYNTAX.items()
        if key in keys.split()
    ]

    def text(line):
        for p in patterns:
            if m := p.fullmatch(line.rstrip("\r\n")):
                return m.group(1)
        return None

    return text


def extract_notes(path, proposed, edited):
    """Strip the comment lines you added in the diff; return (text, notes)."""
    text = comment_text(path)
    pr_lines, ed_lines = proposed.splitlines(True), edited.splitlines(True)
    kept, notes = [], []
    ops = difflib.SequenceMatcher(None, pr_lines, ed_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in ops.get_opcodes():
        # a changed comment line close to one of Claude's is a fix to it, not a note
        theirs = [line for line in pr_lines[i1:i2] if text(line) is not None]
        for line in ed_lines[j1:j2]:
            note = text(line) if tag != "equal" else None
            if not note or any(
                difflib.SequenceMatcher(None, line, t).ratio() > 0.6 for t in theirs
            ):
                kept.append(line)
            elif notes and notes[-1][0] == len(kept):  # lines in a row: one note
                notes[-1][1] += " " + note
            else:
                notes.append([len(kept), note])  # points at the next kept line
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
    edited, notes = extract_notes(tool_input["file_path"], new, edited)
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
            "The user left notes for you as comments in the diff; the hook removed "
            "them from the file. Act on each request and answer each question. "
            "If one reads as a code comment or directive meant to stay (a shebang, "
            "a lint pragma), add it back:\n"
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
