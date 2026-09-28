#!/usr/bin/env python3
"""Lifecycle and health of the Omarchy shell plugins you build and run.

Subcommands (all print JSON on stdout):

  status [--fetch | --fetch-git]
                     every plugin, its stage and its issues; --fetch refreshes
                     everything now, --fetch-git only runs `git fetch`
  test <id>          run the plugin's own test suite, cache the result
  prompt <id>        the prompt `fix` would hand the agent
  fix <id>           open the default agent in the plugin's working copy
  fix-all            one agent session for every plugin that needs work
  prompt-all         the prompt `fix-all` would hand the agent
  explain <id>       every ref, cache age and GitHub answer behind a plugin's row
  seen <id>          mark the plugin's comments as read
  archive <id>       stop tracking a plugin you no longer need (reversible)
  unarchive <id>     track it again

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
Plugins someone else wrote are "installed". A plugin you archived here, or
whose GitHub repo is archived, is "archived": it stops raising issues.

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
CONFIG_DIR = Path(os.environ.get("PLUGIN_LAB_CONFIG_DIR",
                                 Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "omarchy-plugin-lab"))
UPSTREAM = os.environ.get("PLUGIN_LAB_UPSTREAM", "omacom/omarchy")
MARKETPLACE = os.environ.get("PLUGIN_LAB_MARKETPLACE", "omacom/omarchy-plugin-marketplace")
CI_TTL = 300
REPO_TTL = 3600
FEEDBACK_TTL = 900
MARKET_ISSUES_TTL = 600
REGISTRY_TTL = 3600
TEST_TIMEOUT = 600
FETCH_STALE = int(os.environ.get("PLUGIN_LAB_FETCH_STALE", 1800))  # git refs older than this are stale

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
    items = [{"kind": "pr" if "pull_request" in i else "issue", "number": i["number"],
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
        # your own PRs are work in progress (see own_prs_now), not something to review
        "openPrs": sum(1 for i in items if i["kind"] == "pr" and others(i["by"])),
        "items": items[:10], "comments": talk, "upstreamMentions": mentions,
    }


def first_line(body):
    for line in (body or "").splitlines():
        line = line.strip()
        if line and not line.startswith("<!--"):
            return line[:160]
    return ""


def archived_here():
    return read_json(CONFIG_DIR / "archived.json", {}) or {}


def set_archived(pid, archived):
    if not find(pid):
        return {"error": f"No plugin {pid}"}
    state = archived_here()
    if archived:
        state[pid] = {"at": int(time.time())}
    else:
        state.pop(pid, None)
    write_json(CONFIG_DIR / "archived.json", state)
    return {"archived" if archived else "unarchived": pid}


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

def git_dir(path):
    rc, out, _ = git(path, "rev-parse", "--absolute-git-dir")
    return Path(out) if rc == 0 and out else Path(path) / ".git"


def fetch_repo(path):
    """Fetch origin; the fetch time is read back from FETCH_HEAD, so it survives restarts."""
    git(path, "fetch", "--quiet", "--prune", "--tags", "origin", timeout=60)


def fetched_at(path):
    try:
        return int((git_dir(path) / "FETCH_HEAD").stat().st_mtime)
    except OSError:
        return 0


def rev(path, ref):
    rc, out, _ = git(path, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
    return out if rc == 0 else ""


def default_branch(path, fallback=""):
    """The branch GitHub publishes: origin/HEAD, then gh's answer, then main/master."""
    rc, out, _ = git(path, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if rc == 0 and out.startswith("origin/"):
        return out[len("origin/"):]
    if fallback and rev(path, "origin/" + fallback):
        return fallback
    for name in ("main", "master"):
        if rev(path, "origin/" + name):
            return name
    return ""


def resolve_refs(path, default_hint=""):
    """Everything is compared with the published default branch, never with
    whatever branch happens to be checked out."""
    s = {"git": False, "remote": "", "repo": "", "branch": "", "head": "",
         "defaultBranch": "", "published": "", "localDefault": "",
         "dirty": 0, "ahead": 0, "behind": 0, "upstream": False, "fetchedAt": 0,
         "branches": [], "tag": "", "versionTagged": False, "lastCommit": "", "lastCommitAt": 0}
    if not (path / ".git").exists():
        return s
    s["git"] = True
    _, s["branch"], _ = git(path, "rev-parse", "--abbrev-ref", "HEAD")
    s["head"] = rev(path, "HEAD")
    _, remote, _ = git(path, "config", "--get", "remote.origin.url")
    s["remote"], s["repo"] = remote, github_slug(remote)
    s["fetchedAt"] = fetched_at(path) if remote else 0
    _, status, _ = git(path, "status", "--porcelain")
    s["dirty"] = len([l for l in status.splitlines() if l.strip()])
    default = default_branch(path, default_hint) if remote else ""
    s["defaultBranch"] = default or s["branch"]
    s["localDefault"] = rev(path, "refs/heads/" + s["defaultBranch"])
    if default:
        s["published"] = rev(path, "origin/" + default)
    if s["published"] and s["localDefault"]:
        s["upstream"] = True
        _, counts, _ = git(path, "rev-list", "--left-right", "--count", f"{s['localDefault']}...{s['published']}")
        a, b = (counts.split() + ["0", "0"])[:2]
        s["ahead"], s["behind"] = int(a), int(b)
    # work in progress: local branches with commits that are not published
    base = s["published"] or s["localDefault"]
    _, refs, _ = git(path, "for-each-ref", "refs/heads", "--format=%(refname:short)%09%(objectname)")
    for line in refs.splitlines():
        name, _, sha = line.partition("\t")
        if not name or name == s["defaultBranch"]:
            continue
        _, n, _ = git(path, "rev-list", "--count", f"{base}..{sha}") if base else (0, "0", "")
        if int(n or 0) == 0 and name != s["branch"]:
            continue
        s["branches"].append({"name": name, "head": sha, "unpublished": int(n or 0),
                              "current": name == s["branch"],
                              "pushed": rev(path, "origin/" + name) == sha})
    _, last, _ = git(path, "log", "-1", "--format=%ct%x09%s")
    if last:
        ts, _, subj = last.partition("\t")
        s["lastCommitAt"], s["lastCommit"] = int(ts or 0), subj
    _, s["tag"], _ = git(path, "describe", "--tags", "--abbrev=0")
    return s


def running_copy(installed, work, linked=False):
    """What the bar actually loads: a clone's HEAD, or your working tree when linked."""
    if not installed:
        return {"kind": "none"}
    if linked or (work and installed == work):
        return {"kind": "live", "path": str(installed)}
    if not (installed / ".git").exists():
        return {"kind": "files", "path": str(installed)}
    _, branch, _ = git(installed, "rev-parse", "--abbrev-ref", "HEAD")
    return {"kind": "clone", "path": str(installed), "head": rev(installed, "HEAD"), "branch": branch}


def own_prs_now(repo, login):
    prs = gh_json("pr", "list", "-R", repo, "--author", login, "--state", "open", "--json",
                  "number,title,headRefName,url,mergeStateStatus,reviewDecision,statusCheckRollup")
    if prs is None:
        return None
    out = []
    for p in prs:
        states = [str(c.get("conclusion") or c.get("state") or c.get("status") or "").upper()
                  for c in p.get("statusCheckRollup") or []]
        if any(x in ("FAILURE", "ERROR", "TIMED_OUT", "CANCELLED") for x in states):
            checks = "failing"
        elif any(x in ("PENDING", "IN_PROGRESS", "QUEUED", "EXPECTED") for x in states):
            checks = "running"
        else:
            checks = "passing" if states else "none"
        merge = {"CLEAN": "ready to merge", "BEHIND": "behind its base", "DIRTY": "has conflicts",
                 "UNSTABLE": "checks not green", "DRAFT": "draft"}.get(p.get("mergeStateStatus"), "")
        if p.get("mergeStateStatus") == "BLOCKED":
            merge = "blocked: needs an approving review" if p.get("reviewDecision") == "REVIEW_REQUIRED" else "blocked"
        out.append({"number": p["number"], "title": p["title"], "branch": p["headRefName"], "url": p["url"],
                    "checks": checks, "merge": merge})
    return out


def cache_time(name, key):
    hit = (read_json(CACHE_DIR / name, {}) or {}).get(key) or {}
    return int(hit.get("at", 0))


def short(sha):
    return (sha or "")[:7] or "?"


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
        "ownPrs": [], "checked": {},
        "hasTests": test_command(src) is not None,
    }
    _, remote, _ = git(src, "config", "--get", "remote.origin.url") if (src / ".git").exists() else (1, "", "")
    repo = github_slug(remote)
    info["archived"] = "here" if pid in archived_here() else ""
    issues = []

    def issue(level, code, text, evidence="", action=""):
        issues.append({"level": level, "code": code, "text": text, "evidence": evidence, "action": action})

    remote_owner = repo.split("/")[0].lower() if repo else ""
    namespace = pid.split(".")[0].lower()
    info["mine"] = bool(
        remote_owner in owners
        or (not remote_owner and (namespace in owners or info["author"].lower() in owners
                                  or ((src / ".git").exists() and not remote)))
    )

    meta = {}
    if info["mine"] and repo:
        meta = cached("repo-meta.json", repo, REPO_TTL,
                      lambda: gh_json("repo", "view", repo, "--json", "visibility,isArchived,defaultBranchRef"), fetch) or {}
        info["visibility"] = str(meta.get("visibility") or "").lower()
        if meta.get("isArchived"):
            info["archived"] = "github"
    info.update(resolve_refs(src, ((meta.get("defaultBranchRef") or {}).get("name") or "")))
    info["checked"]["git"] = info["fetchedAt"]
    if version and info["git"]:
        _, tags, _ = git(src, "tag", "--list", f"v{version}", version)
        info["versionTagged"] = bool(tags)
    default = info["defaultBranch"]
    published = info["published"]
    fetched = f"fetched {ago(info['fetchedAt'])}" if info["fetchedAt"] else "never fetched"

    rc, out, err = run(["omarchy", "plugin", "validate", str(src)], timeout=15)
    info["manifestValid"] = rc == 0
    if rc != 0:
        detail = (err or out).splitlines()[-1] if (err or out) else ""
        issue("error", "manifest", "Manifest fails validation" + (": " + detail if detail else ""),
              f"omarchy plugin validate {src}")

    # -- GitHub (own plugins only), always about the published default branch --
    if info["mine"] and repo and not info["archived"]:
        def ci_now():
            runs = gh_json("run", "list", "-R", repo, "-b", default, "-L", "1", "--json",
                           "status,conclusion,headSha,url,displayTitle,workflowName,createdAt")
            if runs is None:
                return None
            return runs[0] if runs else {"status": "none"}
        info["ci"] = cached("ci.json", f"{repo}@{default}", CI_TTL, ci_now, fetch)
        info["checked"]["ci"] = cache_time("ci.json", f"{repo}@{default}")
        info["feedback"] = cached("feedback.json", repo, FEEDBACK_TTL, lambda: feedback_now(repo, pid, owners), fetch)
        info["checked"]["feedback"] = cache_time("feedback.json", repo)
        login = gh_login()
        if login:
            info["ownPrs"] = cached("prs.json", repo, CI_TTL, lambda: own_prs_now(repo, login), fetch) or []
        if market is not None:
            info["marketplace"] = marketplace_state(repo, published, *market)
            info["checked"]["marketplace"] = cache_time("marketplace.json", "issues:" + (login or "").lower())

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
    if info["archived"]:
        stage = "archived"
    elif not info["mine"]:
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
        num = f"#{mk['issue']['number']}" if mk.get("issue") else ""
        if stage == "needs-fixes":
            thread = "marketplace " + num
            last_mine = max((c["at"] for c in mk["reviews"] if c["where"] == thread and c["by"].lower() in owners), default=0)
            last_theirs = max((c["at"] for c in mk["reviews"] if c["where"] == thread and c["by"].lower() not in owners), default=0)
            if last_mine > last_theirs:
                issue("info", "re-review", f"You replied with fixes; waiting for the reviewers to look again ({num})")
            else:
                issue("error", "review", f"Marketplace reviewers asked for fixes ({num})")
        elif stage == "submitted":
            issue("info", "submitted", f"Marketplace submission {num} is waiting for a maintainer")
        elif stage in ("listed", "update"):
            # Published is published. What varies is which commit marketplace users get.
            listed, target = mk.get("listedCommit", ""), mk.get("verifyTarget", "")
            since = time.strftime("%b %-d", time.localtime(mk["listedAt"])) if mk.get("listedAt") else ""
            serves = f"marketplace serves {short(listed)}" + (f" (approved {since})" if since else "")
            ev = f"{serves} · GitHub {default} {short(published)} · {fetched}"
            behind_n = git(src, "rev-list", "--count", f"{listed}..{published}")[1] if listed and published else ""
            newer = f"{behind_n} newer commit(s)" if behind_n and behind_n != "0" else "newer commits"
            if mk.get("verifyPending") and published and not published.startswith(target[:7]):
                issue("warn", "verify-outdated",
                      f"Published, but marketplace users still get the {since or 'older'} version. "
                      f"Your update request {num} asks for {short(target)}, and GitHub {default} has moved on to {short(published)}.",
                      ev + f" · request {num} targets {short(target)}",
                      f"Do: point {num} at {short(published)}, or file a new “Verify and publish a newer upstream commit” "
                      f"request for it. Then: this moves to Waiting until a maintainer approves it.")
            elif mk.get("verifyPending"):
                issue("info", "verify",
                      f"Published. Your update to {short(target)} is waiting for a maintainer to verify it ({num}).",
                      ev, "Nothing to do. If you push again before it is approved, the request will need the new commit.")
            elif stage == "update":
                issue("warn", "unverified",
                      f"Published, but marketplace users still get the {since or 'older'} version: "
                      f"GitHub {default} has {newer} since.",
                      ev,
                      f"Do: file a “Verify and publish a newer upstream commit” request for {short(published)}. "
                      "Then: this moves to Waiting until a maintainer approves it.")
            else:
                issue("info", "published",
                      f"Published. Marketplace users get your latest {default} ({short(published)}).", ev)
        elif mk and mk["state"] == "closed":
            issue("warn", "rejected", f"Marketplace submission {num} was closed without a listing")
        elif stage == "pushed" and info["visibility"] == "private":
            issue("info", "private", "Repo is private, so it can't go on the marketplace or be `omarchy plugin add`ed")
        elif stage == "pushed" and info["visibility"] == "public" and market is not None:
            issue("info", "unsubmitted", "Public on GitHub, not submitted to the marketplace yet")

        ci = info["ci"] or {}
        ci_ev = f"latest run on {default} · checked {ago(info['checked'].get('ci', 0))}"
        if ci.get("conclusion") == "failure":
            issue("error", "ci", f"CI failing on {default}: {ci.get('workflowName', '')} “{ci.get('displayTitle', '')}”", ci_ev)
        elif ci.get("status") in ("in_progress", "queued"):
            issue("info", "ci-running", f"CI is running on {default}", ci_ev)
        elif info["repo"] and ci.get("status") == "none":
            issue("warn", "no-ci", f"No CI runs on {default}", ci_ev)
        if info["unseen"]:
            issue("warn", "feedback", f"{info['unseen']} new comment(s) from others")
        if fb.get("openIssues"):
            issue("warn", "open-issues", f"{fb['openIssues']} open issue(s) on GitHub")
        if fb.get("openPrs"):
            issue("warn", "open-prs", f"{fb['openPrs']} pull request(s) from others waiting for your review")
        for pr in info["ownPrs"]:
            if pr["checks"] == "failing" or pr["merge"] == "has conflicts":
                issue("warn", "own-pr", f"Your PR #{pr['number']} ({pr['branch']}): "
                      + ("checks failing" if pr["checks"] == "failing" else "has conflicts"), pr["url"])
        if fb.get("upstreamMentions"):
            issue("info", "upstream", f"Mentioned {len(fb['upstreamMentions'])} time(s) in {UPSTREAM}")
        if t and not t.get("ok"):
            issue("error", "tests", "Local tests failed" + (" (before your latest changes)" if t.get("stale") else ""), t.get("log", ""))
        if not info["hasTests"]:
            issue("warn", "no-tests", "No test suite (test/run or tests/*test*.sh)")
        elif not t:
            issue("info", "tests-unrun", "Tests have not been run here yet")
        if info["dirty"]:
            issue("warn", "dirty", f"{info['dirty']} uncommitted change(s)", f"git status in {src} (branch {info['branch']})")
        if info["ahead"]:
            ev = f"local {default} {short(info['localDefault'])} · GitHub {default} {short(published)} · {fetched}"
            # commits already on a branch with your open PR are pushed, just not merged
            in_pr = next((pr for pr in info["ownPrs"]
                          if rev(src, "origin/" + pr["branch"]) and
                          git(src, "rev-list", "--count", f"origin/{pr['branch']}..{info['localDefault']}")[1] == "0"), None)
            if in_pr:
                issue("info", "ahead-in-pr", f"{info['ahead']} commit(s) on your {default} are waiting in PR #{in_pr['number']} "
                      f"({in_pr['merge'] or in_pr['checks']})", ev + f" · in origin/{in_pr['branch']}")
            else:
                issue("warn", "ahead", f"{info['ahead']} commit(s) on your {default} not pushed", ev)
        if info["behind"]:
            issue("warn", "behind", f"Your local {default} is {info['behind']} commit(s) behind GitHub",
                  f"local {default} {short(info['localDefault'])} · GitHub {default} {short(published)} · {fetched}")
        if info["git"] and info["remote"] and not published:
            issue("warn", "no-upstream", f"No origin/{default or 'main'} to compare with (never fetched?)")
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

    # -- what the bar runs vs what is published --------------------------------
    running = running_copy(installed, work, bool(where["installed"] and where["installed"].is_symlink()))
    info["running"] = running
    if running["kind"] == "clone" and work and published:
        rhead = running["head"]
        if rhead != published:
            older = git(src, "merge-base", "--is-ancestor", rhead, published)[0] == 0
            n = git(src, "rev-list", "--count", f"{rhead}..{published}")[1] if older else ""
            issue("warn", "stale-install",
                  f"Your bar runs {short(rhead)}, {n} commit(s) behind GitHub {default} ({short(published)})"
                  if older else f"Your bar runs {short(rhead)}, which is not on GitHub {default}",
                  f"bar {short(rhead)} ({running['branch']}) at {installed} · GitHub {default} {short(published)} · {fetched}")
    if not installed:
        issue("info", "not-installed", "Not installed in this shell")
    elif info["enabled"] is False:
        issue("info", "disabled", "Installed but not enabled")

    # -- work in progress: branches and your own PRs (facts, not problems) -----
    work_items = []
    prs_by_branch = {pr["branch"]: pr for pr in info["ownPrs"]}
    for b in info["branches"]:
        pr = prs_by_branch.pop(b["name"], None)
        work_items.append(dict(b, pr=pr))
    for pr in prs_by_branch.values():
        work_items.append({"name": pr["branch"], "current": False, "unpublished": 0, "pushed": True, "pr": pr})
    info["openWork"] = work_items

    # -- freshness: say it, never hide it --------------------------------------
    limits = {"git": FETCH_STALE, "ci": 2 * CI_TTL, "feedback": 2 * FEEDBACK_TTL, "marketplace": 2 * MARKET_ISSUES_TTL}
    now = time.time()
    info["stale"] = sorted(k for k, ts in info["checked"].items()
                           if info["mine"] and not info["archived"] and info["remote"] and (not ts or now - ts > limits[k]))

    # Archived: nothing to do, except remember it may still be on your bar.
    if info["archived"]:
        issues[:] = [{"level": "info", "code": "archived", "evidence": "",
                      "text": "Repo archived on GitHub" if info["archived"] == "github" else "Archived in Plugin Lab"}]
        if info["enabled"]:
            issue("info", "archived-enabled", "Still enabled in the shell")

    rank = {"error": 0, "warn": 1, "info": 2}
    issues.sort(key=lambda i: rank[i["level"]])
    info["issues"] = issues
    info["health"] = "ok" if info["archived"] else (issues[0]["level"] if issues else "ok")
    return info


