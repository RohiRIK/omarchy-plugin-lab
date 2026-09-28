// Pure helpers for Panel.qml. No QML imports, so test/model.test.js can load
// this file in node.

// The lifecycle track on the detail page. needs-fixes sits with submitted,
// update with listed.
var STAGE_STEP = { local: 1, dev: 2, pushed: 3, submitted: 4, "needs-fixes": 4, listed: 5, update: 5, installed: 0, archived: 0 }
var TRACK = ["Local", "Git", "GitHub", "Review", "Published"]

var STAGE_LABELS = {
  local: "Local",
  dev: "In dev",
  pushed: "On GitHub",
  submitted: "In review",
  "needs-fixes": "Needs fixes",
  listed: "Published",
  update: "Published",
  installed: "Installed",
  archived: "Archived"
}

// Few words per issue, for the one line under a plugin's name.
var SHORT = {
  ci: "CI failing",
  tests: "Tests failing",
  manifest: "Manifest invalid",
  review: "Reviewers asked for fixes",
  "stale-install": "Bar runs an older copy",
  unverified: "Newer commit not verified",
  "verify-outdated": "Update request outdated",
  "own-pr": "Your PR needs attention",
  rejected: "Submission closed",
  "open-issues": "Open issues",
  "open-prs": "Open pull requests",
  dirty: "Uncommitted changes",
  ahead: "Unpushed commits",
  behind: "Behind GitHub",
  "no-upstream": "Branch not tracking",
  "no-ci": "No CI",
  "no-tests": "No tests"
}

var SECTIONS = [
  { key: "needs", label: "NEEDS YOU" },
  { key: "waiting", label: "WAITING" },
  { key: "good", label: "ALL GOOD" },
  { key: "archived", label: "ARCHIVED" },
  { key: "others", label: "BY OTHERS" }
]

function stageLabel(stage) {
  return STAGE_LABELS[stage] || String(stage || "?")
}

function stageStep(stage) {
  return STAGE_STEP[stage] || 0
}

function track() {
  return TRACK.slice()
}

function healthGlyph(health) {
  if (health === "error") return "󰅚"
  if (health === "warn") return "󰀪"
  if (health === "info") return "󰋽"
  return "󰄬"
}

// The row icon says which section it is in: only "needs you" rows show health.
function rowGlyph(plugin) {
  var s = sectionOf(plugin)
  if (s === "needs") return healthGlyph(plugin.health)
  if (s === "waiting") return "󰔟"
  if (s === "archived") return "󰀼"
  if (s === "others") return "󰏗"
  return "󰄬"
}

function unseen(plugin) {
  return plugin ? Number(plugin.unseen || 0) : 0
}

function isArchived(plugin) {
  return !!plugin && plugin.stage === "archived"
}

function hasIssue(plugin, code) {
  return (plugin.issues || []).some(function (i) { return i.code === code })
}

// Waiting: someone else has the next move (a reviewer or a verifier).
function isWaiting(plugin) {
  return plugin.stage === "submitted" || hasIssue(plugin, "re-review") || hasIssue(plugin, "verify")
      || hasIssue(plugin, "ahead-in-pr")
}

function sectionOf(plugin) {
  if (isArchived(plugin)) return "archived"
  if (!plugin.mine) return "others"
  if (plugin.health === "error" || plugin.health === "warn" || unseen(plugin) > 0) return "needs"
  if (isWaiting(plugin)) return "waiting"
  return "good"
}

function byName(a, b) {
  return String(a.name).localeCompare(String(b.name))
}

// Sections in display order, empty ones dropped. Within "needs", broken first.
// Archived and others are left out unless `showMore`.
function groups(plugins, showMore) {
  var out = []
  SECTIONS.forEach(function (s) {
    if (!showMore && (s.key === "archived" || s.key === "others")) return
    var rows = (plugins || []).filter(function (p) { return sectionOf(p) === s.key })
    rows.sort(function (a, b) {
      if (s.key === "needs" && (a.health === "error") !== (b.health === "error")) return a.health === "error" ? -1 : 1
      return byName(a, b)
    })
    if (rows.length) out.push({ key: s.key, label: s.label, rows: rows })
  })
  return out
}

