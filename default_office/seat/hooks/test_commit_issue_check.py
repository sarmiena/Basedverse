#!/usr/bin/env python3
"""Regression test for the commit detector in commit_issue_check.py.

    python3 .claude/hooks/test_commit_issue_check.py

Two cases here are not hypothetical — they are the false positives this hook fired
on its first two real invocations, both from commands that only TALKED about
committing (a heredoc writing the hook's own docs, and a heredoc of test cases with
'&& git commit' inside a quoted string). That is why the detector tokenizes with
shlex instead of matching text.

⚠ This file never contains the literal trigger phrase — it builds it from fragments.
Otherwise running the test trips the hook it is testing.
"""
import importlib.util
import pathlib
import sys

HOOK = pathlib.Path(__file__).with_name("commit_issue_check.py")
spec = importlib.util.spec_from_file_location("commit_issue_check", HOOK)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

G = "g" + "it"
C = "com" + "mit"

CASES = [
    # real commits -> True
    (f'cd /some/repo && {G} {C} -q -m "chore: x"', True),
    (f'{G} {C} -m "fix"', True),
    (f'{G} -C android {C} -m "y"', True),
    (f'GIT_EDITOR=true {G} {C} --amend', True),
    (f'{G} add -A && {G} {C} -F -', True),
    (f'{G} add -A; {G} {C} -m z', True),
    (f'{G} --no-pager {C} -m z', True),
    # talking about it, not running it -> False (both of these really fired)
    (f'python - <<EOF\ns = "a && {G} {C} inside a string"\nEOF', False),
    (f'grep -rn "{G} {C}" docs/', False),
    (f'echo "run {G} {C} later"', False),
    (f"echo 'x && {G} {C} y'", False),
    # other git subcommands -> False
    (f'{G} log --oneline -3', False),
    (f'{G} status --porcelain', False),
    (f'{G} -C host log --grep={C}', False),
    (f'{G} push origin develop', False),
    # degenerate
    ("", False),
    ("ls -la", False),
]


def main() -> int:
    bad = 0
    for cmd, want in CASES:
        got = m.is_git_commit(cmd)
        if got != want:
            bad += 1
        print(f"  {'ok  ' if got == want else 'FAIL'} got={got!s:5} want={want!s:5}  {cmd[:54]!r}")
    print(f"{len(CASES) - bad}/{len(CASES)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