def ago(ts):
    if not ts:
        return "never"
    d = max(0, int(time.time() - ts))
    if d < 60:
        return "just now"
    if d < 3600:
        return f"{d // 60}m ago"
    if d < 86400:
        return f"{d // 3600}h ago"
    return f"{d // 86400}d ago"


def status(fetch=False, fetch_git=False):
    """fetch: refresh everything now. fetch_git: only `git fetch` every repo (the background timer)."""
    owners = default_owners()
    enabled = enabled_ids()
    login = gh_login()
    found = sorted(discover().items())
    if fetch or fetch_git:
        from concurrent.futures import ThreadPoolExecutor
        repos = {(w["work"] or w["installed"]).resolve() for _, w in found}
        repos |= {w["installed"].resolve() for _, w in found if w["installed"]}
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(fetch_repo, [r for r in repos if (r / ".git").exists()]))
    market = (registry_index(fetch), marketplace_issues(login, fetch)) if login else None
    plugins = [inspect(pid, where, owners, enabled, fetch, market) for pid, where in found]
    mine = [p for p in plugins if p["mine"] and not p["archived"] and p["remote"]]
    checked = [ts for p in mine for ts in p["checked"].values()]
    return {"generatedAt": int(time.time()), "marketplace": MARKETPLACE,
            "checkedAt": min(checked) if checked else 0,
            "stale": sorted({k for p in mine for k in p["stale"]}),
            "plugins": plugins}


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