// The rows in on-screen order, for the keyboard cursor.
function flatRows(groupList) {
  var out = []
  ;(groupList || []).forEach(function (g) { out = out.concat(g.rows) })
  return out
}

function hiddenCount(plugins) {
  return (plugins || []).filter(function (p) {
    var s = sectionOf(p)
    return s === "archived" || s === "others"
  }).length
}

// One short line under the name: what needs you, what you're waiting for, or where it is.
function caption(plugin) {
  var s = sectionOf(plugin)
  if (s === "needs") {
    var shorts = []
    var n = unseen(plugin)
    if (n > 0) shorts.push(n + " new comment" + (n === 1 ? "" : "s"))
    ;(plugin.issues || []).forEach(function (i) {
      if ((i.level === "error" || i.level === "warn") && SHORT[i.code]) shorts.push(SHORT[i.code])
    })
    return shorts.slice(0, 2).join(" · ") + (shorts.length > 2 ? " · +" + (shorts.length - 2) : "")
  }
  var mk = plugin.marketplace || {}
  var num = mk.issue ? " · #" + mk.issue.number : ""
  if (s === "waiting") {
    if (hasIssue(plugin, "re-review")) return "Waiting for re-review" + num
    if (hasIssue(plugin, "verify")) return "Published · update waiting for verification" + num
    if (hasIssue(plugin, "ahead-in-pr")) return "Your PR is waiting to merge"
    return "In marketplace review" + num
  }
  if (s === "archived") return plugin.archived === "github" ? "Archived on GitHub" : "Archived"
  if (s === "others") return (plugin.version ? "v" + plugin.version : "") + (plugin.author ? " · by " + plugin.author : "")
  return facts(plugin)
}

function summarize(plugins) {
  var s = { mine: 0, installed: 0, errors: 0, warnings: 0, unseen: 0, listed: 0, stars: 0, forks: 0,
            archived: 0, needs: 0, waiting: 0, good: 0 }
  ;(plugins || []).forEach(function (p) {
    var sec = sectionOf(p)
    if (sec === "archived") { s.archived++; return }
    if (p.mine) s.mine++
    else s.installed++
    if (sec === "needs" || sec === "waiting" || sec === "good") s[sec]++
    if (p.health === "error") s.errors++
    else if (p.health === "warn") s.warnings++
    if (p.stage === "listed" || p.stage === "update") s.listed++
    s.unseen += unseen(p)
    s.stars += Number(p.stars || 0)
    s.forks += Number(p.forks || 0)
  })
  return s
}

// The hero's status line, already in caps like Omarchy's own panels.
function heroStatus(s) {
  var parts = []
  if (s.needs) parts.push(s.needs + " NEED YOU")
  if (s.waiting) parts.push(s.waiting + " WAITING")
  if (s.good) parts.push(s.good + " FINE")
  if (!parts.length) return s.mine ? "ALL GOOD" : "NO PLUGINS YET"
  return parts.join(" · ")
}

// "CHECKED 3M AGO", or a plain warning when any source is older than it should be.
function freshness(checkedAt, stale, now) {
  if (stale && stale.length) return "STALE " + stale.join(", ").toUpperCase() + " · PRESS R"
  if (!checkedAt) return ""
  return "CHECKED " + relativeTime(checkedAt, now).toUpperCase()
}

// One line per branch or PR you have in flight.
function workLine(w) {
  var bits = [w.name + (w.current ? " (checked out)" : "")]
  if (w.unpublished) bits.push(w.unpublished + " commit" + (w.unpublished === 1 ? "" : "s") + " not on main")
  if (w.pr) bits.push("PR #" + w.pr.number + " · checks " + w.pr.checks + (w.pr.merge ? " · " + w.pr.merge : ""))
  else if (!w.pushed) bits.push("not pushed")
  return bits.join(" · ")
}

