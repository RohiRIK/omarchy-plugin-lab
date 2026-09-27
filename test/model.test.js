// Unit tests for Model.js. Fixtures only.
//
//   node test/model.test.js

var assert = require("assert")
var Model = require("../Model.js")

var tests = []
function test(name, fn) { tests.push([name, fn]) }

var mineBroken = { id: "a", name: "Zed", mine: true, health: "error", stage: "needs-fixes", unseen: 2, stars: 3, forks: 1, activityAt: 50, repo: "me/zed" }
var mineOk = { id: "b", name: "Alpha", mine: true, health: "ok", stage: "listed", stars: 9, activityAt: 100 }
var gone = { id: "d", name: "Old", mine: true, health: "ok", stage: "archived", archived: "github", stars: 50 }
var shelved = { id: "e", name: "Shelved", mine: true, health: "ok", stage: "archived", archived: "here" }
var theirs = { id: "c", name: "Beta", mine: false, health: "warn", stage: "installed", author: "Ajex" }

test("stages walk the lifecycle in order", function () {
  assert.deepStrictEqual(["local", "dev", "pushed", "submitted", "needs-fixes", "listed", "update"].map(Model.stageStep),
                         [1, 2, 3, 4, 4, 5, 5])
  assert.strictEqual(Model.stageStep("installed"), 0)
  assert.strictEqual(Model.stageLabel("needs-fixes"), "Needs fixes")
})

test("sort keys", function () {
  var ids = function (key) { return Model.sortPlugins([theirs, mineOk, mineBroken], key).map(function (p) { return p.id }) }
  assert.deepStrictEqual(ids("health"), ["a", "b", "c"])
  assert.deepStrictEqual(ids("stars"), ["b", "a", "c"])
  assert.deepStrictEqual(ids("stage"), ["b", "a", "c"])
  assert.deepStrictEqual(ids("activity"), ["b", "a", "c"])
  assert.deepStrictEqual(ids("name"), ["b", "a", "c"])
  assert.strictEqual(Model.nextSort("name"), "health")
})

test("filters and search", function () {
  var all = [theirs, mineOk, mineBroken]
  var ids = function (f, q) { return Model.visibleRows(all, f, q, "name").map(function (p) { return p.id }) }
  assert.deepStrictEqual(ids("attention", ""), ["a", "c"])
  assert.deepStrictEqual(ids("review", ""), ["a"])
  assert.deepStrictEqual(ids("listed", ""), ["b"])
  assert.deepStrictEqual(ids("others", ""), ["c"])
  assert.deepStrictEqual(ids("all", "ajex"), ["c"])
  assert.deepStrictEqual(ids("all", "needs fix"), ["a"])
  assert.strictEqual(Model.countFor(all, "mine"), 2)
})

test("archived plugins live only under their own filter and out of the counts", function () {
  var all = [theirs, mineOk, mineBroken, gone, shelved]
  var ids = function (f) { return Model.visibleRows(all, f, "", "name").map(function (p) { return p.id }) }
  assert.deepStrictEqual(ids("all"), ["b", "a", "c"])
  assert.deepStrictEqual(ids("mine"), ["b", "a"])
  assert.deepStrictEqual(ids("archived"), ["d", "e"])
  var s = Model.summarize(all)
  assert.strictEqual(s.archived, 2)
  assert.strictEqual(s.mine, 2)
  assert.strictEqual(s.stars, 12)
})

test("archive and unarchive buttons", function () {
  assert.ok(Model.canArchive(mineOk))
  assert.ok(!Model.canArchive(theirs))
  assert.ok(!Model.canArchive(shelved))
  assert.ok(Model.canUnarchive(shelved))
  assert.ok(!Model.canUnarchive(gone))
})

test("fix all counts your broken or warned, unarchived plugins", function () {
  var warnedButArchived = { id: "f", mine: true, health: "warn", stage: "archived", archived: "here" }
  assert.strictEqual(Model.fixableCount([mineBroken, mineOk, theirs, gone, warnedButArchived]), 1)
})

test("stats line only for your GitHub plugins", function () {
  assert.strictEqual(Model.statsLine(mineBroken), "★ 3  ⑂ 1  󰈈 0")
  assert.strictEqual(Model.statsLine(theirs), "")
})

test("yours come first, worst health first", function () {
  var ids = Model.sortPlugins([theirs, mineOk, mineBroken]).map(function (p) { return p.id })
  assert.deepStrictEqual(ids, ["a", "b", "c"])
})

test("sortPlugins does not mutate its input", function () {
  var input = [theirs, mineOk]
  Model.sortPlugins(input)
  assert.strictEqual(input[0], theirs)
})

test("summary counts broken plugins and unread comments", function () {
  var s = Model.summarize([mineBroken, mineOk, theirs])
  assert.deepStrictEqual(s, { mine: 2, installed: 1, errors: 1, warnings: 1, unseen: 2, listed: 1, stars: 12, forks: 1, archived: 0 })
  assert.strictEqual(Model.badge(s), "3")
  assert.ok(Model.summaryLine(s).indexOf("2 new comments") >= 0)
})

test("badge is empty when all is well", function () {
  assert.strictEqual(Model.badge(Model.summarize([mineOk])), "")
})

test("only your broken or warned plugins can be sent to the agent", function () {
  assert.ok(Model.canFix(mineBroken))
  assert.ok(!Model.canFix(mineOk))
  assert.ok(!Model.canFix(theirs))
})

test("CI and test labels", function () {
  assert.strictEqual(Model.ciLabel({ status: "completed", conclusion: "failure" }), "CI ✗")
  assert.strictEqual(Model.ciLabel({ status: "in_progress" }), "CI running")
  assert.strictEqual(Model.ciLabel({ status: "none" }), "no CI")
  assert.strictEqual(Model.testsLabel({ hasTests: false }), "no tests")
  assert.strictEqual(Model.testsLabel({ hasTests: true, tests: { ok: true, stale: true } }), "tests ✓ (stale)")
})

test("subtitle shows author for someone else's plugin", function () {
  assert.strictEqual(Model.subtitle({ version: "1.0", author: "Ajex" }), "v1.0 · by Ajex")
})

test("relativeTime", function () {
  assert.strictEqual(Model.relativeTime(1000, 1030), "just now")
  assert.strictEqual(Model.relativeTime(1000, 1000 + 7200), "2h ago")
  assert.strictEqual(Model.relativeTime(0, 5), "")
})

var failed = 0
tests.forEach(function (t) {
  try { t[1](); console.log("ok   " + t[0]) }
  catch (e) { failed++; console.log("FAIL " + t[0] + "\n     " + e.message) }
})
if (failed) { console.log(failed + " failed"); process.exit(1) }