# How to fix each kind of issue. The agent gets these next to the issue itself.
FIX_HINTS = {
    "ci": "Read the failing run with `gh run view <run> --log-failed`; reproduce locally before changing code.",
    "tests": "Re-run the suite, read the log, fix the code (not the test) unless the test is wrong.",
    "no-tests": "Add a `test/run` suite like the one in rohi.passwords: node for Model.js, python for backends, a manifest test.",
    "no-ci": "Add .github/workflows/test.yml that runs the test suite (stub `omarchy` and `gh`; the runner has neither).",
    "review": "Address every reviewer point in code, add a test that fails before the fix, then draft a reply for me.",
    "stale-install": "Update the bar copy with `omarchy plugin update {id} --yes` (a fast-forward from GitHub). "
                     "First check the bar copy has no uncommitted changes and the working copy is pushed; otherwise ask me.",
    "unverified": "Draft a marketplace “Verify and publish a newer upstream commit” request for the pushed HEAD; do not file it.",
    "verify-outdated": "Draft an update to the pending verify request so it targets the current published commit; do not post it.",
    "dirty": "Look at the uncommitted changes: commit them if they are finished, otherwise tell me what they are.",
    "ahead": "Unpushed commits: tell me what they are; do not push.",
    "behind": "Pull with `git pull --ff-only` if the working tree is clean.",
    "open-issues": "Read the open issues and propose fixes; fix the ones that are clearly bugs.",
    "open-prs": "Review the open pull requests and summarize them for me; do not merge.",
    "manifest": "Fix the manifest until `omarchy plugin validate` passes.",
}

