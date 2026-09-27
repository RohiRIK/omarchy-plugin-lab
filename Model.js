// Pure helpers for Panel.qml. No QML imports, so test/model.test.js can load
// this file in node.

// The pip track: needs-fixes sits with submitted, update with listed.
var STAGE_STEP = { local: 1, dev: 2, pushed: 3, submitted: 4, "needs-fixes": 4, listed: 5, update: 5, installed: 0 }

var STAGE_LABELS = {
  local: "Local",
  dev: "In dev",
  pushed: "On GitHub",
  submitted: "In review",
  "needs-fixes": "Needs fixes",
  listed: "Listed",
  update: "Update pending",
  installed: "Installed"
}

var HEALTH_RANK = { error: 0, warn: 1, info: 2, ok: 3 }

var SORTS = [
  { key: "health", label: "Health" },
  { key: "stage", label: "Stage" },
  { key: "activity", label: "Activity" },
  { key: "stars", label: "Stars" },
  { key: "name", label: "Name" }
]

var FILTERS = [
  { key: "all", label: "All" },
  { key: "attention", label: "Needs attention" },
  { key: "mine", label: "Mine" },
  { key: "review", label: "In review" },
  { key: "listed", label: "Listed" },
  { key: "others", label: "Others" }
]

function stageLabel(stage) {
  return STAGE_LABELS[stage] || String(stage || "?")
}

function stageStep(stage) {
  return STAGE_STEP[stage] || 0
}

function stageCount() {
  return 5
}

function healthGlyph(health) {
  if (health === "error") return "󰅚"
  if (health === "warn") return "󰀪"
  if (health === "info") return "󰋽"
  return "󰄬"
}

function unseen(plugin) {
  return plugin ? Number(plugin.unseen || 0) : 0
}

function byName(a, b) {
  return String(a.name).localeCompare(String(b.name))
}

var COMPARATORS = {
  health: function (a, b) { return (HEALTH_RANK[a.health] || 0) - (HEALTH_RANK[b.health] || 0) },
  stage: function (a, b) { return stageStep(b.stage) - stageStep(a.stage) },
  activity: function (a, b) { return (b.activityAt || 0) - (a.activityAt || 0) },
  stars: function (a, b) { return ((b.stars || 0) + (b.forks || 0)) - ((a.stars || 0) + (a.forks || 0)) },
  name: function () { return 0 }
}

// Yours before others', then by the chosen key, then by name. Never mutates.
function sortPlugins(plugins, key) {
  var cmp = COMPARATORS[key] || COMPARATORS.health
  return (plugins || []).slice().sort(function (a, b) {
    if (!!a.mine !== !!b.mine) return a.mine ? -1 : 1
    return cmp(a, b) || byName(a, b)
  })
}

function matchesFilter(p, key) {
  if (key === "attention") return p.health === "error" || p.health === "warn" || unseen(p) > 0
  if (key === "mine") return !!p.mine
  if (key === "review") return p.stage === "submitted" || p.stage === "needs-fixes"
  if (key === "listed") return p.stage === "listed" || p.stage === "update"
  if (key === "others") return !p.mine
  return true
}

function matchesText(p, text) {
  var q = String(text || "").trim().toLowerCase()
  if (!q) return true
  return [p.name, p.id, p.author, p.repo, stageLabel(p.stage)].some(function (s) {
    return String(s || "").toLowerCase().indexOf(q) >= 0
  })
}

function visibleRows(plugins, filterKey, text, sortKey) {
  return sortPlugins((plugins || []).filter(function (p) {
    return matchesFilter(p, filterKey) && matchesText(p, text)
  }), sortKey)
}

function countFor(plugins, key) {
  return (plugins || []).filter(function (p) { return matchesFilter(p, key) }).length
}

function nextSort(key) {
  for (var i = 0; i < SORTS.length; i++)
    if (SORTS[i].key === key) return SORTS[(i + 1) % SORTS.length].key
  return SORTS[0].key
}

function sortLabel(key) {
  for (var i = 0; i < SORTS.length; i++)
    if (SORTS[i].key === key) return SORTS[i].label
  return SORTS[0].label
}

function summarize(plugins) {
  var s = { mine: 0, installed: 0, errors: 0, warnings: 0, unseen: 0, listed: 0, stars: 0, forks: 0 }
  ;(plugins || []).forEach(function (p) {
    if (p.mine) s.mine++
    else s.installed++
    if (p.health === "error") s.errors++
    else if (p.health === "warn") s.warnings++
    if (p.stage === "listed" || p.stage === "update") s.listed++
    s.unseen += unseen(p)
    s.stars += Number(p.stars || 0)
    s.forks += Number(p.forks || 0)
  })
  return s
}

function summaryLine(s) {
  var parts = [s.mine + " yours", s.listed + " listed", "★ " + s.stars, "⑂ " + s.forks]
  if (s.errors) parts.push(s.errors + " broken")
  if (s.unseen) parts.push(s.unseen + " new comment" + (s.unseen === 1 ? "" : "s"))
  return parts.join(" · ")
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

function subtitle(plugin) {
  var bits = []
  if (plugin.version) bits.push("v" + plugin.version)
  if (plugin.mine) {
    var ci = ciLabel(plugin.ci)
    if (ci) bits.push(ci)
    bits.push(testsLabel(plugin))
  } else if (plugin.author) {
    bits.push("by " + plugin.author)
  }
  return bits.join(" · ")
}

// "★ 3  ⑂ 1  👁 2" for a plugin on GitHub, empty otherwise.
function statsLine(plugin) {
  if (!plugin || !plugin.repo || !plugin.mine) return ""
  return "★ " + (plugin.stars || 0) + "  ⑂ " + (plugin.forks || 0) + "  󰈈 " + (plugin.watchers || 0)
}

function repoUrl(plugin) {
  return plugin && plugin.repo ? "https://github.com/" + plugin.repo : ""
}

function marketplaceUrl(plugin) {
  var mk = plugin && plugin.marketplace
  return mk && mk.issue ? mk.issue.url : ""
}

// Fixing makes sense when there is something to fix and the plugin is yours.
function canFix(plugin) {
  return !!plugin && !!plugin.mine && (plugin.health === "error" || plugin.health === "warn")
}

if (typeof module !== "undefined") {
  module.exports = {
    SORTS: SORTS, FILTERS: FILTERS,
    stageLabel: stageLabel, stageStep: stageStep, stageCount: stageCount,
    healthGlyph: healthGlyph, unseen: unseen, sortPlugins: sortPlugins,
    matchesFilter: matchesFilter, matchesText: matchesText, visibleRows: visibleRows,
    countFor: countFor, nextSort: nextSort, sortLabel: sortLabel,
    summarize: summarize, summaryLine: summaryLine, badge: badge,
    relativeTime: relativeTime, ciLabel: ciLabel, testsLabel: testsLabel,
    subtitle: subtitle, statsLine: statsLine, repoUrl: repoUrl,
    marketplaceUrl: marketplaceUrl, canFix: canFix
  }
}
