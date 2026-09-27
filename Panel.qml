// rohirik.plugin-lab -- the lifecycle and health of the Omarchy shell plugins
// you build, from first commit to marketplace listing, plus what people are
// saying about them.
//
// Two pages, like Omarchy's own panels: a calm list grouped into NEEDS YOU,
// WAITING and ALL GOOD, and a detail page per plugin that holds everything
// else. All the looking happens in collector.py; this file renders its JSON
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

  // ---- data ---------------------------------------------------------------
  property var plugins: []
  property string loadError: ""
  property bool loading: false
  property string testingId: ""

  // ---- ui state -----------------------------------------------------------
  property bool showMore: false
  property int cursor: 0
  property string selectedId: ""

  readonly property var groups: Model.groups(root.plugins, root.showMore)
  readonly property var flat: Model.flatRows(root.groups)
  readonly property var summary: Model.summarize(root.plugins)
  readonly property int fixable: Model.fixableCount(root.plugins)
  readonly property var selected: {
    for (var i = 0; i < root.plugins.length; i++)
      if (root.plugins[i].id === root.selectedId) return root.plugins[i]
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

  function refresh(fetch) {
    if (statusProc.running)
      return
    root.loading = true
    statusProc.command = fetch ? ["python3", root.collector, "status", "--fetch"]
                               : ["python3", root.collector, "status"]
    statusProc.running = true
  }

  function openDetail(plugin) {
    if (!plugin)
      return
    root.selectedId = plugin.id
    if (Model.unseen(plugin) > 0)
      Quickshell.execDetached(["python3", root.collector, "seen", plugin.id])
  }

  function back() {
    if (root.selected && Model.unseen(root.selected) > 0)
      root.refresh(false)
    root.selectedId = ""
  }

  function moveCursor(dy) {
    if (root.selected || root.flat.length === 0)
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
    root.selectedId = ""
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
      root.selectedId = ""
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
          root.plugins = JSON.parse(text).plugins || []
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
    contentWidth: panel.fittedContentWidth(Style.space(420))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function (dx, dy) {
        if (root.selected && dx < 0) root.back()
        else root.moveCursor(dy)
      }
      onActivateRequested: if (!root.selected) root.openDetail(root.flat[root.cursor])
      onCloseRequested: root.selected ? root.back() : root.close()
      onTabRequested: function (direction) { root.switchPanel(direction) }
      onTextKey: function (t) {
        if (t === "r") root.refresh(true)
        else if (t === "f") root.selected ? (Model.canFix(root.selected) && root.fix(root.selected)) : root.fixAll()
        else if (t === "t" && root.selected) root.runTests(root.selected)
        else if (t === "m" && !root.selected) root.showMore = !root.showMore
      }

      Column {
        id: column
        anchors.fill: parent
        spacing: Style.space(14)

        // ---------- hero ----------
        Item {
          width: parent.width
          implicitHeight: Math.max(heroIcon.implicitHeight, heroLabels.implicitHeight, heroActions.implicitHeight)

          PanelActionButton {
            id: backButton
            visible: !!root.selected
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            iconText: "󰁍"
            tooltipText: "Back (Esc)"
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: root.back()
          }

          Text {
            id: heroIcon
            visible: !root.selected
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
            anchors.left: root.selected ? backButton.right : heroIcon.right
            anchors.leftMargin: Style.space(14)
            anchors.right: heroActions.left
            anchors.rightMargin: Style.space(8)
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(2)

            Text {
              width: parent.width
              textFormat: Text.PlainText
              text: root.selected ? root.selected.name : "Plugin Lab"
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
                  : root.selected ? Model.stageLabel(root.selected.stage).toUpperCase()
                  : root.plugins.length === 0 && root.loading ? "LOOKING…"
                  : Model.heroStatus(root.summary)
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
              visible: root.selected ? Model.canFix(root.selected) : root.fixable > 0
              iconText: "󱚣"
              tooltipText: root.selected ? "Fix with agent (f)" : "Fix all " + root.fixable + " with agent (f)"
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.selected ? root.fix(root.selected) : root.fixAll()
            }

            PanelActionButton {
              visible: !root.selected
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
          visible: !root.selected
          width: parent.width
          height: Math.min(contentHeight, Style.space(460))
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

        // ---------- detail page ----------
        Column {
          id: detail
          visible: !!root.selected
          width: parent.width
          spacing: Style.space(12)
          readonly property var p: root.selected || ({})

          // lifecycle track, your plugins only
          RowLayout {
            visible: !!detail.p.mine && !Model.isArchived(detail.p)
            width: parent.width
            spacing: 0

            Repeater {
              model: Model.track()
              ColumnLayout {
                required property string modelData
                required property int index
                readonly property bool reached: index < Model.stageStep(detail.p.stage)
                readonly property bool here: index === Model.stageStep(detail.p.stage) - 1
                Layout.fillWidth: true
                spacing: Style.space(4)

                Rectangle {
                  Layout.alignment: Qt.AlignHCenter
                  width: Style.space(here ? 10 : 7)
                  height: width
                  radius: width / 2
                  color: reached ? root.accent : "transparent"
                  border.width: 1
                  border.color: reached ? root.accent : root.dim
                }
                Text {
                  Layout.alignment: Qt.AlignHCenter
                  text: modelData
                  color: here ? root.foreground : root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  font.bold: here
                }
              }
            }
          }

          Text {
            width: parent.width
            textFormat: Text.PlainText
            text: Model.facts(detail.p)
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }

          Column {
            width: parent.width
            spacing: Style.space(6)

            Repeater {
              model: detail.p.issues || []
              RowLayout {
                required property var modelData
                width: parent.width
                spacing: Style.space(8)
                Text {
                  Layout.alignment: Qt.AlignTop
                  text: Model.healthGlyph(modelData.level)
                  color: root.healthColor(modelData.level)
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                }
                Text {
                  Layout.fillWidth: true
                  textFormat: Text.PlainText
                  text: modelData.text
                  color: modelData.level === "info" ? root.dim : root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  wrapMode: Text.WordWrap
                }
              }
            }

            Text {
              visible: (detail.p.issues || []).length === 0
              text: "Nothing to do."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
          }

          Column {
            visible: (detail.p.comments || []).length > 0
            width: parent.width
            spacing: Style.space(6)

            PanelSectionHeader {
              text: "COMMENTS"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Repeater {
              model: (detail.p.comments || []).slice(0, 4)
              Column {
                id: comment
                required property var modelData
                width: parent.width
                spacing: Style.space(1)
                Text {
                  width: parent.width
                  textFormat: Text.PlainText
                  text: (comment.modelData.new ? "● " : "") + comment.modelData.by + " · " + comment.modelData.where
                        + " · " + Model.relativeTime(comment.modelData.at)
                  color: comment.modelData.new ? root.accent : root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                }
                Text {
                  width: parent.width
                  textFormat: Text.PlainText
                  text: comment.modelData.text
                  color: commentMouse.containsMouse ? root.accent : root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
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
            }
          }

          PanelSeparator { foreground: root.foreground }

          // primary actions in words, the rest as quiet icons
          RowLayout {
            width: parent.width
            spacing: Style.space(6)

            Button {
              visible: Model.canFix(detail.p)
              text: "Fix with agent"
              bordered: true
              fontSize: Style.font.caption
              onClicked: root.fix(detail.p)
            }
            Button {
              visible: !!detail.p.mine && !!detail.p.hasTests && !Model.isArchived(detail.p)
              text: root.testingId === detail.p.id ? "Testing…" : "Run tests"
              bordered: true
              fontSize: Style.font.caption
              enabled: root.testingId === ""
              onClicked: root.runTests(detail.p)
            }
            Button {
              visible: Model.canUnarchive(detail.p)
              text: "Unarchive"
              bordered: true
              fontSize: Style.font.caption
              onClicked: root.setArchived(detail.p, false)
            }

            Item { Layout.fillWidth: true }

            PanelActionButton {
              visible: Model.repoUrl(detail.p) !== ""
              iconText: "󰊤"
              tooltipText: "GitHub"
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.openUrl(Model.repoUrl(detail.p))
            }
            PanelActionButton {
              visible: !!detail.p.ci && !!detail.p.ci.url
              iconText: "󰙨"
              tooltipText: "Latest CI run"
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.openUrl(detail.p.ci.url)
            }
            PanelActionButton {
              visible: Model.marketplaceUrl(detail.p) !== ""
              iconText: "󰏓"
              tooltipText: "Marketplace issue"
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.openUrl(Model.marketplaceUrl(detail.p))
            }
            PanelActionButton {
              visible: !!detail.p.tests
              iconText: "󰈙"
              tooltipText: "Test log"
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.openPath(detail.p.tests.log)
            }
            PanelActionButton {
              iconText: "󰉋"
              tooltipText: "Open " + (detail.p.path || "")
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.openPath(detail.p.path)
            }
            PanelActionButton {
              visible: Model.canArchive(detail.p)
              iconText: "󰀼"
              tooltipText: "Archive: stop tracking it here. Nothing is uninstalled or changed on GitHub."
              foreground: root.foreground
              hoverColor: root.urgent
              fontFamily: root.fontFamily
              onClicked: root.setArchived(detail.p, true)
            }
          }
        }

        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: root.selected ? "Esc back · f fix · t run tests"
                              : "↑↓ move · Enter open · f fix all · r check GitHub · m more"
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

    hasCursor: root.cursor === row.flatIndex
    foreground: root.foreground
    implicitHeight: rowContent.implicitHeight + Style.spacing.rowPaddingX

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onContainsMouseChanged: if (containsMouse) root.cursor = row.flatIndex
      onClicked: root.openDetail(row.p)
    }

    Item {
      id: rowContent
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      implicitHeight: Math.max(glyph.implicitHeight, info.implicitHeight)

      Text {
        id: glyph
        textFormat: Text.PlainText
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        text: Model.healthGlyph(row.p.health)
        color: root.healthColor(row.p.health)
        font.family: root.fontFamily
        font.pixelSize: Style.font.heading
      }

      Column {
        id: info
        anchors.left: glyph.right
        anchors.leftMargin: Style.space(10)
        anchors.right: trailing.left
        anchors.rightMargin: Style.space(8)
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(1)

        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: row.p.name || ""
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
        }
        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: Model.caption(row.p)
          color: row.p.health === "error" ? root.urgent : root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }

      Text {
        id: trailing
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        text: Model.unseen(row.p) > 0 ? "●" : (row.hasCursor ? "󰅂" : "")
        color: Model.unseen(row.p) > 0 ? root.accent : root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
      }
    }
  }
}
