#!/usr/bin/env python3
"""install.py — wire this checkout into Claude Code, for this machine and the projects named.

    python3 seat/install.py [--dry-run] [PROJECT_DIR ...]

1. ~/.claude/tools/forge.py becomes a link to seat/tools/forge.py (the one copy; never copied around).
2. For each PROJECT_DIR (a checkout agents work in): <project>/.claude/skills/go and /stop become links
   to seat/skills/, and the hooks in seat/settings.json are merged into
   <project>/.claude/settings.local.json with this checkout's absolute paths, with its statusLine
   (hooks/usage_line.py: the account's usage limits for the board, then the status line you had), unless
   that file has a statusLine of its own, which is kept. A project on another board than the home board's
   (bin/init-agent says which) gets its board there too, `env.REPO_BOARD`, which forge.py reads. Whichever of
   these the checkout does not ignore go in its .git/info/exclude (local: a public repo keeps no trace of them).

Idempotent and never destructive: a real file where the forge.py link goes is moved to
`*.pre-install`; a skill directory that is not our link is left alone and reported; settings.local.json
keeps every key and every hook that is not ours, is backed up before it is changed, and our own hook
entries (commands inside this checkout's seat/hooks) are replaced, never duplicated. --dry-run prints
the plan and writes nothing. Stdlib only.
"""
import argparse
import filecmp
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))  # <root>/seat
ROOT = os.path.dirname(HERE)
FORGE = os.path.join(HERE, "tools", "forge.py")
HOOKS = os.path.join(HERE, "hooks")
TEMPLATE = os.path.join(HERE, "settings.json")
SKILLS = ("go", "stop")
SEAT_PATHS = (".claude/settings.local.json", ".claude/skills/go", ".claude/skills/stop")   # this machine's, never committed
PYTHON = "python" if os.name == "nt" else "python3"
WIN = os.name == "nt"


def same(a, b):
    """Whether path `a` already resolves to `b` (a symlink, a junction, or a hardlink to it)."""
    try:
        return os.path.realpath(a) == os.path.realpath(b) or os.path.samefile(a, b)
    except OSError:
        return False


def aside(path):
    """A free name to move `path` to: `<path>.pre-install`, timestamped if that is taken."""
    p = path + ".pre-install"
    return p if not os.path.lexists(p) else "%s.%s" % (p, time.strftime("%Y%m%d-%H%M%S"))