// Bar label next to the icon: broken plugins plus unread comments, or nothing.
function badge(s) {
  var n = (s.errors || 0) + (s.unseen || 0)
  return n > 0 ? String(n) : ""
}

function relativeTime(ts, now) {
  if (!ts) return ""
  var d = Math.max(0, Math.floor((now || Date.now() / 1000) - ts))
  if (d < 60) return "just now"
  if (d < 3600) return Math.floor(d / 60) + "m ago"
  if (d < 86400) return Math.floor(d / 3600) + "h ago"
  return Math.floor(d / 86400) + "d ago"
}

function ciLabel(ci) {
  if (!ci) return ""
  if (ci.status === "none") return "no CI"
  if (ci.status && ci.status !== "completed") return "CI running"
  if (ci.conclusion === "success") return "CI ✓"
  if (ci.conclusion === "failure") return "CI ✗"
  return "CI " + (ci.conclusion || ci.status || "?")
}

function testsLabel(plugin) {
  if (!plugin.hasTests) return "no tests"
  var t = plugin.tests
  if (!t) return "tests not run"
  return (t.ok ? "tests ✓" : "tests ✗") + (t.stale ? " (stale)" : "")
}

// "★ 3  ⑂ 1  󰈈 2" for your plugins on GitHub, empty otherwise.
function stats(plugin) {
  if (!plugin || !plugin.repo || !plugin.mine) return ""
  return "★ " + (plugin.stars || 0) + "  ⑂ " + (plugin.forks || 0) + "  󰈈 " + (plugin.watchers || 0)
}

// Version, CI and tests.
function facts(plugin) {
  var bits = []
  if (plugin.version) bits.push("v" + plugin.version)
  if (plugin.mine && !isArchived(plugin)) {
    var ci = ciLabel(plugin.ci)
    if (ci) bits.push(ci)
    bits.push(testsLabel(plugin))
  }
  return bits.join(" · ")
}

function repoUrl(plugin) {
  return plugin && plugin.repo ? "https://github.com/" + plugin.repo : ""
}

function marketplaceUrl(plugin) {
  var mk = plugin && plugin.marketplace
  return mk && mk.issue ? mk.issue.url : ""
}

// Only an archive made here can be undone here; a GitHub archive is undone on GitHub.
function canUnarchive(plugin) {
  return isArchived(plugin) && plugin.archived === "here"
}

function canArchive(plugin) {
  return !!plugin && !!plugin.mine && !isArchived(plugin)
}

// Fixing makes sense when there is something to fix and the plugin is yours.
function canFix(plugin) {
  return !!plugin && !!plugin.mine && !isArchived(plugin) && (plugin.health === "error" || plugin.health === "warn")
}

// How many plugins "Fix all" would hand the agent.
function fixableCount(plugins) {
  return (plugins || []).filter(canFix).length
}

if (typeof module !== "undefined") {
  module.exports = {
    stageLabel: stageLabel, stageStep: stageStep, track: track, healthGlyph: healthGlyph, rowGlyph: rowGlyph, stats: stats,
    unseen: unseen, isArchived: isArchived, isWaiting: isWaiting, sectionOf: sectionOf,
    groups: groups, flatRows: flatRows, hiddenCount: hiddenCount, caption: caption,
    summarize: summarize, heroStatus: heroStatus, badge: badge, freshness: freshness, workLine: workLine,
    relativeTime: relativeTime, ciLabel: ciLabel, testsLabel: testsLabel, facts: facts,
    repoUrl: repoUrl, marketplaceUrl: marketplaceUrl,
    canArchive: canArchive, canUnarchive: canUnarchive, canFix: canFix, fixableCount: fixableCount
  }
}