RULES = [
    "Before trusting a flagged state, check it: `python3 " + str(Path(__file__).resolve()) + " explain <id>` "
    "prints the refs and GitHub answers behind it. Trust that and git/gh over this summary.",
    "Never edit /usr/share/omarchy. After changing code the bar runs, run `omarchy restart shell` to load it.",
    "Commit fixes locally, but ask me before pushing, tagging, commenting on GitHub, filing marketplace issues or submitting anything.",
]


def plugin_block(info):
    lines = [f"{info['name']} ({info['id']} {info['version']}), stage {info['stage']}, in {info['path']}"
             + (f"; the bar runs {info['installedPath']}" if info.get("installedPath") and info["installedPath"] != info["path"] else "")]
    for i in info["issues"]:
        lines.append(f"- [{i['level']}] {i['text']}" + (f" {i['action']}" if i.get("action") else ""))
        hint = FIX_HINTS.get(i["code"])
        if hint:
            lines.append("    fix: " + hint.format(id=info["id"]))
    if not info["issues"]:
        lines.append("- (none flagged; do a general health check)")
    ci = info.get("ci") or {}
    if ci.get("conclusion") == "failure" and ci.get("url"):
        lines.append(f"Failing CI run: {ci['url']}")
    mk = info.get("marketplace") or {}
    if mk.get("issue"):
        lines.append(f"Marketplace issue: {mk['issue']['url']} (labels: {', '.join(mk['issue']['labels'])}).")
    if mk.get("state") == "needs-fixes":
        lines.append("Reviewer comments, oldest first (full thread: `gh issue view --comments`):")
        for c in sorted(mk.get("reviews", []), key=lambda c: c["at"]):
            lines.append(f"--- {c['by']}:\n{c['body']}")
    for i in (info.get("feedback") or {}).get("items", []):
        lines.append(f"Open {i['kind']} #{i['number']} by {i['by']}: {i['title']} ({i['url']})")
    t = info.get("tests") or {}
    if t and not t.get("ok"):
        lines.append(f"Last local test log: {t.get('log')}")
    return "\n".join(lines)