class Run:
    def __init__(self, dry):
        self.dry, self.errors, self.notes = dry, 0, []

    def say(self, msg):
        print(("would: " if self.dry else "") + msg if not msg.startswith(("ok", "!!")) else msg)

    def fail(self, msg):
        self.errors += 1
        print("!! " + msg)

    # -- 1. forge.py ----------------------------------------------------------------------------
    def link_forge(self):
        dst = os.path.join(os.path.expanduser("~"), ".claude", "tools", "forge.py")
        if os.path.lexists(dst) and same(dst, FORGE):
            return self.say("ok   %s -> %s" % (dst, FORGE))
        if os.path.lexists(dst) and not os.path.islink(dst) and filecmp.cmp(dst, FORGE, shallow=False) and WIN:
            return self.say("ok   %s is an up-to-date copy of %s" % (dst, FORGE))
        if os.path.lexists(dst):
            to = aside(dst)
            self.say("move %s aside to %s" % (dst, to))
            if not self.dry:
                os.rename(dst, to)
        self.say("link %s -> %s" % (dst, FORGE))
        if self.dry:
            return
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            os.symlink(FORGE, dst)
            return
        except OSError as e:
            if not WIN:
                return self.fail("cannot link %s: %s" % (dst, e))
        # Windows without the symlink privilege: a hardlink, else a copy. Neither resolves back to this
        # checkout, so forge.py cannot find the project .env by itself: REPO_ENV must name it.
        try:
            os.link(FORGE, dst)
            how = "a hardlink (re-run install.py if seat/tools/forge.py is ever replaced, e.g. by git)"
        except OSError:
            shutil.copy2(FORGE, dst)
            how = "a COPY (re-run install.py after every update of seat/tools/forge.py)"
        print("!! %s is %s: no symlink privilege here (enable Developer Mode to get one)" % (dst, how))
        self.notes.append("set REPO_ENV=%s in your environment: forge.py is not a symlink here, so it cannot "
                          "find the .env beside this checkout by itself" % os.path.join(ROOT, ".env"))

    # -- 2. a project ---------------------------------------------------------------------------
    def project(self, proj, board=None):
        """Set up `proj`. `board`: its board for forge.py (REPO_BOARD: `owner/repo`, or `none`), '' for the home board
        (no REPO_BOARD), None to leave whatever it has."""
        proj = os.path.abspath(proj)
        if not os.path.isdir(proj):
            return self.fail("%s is not a directory: skipped" % proj)
        if os.path.realpath(proj) == os.path.realpath(ROOT):
            return self.fail("%s is this checkout, not a project agents work in: skipped" % proj)
        print("\n%s" % proj)
        for name in SKILLS:
            self.link_skill(proj, name)
        self.merge_hooks(proj, board)
        self.hide(proj)

    def link_skill(self, proj, name):
        src, dst = os.path.join(HERE, "skills", name), os.path.join(proj, ".claude", "skills", name)
        if os.path.lexists(dst) and same(dst, src):
            return self.say("ok   skill /%s -> %s" % (name, src))
        if os.path.lexists(dst):
            return self.fail("%s exists and is not a link to %s: left alone. Move it away and re-run to "
                             "install /%s" % (dst, src, name))
        self.say("link skill /%s: %s -> %s" % (name, dst, src))
        if self.dry:
            return
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            os.symlink(src, dst, target_is_directory=True)
        except OSError as e:
            # Windows without the symlink privilege: a directory junction needs none.
            r = subprocess.run(["cmd", "/c", "mklink", "/J", dst, src], capture_output=True, text=True) if WIN else None
            if not r or r.returncode:
                self.fail("cannot link %s: %s" % (dst, (r and r.stderr.strip()) or e))

    def merge_hooks(self, proj, board=None):
        path = os.path.join(proj, ".claude", "settings.local.json")
        try:
            with open(path, encoding="utf-8") as f:
                cur = json.load(f)
        except FileNotFoundError:
            cur = None
        except (OSError, ValueError) as e:
            return self.fail("%s cannot be read as JSON (%s): left alone; fix it and re-run" % (path, e))
        if cur is not None and not isinstance(cur, dict):
            return self.fail("%s is not a JSON object: left alone" % path)
        new = merged(cur or {}, template(), board)
        sl = (cur or {}).get("statusLine")
        if sl is not None and not (isinstance(sl, dict) and ours(sl)):
            self.say("keep its own statusLine in %s: the board learns the usage limits from sessions with the seat's" % path)
        if cur == new:
            return self.say("ok   hooks in %s" % path)
        if cur is not None:
            bak = "%s.bak-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
            self.say("back up %s to %s" % (path, os.path.basename(bak)))
            if not self.dry:
                shutil.copy2(path, bak)
        n = sum(len(g["hooks"]) for gs in template()["hooks"].values() for g in gs)
        self.say("%s %s with %d hooks from %s%s" % ("write" if cur is None else "merge into", path, n, HOOKS,
                                                   ", its board %s" % board if board else ""))
        if not self.dry:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(new, f, indent=2)
                f.write("\n")
            os.replace(tmp, path)

    def hide(self, proj):
        """The seat's files are this machine's (its absolute paths, links into this checkout): never to be committed.
        Those the checkout does not ignore already go in its .git/info/exclude, which is local and never committed
        itself, so a repo (a public one too) is left with no trace of them; a .gitignore line would be one."""
        try:
            todo, path = unignored(proj)
        except OSError as e:
            return self.fail("%s: %s: add %s to its .git/info/exclude by hand" % (proj, e, ", ".join(SEAT_PATHS)))
        if not todo:
            return
        self.say("hide %s in %s (local, never committed)" % (", ".join(todo), path))
        if self.dry:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n# This machine's agent seat: links and paths of this machine only, never committed.\n"
                    + "".join("/%s\n" % p for p in todo))


