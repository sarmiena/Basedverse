#!/usr/bin/env python3
"""PostToolUse(Bash) — after a git commit that cites no issue, remind the model
that the loop closes on the board, not just in git.

Why this exists: issues stay OPEN long after their work has shipped when nothing
connects "commit landed" to "close the ticket", and the board drifts out of truth
silently. This is that connection.

Non-blocking by design — it injects context, it never fails a tool call.
Deliberately NOT written against jq: jq is not installed everywhere, and a hook
whose dependency is missing fails silently forever.
"""
import json
import re
import shlex
import sys


# `git` must sit at a COMMAND position — not merely appear somewhere in the string.
#
# Two false positives caught while building this, both from commands that only TALKED
# about git: a heredoc writing the docs for this hook, and a heredoc of test cases
# containing '... && git commit ...' inside a quoted Python string. Substring and
# regex-segment matching both fire on those because neither can see shell quoting.
#
# So: strip heredoc bodies (they are data, never commands), then tokenize with shlex,
# which DOES respect quoting, and only accept `git ... commit` at the head of a real
# command segment.
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1.*?^\2$", re.DOTALL | re.MULTILINE)
_SEPARATORS = {";", "&&", "||", "|", "&", "\n"}
_ENV_PREFIX = re.compile(r"^\w+=")


def is_git_commit(cmd: str) -> bool:
    cmd = _HEREDOC.sub(" ", cmd)
    try:
        lexer = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False  # unbalanced quotes — not something we can judge

    segment: list[str] = []
    for tok in tokens + [";"]:
        if tok in _SEPARATORS or all(c in ";&|" for c in tok):
            if _segment_is_git_commit(segment):
                return True
            segment = []
        else:
            segment.append(tok)
    return False


def _segment_is_git_commit(seg: list[str]) -> bool:
    i = 0
    while i < len(seg) and _ENV_PREFIX.match(seg[i]):  # VAR=val prefixes
        i += 1
    # `cd <dir> && git commit` already split into its own segment, so the head is git.
    if i >= len(seg) or seg[i] != "git":
        return False
    i += 1
    while i < len(seg):
        tok = seg[i]
        if tok == "commit":
            return True
        if tok.startswith("-"):
            # -C <path>, --git-dir <path> etc. consume a value; --flag=val does not.
            if "=" not in tok and tok in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
                i += 1
            i += 1
            continue
        return False  # some other subcommand (log, status, ...)
    return False


def board_link() -> str:
    """Where the board's issues live, from the project .env (the root above seat/): the owner, or
    the one repo when REPO_NAMES names one. Plain github.com when there is no .env."""
    import os
    env = {}
    p = os.environ.get("REPO_ENV") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))), ".env")
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                k, sep, v = line.strip().partition("=")
                if sep and not k.lstrip().startswith("#"):
                    env[k.strip()] = v.strip().strip("'\"")
    except OSError:
        pass
    owner = os.environ.get("REPO_OWNER") or env.get("REPO_OWNER") or ""
    repos = (os.environ.get("REPO_NAMES") or env.get("REPO_NAMES") or "").replace(",", " ").split()
    board = (os.environ.get("REPO_BOARD") or "").strip()   # another board's checkout (its settings.local.json): its repo
    if re.fullmatch(r"[\w.-]+/[\w.-]+", board):
        owner, repos = board.split("/")[0], [board.split("/")[1]]
    if not owner:
        return "https://github.com"
    return "https://github.com/%s%s" % (owner, "/" + repos[0] + "/issues" if len(repos) == 1 else "")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if not is_git_commit(cmd):
        return 0

    # Already cites an issue (#12, repo#12, "closes #12") — nothing to say.
    if re.search(r"#\d+", cmd):
        return 0

    context = (
        "TRACKING CHECK — that commit cited no issue.\n"
        "Board: %s  (open issues ARE the board)\n" % board_link() +
        "  - scope remains -> update the issue body with what is left\n"
        '  - item is done  -> python3 ~/.claude/tools/forge.py close <repo>#<N> -m "<sha + doc trail>"\n'
        "If it maps to NOTHING on the board, ask what REMAINS. Pending or partial scope gets\n"
        "an issue (forge.py create, with its rank label), so the next session can see it —\n"
        "untracked work is invisible to every /go. But if this\n"
        "commit FINISHED the thing outright, the commit message is already the record: do NOT\n"
        "file an issue just to close it in the same breath, that tracks nothing.\n"
        "If the commit is genuinely incidental (typo, sync, this tooling), ignore this."
    )
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": context,
            }
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