def fix_prompt(info):
    return "\n".join([
        "You're fixing one of my Omarchy shell plugins. Plugin Lab flagged it:",
        "",
        plugin_block(info),
        "",
        "Fix the errors first, then the warnings, and run the plugin's test suite to confirm.",
        *RULES,
    ])


def needs_fixing(info):
    return info["mine"] and info["stage"] != "archived" and info["health"] in ("error", "warn")


def fix_all_prompt(plugins):
    todo = sorted((p for p in plugins if needs_fixing(p)),
                  key=lambda p: (0 if p["health"] == "error" else 1, p["name"]))
    if not todo:
        return ""
    blocks = [f"{n}. " + plugin_block(p) for n, p in enumerate(todo, 1)]
    return "\n".join([
        f"You're maintaining my Omarchy shell plugins. Plugin Lab flagged {len(todo)} that need work, "
        "broken ones first. Work through them one at a time, in this order:",
        "",
        "\n\n".join(blocks),
        "",
        "For each plugin: fix what you can, run its test suite, and commit in that plugin's own repo. "
        "Look for the same problem across plugins and fix it the same way everywhere "
        "(shared test layout, CI workflow, README sections).",
        *RULES,
        "Finish with a short table: plugin, what you fixed, what is waiting for my approval.",
    ])


