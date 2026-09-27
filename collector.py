#!/usr/bin/env python3
"""Lifecycle and health of the Omarchy shell plugins you build and run.

Subcommands (all print JSON on stdout):

  status [--fetch]   every plugin, its stage and its issues
  test <id>          run the plugin's own test suite, cache the result
  prompt <id>        the prompt `fix` would hand the agent
  fix <id>           open the default agent in the plugin's working copy
  seen <id>          mark the plugin's comments as read

Plugins come from two places: the ones installed in ~/.config/omarchy/plugins,
and git working copies of plugins under PLUGIN_LAB_WORK_DIRS (default ~/Work).
When a plugin has both, its working copy is where the git state is read.

Stages for your own plugins, in order:
  local        no git yet
  dev          git, no remote
  pushed       on GitHub, not submitted to the marketplace (a private repo stays here)
  submitted    marketplace submission open, waiting for a maintainer
  needs-fixes  marketplace reviewers asked for changes
  listed       listed on the marketplace at your current commit
  update       listed, but a newer commit is waiting for (or needs) verification
Plugins someone else wrote are "installed".

Nothing here pushes, tags, submits or edits a plugin. GitHub is only read,
through `gh`, with results cached so an open panel does not hammer the API.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
PLUGINS_DIR = Path(os.environ.get("PLUGIN_LAB_PLUGINS_DIR", HOME / ".config/omarchy/plugins"))
WORK_DIRS = [Path(p).expanduser() for p in
             os.environ.get("PLUGIN_LAB_WORK_DIRS", str(HOME / "Work")).split(":") if p]
CACHE_DIR = Path(os.environ.get("PLUGIN_LAB_CACHE_DIR",
                                Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache")) / "omarchy-plugin-lab"))
UPSTREAM = os.environ.get("PLUGIN_LAB_UPSTREAM", "omacom/omarchy")
MARKETPLACE = os.environ.get("PLUGIN_LAB_MARKETPLACE", "omacom/omarchy-plugin-marketplace")
CI_TTL = 300
REPO_TTL = 3600
FEEDBACK_TTL = 900
MARKET_ISSUES_TTL = 600
REGISTRY_TTL = 3600
TEST_TIMEOUT = 600

STAGE_ORDER = ["local", "dev", "pushed", "submitted", "needs-fixes", "listed", "update"]


# ---- small helpers ---------------------------------------------------------

def run(cmd, cwd=None, timeout=30):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, "", str(e)


def git(path, *args, timeout=30):
    return run(["git", "-C", str(path), *args], timeout=timeout)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def cached(name, key, ttl, compute, force=False):
    """Memoise `compute()` in the cache dir for `ttl` seconds. None is not cached."""
    store = read_json(CACHE_DIR / name, {}) or {}
    hit = store.get(key)
    if hit and not force and time.time() - hit.get("at", 0) < ttl:
        return hit.get("value")
    value = compute()
    if value is None:
        return hit.get("value") if hit else None
    store[key] = {"at": time.time(), "value": value}
    write_json(CACHE_DIR / name, store)
    return value


def github_slug(url):
    m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", url or "")
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def iso_ts(s):
    try:
        return int(time.mktime(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone)
    except (TypeError, ValueError):
        return 0


def gh_json(*args, timeout=30):
    rc, out, _ = run(["gh", *args], timeout=timeout)
    if rc != 0:
        return None
    try:
        return json.loads(out or "null")
    except ValueError:
        return None


def is_bot(login):
    login = (login or "").lower()
    return login.endswith("[bot]") or login in ("github-actions", "dependabot")


def gh_login():
    if os.environ.get("PLUGIN_LAB_OWNERS"):
        return os.environ["PLUGIN_LAB_OWNERS"].split(",")[0].strip()
    return cached("gh.json", "login", 86400,
                  lambda: (lambda r: r[1] if r[0] == 0 and r[1] else None)(run(["gh", "api", "user", "-q", ".login"])))


def default_owners():
    """Your $USER and GitHub login, or PLUGIN_LAB_OWNERS (comma-separated)."""
    if os.environ.get("PLUGIN_LAB_OWNERS"):
        return {o.strip().lower() for o in os.environ["PLUGIN_LAB_OWNERS"].split(",") if o.strip()}
    owners = {os.environ.get("USER", ""), gh_login() or ""}
    return {o.lower() for o in owners if o}


# ---- discovery -------------------------------------------------------------

def manifest_id(d):
    m = read_json(Path(d) / "manifest.json", {}) or {}
    return m.get("id") or Path(d).name


def discover():
    """{id: {"installed": Path|None, "work": Path|None}} over plugins dir and work dirs."""
    found = {}
    if PLUGINS_DIR.is_dir():
        for d in sorted(PLUGINS_DIR.iterdir()):
            if (d / "manifest.json").is_file():
                found.setdefault(manifest_id(d), {"installed": None, "work": None})["installed"] = d
    for root in WORK_DIRS:
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            if d.is_dir() and (d / "manifest.json").is_file() and (d / ".git").exists():
                entry = found.setdefault(manifest_id(d), {"installed": None, "work": None})
                if entry["installed"] and entry["installed"].resolve() == d.resolve():
                    continue
                entry["work"] = entry["work"] or d
    return found


def enabled_ids():
    rc, out, _ = run(["omarchy", "plugin", "list", "--json"], timeout=15)
    if rc != 0:
        return None
    try:
        return {p["id"] for p in json.loads(out) if p.get("enabled")}
    except (ValueError, KeyError, TypeError):
        return None


def test_command(path):
    """How this plugin runs its tests, or None when it has none."""
    path = Path(path)
    if (path / "test/run").is_file():
        return ["bash", "test/run"]
    for d in ("tests", "test"):
        scripts = sorted((path / d).glob("*test*.sh")) if (path / d).is_dir() else []
        if scripts:
            rel = " ".join(f"'{s.relative_to(path)}'" for s in scripts)
            return ["bash", "-c", f"rc=0; for t in {rel}; do echo \"==> $t\"; bash \"$t\" || rc=1; done; exit $rc"]
    return None


def test_cache_path(pid):
    return CACHE_DIR / "tests" / f"{pid}.json"


def tree_fingerprint(path):
    """HEAD plus uncommitted changes, so a cached test result goes stale on edit."""
    _, head, _ = git(path, "rev-parse", "HEAD")
    _, diff, _ = git(path, "status", "--porcelain")
    _, patch, _ = git(path, "diff", "HEAD")
    return head + ":" + hashlib.sha1((diff + patch).encode()).hexdigest()[:12]


# ---- GitHub: feedback ------------------------------------------------------

def feedback_now(repo, pid, owners):
    """What other people are saying: issues, PRs, comments, stars, Omarchy mentions."""
    meta = gh_json("api", f"repos/{repo}")
    if meta is None:
        return None
    others = lambda login: (login or "").lower() not in owners and not is_bot(login)
    open_items = gh_json("api", f"repos/{repo}/issues?state=open&per_page=50") or []
    comments = gh_json("api", f"repos/{repo}/issues/comments?per_page=30&sort=created&direction=desc") or []
    reviews = gh_json("api", f"repos/{repo}/pulls/comments?per_page=30&sort=created&direction=desc") or []
    items = [{"kind": "pr" if i.get("pull_request") else "issue", "number": i["number"],
              "title": i["title"], "by": i["user"]["login"], "url": i["html_url"],
              "at": iso_ts(i["created_at"])} for i in open_items]
    talk = [{"by": c["user"]["login"], "url": c["html_url"], "at": iso_ts(c["created_at"]), "where": "repo",
             "text": first_line(c.get("body"))}
            for c in comments + reviews if others(c["user"]["login"])]
    talk += [{"by": i["by"], "url": i["url"], "at": i["at"], "where": "repo",
              "text": f"opened {i['kind']} #{i['number']}: {i['title']}"}
             for i in items if others(i["by"])]
    # Upstream Omarchy: issues, PRs and discussions that link the repo or name the plugin id.
    mentions = []
    for q in (f'"{repo}"', f'"{pid}"'):
        found = gh_json("api", "-X", "GET", "search/issues", "-f", f"q={q} repo:{UPSTREAM}", "-f", "per_page=10") or {}
        for i in found.get("items", []):
            if all(m["url"] != i["html_url"] for m in mentions):
                mentions.append({"url": i["html_url"], "title": i["title"], "by": i["user"]["login"],
                                 "state": i["state"], "at": iso_ts(i["updated_at"])})
    return {
        "stars": meta.get("stargazers_count", 0), "forks": meta.get("forks_count", 0),
        "watchers": meta.get("subscribers_count", 0),
        "pushedAt": iso_ts(meta.get("pushed_at")),
        "openIssues": sum(1 for i in items if i["kind"] == "issue"),
        "openPrs": sum(1 for i in items if i["kind"] == "pr"),
        "items": items[:10], "comments": talk, "upstreamMentions": mentions,
    }


def first_line(body):
    for line in (body or "").splitlines():
        line = line.strip()
        if line and not line.startswith("<!--"):
            return line[:160]
    return ""


def mark_seen(pid):
    seen = read_json(CACHE_DIR / "seen.json", {}) or {}
    seen[pid] = int(time.time())
    write_json(CACHE_DIR / "seen.json", seen)
    return {"seen": pid}


# ---- GitHub: marketplace ---------------------------------------------------

def registry_index(force=False):
    """{repo slug (lower): {listedAt, commit, plugins}} from the marketplace registry.json."""
    def fetch():
        rc, out, _ = run(["gh", "api", f"repos/{MARKETPLACE}/contents/registry.json",
                          "-H", "Accept: application/vnd.github.raw"], timeout=90)
        if rc != 0:
            return None
        try:
            reg = json.loads(out)
        except ValueError:
            return None
        index = {}
        for s in reg.get("sources", []):
            slug = github_slug(s.get("repo", "")).lower()
            if slug:
                index[slug] = {"listedAt": iso_ts((s.get("listedAt") or s.get("addedAt") or "")[:19] + "Z"),
                               "commit": s.get("listingValidatedCommit", ""),
                               "plugins": sorted((s.get("plugins") or {}).keys())}
        return index
    return cached("marketplace.json", "registry", REGISTRY_TTL, fetch, force) or {}


def marketplace_issues(login, force=False):
    """Your submission and verification issues on the marketplace, newest first."""
    def fetch():
        items = gh_json("issue", "list", "-R", MARKETPLACE, "--author", login, "--state", "all",
                        "--limit", "100", "--json", "number,title,state,labels,url,body,updatedAt,comments")
        if items is None:
            return None
        out = []
        for i in items:
            body = i.get("body") or ""
            m = re.search(r"### Repository URL\s*\|?\s*(\S+)", body)
            t = re.search(r"### Target commit\s*\|?\s*([0-9a-f]{7,40})", body)
            out.append({
                "number": i["number"], "title": i["title"], "state": i["state"].lower(), "url": i["url"],
                "labels": [l["name"] for l in i.get("labels", [])],
                "repo": github_slug(m.group(1) if m else "").lower(),
                "kind": "verify" if "### Verification action" in body else "submission",
                "target": t.group(1) if t else "",
                "updatedAt": iso_ts(i.get("updatedAt")),
                "comments": [{"by": c["author"]["login"], "at": iso_ts(c.get("createdAt")), "url": c.get("url", i["url"]),
                              "text": first_line(c.get("body")), "body": (c.get("body") or "")[:2000]}
                             for c in i.get("comments", []) if not is_bot(c["author"]["login"])],
            })
        return out
    if not login:
        return []
    return cached("marketplace.json", "issues:" + login.lower(), MARKET_ISSUES_TTL, fetch, force) or []


def marketplace_state(repo, head, registry, issues):
    """Where this repo is on the marketplace, or None if it never went there."""
    slug = repo.lower()
    mine = sorted((i for i in issues if i["repo"] == slug), key=lambda i: -i["number"])
    listing = registry.get(slug)
    subs = [i for i in mine if i["kind"] == "submission"]
    verifies = [i for i in mine if i["kind"] == "verify" and i["state"] == "open"]
    latest = subs[0] if subs else None
    if listing:
        state = "listed"
        if verifies:
            state = "update"
        elif head and listing["commit"] and not head.startswith(listing["commit"][:len(head)]) \
                and not listing["commit"].startswith(head):
            state = "update"
        issue = verifies[0] if verifies else latest
    elif latest and latest["state"] == "open":
        state = "needs-fixes" if "needs-fixes" in latest["labels"] else "submitted"
        issue = latest
    elif latest:
        state, issue = "closed", latest
    else:
        return None
    return {
        "state": state,
        "listedCommit": listing["commit"] if listing else "",
        "listedAt": listing["listedAt"] if listing else 0,
        "verifyPending": bool(verifies),
        "verifyTarget": verifies[0]["target"] if verifies else "",
        "issue": {k: issue[k] for k in ("number", "title", "url", "labels", "state")} if issue else None,
        "reviews": [dict(c, where="marketplace #" + str(i["number"])) for i in mine for c in i["comments"]],
    }


# ---- one plugin ------------------------------------------------------------

def git_state(path, fetch):
    s = {"git": False, "remote": "", "repo": "", "branch": "", "head": "", "remoteHead": "",
         "dirty": 0, "ahead": 0, "behind": 0, "upstream": False,
         "tag": "", "versionTagged": False, "lastCommit": "", "lastCommitAt": 0}
    if not (path / ".git").exists():
        return s
    s["git"] = True
    _, s["branch"], _ = git(path, "rev-parse", "--abbrev-ref", "HEAD")
    _, s["head"], _ = git(path, "rev-parse", "HEAD")
    _, remote, _ = git(path, "config", "--get", "remote.origin.url")
    s["remote"], s["repo"] = remote, github_slug(remote)
    if fetch and remote:
        git(path, "fetch", "--quiet", "--tags", "origin", timeout=60)
    _, status, _ = git(path, "status", "--porcelain")
    s["dirty"] = len([l for l in status.splitlines() if l.strip()])
    rc, counts, _ = git(path, "rev-list", "--left-right", "--count", "HEAD...@{u}")
    if rc == 0 and counts:
        s["upstream"] = True
        a, b = counts.split()
        s["ahead"], s["behind"] = int(a), int(b)
        _, s["remoteHead"], _ = git(path, "rev-parse", "@{u}")
    _, last, _ = git(path, "log", "-1", "--format=%ct%x09%s")
    if last:
        ts, _, subj = last.partition("\t")
        s["lastCommitAt"], s["lastCommit"] = int(ts or 0), subj
    _, s["tag"], _ = git(path, "describe", "--tags", "--abbrev=0")
    return s


def inspect(pid, where, owners, enabled, fetch=False, market=None):
    installed = where["installed"].resolve() if where["installed"] else None
    work = where["work"].resolve() if where["work"] else None
    src = work or installed
    manifest = read_json(src / "manifest.json", {}) or {}
    version = str(manifest.get("version") or "")
    info = {
        "id": pid,
        "name": manifest.get("name") or pid,
        "version": version,
        "author": manifest.get("author") or "",
        "path": str(src),
        "installedPath": str(installed) if installed else "",
        "installed": installed is not None,
        "enabled": None if enabled is None or not installed else pid in enabled,
        "visibility": "", "ci": None, "tests": None, "feedback": None, "marketplace": None,
        "hasTests": test_command(src) is not None,
    }
    info.update(git_state(src, fetch))
    if version and info["git"]:
        _, tags, _ = git(src, "tag", "--list", f"v{version}", version)
        info["versionTagged"] = bool(tags)
    issues = []

    def issue(level, code, text):
        issues.append({"level": level, "code": code, "text": text})

    rc, out, err = run(["omarchy", "plugin", "validate", str(src)], timeout=15)
    info["manifestValid"] = rc == 0
    if rc != 0:
        detail = (err or out).splitlines()[-1] if (err or out) else ""
        issue("error", "manifest", "Manifest fails validation" + (": " + detail if detail else ""))

    remote_owner = info["repo"].split("/")[0].lower() if info["repo"] else ""
    namespace = pid.split(".")[0].lower()
    info["mine"] = bool(
        remote_owner in owners
        or (not remote_owner and (namespace in owners or info["author"].lower() in owners
                                  or (info["git"] and not info["remote"])))
    )

    # -- GitHub (own plugins only) ------------------------------------------
    if info["mine"] and info["repo"]:
        repo = info["repo"]
        vis = cached("repos.json", repo, REPO_TTL, lambda: (
            lambda r: r[1] if r[0] == 0 and r[1] else None)(
            run(["gh", "repo", "view", repo, "--json", "visibility", "-q", ".visibility"])), fetch)
        info["visibility"] = (vis or "").lower()
        branch = info["branch"] or "main"

        def ci_now():
            runs = gh_json("run", "list", "-R", repo, "-b", branch, "-L", "1", "--json",
                           "status,conclusion,headSha,url,displayTitle,workflowName,createdAt")
            if runs is None:
                return None
            return runs[0] if runs else {"status": "none"}
        info["ci"] = cached("ci.json", f"{repo}@{branch}", CI_TTL, ci_now, fetch)
        info["feedback"] = cached("feedback.json", repo, FEEDBACK_TTL, lambda: feedback_now(repo, pid, owners), fetch)
        if market is not None:
            info["marketplace"] = marketplace_state(repo, info["remoteHead"] or info["head"], *market)

    # -- comments from others, wherever they were left -------------------------
    fb = info["feedback"] or {}
    mk = info["marketplace"] or {}
    talk = list(fb.get("comments", [])) + [c for c in mk.get("reviews", []) if c["by"].lower() not in owners]
    talk.sort(key=lambda c: -c["at"])
    # A comment is read once you open the plugin here, or once you replied after it in that thread.
    seen = (read_json(CACHE_DIR / "seen.json", {}) or {}).get(pid, 0)
    replied = {}
    for c in mk.get("reviews", []):
        if c["by"].lower() in owners:
            replied[c["where"]] = max(replied.get(c["where"], 0), c["at"])
    info["comments"] = [dict({k: c.get(k) for k in ("by", "at", "url", "text", "where")},
                             new=c["at"] > max(seen, replied.get(c["where"], 0))) for c in talk[:15]]
    info["unseen"] = sum(1 for c in talk if c["at"] > max(seen, replied.get(c["where"], 0)))
    info["stars"] = fb.get("stars", 0)
    info["forks"] = fb.get("forks", 0)
    info["watchers"] = fb.get("watchers", 0)
    info["activityAt"] = max([info["lastCommitAt"], fb.get("pushedAt", 0), mk.get("listedAt", 0)]
                             + [c["at"] for c in talk])

    # -- stage -----------------------------------------------------------------
    if not info["mine"]:
        stage = "installed"
    elif not info["git"]:
        stage = "local"
    elif not info["remote"]:
        stage = "dev"
    elif mk and mk["state"] in ("submitted", "needs-fixes", "listed", "update"):
        stage = mk["state"]
    else:
        stage = "pushed"
    info["stage"] = stage

    # -- local test results ---------------------------------------------------
    t = read_json(test_cache_path(pid))
    if t:
        t["stale"] = info["git"] and t.get("fingerprint") != tree_fingerprint(src)
        info["tests"] = t

    # -- issues ---------------------------------------------------------------
    if info["mine"]:
        if stage == "needs-fixes":
            issue("error", "review", f"Marketplace reviewers asked for fixes (#{mk['issue']['number']})")
        elif stage == "submitted":
            issue("info", "submitted", f"Marketplace submission #{mk['issue']['number']} is waiting for a maintainer")
        elif stage == "update" and mk.get("verifyPending"):
            issue("info", "verify", f"Update to {mk['verifyTarget'][:7]} is waiting for verification (#{mk['issue']['number']})")
        elif stage == "update":
            issue("warn", "unverified", f"Listed at {mk['listedCommit'][:7]}, but GitHub has newer commits: "
                                        "file a “Verify and publish a newer upstream commit” request")
        elif mk and mk["state"] == "closed":
            issue("warn", "rejected", f"Marketplace submission #{mk['issue']['number']} was closed without a listing")
        elif stage == "pushed" and info["visibility"] == "private":
            issue("info", "private", "Repo is private, so it can't go on the marketplace or be `omarchy plugin add`ed")
        elif stage == "pushed" and info["visibility"] == "public" and market is not None:
            issue("info", "unsubmitted", "Public on GitHub, not submitted to the marketplace yet")

        ci = info["ci"] or {}
        if ci.get("conclusion") == "failure":
            issue("error", "ci", f"CI failing on {info['branch']}: {ci.get('workflowName', '')} “{ci.get('displayTitle', '')}”")
        elif ci.get("status") in ("in_progress", "queued"):
            issue("info", "ci-running", "CI is running")
        elif info["repo"] and ci.get("status") == "none":
            issue("warn", "no-ci", "No CI runs on GitHub")
        if info["unseen"]:
            issue("warn", "feedback", f"{info['unseen']} new comment(s) from others")
        if fb.get("openIssues"):
            issue("warn", "open-issues", f"{fb['openIssues']} open issue(s) on GitHub")
        if fb.get("openPrs"):
            issue("warn", "open-prs", f"{fb['openPrs']} open pull request(s) waiting for review")
        if fb.get("upstreamMentions"):
            issue("info", "upstream", f"Mentioned {len(fb['upstreamMentions'])} time(s) in {UPSTREAM}")
        if t and not t.get("ok"):
            issue("error", "tests", "Local tests failed" + (" (before your latest changes)" if t.get("stale") else ""))
        if not info["hasTests"]:
            issue("warn", "no-tests", "No test suite (test/run or tests/*test*.sh)")
        elif not t:
            issue("info", "tests-unrun", "Tests have not been run here yet")
        if info["dirty"]:
            issue("warn", "dirty", f"{info['dirty']} uncommitted change(s)")
        if info["ahead"]:
            issue("warn", "ahead", f"{info['ahead']} commit(s) not pushed")
        if info["behind"]:
            issue("warn", "behind", f"{info['behind']} commit(s) on GitHub not pulled into {src}")
        if info["git"] and info["remote"] and not info["upstream"]:
            issue("warn", "no-upstream", f"Branch {info['branch']} does not track a remote branch")
        if not info["git"]:
            issue("info", "no-git", "Not a git repo yet")
        elif not info["remote"]:
            issue("info", "no-remote", "No GitHub remote yet")
        for f in ("README.md", "LICENSE", "preview.png"):
            if not (src / f).exists():
                issue("info", "missing-" + f.lower(), f"Missing {f}")
    else:
        if info["behind"]:
            issue("info", "update-available", f"Update available ({info['behind']} new commit(s))")
        if info["dirty"]:
            issue("warn", "dirty", f"{info['dirty']} local edit(s) to someone else's plugin, lost on update")

    # -- the copy the bar runs vs the copy you work in ------------------------
    if installed and work and (installed / ".git").exists():
        _, ihead, _ = git(installed, "rev-parse", "HEAD")
        if ihead and ihead != info["head"]:
            behind = git(work, "merge-base", "--is-ancestor", ihead, info["head"])[0] == 0
            issue("warn", "stale-install",
                  f"Your bar runs {ihead[:7]} from {installed}, older than your working copy ({info['head'][:7]})"
                  if behind else f"Your bar runs {ihead[:7]} from {installed}, which differs from your working copy")
        info["installedHead"] = ihead
    if not installed:
        issue("info", "not-installed", "Not installed in this shell")
    elif info["enabled"] is False:
        issue("info", "disabled", "Installed but not enabled")

    rank = {"error": 0, "warn": 1, "info": 2}
    issues.sort(key=lambda i: rank[i["level"]])
    info["issues"] = issues
    info["health"] = issues[0]["level"] if issues else "ok"
    return info


def status(fetch=False):
    owners = default_owners()
    enabled = enabled_ids()
    login = gh_login()
    market = (registry_index(fetch), marketplace_issues(login, fetch)) if login else None
    plugins = [inspect(pid, where, owners, enabled, fetch, market) for pid, where in sorted(discover().items())]
    return {"generatedAt": int(time.time()), "marketplace": MARKETPLACE, "plugins": plugins}


def find(pid):
    return discover().get(pid)


def inspect_one(pid):
    where = find(pid)
    if not where:
        return None
    login = gh_login()
    market = (registry_index(), marketplace_issues(login)) if login else None
    return inspect(pid, where, default_owners(), enabled_ids(), market=market)


# ---- actions ---------------------------------------------------------------

def run_tests(pid):
    where = find(pid)
    if not where:
        return {"error": f"No plugin {pid}"}
    real = (where["work"] or where["installed"]).resolve()
    cmd = test_command(real)
    if not cmd:
        return {"error": f"{pid} has no test suite"}
    log = CACHE_DIR / "tests" / f"{pid}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    try:
        with open(log, "w") as fh:
            p = subprocess.run(cmd, cwd=real, stdout=fh, stderr=subprocess.STDOUT, timeout=TEST_TIMEOUT)
        code = p.returncode
    except subprocess.TimeoutExpired:
        code = 124
        with open(log, "a") as fh:
            fh.write(f"\nTimed out after {TEST_TIMEOUT}s\n")
    tail = log.read_text(errors="replace").splitlines()[-40:]
    result = {
        "ok": code == 0, "exitCode": code,
        "startedAt": int(started), "seconds": round(time.time() - started, 1),
        "fingerprint": tree_fingerprint(real) if (real / ".git").exists() else "",
        "log": str(log), "tail": tail,
    }
    write_json(test_cache_path(pid), result)
    return result


def fix_prompt(info):
    lines = [
        f"You're working on the Omarchy shell plugin {info['id']} ({info['name']} {info['version']}) in {info['path']}.",
        f"Its lifecycle stage is {info['stage']}.",
        "Plugin Lab flagged these issues:",
    ]
    lines += [f"- [{i['level']}] {i['text']}" for i in info["issues"]] or ["- (none flagged; do a general health check)"]
    ci = info.get("ci") or {}
    if ci.get("conclusion") == "failure" and ci.get("url"):
        lines.append(f"Failing CI run: {ci['url']} (read it with `gh run view --log-failed`).")
    mk = info.get("marketplace") or {}
    if mk.get("issue"):
        lines.append(f"Marketplace issue: {mk['issue']['url']} (labels: {', '.join(mk['issue']['labels'])}).")
    if mk.get("state") == "needs-fixes":
        lines.append("Reviewer comments, oldest first (read the full thread with `gh issue view --comments`):")
        for c in sorted(mk.get("reviews", []), key=lambda c: c["at"]):
            lines.append(f"--- {c['by']}:\n{c['body']}")
    for i in (info.get("feedback") or {}).get("items", []):
        lines.append(f"Open {i['kind']} #{i['number']} by {i['by']}: {i['title']} ({i['url']})")
    t = info.get("tests") or {}
    if t and not t.get("ok"):
        lines.append(f"Last local test log: {t.get('log')}")
    lines += [
        "Investigate the errors and warnings first, fix what is broken, and run the plugin's test suite to confirm.",
        "Plugin code hot-reloads from ~/.config/omarchy/plugins. Never edit /usr/share/omarchy.",
        "Commit fixes locally, but ask me before pushing, tagging, commenting on GitHub or submitting anything.",
    ]
    return "\n".join(lines)


def launch_fix(pid):
    info = inspect_one(pid)
    if not info:
        return {"error": f"No plugin {pid}"}
    subprocess.Popen(["omarchy-agent-prompt", fix_prompt(info)], cwd=info["path"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return {"launched": pid}


def main(argv):
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "status":
        out = status(fetch="--fetch" in rest)
    elif cmd == "seen" and rest:
        out = mark_seen(rest[0])
    elif cmd == "test" and rest:
        out = run_tests(rest[0])
    elif cmd == "fix" and rest:
        out = launch_fix(rest[0])
    elif cmd == "prompt" and rest:
        info = inspect_one(rest[0])
        out = {"prompt": fix_prompt(info)} if info else {"error": f"No plugin {rest[0]}"}
    else:
        print(__doc__, file=sys.stderr)
        return 2
    print(json.dumps(out))
    return 0 if "error" not in out else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
