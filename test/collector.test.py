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
        # installed checkout that lags behind its working copy
        cls.make("me.split", remote=True)
        sh(cls.plugins.parent, "git", "clone", "-q", str(cls.plugins / "me.split"), str(cls.work / "me.split"))
        (cls.work / "me.split/new.qml").write_text("x")
        sh(cls.work / "me.split", "git", "add", "-A")
        sh(cls.work / "me.split", "git", "commit", "-qm", "newer")

        head = lambda pid: subprocess.run(["git", "-C", str(cls.plugins / pid), "rev-parse", "HEAD"],
                                          capture_output=True, text=True).stdout.strip()
        url = lambda pid: f"https://github.com/me/{pid}"
        body = lambda pid: f"### Repository URL\n\n{url(pid)}\n\n### Category\n\nSystem"
        verify = lambda pid: f"### Verification action\n\nVerify\n\n### Repository URL\n\n{url(pid)}\n\n### Target commit\n\n{'a' * 40}"
        issue = lambda n, pid, labels, state="OPEN", b=None, comments=(): {
            "number": n, "title": pid, "state": state, "url": f"https://x/{n}", "updatedAt": "2026-09-01T00:00:00Z",
            "labels": [{"name": l} for l in labels], "body": b or body(pid), "comments": list(comments)}
        review = {"author": {"login": "reviewer"}, "createdAt": "2026-09-02T00:00:00Z", "url": "https://x/c", "body": "Please fix the paths"}
        (cls.fixtures / "issues.json").write_text(json.dumps([
            issue(1, "me.submitted", ["submission", "validated"]),
            issue(2, "me.reviewed", ["submission", "needs-fixes"], comments=[review]),
            issue(3, "me.listed", ["submission", "listed"], state="CLOSED"),
            issue(4, "me.verifying", ["submission", "listed"], state="CLOSED"),
            issue(5, "me.verifying", ["plugin-update"], b=verify("me.verifying")),
        ]))
        (cls.fixtures / "registry.json").write_text(json.dumps({"sources": [
            {"repo": url("me.listed"), "listedAt": "2026-09-03T00:00:00.000Z", "listingValidatedCommit": head("me.listed")},
            {"repo": url("me.verifying"), "listedAt": "2026-09-03T00:00:00.000Z", "listingValidatedCommit": "b" * 40},
            {"repo": url("me.stale"), "listedAt": "2026-09-03T00:00:00.000Z", "listingValidatedCommit": "c" * 40},
        ]}))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

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
        stages = {k: v["stage"] for k, v in s.items() if k != "me.dirty"}
        self.assertEqual(stages, {
            "me.local": "local", "me.dev": "dev", "me.unpushed": "pushed",
            "me.published": "pushed", "me.released": "pushed", "me.failing": "pushed",
            "me.broken": "local", "them.widget": "installed",
            "me.submitted": "submitted", "me.reviewed": "needs-fixes", "me.listed": "listed",
            "me.verifying": "update", "me.stale": "update", "me.workonly": "pushed", "me.split": "pushed",
            "me.gone": "archived", "me.retired": "pushed",
        })

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
