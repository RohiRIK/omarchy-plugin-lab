// rohirik.plugin-lab -- the lifecycle and health of the Omarchy shell plugins
// you build, from first commit to marketplace listing, plus what people are
// saying about them.
//
// Laid out like Omarchy's own panels: a hero, then plugins grouped into
// NEEDS YOU, WAITING and ALL GOOD. Each row shows its lifecycle, stage, CI,
// tests and GitHub counters; clicking it expands the issues and actions. All the looking happens in collector.py; this file renders its JSON
// and fires its actions. Nothing here pushes, tags or edits a plugin: fixes go
// to your default agent, which is told to ask before anything leaves the machine.

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: root

  moduleName: "rohirik.plugin-lab"
  ipcTarget: "rohirik.plugin-lab"

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  readonly property string collector: Qt.resolvedUrl("collector.py").toString().replace("file://", "")
  readonly property int refreshIntervalSec: Math.max(60, Number(setting("refreshIntervalSec", 300)))
  readonly property int fetchIntervalMin: Math.max(5, Number(setting("fetchIntervalMin", 15)))

  // ---- data ---------------------------------------------------------------
  property var plugins: []
  property int checkedAt: 0
  property var staleSources: []
  property string loadError: ""
  property bool loading: false
  property string testingId: ""

  // ---- ui state -----------------------------------------------------------
  property bool showMore: false
  property int cursor: 0
  property string expandedId: ""

  readonly property var groups: Model.groups(root.plugins, root.showMore)
  readonly property var flat: Model.flatRows(root.groups)
  readonly property var summary: Model.summarize(root.plugins)
  readonly property int fixable: Model.fixableCount(root.plugins)
  readonly property var expanded: {
    for (var i = 0; i < root.plugins.length; i++)
      if (root.plugins[i].id === root.expandedId) return root.plugins[i]
    return null
  }

  // ---- theme --------------------------------------------------------------
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(root.foreground, 1.5)
  readonly property color accent: Color.accent
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  // Only broken is loud; everything else stays in the foreground or dims.
  function healthColor(health) {
    if (health === "error") return root.urgent
    if (health === "warn") return root.foreground
    return root.dim
  }

  // ---- behaviour ----------------------------------------------------------

  // mode: "" cached, "git" background git fetch, "all" refresh everything now
  function refresh(fetch, mode) {
    if (statusProc.running)
      return
    root.loading = true
    var flag = fetch ? "--fetch" : (mode === "git" ? "--fetch-git" : "")
    statusProc.command = flag ? ["python3", root.collector, "status", flag] : ["python3", root.collector, "status"]
    statusProc.running = true
  }

  function toggleExpanded(plugin) {
    if (!plugin)
      return
    if (root.expandedId === plugin.id) {
      root.collapse()
      return
    }
    root.collapse()
    root.expandedId = plugin.id
    if (Model.unseen(plugin) > 0)
      Quickshell.execDetached(["python3", root.collector, "seen", plugin.id])
  }

  function collapse() {
    if (root.expanded && Model.unseen(root.expanded) > 0)
      root.refresh(false)
    root.expandedId = ""
  }

  function moveCursor(dy) {
    if (root.flat.length === 0)
      return
    root.cursor = Math.max(0, Math.min(root.flat.length - 1, root.cursor + dy))
  }

  function runTests(plugin) {
    if (testProc.running || !plugin.hasTests)
      return
    root.testingId = plugin.id
    testProc.command = ["python3", root.collector, "test", plugin.id]
    testProc.running = true
  }

  function fix(plugin) {
    Quickshell.execDetached(["python3", root.collector, "fix", plugin.id])
    root.close()
  }

  function fixAll() {
    if (root.fixable === 0)
      return
    Quickshell.execDetached(["python3", root.collector, "fix-all"])
    root.close()
  }

  function setArchived(plugin, archived) {
    root.expandedId = ""
    archiveProc.command = ["python3", root.collector, archived ? "archive" : "unarchive", plugin.id]
    archiveProc.running = true
  }

  function openUrl(url) {
    if (url)
      Quickshell.execDetached(["xdg-open", url])
  }

  function openPath(path) {
    if (!path)
      return
    Quickshell.execDetached(["omarchy-launch-editor", path])
    root.close()
  }

  onOpenedChanged: {
    if (root.opened) {
      root.refresh(false)
    } else {
      root.expandedId = ""
      root.showMore = false
      root.cursor = 0
    }
  }

  onFlatChanged: if (root.cursor >= root.flat.length) root.cursor = Math.max(0, root.flat.length - 1)

  Component.onCompleted: root.refresh(false)

  // ---- processes ----------------------------------------------------------

  Process {
    id: statusProc
    running: false
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.loading = false
        try {
          var data = JSON.parse(text)
          root.plugins = data.plugins || []
          root.checkedAt = Number(data.checkedAt || 0)
          root.staleSources = data.stale || []
          root.loadError = ""
        } catch (e) {
          root.loadError = "Could not read plugin status"
        }
      }
    }
  }

  Process {
    id: testProc
    running: false
    onExited: {
      root.testingId = ""
      root.refresh(false)
    }
  }

  Process {
    id: archiveProc
    running: false
    onExited: root.refresh(false)
  }

  Timer {
    interval: root.refreshIntervalSec * 1000
    running: true
    repeat: true
    onTriggered: root.refresh(false)
  }

  // Remote refs go stale without a fetch, so fetch in the background too.
  Timer {
    interval: root.fetchIntervalMin * 60 * 1000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh(false, "git")
  }

  // ---- bar button ---------------------------------------------------------

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: Model.badge(root.summary) ? "󰏗 " + Model.badge(root.summary) : "󰏗"
    active: root.opened || Model.badge(root.summary) !== ""
    onPressed: function (buttonCode) {
      if (buttonCode === Qt.RightButton)
        root.refresh(true)
      else
        root.toggle()
    }
  }

  // ---- panel --------------------------------------------------------------

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(480))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function (dx, dy) { root.moveCursor(dy) }
      onActivateRequested: root.toggleExpanded(root.flat[root.cursor])
      onCloseRequested: root.expanded ? root.collapse() : root.close()
      onTabRequested: function (direction) { root.switchPanel(direction) }
      onTextKey: function (t) {
        if (t === "r") root.refresh(true)
        else if (t === "f") root.expanded ? (Model.canFix(root.expanded) && root.fix(root.expanded)) : root.fixAll()
        else if (t === "t" && root.expanded) root.runTests(root.expanded)
        else if (t === "m") root.showMore = !root.showMore
      }

      Column {
        id: column
        anchors.fill: parent
        spacing: Style.space(14)

        // ---------- hero ----------
        Item {
          width: parent.width
          implicitHeight: Math.max(heroIcon.implicitHeight, heroLabels.implicitHeight, heroActions.implicitHeight)

          Text {
            id: heroIcon
            textFormat: Text.PlainText
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: "󰏗"
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.display
          }

          Column {
            id: heroLabels
            anchors.left: heroIcon.right
            anchors.leftMargin: Style.space(14)
            anchors.right: heroActions.left
            anchors.rightMargin: Style.space(8)
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(2)

            Text {
              width: parent.width
              textFormat: Text.PlainText
              text: "Plugin Lab"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
              elide: Text.ElideRight
            }

            Text {
              width: parent.width
              textFormat: Text.PlainText
              text: root.loadError !== "" ? root.loadError.toUpperCase()
                  : root.plugins.length === 0 && root.loading ? "LOOKING…"
                  : Model.heroStatus(root.summary)
                    + (Model.freshness(root.checkedAt, root.staleSources) ? " · " + Model.freshness(root.checkedAt, root.staleSources) : "")
              color: root.loadError !== "" ? root.urgent : Qt.darker(root.foreground, 1.4)
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
              elide: Text.ElideRight
            }
          }

          Row {
            id: heroActions
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(4)

            PanelActionButton {
              visible: root.fixable > 0
              iconText: "󱚣"
              tooltipText: "Fix all " + root.fixable + " with agent (f)"
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.fixAll()
            }

            PanelActionButton {
              enabled: !root.loading
              iconText: "󰑐"
              tooltipText: root.loading ? "Checking…" : "Check GitHub now (r)"
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.refresh(true)
            }
          }
        }

        PanelSeparator { foreground: root.foreground }

        // ---------- list page ----------
        Flickable {
          id: listView
          width: parent.width
          height: Math.min(contentHeight, Style.space(560))
          contentHeight: listColumn.implicitHeight
          clip: true
          boundsBehavior: Flickable.StopAtBounds
          interactive: contentHeight > height
          ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

          Column {
            id: listColumn
            width: listView.width
            spacing: Style.space(14)

            Repeater {
              model: root.groups

              Column {
                id: group
                required property var modelData
                width: listColumn.width
                spacing: Style.space(6)

                PanelSectionHeader {
                  text: group.modelData.label
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                }

                Repeater {
                  model: group.modelData.rows
                  PluginRow {
                    required property var modelData
                    width: group.width
                    p: modelData
                    flatIndex: root.flat.indexOf(modelData)
                  }
                }
              }
            }

            Text {
              visible: root.plugins.length > 0 && root.groups.length === 0
              width: parent.width
              text: "Nothing to show"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }

            // archived and other people's plugins fold into one quiet line
            Text {
              visible: Model.hiddenCount(root.plugins) > 0
              width: parent.width
              textFormat: Text.PlainText
              text: root.showMore ? "Hide archived and others"
                                  : "Show archived and others (" + Model.hiddenCount(root.plugins) + ")"
              color: moreMouse.containsMouse ? root.foreground : root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              horizontalAlignment: Text.AlignHCenter
              MouseArea {
                id: moreMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.showMore = !root.showMore
              }
            }
          }
        }

        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: root.expanded ? "Esc collapse · f fix this · t run tests · r check GitHub"
                              : "↑↓ move · Enter expand · f fix all · r check GitHub · m more"
          color: Qt.darker(root.foreground, 1.8)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          horizontalAlignment: Text.AlignHCenter
        }
      }
    }
  }

  // ---- one plugin in the list ---------------------------------------------

  component PluginRow: CursorSurface {
    id: row
    property var p: ({})
    property int flatIndex: -1
    readonly property bool open: root.expandedId === (row.p.id || "")
    readonly property bool mine: !!row.p.mine && !Model.isArchived(row.p)

    hasCursor: root.cursor === row.flatIndex
    current: row.open
    foreground: root.foreground
    implicitHeight: rowContent.implicitHeight + Style.spacing.rowPaddingX

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onContainsMouseChanged: if (containsMouse) root.cursor = row.flatIndex
      onClicked: root.toggleExpanded(row.p)
    }

    ColumnLayout {
      id: rowContent
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      spacing: Style.space(2)

      // name · lifecycle pips · stage
      RowLayout {
        Layout.fillWidth: true
        spacing: Style.space(10)

        Text {
          Layout.preferredWidth: Style.space(20)
          textFormat: Text.PlainText
          text: Model.rowGlyph(row.p)
          color: Model.sectionOf(row.p) === "needs" ? root.healthColor(row.p.health) : root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.heading
        }

        Text {
          Layout.fillWidth: true
          textFormat: Text.PlainText
          text: row.p.name || ""
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
        }

        Text {
          visible: Model.unseen(row.p) > 0
          text: "● " + Model.unseen(row.p)
          color: root.accent
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }

        Row {
          visible: row.mine
          spacing: Style.space(3)
          Repeater {
            model: 5
            Rectangle {
              required property int index
              readonly property bool reached: index < Model.stageStep(row.p.stage)
              width: Style.space(7)
              height: width
              radius: width / 2
              color: reached ? root.accent : "transparent"
              border.width: 1
              border.color: reached ? root.accent : root.dim
            }
          }
        }

        Text {
          Layout.preferredWidth: Style.space(92)
          horizontalAlignment: Text.AlignRight
          textFormat: Text.PlainText
          text: Model.stageLabel(row.p.stage)
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideLeft
        }
      }

      // why it is here · GitHub counters
      RowLayout {
        Layout.fillWidth: true
        Layout.leftMargin: Style.space(30)
        spacing: Style.space(10)

        Text {
          Layout.fillWidth: true
          textFormat: Text.PlainText
          text: Model.caption(row.p)
          color: row.p.health === "error" && Model.sectionOf(row.p) === "needs" ? root.urgent : root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }

        Text {
          visible: text !== ""
          textFormat: Text.PlainText
          text: Model.stats(row.p)
          color: (row.p.stars || row.p.forks) ? root.foreground : root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }
      }

      // ---- expanded: lifecycle names, issues, comments, actions -------------
      ColumnLayout {
        visible: row.open
        Layout.fillWidth: true
        Layout.leftMargin: Style.space(30)
        Layout.topMargin: Style.space(8)
        Layout.bottomMargin: Style.space(4)
        spacing: Style.space(6)

        Text {
          visible: row.mine
          Layout.fillWidth: true
          textFormat: Text.PlainText
          text: Model.track().map(function (name, i) {
            return i === Model.stageStep(row.p.stage) - 1 ? name.toUpperCase() : name
          }).join("  ›  ")
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }

        Repeater {
          model: row.open ? (row.p.issues || []) : []
          RowLayout {
            required property var modelData
            Layout.fillWidth: true
            spacing: Style.space(8)
            Text {
              Layout.alignment: Qt.AlignTop
              text: Model.healthGlyph(modelData.level)
              color: root.healthColor(modelData.level)
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
            ColumnLayout {
              Layout.fillWidth: true
              spacing: Style.space(1)
              Text {
                Layout.fillWidth: true
                textFormat: Text.PlainText
                text: modelData.text
                color: modelData.level === "info" ? root.dim : root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                wrapMode: Text.WordWrap
              }
              // what to do about it, and what happens after
              Text {
                visible: !!modelData.action
                Layout.fillWidth: true
                textFormat: Text.PlainText
                text: modelData.action || ""
                color: modelData.level === "info" ? root.dim : root.accent
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                wrapMode: Text.WordWrap
              }
              // what the claim is based on, so it can be checked in place
              Text {
                visible: !!modelData.evidence
                Layout.fillWidth: true
                textFormat: Text.PlainText
                text: modelData.evidence || ""
                color: Qt.darker(root.foreground, 1.9)
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                wrapMode: Text.WordWrap
              }
            }
          }
        }

        Text {
          visible: (row.p.issues || []).length === 0
          text: "Nothing to do."
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
        }

        PanelSectionHeader {
          visible: (row.p.openWork || []).length > 0
          Layout.topMargin: Style.space(4)
          text: "OPEN WORK"
          foreground: root.foreground
          fontFamily: root.fontFamily
        }

        Repeater {
          model: row.open ? (row.p.openWork || []) : []
          Text {
            id: workItem
            required property var modelData
            Layout.fillWidth: true
            textFormat: Text.PlainText
            text: Model.workLine(modelData)
            color: workMouse.containsMouse && modelData.pr ? root.accent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            elide: Text.ElideRight
            MouseArea {
              id: workMouse
              anchors.fill: parent
              hoverEnabled: true
              enabled: !!workItem.modelData.pr
              cursorShape: Qt.PointingHandCursor
              onClicked: root.openUrl(workItem.modelData.pr.url)
            }
          }
        }

        PanelSectionHeader {
          visible: (row.p.comments || []).length > 0
          Layout.topMargin: Style.space(4)
          text: "COMMENTS"
          foreground: root.foreground
          fontFamily: root.fontFamily
        }

        Repeater {
          model: row.open ? (row.p.comments || []).slice(0, 4) : []
          Text {
            id: comment
            required property var modelData
            Layout.fillWidth: true
            textFormat: Text.PlainText
            text: (modelData.new ? "● " : "") + modelData.by + " · " + modelData.where + " · "
                  + Model.relativeTime(modelData.at) + "  " + modelData.text
            color: commentMouse.containsMouse ? root.accent : (modelData.new ? root.foreground : root.dim)
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            elide: Text.ElideRight
            MouseArea {
              id: commentMouse
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onClicked: root.openUrl(comment.modelData.url)
            }
          }
        }

        // primary actions in words, the rest as quiet icons
        RowLayout {
          Layout.fillWidth: true
          Layout.topMargin: Style.space(4)
          spacing: Style.space(6)

          Button {
            visible: Model.canFix(row.p)
            text: "Fix with agent"
            bordered: true
            fontSize: Style.font.caption
            onClicked: root.fix(row.p)
          }
          Button {
            visible: row.mine && !!row.p.hasTests
            text: root.testingId === row.p.id ? "Testing…" : "Run tests"
            bordered: true
            fontSize: Style.font.caption
            enabled: root.testingId === ""
            onClicked: root.runTests(row.p)
          }
          Button {
            visible: Model.canUnarchive(row.p)
            text: "Unarchive"
            bordered: true
            fontSize: Style.font.caption
            onClicked: root.setArchived(row.p, false)
          }

          Item { Layout.fillWidth: true }

          PanelActionButton {
            visible: Model.repoUrl(row.p) !== ""
            iconText: "󰊤"
            tooltipText: "GitHub"
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: root.openUrl(Model.repoUrl(row.p))
          }
          PanelActionButton {
            visible: !!row.p.ci && !!row.p.ci.url
            iconText: "󰙨"
            tooltipText: "Latest CI run"
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: root.openUrl(row.p.ci.url)
          }
          PanelActionButton {
            visible: Model.marketplaceUrl(row.p) !== ""
            iconText: "󰏓"
            tooltipText: "Marketplace issue"
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: root.openUrl(Model.marketplaceUrl(row.p))
          }
          PanelActionButton {
            visible: !!row.p.tests
            iconText: "󰈙"
            tooltipText: "Test log"
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: root.openPath(row.p.tests.log)
          }
          PanelActionButton {
            iconText: "󰉋"
            tooltipText: "Open " + (row.p.path || "")
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: root.openPath(row.p.path)
          }
          PanelActionButton {
            visible: Model.canArchive(row.p)
            iconText: "󰀼"
            tooltipText: "Archive: stop tracking it here. Nothing is uninstalled or changed on GitHub."
            foreground: root.foreground
            hoverColor: root.urgent
            fontFamily: root.fontFamily
            onClicked: root.setArchived(row.p, true)
          }
        }
      }
    }
  }
}
