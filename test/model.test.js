// Unit tests for Model.js. Fixtures only.
//
//   node test/model.test.js

var assert = require("assert")
var Model = require("../Model.js")

var tests = []
function test(name, fn) { tests.push([name, fn]) }

var issue = function (level, code) { return { level: level, code: code, text: code } }
var broken = { id: "a", name: "Zed", mine: true, health: "error", stage: "pushed", stars: 3, forks: 1, repo: "me/zed",
               issues: [issue("error", "ci"), issue("warn", "no-tests"), issue("warn", "dirty")] }
var warned = { id: "w", name: "Mid", mine: true, health: "warn", stage: "update", unseen: 2, issues: [issue("warn", "stale-install")] }
var waiting = { id: "r", name: "Rev", mine: true, health: "info", stage: "needs-fixes", marketplace: { issue: { number: 7, url: "u" } },
                issues: [issue("info", "re-review")] }
var fine = { id: "b", name: "Alpha", mine: true, health: "ok", stage: "listed", version: "1.0", issues: [] }
var gone = { id: "d", name: "Old", mine: true, health: "ok", stage: "archived", archived: "github", issues: [] }
var shelved = { id: "e", name: "Shelved", mine: true, health: "ok", stage: "archived", archived: "here", issues: [] }
var theirs = { id: "c", name: "Beta", mine: false, health: "warn", stage: "installed", author: "Ajex", version: "2", issues: [] }
var all = [theirs, fine, broken, warned, waiting, gone, shelved]
var ids = function (rows) { return rows.map(function (p) { return p.id }) }

test("sections: needs you, waiting, all good; the rest folded away", function () {
  var g = Model.groups(all, false)
  assert.deepStrictEqual(g.map(function (x) { return x.key }), ["needs", "waiting", "good"])
  assert.deepStrictEqual(ids(g[0].rows), ["a", "w"])
  assert.deepStrictEqual(ids(g[1].rows), ["r"])
  assert.strictEqual(Model.hiddenCount(all), 3)
  var more = Model.groups(all, true)
  assert.deepStrictEqual(more.map(function (x) { return x.key }), ["needs", "waiting", "good", "archived", "others"])
  assert.deepStrictEqual(ids(Model.flatRows(more)), ["a", "w", "r", "b", "d", "e", "c"])
})

test("captions are short", function () {
  assert.strictEqual(Model.caption(broken), "CI failing · No tests · +1")
  assert.strictEqual(Model.caption(warned), "2 new comments · Bar runs an older copy")
  assert.strictEqual(Model.caption(waiting), "Waiting for re-review · #7")
  assert.strictEqual(Model.caption(fine), "v1.0 · no tests")
  assert.strictEqual(Model.caption(gone), "Archived on GitHub")
  assert.strictEqual(Model.caption(theirs), "v2 · by Ajex")
})

test("hero status and badge", function () {
  var s = Model.summarize(all)
  assert.strictEqual(Model.heroStatus(s), "2 NEED YOU · 1 WAITING · 1 FINE")
  assert.strictEqual(s.archived, 2)
  assert.strictEqual(Model.badge(s), "3")
  assert.strictEqual(Model.heroStatus(Model.summarize([fine])), "1 FINE")
  assert.strictEqual(Model.badge(Model.summarize([fine])), "")
})

test("lifecycle track", function () {
  assert.deepStrictEqual(["local", "dev", "pushed", "submitted", "needs-fixes", "listed", "update"].map(Model.stageStep),
                         [1, 2, 3, 4, 4, 5, 5])
  assert.strictEqual(Model.track().length, 5)
  assert.strictEqual(Model.stageLabel("needs-fixes"), "Needs fixes")
})

test("fix, archive and unarchive", function () {
  assert.strictEqual(Model.fixableCount(all), 2)
  assert.ok(!Model.canFix(theirs))
  assert.ok(Model.canArchive(fine) && !Model.canArchive(theirs) && !Model.canArchive(shelved))
  assert.ok(Model.canUnarchive(shelved) && !Model.canUnarchive(gone))
})

test("row glyph follows the section", function () {
  assert.deepStrictEqual([broken, warned, waiting, fine, gone, theirs].map(Model.rowGlyph), ["󰅚", "󰀪", "󰔟", "󰄬", "󰀼", "󰏗"])
})

test("facts and stats", function () {
  assert.strictEqual(Model.stats(broken), "★ 3  ⑂ 1  󰈈 0")
  assert.strictEqual(Model.stats(theirs), "")
  assert.strictEqual(Model.ciLabel({ status: "completed", conclusion: "failure" }), "CI ✗")
  assert.strictEqual(Model.relativeTime(1000, 1000 + 7200), "2h ago")
})

test("published is published, whatever the pending update", function () {
  assert.strictEqual(Model.stageLabel("listed"), "Published")
  assert.strictEqual(Model.stageLabel("update"), "Published")
  var verifying = { id: "v", name: "V", mine: true, health: "info", stage: "update",
                    marketplace: { issue: { number: 8 } }, issues: [issue("info", "verify")] }
  assert.strictEqual(Model.sectionOf(verifying), "waiting")
  assert.strictEqual(Model.caption(verifying), "Published · update waiting for verification · #8")
  var outdated = { id: "o", name: "O", mine: true, health: "warn", stage: "update", issues: [issue("warn", "verify-outdated")] }
  assert.strictEqual(Model.sectionOf(outdated), "needs")
  assert.strictEqual(Model.caption(outdated), "Update request outdated")
})

test("freshness is always stated", function () {
  assert.strictEqual(Model.freshness(1000, [], 1000 + 180), "CHECKED 3M AGO")
  assert.strictEqual(Model.freshness(1000, ["git"], 1000 + 180), "STALE GIT · PRESS R")
  assert.strictEqual(Model.freshness(0, []), "")
})

test("open work lines", function () {
  assert.strictEqual(Model.workLine({ name: "feat", current: true, unpublished: 1, pushed: false }),
                     "feat (checked out) · 1 commit not on main · not pushed")
  assert.strictEqual(Model.workLine({ name: "fix", pushed: true, unpublished: 0,
                                      pr: { number: 1, checks: "passing", merge: "blocked: needs an approving review" } }),
                     "fix · PR #1 · checks passing · blocked: needs an approving review")
})

var failed = 0
tests.forEach(function (t) {
  try { t[1](); console.log("ok   " + t[0]) }
  catch (e) { failed++; console.log("FAIL " + t[0] + "\n     " + e.message) }
})
if (failed) { console.log(failed + " failed"); process.exit(1) }
