#!/usr/bin/env python3
"""Tests for collector.py against throwaway git repos, one per lifecycle stage.

`omarchy` and `gh` are replaced with stubs on PATH, so this runs anywhere
(including CI) and never touches GitHub or the real plugin directory.

Run: python3 test/collector.test.py
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COLLECTOR = ROOT / "collector.py"


def sh(cwd, *cmd):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


class CollectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.plugins = base / "plugins"
        cls.cache = base / "cache"
        cls.bin = base / "bin"
        for d in (cls.plugins, cls.cache, cls.bin):
            d.mkdir()
        cls.stub("omarchy", """
            if [ "$2" = validate ]; then grep -q BROKEN "$3/manifest.json" && { echo "bad id" >&2; exit 1; }; exit 0; fi
            echo '[{"id":"me.published","enabled":true}]'
        """)
        cls.fixtures = base / "fixtures"
        cls.fixtures.mkdir()
        # gh: marketplace issues and registry come from fixtures, everything else fails
        cls.stub("gh", f"""
            case "$*" in
              "issue list"*) cat {cls.fixtures}/issues.json ;;
              "repo view me/me.gone"*) echo '{{"visibility":"PUBLIC","isArchived":true}}' ;;
              *registry.json*) cat {cls.fixtures}/registry.json ;;
              "run list -R me/me.feature -b main"*) echo '[{{"status":"completed","conclusion":"success","url":"u","displayTitle":"t","workflowName":"w","headSha":"x","createdAt":"2026-09-01T00:00:00Z"}}]' ;;
              "run list -R me/me.feature"*) echo '[{{"status":"completed","conclusion":"failure","url":"u","displayTitle":"branch","workflowName":"w","headSha":"y","createdAt":"2026-09-01T00:00:00Z"}}]' ;;
              "run list -R me/me.prs -b main"*) echo '[{{"status":"completed","conclusion":"failure","url":"https://x/runs/9","displayTitle":"Secret CI title","workflowName":"test","headSha":"z","createdAt":"2026-09-01T00:00:00Z"}}]' ;;
              "pr list -R me/me.prs"*) cat {cls.fixtures}/prs.json ;;
              "api repos/me/me.prs/issues?"*) cat {cls.fixtures}/prs-issues.json ;;
              "api repos/me/me.prs/"*) echo '[]' ;;
              "api repos/me/me.prs") echo '{{"stargazers_count":2,"forks_count":1,"subscribers_count":0,"pushed_at":"2026-09-01T00:00:00Z"}}' ;;
              "api -X GET search/issues"*) echo '{{"items":[]}}' ;;
              *) exit 1 ;;
            esac
        """)
        cls.env = dict(os.environ, PATH=f"{cls.bin}:{os.environ['PATH']}",
                       PLUGIN_LAB_PLUGINS_DIR=str(cls.plugins), PLUGIN_LAB_OWNERS="me", PLUGIN_LAB_CONFIG_DIR=str(base / "config"), PLUGIN_LAB_CACHE_DIR=str(cls.cache),
                       GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        os.environ.update({k: cls.env[k] for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                                                    "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")})

        cls.gitconfig = base / "gitconfig"
        cls.gitconfig.write_text("")
        cls.env["GIT_CONFIG_GLOBAL"] = str(cls.gitconfig)
        os.environ["GIT_CONFIG_GLOBAL"] = str(cls.gitconfig)
        cls.work = base / "work"
        cls.work.mkdir()
        cls.env["PLUGIN_LAB_WORK_DIRS"] = str(cls.work)

        cls.make("me.local", git=False)
        cls.make("me.dev")
        cls.make("me.unpushed", remote=True)
        (cls.plugins / "me.unpushed/new.qml").write_text("x")
        sh(cls.plugins / "me.unpushed", "git", "add", "-A")
        sh(cls.plugins / "me.unpushed", "git", "commit", "-qm", "ahead")
        cls.make("me.published", remote=True, tests="exit 0")
        cls.make("me.released", remote=True)
        sh(cls.plugins / "me.released", "git", "tag", "v1.0.0")
        cls.make("me.failing", remote=True, tests="echo boom; exit 3")
        cls.make("me.broken", git=False, extra={"note": "BROKEN"})
        cls.make("them.widget", git=False, author="Someone")
        for pid in ("me.submitted", "me.reviewed", "me.listed", "me.verifying", "me.stale", "me.gone", "me.retired"):
            cls.make(pid, remote=True)
        # a plugin you work on in ~/Work but never installed
        cls.make("me.workonly", remote=True, root=cls.work)
        # installed clone that lags behind what is published (a newer commit was pushed from ~/Work)
        cls.make("me.split", remote=True)
        cls.clone("me.split")
        cls.commit(cls.work / "me.split", "newer")
        sh(cls.work / "me.split", "git", "push", "-q", "origin", "main")

        # working copy checked out on a feature branch; the bar runs published main (the rohi.network case)
        cls.make("me.feature", remote=True)
        cls.clone("me.feature")
        sh(cls.work / "me.feature", "git", "checkout", "-q", "-b", "feat")
        cls.commit(cls.work / "me.feature", "wip")

        # installed as a symlink to the working copy, like nanoclaw
        cls.make("me.linked", remote=True, root=cls.work)
        (cls.plugins / "me.linked").symlink_to(cls.work / "me.linked")

        # your own PR holds the commit your local main has; someone else also opened a PR
        cls.make("me.prs", remote=True)
        sh(cls.plugins / "me.prs", "git", "checkout", "-q", "-b", "fix")
        cls.commit(cls.plugins / "me.prs", "fix it")
        sh(cls.plugins / "me.prs", "git", "push", "-q", "origin", "fix")
        sh(cls.plugins / "me.prs", "git", "checkout", "-q", "main")
        sh(cls.plugins / "me.prs", "git", "merge", "-q", "--ff-only", "fix")
        (cls.fixtures / "prs.json").write_text(json.dumps([{
            "number": 1, "title": "Fix it", "headRefName": "fix", "url": "https://x/pr/1",
            "mergeStateStatus": "BLOCKED", "reviewDecision": "REVIEW_REQUIRED",
            "statusCheckRollup": [{"conclusion": "SUCCESS"}]}, {
            "number": 3, "title": "Hush", "headRefName": "secret-branch-name", "url": "https://x/pr/3",
            "mergeStateStatus": "DIRTY", "reviewDecision": "",
            "statusCheckRollup": [{"conclusion": "FAILURE"}]}]))
        pr = lambda n, by: {"number": n, "title": f"pr {n}", "user": {"login": by}, "html_url": f"https://x/pr/{n}",
                            "created_at": "2026-09-01T00:00:00Z", "pull_request": {}}
        (cls.fixtures / "prs-issues.json").write_text(json.dumps([pr(1, "me"), pr(2, "stranger")]))

        # GitHub moves on after the last fetch
        cls.make("me.moving", remote=True)
        other = cls.plugins.parent / "moving-elsewhere"
        sh(cls.plugins.parent, "git", "clone", "-q", "https://github.com/me/me.moving", str(other))
        cls.commit(other, "pushed from elsewhere")
        sh(other, "git", "push", "-q", "origin", "main")

        cls.make("me.outdated", remote=True)
        cls.make("me.old", remote=True)

        head = lambda pid: subprocess.run(["git", "-C", str(cls.plugins / pid), "rev-parse", "HEAD"],
                                          capture_output=True, text=True).stdout.strip()
        url = lambda pid: f"https://github.com/me/{pid}"
        body = lambda pid: f"### Repository URL\n\n{url(pid)}\n\n### Category\n\nSystem"
        verify = lambda pid, target: f"### Verification action\n\nVerify\n\n### Repository URL\n\n{url(pid)}\n\n### Target commit\n\n{target}"
        issue = lambda n, pid, labels, state="OPEN", b=None, comments=(): {
            "number": n, "title": pid, "state": state, "url": f"https://x/{n}", "updatedAt": "2026-09-01T00:00:00Z",
            "labels": [{"name": l} for l in labels], "body": b or body(pid), "comments": list(comments)}
        review = {"author": {"login": "reviewer"}, "createdAt": "2026-09-02T00:00:00Z", "url": "https://x/c", "body": "Please fix the paths"}
        (cls.fixtures / "issues.json").write_text(json.dumps([
            issue(1, "me.submitted", ["submission", "validated"]),
            issue(2, "me.reviewed", ["submission", "needs-fixes"], comments=[review]),
            issue(3, "me.listed", ["submission", "listed"], state="CLOSED"),
            issue(4, "me.verifying", ["submission", "listed"], state="CLOSED"),
            issue(5, "me.verifying", ["plugin-update"], b=verify("me.verifying", head("me.verifying"))),
            issue(6, "me.outdated", ["submission", "listed"], state="CLOSED"),
            issue(7, "me.outdated", ["plugin-update"], b=verify("me.outdated", "a" * 40)),
        ]))
        (cls.fixtures / "registry.json").write_text(json.dumps({"sources": [
            {"repo": url("me.listed"), "listedAt": "2026-09-03T00:00:00.000Z", "listingValidatedCommit": head("me.listed")},
            {"repo": url("me.verifying"), "listedAt": "2026-09-03T00:00:00.000Z", "listingValidatedCommit": "b" * 40},
            {"repo": url("me.stale"), "listedAt": "2026-09-03T00:00:00.000Z", "listingValidatedCommit": "c" * 40},
            {"repo": url("me.outdated"), "listedAt": "2026-09-03T00:00:00.000Z", "listingValidatedCommit": "b" * 40},
        ]}))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def clone(cls, pid):
        sh(cls.plugins.parent, "git", "clone", "-q", f"https://github.com/me/{pid}", str(cls.work / pid))

    @staticmethod
    def commit(repo, message):
        (repo / (message.replace(" ", "-") + ".qml")).write_text(message)
        sh(repo, "git", "add", "-A")
        sh(repo, "git", "commit", "-qm", message)

    @classmethod
    def stub(cls, name, body):
        p = cls.bin / name
        p.write_text("#!/bin/bash\n" + textwrap.dedent(body))
        p.chmod(0o755)

    @classmethod
    def make(cls, pid, git=True, remote=False, tests=None, author="me", extra=None, root=None):
        d = (root or cls.plugins) / pid
        d.mkdir()
        manifest = {"schemaVersion": 1, "id": pid, "name": pid, "version": "1.0.0", "author": author}
        manifest.update(extra or {})
        (d / "manifest.json").write_text(json.dumps(manifest))
        for f in ("README.md", "LICENSE", "preview.png"):
            (d / f).write_text("x")
        if tests:
            (d / "test").mkdir()
            (d / "test/run").write_text(tests + "\n")
        if not git:
            return
        sh(d, "git", "init", "-q", "-b", "main")
        sh(d, "git", "add", "-A")
        sh(d, "git", "commit", "-qm", "init")
        if remote:
            bare = cls.plugins.parent / (pid + ".git")
            sh(cls.plugins.parent, "git", "clone", "-q", "--bare", str(d), str(bare))
            # the remote reads as github.com/me/<id> but resolves to the local bare repo
            with open(cls.gitconfig, "a") as fh:
                fh.write(f'[url "{bare}"]\n\tinsteadOf = https://github.com/me/{pid}\n')
            sh(d, "git", "remote", "add", "origin", f"https://github.com/me/{pid}")
            sh(d, "git", "fetch", "-q", "origin")
            sh(d, "git", "branch", "-q", "--set-upstream-to=origin/main")

    def collect(self, *args):
        p = subprocess.run([sys.executable, str(COLLECTOR), *args], env=self.env, capture_output=True, text=True)
        return p.returncode, json.loads(p.stdout or "{}")

    def status(self):
        rc, out = self.collect("status")
        self.assertEqual(rc, 0)
        return {p["id"]: p for p in out["plugins"]}

    def codes(self, plugin):
        return {i["code"] for i in plugin["issues"]}

    def test_stages(self):
        s = self.status()
        expected = {
            "me.local": "local", "me.dev": "dev", "me.unpushed": "pushed",
            "me.published": "pushed", "me.released": "pushed", "me.failing": "pushed",
            "me.broken": "local", "them.widget": "installed",
            "me.submitted": "submitted", "me.reviewed": "needs-fixes", "me.listed": "listed",
            "me.verifying": "update", "me.stale": "update", "me.workonly": "pushed", "me.split": "pushed",
            "me.gone": "archived", "me.retired": "pushed", "me.outdated": "update", "me.feature": "pushed",
        }
        self.assertEqual({k: s[k]["stage"] for k in expected}, expected)

    # ---- accuracy: one test per way the panel has been, or could be, wrong ----

    def test_feature_branch_checkout_is_not_a_stale_bar(self):
        p = self.status()["me.feature"]
        self.assertEqual((p["branch"], p["defaultBranch"]), ("feat", "main"))
        self.assertNotIn("stale-install", self.codes(p))
        self.assertNotIn("ahead", self.codes(p))
        work = {w["name"]: w for w in p["openWork"]}
        self.assertTrue(work["feat"]["current"])
        self.assertEqual((work["feat"]["unpublished"], work["feat"]["pushed"]), (1, False))

    def test_bar_behind_published_is_flagged_with_both_commits(self):
        p = self.status()["me.split"]
        i = next(i for i in p["issues"] if i["code"] == "stale-install")
        self.assertIn("1 commit(s) behind GitHub main", i["text"])
        self.assertIn(p["published"][:7], i["evidence"])
        self.assertIn(p["running"]["head"][:7], i["evidence"])

    def test_symlinked_install_runs_live(self):
        p = self.status()["me.linked"]
        self.assertEqual(p["running"]["kind"], "live")
        self.assertNotIn("stale-install", self.codes(p))

    def test_background_fetch_sees_github_move(self):
        before = self.status()["me.moving"]
        self.assertEqual(before["behind"], 0)
        rc, out = self.collect("status", "--fetch-git")
        after = {p["id"]: p for p in out["plugins"]}["me.moving"]
        self.assertEqual(after["behind"], 1)
        self.assertGreaterEqual(after["fetchedAt"], before["fetchedAt"])

    def test_only_other_peoples_prs_need_you(self):
        p = self.status()["me.prs"]
        self.assertEqual(p["feedback"]["openPrs"], 1)
        codes = self.codes(p)
        self.assertIn("open-prs", codes)
        self.assertNotIn("ahead", codes)
        self.assertIn("ahead-in-pr", codes)
        pr = next(w["pr"] for w in p["openWork"] if w["name"] == "fix")
        self.assertEqual((pr["checks"], pr["merge"]), ("passing", "blocked: needs an approving review"))

    def test_ci_comes_from_the_default_branch(self):
        p = self.status()["me.feature"]
        self.assertEqual(p["ci"]["conclusion"], "success")
        self.assertNotIn("ci", self.codes(p))

    def test_outdated_verify_request_needs_you(self):
        s = self.status()
        self.assertIn("verify-outdated", self.codes(s["me.outdated"]))
        self.assertEqual(s["me.outdated"]["health"], "warn")
        self.assertIn("verify", self.codes(s["me.verifying"]))
        self.assertNotIn("verify-outdated", self.codes(s["me.verifying"]))

    def test_default_branch_without_origin_head(self):
        p = self.status()["me.published"]
        self.assertEqual(p["defaultBranch"], "main")
        self.assertTrue(p["published"])

    def test_old_git_data_is_marked_stale(self):
        fh = self.plugins / "me.old/.git/FETCH_HEAD"
        old = __import__("time").time() - 7200
        os.utime(fh, (old, old))
        rc, out = self.collect("status")
        p = {p["id"]: p for p in out["plugins"]}["me.old"]
        self.assertIn("git", p["stale"])
        self.assertIn("git", out["stale"])

    def test_repo_archived_on_github_stops_raising_issues(self):
        p = self.status()["me.gone"]
        self.assertEqual((p["stage"], p["archived"], p["health"]), ("archived", "github", "ok"))
        self.assertEqual(self.codes(p), {"archived"})

    def test_archive_here_is_reversible(self):
        rc, _ = self.collect("archive", "me.retired")
        self.assertEqual(rc, 0)
        p = self.status()["me.retired"]
        self.assertEqual((p["stage"], p["archived"], p["health"]), ("archived", "here", "ok"))
        self.assertNotIn("no-tests", self.codes(p))
        self.collect("unarchive", "me.retired")
        self.assertEqual(self.status()["me.retired"]["stage"], "pushed")

    def test_archiving_an_unknown_plugin_fails(self):
        rc, r = self.collect("archive", "nope")
        self.assertEqual(rc, 1)

    def test_needs_fixes_is_an_error_and_the_review_reaches_the_prompt(self):
        p = self.status()["me.reviewed"]
        self.assertEqual(p["health"], "error")
        self.assertIn("review", self.codes(p))
        self.assertEqual(p["unseen"], 1)
        self.assertEqual(p["comments"][0]["where"], "marketplace #2")
        rc, r = self.collect("prompt", "me.reviewed")
        self.assertIn("Please fix the paths", r["prompt"])

    def test_listed_at_head_has_no_marketplace_nag(self):
        p = self.status()["me.listed"]
        self.assertFalse(self.codes(p) & {"verify", "unverified", "unsubmitted"})

    def test_update_is_pending_or_needs_a_verify_request(self):
        s = self.status()
        self.assertIn("verify", self.codes(s["me.verifying"]))
        self.assertIn("unverified", self.codes(s["me.stale"]))

    def test_replying_marks_review_comments_read(self):
        issues = json.loads((self.fixtures / "issues.json").read_text())
        issues[1]["comments"].append({"author": {"login": "me"}, "createdAt": "2026-09-05T00:00:00Z",
                                      "url": "https://x/r", "body": "Fixed"})
        (self.fixtures / "issues.json").write_text(json.dumps(issues))
        (self.cache / "marketplace.json").unlink()
        try:
            p = self.status()["me.reviewed"]
            self.assertEqual(p["unseen"], 0)
            # your reply is the latest word: waiting for re-review, not broken
            self.assertEqual(p["stage"], "needs-fixes")
            self.assertNotEqual(p["health"], "error")
            self.assertIn("re-review", self.codes(p))
            self.assertNotIn("review", self.codes(p))
        finally:
            issues[1]["comments"].pop()
            (self.fixtures / "issues.json").write_text(json.dumps(issues))
            (self.cache / "marketplace.json").unlink()

    def test_fix_all_covers_every_plugin_that_needs_work_errors_first(self):
        rc, r = self.collect("prompt-all")
        self.assertEqual(rc, 0)
        prompt = r["prompt"]
        self.assertIn("me.reviewed", prompt)
        self.assertIn("omarchy plugin update me.split --yes", prompt)
        self.assertNotIn("me.gone", prompt)
        self.assertNotIn("them.widget", prompt)
        self.assertLess(prompt.index("me.broken"), prompt.index("me.split"))
        self.assertIn("explain <id>", prompt)
        self.assertIn("ask me before pushing", prompt)

    def test_work_dir_plugins_are_found_even_when_not_installed(self):
        p = self.status()["me.workonly"]
        self.assertFalse(p["installed"])
        self.assertIn("not-installed", self.codes(p))

    def test_bar_running_older_code_than_the_working_copy_is_flagged(self):
        p = self.status()["me.split"]
        self.assertEqual(p["path"], str((self.work / "me.split").resolve()))
        self.assertIn("stale-install", self.codes(p))

    def test_unpushed_commits_are_flagged(self):
        p = self.status()["me.unpushed"]
        self.assertEqual(p["ahead"], 1)
        self.assertIn("ahead", self.codes(p))
        self.assertEqual(p["health"], "warn")

    def test_uncommitted_edits_are_flagged(self):
        self.make("me.dirty", remote=True)
        (self.plugins / "me.dirty/README.md").write_text("changed")
        p = self.status()["me.dirty"]
        self.assertEqual(p["dirty"], 1)
        self.assertIn("dirty", self.codes(p))

    def test_invalid_manifest_is_an_error(self):
        p = self.status()["me.broken"]
        self.assertEqual(p["health"], "error")
        self.assertIn("manifest", self.codes(p))

    def test_someone_elses_plugin_is_not_nagged_about_publishing(self):
        p = self.status()["them.widget"]
        self.assertFalse(p["mine"])
        self.assertFalse(self.codes(p) & {"no-tests", "no-git", "untagged"})

    def test_enabled_state_comes_from_the_shell(self):
        s = self.status()
        self.assertTrue(s["me.published"]["enabled"])
        self.assertIn("disabled", self.codes(s["me.dev"]))

    def test_passing_tests_are_cached_until_the_tree_changes(self):
        rc, r = self.collect("test", "me.published")
        self.assertEqual((rc, r["ok"]), (0, True))
        p = self.status()["me.published"]
        self.assertFalse(p["tests"]["stale"])
        self.assertNotIn("tests-unrun", self.codes(p))
        (self.plugins / "me.published/README.md").write_text("edited")
        self.assertTrue(self.status()["me.published"]["tests"]["stale"])
        (self.plugins / "me.published/README.md").write_text("x")

    def test_failing_tests_are_an_error_with_a_log(self):
        rc, r = self.collect("test", "me.failing")
        self.assertEqual((r["ok"], r["exitCode"]), (False, 3))
        self.assertIn("boom", "\n".join(r["tail"]))
        p = self.status()["me.failing"]
        self.assertEqual(p["health"], "error")
        self.assertIn("tests", self.codes(p))

    def test_fix_prompt_names_the_issues_and_forbids_publishing(self):
        rc, r = self.collect("prompt", "me.unpushed")
        self.assertEqual(rc, 0)
        self.assertIn("not pushed", r["prompt"])
        self.assertIn(str(self.plugins / "me.unpushed"), r["prompt"])
        self.assertIn("ask me before pushing", r["prompt"])

    def test_unknown_plugin(self):
        rc, r = self.collect("test", "nope")
        self.assertEqual(rc, 1)
        self.assertIn("error", r)

    def test_seen_is_recorded(self):
        rc, _ = self.collect("seen", "me.dev")
        self.assertEqual(rc, 0)
        self.assertIn("me.dev", json.loads((self.cache / "seen.json").read_text()))

    def test_cache_and_config_are_owner_only_under_umask_022(self):
        old_umask = os.umask(0o022)
        try:
            self.collect("status")
            self.collect("test", "me.published")
            self.collect("seen", "me.dev")
            self.collect("archive", "me.retired")
            self.collect("unarchive", "me.retired")
        finally:
            os.umask(old_umask)
        config_dir = Path(self.env["PLUGIN_LAB_CONFIG_DIR"])
        for d in (self.cache, self.cache / "tests", config_dir):
            self.assertEqual(d.stat().st_mode & 0o777, 0o700, f"{d} should be 0700")
        files = [p for p in self.cache.rglob("*") if p.is_file()] + [config_dir / "archived.json"]
        self.assertTrue(files)
        for f in files:
            self.assertEqual(f.stat().st_mode & 0o777, 0o600, f"{f} should be 0600")

    def test_fix_prompt_and_agent_argv_omit_issue_and_pr_titles(self):
        argv_file = self.fixtures / "agent-argv.json"
        self.stub("omarchy-agent-prompt", f"""
            python3 -c 'import json, sys; open(sys.argv[1], "w").write(json.dumps(sys.argv[2:]))' "{argv_file}" "$@"
        """)
        rc, r = self.collect("prompt", "me.prs")
        self.assertEqual(rc, 0)
        self.assertIn("Open pr #2 (https://x/pr/2)", r["prompt"])
        for secret in ("pr 1", "pr 2", "Fix it", "Secret CI title"):
            self.assertNotIn(secret, r["prompt"])
        rc, all_r = self.collect("prompt-all")
        self.assertEqual(rc, 0)
        for secret in ("pr 1", "pr 2", "Fix it", "Secret CI title"):
            self.assertNotIn(secret, all_r["prompt"])
        rc, _ = self.collect("fix", "me.prs")
        self.assertEqual(rc, 0)
        for _ in range(50):
            if argv_file.exists() and argv_file.stat().st_size > 0:
                break
            __import__("time").sleep(0.02)
        argv = "\n".join(json.loads(argv_file.read_text()))
        for secret in ("pr 1", "pr 2", "Fix it", "Secret CI title"):
            self.assertNotIn(secret, argv)

    def test_pre_upgrade_cache_and_config_are_tightened_before_reads(self):
        self.collect("status")
        self.collect("test", "me.published")
        self.collect("archive", "me.retired")
        self.collect("unarchive", "me.retired")
        config_dir = Path(self.env["PLUGIN_LAB_CONFIG_DIR"])
        # what an older Plugin Lab left behind under umask 022
        for root in (self.cache, config_dir):
            for p in [root, *root.rglob("*")]:
                p.chmod(0o755 if p.is_dir() else 0o644)
        old_umask = os.umask(0o022)
        try:
            rc, _ = self.collect("explain", "me.published")  # served from the warm cache
        finally:
            os.umask(old_umask)
        self.assertEqual(rc, 0)
        for root in (self.cache, config_dir):
            for p in [root, *root.rglob("*")]:
                want = 0o700 if p.is_dir() else 0o600
                self.assertEqual(p.stat().st_mode & 0o777, want, f"{p} should be {oct(want)}")

    def test_fix_prompt_and_agent_argv_omit_private_pr_branch_names(self):
        p = self.status()["me.prs"]
        self.assertIn("own-pr", self.codes(p))
        argv_file = self.fixtures / "agent-argv-branch.json"
        self.stub("omarchy-agent-prompt", f"""
            python3 -c 'import json, sys; open(sys.argv[1], "w").write(json.dumps(sys.argv[2:]))' "{argv_file}" "$@"
        """)
        rc, r = self.collect("prompt", "me.prs")
        self.assertEqual(rc, 0)
        self.assertIn("Your PR #3", r["prompt"])
        self.assertNotIn("secret-branch-name", r["prompt"])
        rc, all_r = self.collect("prompt-all")
        self.assertEqual(rc, 0)
        self.assertNotIn("secret-branch-name", all_r["prompt"])
        rc, _ = self.collect("fix", "me.prs")
        self.assertEqual(rc, 0)
        for _ in range(50):
            if argv_file.exists() and argv_file.stat().st_size > 0:
                break
            __import__("time").sleep(0.02)
        self.assertNotIn("secret-branch-name", "\n".join(json.loads(argv_file.read_text())))


if __name__ == "__main__":
    unittest.main(verbosity=2)