def unignored(proj):
    """([the seat's files git does not ignore in checkout `proj`], its .git/info/exclude), ([], None) for a directory
    that is no git checkout; OSError when git cannot say."""
    if not os.path.exists(os.path.join(proj, ".git")):
        return [], None
    try:
        r = subprocess.run(["git", "-C", proj, "check-ignore", "--no-index"] + list(SEAT_PATHS),
                           capture_output=True, text=True, timeout=10)
        x = subprocess.run(["git", "-C", proj, "rev-parse", "--git-path", "info/exclude"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        raise OSError("git could not say what it ignores (%s)" % e)
    if r.returncode > 1 or x.returncode:
        raise OSError("git could not say what it ignores (%s)" % (r.stderr or x.stderr).strip())
    return [p for p in SEAT_PATHS if p not in set(r.stdout.split())], os.path.join(proj, x.stdout.strip())


def template():
    """seat/settings.json's hooks, with $HOOKS and $PYTHON filled in for this machine."""
    with open(TEMPLATE, encoding="utf-8") as f:
        t = json.load(f)

    def fill(o):
        if isinstance(o, dict):
            return {k: fill(v) for k, v in o.items()}
        if isinstance(o, list):
            return [fill(v) for v in o]
        if isinstance(o, str):
            return o.replace("$HOOKS", HOOKS.replace("\\", "/")).replace("$PYTHON", PYTHON)
        return o
    out = {"hooks": fill(t["hooks"])}
    if "statusLine" in t:
        out["statusLine"] = fill(t["statusLine"])
    return out


def ours(hook):
    """A hook entry this script installed: its command runs a file in this checkout's seat/hooks."""
    cmd = str(hook.get("command") or "").replace("\\", "/")
    h = HOOKS.replace("\\", "/")
    return (h.lower() in cmd.lower()) if WIN else (h in cmd)


def merged(cur, tpl, board=None):
    """PURE: `cur` with every hook entry of ours dropped (a group, and an event, left empty only by
    that drop go too) and the template's groups appended. Every other key and hook is kept as is.
    `board` (not None): `env.REPO_BOARD` set to it, or taken out for '' (the home board)."""
    out = json.loads(json.dumps(cur))
    hooks = out.get("hooks") if isinstance(out.get("hooks"), dict) else {}
    for ev in list(hooks):
        if not isinstance(hooks[ev], list):
            continue  # not a shape we know: left exactly as it is
        groups = []
        for g in hooks[ev]:
            hs = g.get("hooks") if isinstance(g, dict) else None
            if isinstance(hs, list) and any(isinstance(h, dict) and ours(h) for h in hs):
                hs = [h for h in hs if not (isinstance(h, dict) and ours(h))]
                if not hs:
                    continue
                g = dict(g, hooks=hs)
            groups.append(g)
        if groups or not hooks[ev]:
            hooks[ev] = groups
        else:
            del hooks[ev]
    for ev, gs in tpl["hooks"].items():
        hooks.setdefault(ev, []).extend(json.loads(json.dumps(gs)))
    out["hooks"] = hooks
    # The status line: ours (replaced, as the hooks are), or none yet; one of the project's own is kept.
    sl = out.get("statusLine")
    if "statusLine" in tpl and (sl is None or (isinstance(sl, dict) and ours(sl))):
        out["statusLine"] = json.loads(json.dumps(tpl["statusLine"]))
    if board is not None:
        env = dict(out["env"]) if isinstance(out.get("env"), dict) else {}
        if board:
            env["REPO_BOARD"] = board
        else:
            env.pop("REPO_BOARD", None)
        if env:
            out["env"] = env
        else:
            out.pop("env", None)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("projects", nargs="*", metavar="PROJECT_DIR", help="a checkout agents work in")
    p.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    a = p.parse_args()
    run = Run(a.dry_run)
    print("forge.py")
    run.link_forge()
    for proj in a.projects:
        run.project(proj)
    env = os.path.join(ROOT, ".env")
    print("\nnext:")
    steps = ["fill %s: REPO_OWNER, REPO_NAMES, REPO_HOME, REPO_RANK; the columns are %s"
             % (env, os.path.join(ROOT, "workflow.json")),
             "once per home repo: python3 ~/.claude/tools/forge.py newlabel off-board",
             "the board: %s   (fills a blank REPO_TOKEN from `gh auth token`; http://localhost:3001, /room)"
             % os.path.join(ROOT, "bin", "start"),
             "in a Claude Code session in the project: /go"]
    if not a.projects:
        steps.insert(0, "add a project: %s <project-dir>" % os.path.join(ROOT, "bin", "init-agent"))
    for i, s in enumerate(run.notes + steps, 1):
        print("  %d. %s" % (i, s))
    if a.dry_run:
        print("\n(dry run: nothing was written)")
    return 1 if run.errors else 0


if __name__ == "__main__":
    sys.exit(main())
