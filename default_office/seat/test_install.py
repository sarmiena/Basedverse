"""Cases for setting up a directory on its board: install.py's REPO_BOARD and its hiding of the seat's files, and
bin/init-agent's reading of a checkout's board. Temporary git repos and a stubbed GitHub, no network.

Run: python3 seat/test_install.py
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)


def load(name, path):
    spec = importlib.util.spec_from_loader(name, importlib.machinery.SourceFileLoader(name, path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


install = load("install", os.path.join(HERE, "install.py"))
init = load("init_agent", os.path.join(ROOT, "bin", "init-agent"))
fails = 0


def case(label, fn, want):
    global fails
    try:
        got = fn()
    except Exception as e:  # a raise is a result too, by its type
        got = type(e).__name__
    ok = got == want
    fails += not ok
    print("[%s] %-72s %s" % ("ok " if ok else "FAIL", label, repr(got)[:60]))


def repo(origin=None, ignore=""):
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q", d], check=True)
    if origin:
        subprocess.run(["git", "-C", d, "remote", "add", "origin", origin], check=True)
    if ignore:
        with open(os.path.join(d, ".gitignore"), "w") as f:
            f.write(ignore)
    return d


TPL = install.template()
print("merged() — a project's board in its settings.local.json")
case("another board: env.REPO_BOARD set, the rest kept",
     lambda: install.merged({"env": {"X": "1"}, "model": "m"}, TPL, "sam/Tool")["env"], {"X": "1", "REPO_BOARD": "sam/Tool"})
case("the home board: REPO_BOARD taken out, and an env left empty goes",
     lambda: "env" in install.merged({"env": {"REPO_BOARD": "sam/Tool"}}, TPL, ""), False)
case("None leaves it as it is (the home board's own directories: nothing changes)",
     lambda: install.merged({"env": {"REPO_BOARD": "x/y"}}, TPL)["env"], {"REPO_BOARD": "x/y"})
case("...so a set-up directory with no board still reads as set up",
     lambda: (lambda cur: cur == install.merged(cur, TPL))(install.merged({}, TPL)), True)

print("\nhide() — the seat's files out of git, locally")
os.environ["GIT_CONFIG_GLOBAL"] = os.devnull   # this machine's own global ignores must not decide the cases
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()


def hidden(ignore="", dry=False):
    d = repo(ignore=ignore)
    run = install.Run(dry)
    run.hide(d)
    run.hide(d)   # twice: nothing added the second time
    with open(os.path.join(d, ".git", "info", "exclude")) as f:
        lines = [l.strip() for l in f if l.strip().startswith("/")]
    st = subprocess.run(["git", "-C", d, "check-ignore"] + list(install.SEAT_PATHS), capture_output=True, text=True).stdout.split()
    return lines, sorted(st)


case("a repo that ignores none of them: all three go in .git/info/exclude, once",
     lambda: hidden(), (["/.claude/settings.local.json", "/.claude/skills/go", "/.claude/skills/stop"], sorted(install.SEAT_PATHS)))
case("one that ignores settings.local.json already: only the skills",
     lambda: hidden(".claude/settings.local.json\n")[0], ["/.claude/skills/go", "/.claude/skills/stop"])
case("one that ignores .claude/: nothing (some repos do)", lambda: hidden(".claude/\n")[0], [])
case("a dry run writes nothing", lambda: hidden(dry=True)[0], [])
case("...and .gitignore is never touched", lambda: (lambda d: (install.Run(False).hide(d), os.path.exists(os.path.join(d, ".gitignore")))[1])(repo()), False)
case("no git checkout: nothing to do", lambda: install.Run(False).hide(tempfile.mkdtemp()), None)

print("\ninit-agent — a directory's board")
for url, want in (("git@github.com:sam/Tool.git", ("sam", "Tool")), ("https://github.com/sam/Tool", ("sam", "Tool")),
                  ("https://token@github.com/sam/Tool.git", ("sam", "Tool")), ("ssh://git@github.com/sam/my.lib.git", ("sam", "my.lib")),
                  ("git@gitlab.com:sam/Tool.git", None)):
    case("origin %s" % url, lambda u=url: init.origin_repo(repo(u)), want)
case("no remote at all", lambda: init.origin_repo(repo()), None)
case("not a checkout", lambda: init.origin_repo(tempfile.mkdtemp()), None)


class A:
    dry_run, repo = True, None


def board(origin, names="App", spelled=None):
    s = init.Session(A(), {"REPO_OWNER": "acme", "REPO_NAMES": names})
    s.gh = lambda path, method="GET", body=None: {"full_name": spelled} if spelled else (_ for _ in ()).throw(OSError("offline"))
    return s.board_of(repo(origin) if origin else tempfile.mkdtemp())


case("a checkout of a home repo: the home board ('')", lambda: board("git@github.com:acme/app.git"), "")
case("...of any repo of the owner when REPO_NAMES is blank", lambda: board("git@github.com:acme/other.git", names=""), "")
case("another owner's repo: its own board", lambda: board("git@github.com:sam/Tool.git"), "sam/Tool")
case("...spelled as GitHub spells it", lambda: board("git@github.com:SAM/tool.git", spelled="sam/Tool"), "sam/Tool")
case("the owner's repo off REPO_NAMES: a board of its own", lambda: board("git@github.com:acme/Site.git"), "acme/Site")
case("no GitHub repo: no board", lambda: board(None), "none")

print("\ninit-agent — the home board's flow, made in another repo")


def flow(have):
    wf = os.path.join(tempfile.mkdtemp(), "workflow.json")
    with open(wf, "w") as f:
        json.dump({"claim_label": "status: doing", "columns": [{"key": "a", "label": "status: later"},
                                                               {"key": "b", "label": "status: doing"}, {"key": "c"}]}, f)
    calls = []

    class Live:
        dry_run, repo = False, None
    s = init.Session(Live(), {"REPO_OWNER": "acme", "REPO_NAMES": "app", "REPO_WORKFLOW": wf, "REPO_RANK": "sev: hi, sev: lo"})

    def gh(path, method="GET", body=None):
        calls.append((method, path, body))
        if path.startswith("/repos/sam/Tool/labels?"):
            return [{"name": n} for n in have]
        if path.startswith("/repos/acme/app/labels?"):
            return [{"name": "status: later", "color": "abcdef", "description": "Deliberately not now."}]
        return {}
    s.gh = gh
    s.make_flow("sam/Tool")
    return [(c[1], c[2]["name"], c[2]["color"]) for c in calls if c[0] == "POST"]


case("each flow label the repo lacks is made, in the home repo's colour",
     lambda: flow(["sev: hi"]), [("/repos/sam/Tool/labels", "status: later", "abcdef"), ("/repos/sam/Tool/labels", "status: doing", "888888"),
                                 ("/repos/sam/Tool/labels", "sev: lo", "888888")])
case("a repo that has them all: nothing made", lambda: flow(["status: later", "status: doing", "sev: hi", "sev: lo"]), [])

print("\ninit-agent --plan — what setting a directory up would do, changing nothing")


def planned(origin, agents=(), boards="", setup=False, private=False, have=()):
    """Session.plan() of a temporary checkout of `origin` (set up first when `setup`): (its board, refused, steps)."""
    d = repo(origin) if origin else tempfile.mkdtemp()
    if setup:
        install.Run(False).project(d, None)
    wf = os.path.join(tempfile.mkdtemp(), "workflow.json")
    with open(wf, "w") as f:
        json.dump({"claim_label": "status: doing", "columns": [{"key": "b", "label": "status: doing"}]}, f)
    s = init.Session(A(), {"REPO_OWNER": "acme", "REPO_NAMES": "app", "REPO_WORKFLOW": wf, "REPO_BOARDS": boards,
                           "AGENTS": ",".join(list(agents) + ([d] if setup else []))})

    def gh(path, method="GET", body=None):
        if method != "GET":
            raise AssertionError("a plan writes nothing: " + method + " " + path)
        if path.endswith("/labels?per_page=100&page=1"):
            return [{"name": n} for n in have]
        m = path.split("/")
        return {"full_name": "%s/%s" % (m[2], m[3]), "private": private}
    s.gh = gh
    before = sorted(os.listdir(d))
    got = s.plan(d)
    assert sorted(os.listdir(d)) == before, "a plan changed the directory"
    return got["board"], got["refused"], got["steps"]


case("a home-board directory set up already: nothing to do", lambda: planned("git@github.com:acme/app.git", setup=True), ("", None, []))
b2, r2, st2 = planned("git@github.com:sam/Tool.git")
case("another owner's repo: a board of its own", lambda: (b2, r2), ("sam/Tool", None))
case("...every step said, its labels in a public repo too",
     lambda: [x.split(" ")[0] for x in st2], ["link", "write", "hide", "make", "add", "add"])
case("...the labels named, and the repo said public", lambda: [x for x in st2 if x.startswith("make")],
     ["make 1 label in sam/Tool (a public repo): status: doing"])
case("...REPO_BOARD in its hooks' file", lambda: any("REPO_BOARD=sam/Tool" in x for x in st2), True)
case("a board named already, with its labels: no REPO_BOARDS or labels step",
     lambda: [x.split(" ")[0] for x in planned("git@github.com:sam/Tool.git", boards="sam/Tool", have=["status: doing"])[2]],
     ["link", "write", "hide", "add"])
case("no GitHub repo: no board, no labels", lambda: planned(None)[:2] + ([x for x in planned(None)[2] if "label" in x],), ("none", None, []))
case("a repo name another board has: refused", lambda: planned("git@github.com:bob/App.git")[1],
     "another board has a repo named app already, and repo#N would mean either")
case("the board's own checkout: refused", lambda: init.Session(A(), {"REPO_OWNER": "acme"}).plan(ROOT)["refused"],
     "it is the board's own checkout, not one agents work in")
case("no such directory: refused", lambda: init.Session(A(), {"REPO_OWNER": "acme"}).plan("/nope/x")["refused"], "there is no such directory")

print("\nall pass" if not fails else "\n%d FAILED" % fails)
sys.exit(1 if fails else 0)