def explain(pid):
    """Every ref, cache age and raw GitHub answer behind one plugin's row, to check it by hand."""
    info = inspect_one(pid)
    if not info:
        return {"error": f"No plugin {pid}"}
    keys = ("id", "stage", "health", "path", "branch", "head", "defaultBranch", "published", "localDefault",
            "ahead", "behind", "dirty", "fetchedAt", "running", "openWork", "ownPrs", "ci", "marketplace",
            "visibility", "archived", "stale")
    out = {k: info.get(k) for k in keys}
    if out.get("marketplace"):
        out["marketplace"] = {k: v for k, v in out["marketplace"].items() if k != "reviews"}
    out["checked"] = {k: ago(v) for k, v in info["checked"].items()}
    out["issues"] = [f"[{i['level']}] {i['text']}" + (f"  <- {i['evidence']}" if i.get("evidence") else "")
                     for i in info["issues"]]
    return out


def launch_agent(prompt, cwd):
    subprocess.Popen(["omarchy-agent-prompt", prompt], cwd=cwd,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def launch_fix(pid):
    info = inspect_one(pid)
    if not info:
        return {"error": f"No plugin {pid}"}
    launch_agent(fix_prompt(info), info["path"])
    return {"launched": pid}


def launch_fix_all():
    plugins = status()["plugins"]
    prompt = fix_all_prompt(plugins)
    if not prompt:
        return {"error": "Nothing needs fixing"}
    cwd = next((str(d) for d in WORK_DIRS if d.is_dir()), str(HOME))
    launch_agent(prompt, cwd)
    return {"launched": [p["id"] for p in plugins if needs_fixing(p)]}


def main(argv):
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "status":
        out = status(fetch="--fetch" in rest, fetch_git="--fetch-git" in rest)
    elif cmd == "fix-all":
        out = launch_fix_all()
    elif cmd == "explain" and rest:
        print(json.dumps(explain(rest[0]), indent=2))
        return 0
    elif cmd == "prompt-all":
        out = {"prompt": fix_all_prompt(status()["plugins"])}
    elif cmd in ("archive", "unarchive") and rest:
        out = set_archived(rest[0], cmd == "archive")
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
