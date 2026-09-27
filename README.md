# Plugin Lab

An Omarchy bar widget that tracks the Omarchy shell plugins you build, from
first commit to marketplace listing, and tells you when one needs you.

## What it shows

For every plugin in `~/.config/omarchy/plugins` and every plugin working copy
under `~/Work`:

- **Lifecycle stage**: Local → In dev → On GitHub → In review / Needs fixes →
  Listed / Update pending, read from git, GitHub and the
  [Omarchy plugin marketplace](https://github.com/omacom/omarchy-plugin-marketplace)
  (your submission and verification issues, and `registry.json`).
- **Health**: CI on GitHub, local test results, manifest validation,
  uncommitted or unpushed work, a bar running older code than your working copy.
- **Feedback**: stars, forks, watchers, open issues and PRs, comments from
  other people on your repo and marketplace threads, and mentions in
  `omacom/omarchy`. A comment counts as read once you open the plugin here or
  reply after it.

The bar icon shows a count of broken plugins plus unread comments.

## Actions

- **Fix with agent** opens your default agent (`omarchy agent prompt`) in the
  plugin's working copy with every flagged issue, the failing CI run and any
  marketplace review comments. The agent is told to commit locally and ask
  before pushing, tagging, commenting or submitting.
- **Fix all** (panel header) opens one agent session for every plugin that
  needs work, broken ones first, with a concrete fix for each issue (for
  example `omarchy plugin update <id> --yes` when your bar runs an older copy
  than your working copy). Same rules: commit locally, ask before anything
  leaves your machine, and end with a table of what was fixed and what is
  waiting for you.
- **Run tests** runs `test/run` or `tests/*test*.sh`; results are cached and
  marked stale when the tree changes.
- Links to GitHub, the CI run, the marketplace issue, the test log and the folder.

- **Archive** a plugin you no longer need: it moves to the Archived filter and
  stops raising issues. Nothing is uninstalled and nothing changes on GitHub;
  **Unarchive** brings it back. Repos archived on GitHub are detected and
  archived automatically. The list lives in `~/.config/omarchy-plugin-lab/archived.json`.

The list groups plugins into Needs you, Waiting and All good; archived and
other people's plugins fold into one line. Click a plugin for its detail page.

## Install

```bash
omarchy plugin add https://github.com/RohiRIK/omarchy-plugin-lab --enable
```

Needs `git`, `python3` and an authenticated `gh`. GitHub data is cached
(CI 5 min, feedback 15 min, marketplace 10 min / 1 h); right-click the bar
icon or press **Check GitHub** to fetch now.

Environment overrides: `PLUGIN_LAB_WORK_DIRS` (colon-separated, default
`~/Work`), `PLUGIN_LAB_OWNERS`, `PLUGIN_LAB_MARKETPLACE`, `PLUGIN_LAB_UPSTREAM`.

## Remove

```bash
omarchy plugin remove rohirik.plugin-lab
rm -rf ~/.cache/omarchy-plugin-lab ~/.config/omarchy-plugin-lab
```

## Tests

```bash
bash test/run
```
