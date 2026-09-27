// rohirik.plugin-lab -- the lifecycle and health of every shell plugin in
// ~/.config/omarchy/plugins, from first commit to tagged release, plus what
// people are saying about them on GitHub and upstream Omarchy.
//
// All the looking happens in collector.py; this file only renders its JSON
// and fires its actions. Nothing here pushes, tags or edits a plugin: fixes go
// to your default agent, which is told to ask before publishing anything.

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

  // ---- settings -----------------------------------------------------------
  readonly property int refreshIntervalSec: Math.max(60, Number(setting("refreshIntervalSec", 300)))
  readonly property bool showInstalled: setting("showInstalled", true) !== false

  // ---- data ---------------------------------------------------------------
  property var plugins: []
  property int generatedAt: 0
  property string loadError: ""
  property bool loading: false
  property string testingId: ""
  property string expandedId: ""
  property string sortKey: String(setting("sortBy", "health"))
  property string filterKey: String(setting("filter", "all"))
  property string searchText: ""

  readonly property var shown: root.plugins.filter(function (p) { return p.mine || root.showInstalled })
  readonly property var rows: Model.visibleRows(root.shown, root.filterKey, root.searchText, root.sortKey)
  readonly property var summary: Model.summarize(root.plugins)

  // ---- theme --------------------------------------------------------------
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(root.foreground, 1.55)
  readonly property color accent: Color.accent
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color selectedBackground: Color.menu.selectedBackground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  function healthColor(health) {
    if (health === "error") return root.urgent
    if (health === "warn") return root.accent
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

  function toggleExpanded(plugin) {
    root.expandedId = root.expandedId === plugin.id ? "" : plugin.id
    if (root.expandedId && Model.unseen(plugin) > 0)
      Quickshell.execDetached(["python3", root.collector, "seen", plugin.id])
  }

  function openUrl(url) {
    if (url)
      Quickshell.execDetached(["xdg-open", url])
  }

  function openFolder(plugin) {
    Quickshell.execDetached(["omarchy-launch-editor", plugin.path])
    root.close()
  }

  function openLog(plugin) {
    if (plugin.tests && plugin.tests.log)
      Quickshell.execDetached(["omarchy-launch-editor", plugin.tests.log])
    root.close()
  }

  onOpenedChanged: {
    if (root.opened)
      root.refresh(false)
    else if (root.plugins.some(function (p) { return p.id === root.expandedId && Model.unseen(p) > 0 }))
      root.refresh(false)
  }

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
          root.generatedAt = Number(data.generatedAt || 0)
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
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.testingId = ""
        root.refresh(false)
      }
    }
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
    focusTarget: searchField
    contentWidth: panel.fittedContentWidth(Style.space(520))
    contentHeight: panel.fittedContentHeight(layout.implicitHeight, Style.space(680))

    PanelKeyCatcher {
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function (direction) { root.switchPanel(direction) }

      ColumnLayout {
        id: layout
        anchors.fill: parent
        spacing: Style.spacing.sm

        RowLayout {
          Layout.fillWidth: true
          spacing: Style.spacing.sm

          ColumnLayout {
            Layout.fillWidth: true
            spacing: 0
            Text {
              text: "Plugin Lab"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              font.bold: true
            }
            Text {
              Layout.fillWidth: true
              textFormat: Text.PlainText
              text: root.loadError !== "" ? root.loadError
                  : root.plugins.length === 0 && root.loading ? "Looking at your plugins…"
                  : Model.summaryLine(root.summary)
              color: root.loadError !== "" ? root.urgent : root.dim
              elide: Text.ElideRight
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          Button {
            text: root.loading ? "Checking…" : "Check GitHub"
            tooltipText: "Fetch every repo and refresh (also: right-click the bar icon)"
            bordered: true
            fontSize: Style.font.caption
            enabled: !root.loading
            onClicked: root.refresh(true)
          }
        }

        RowLayout {
          Layout.fillWidth: true
          spacing: Style.spacing.sm

          TextField {
            id: searchField
            Layout.fillWidth: true
            foreground: root.foreground
            placeholderText: "Filter by name, id, repo or stage"
            text: root.searchText
            onTextChanged: if (text !== root.searchText) root.searchText = text
            Keys.onEscapePressed: function (event) {
              if (root.searchText) root.searchText = ""
              else root.close()
              event.accepted = true
            }
          }

          Button {
            text: "󰒺  " + Model.sortLabel(root.sortKey)
            tooltipText: "Sort by (click to cycle)"
            bordered: true
            fontSize: Style.font.caption
            onClicked: root.sortKey = Model.nextSort(root.sortKey)
          }
        }

        Flow {
          Layout.fillWidth: true
          spacing: Style.spacing.xs

          Repeater {
            model: Model.FILTERS
            Button {
              required property var modelData
              readonly property int count: Model.countFor(root.shown, modelData.key)
              visible: modelData.key === "all" || count > 0 || root.filterKey === modelData.key
              text: modelData.label + (modelData.key === "all" ? "" : " " + count)
              selected: root.filterKey === modelData.key
              bordered: true
              fontSize: Style.font.caption
              onClicked: root.filterKey = modelData.key
            }
          }
        }

        PanelSeparator { Layout.fillWidth: true }

        Text {
          Layout.fillWidth: true
          visible: root.rows.length === 0 && !root.loading
          text: root.plugins.length === 0 ? "No plugins found" : "Nothing matches this filter"
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          horizontalAlignment: Text.AlignHCenter
        }

        ListView {
          id: list
          Layout.fillWidth: true
          Layout.fillHeight: true
          Layout.preferredHeight: contentHeight
          model: root.rows
          clip: true
          spacing: Style.spacing.xxs
          ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

          delegate: Rectangle {
            id: row
            required property var modelData
            required property int index
            readonly property var p: row.modelData
            readonly property bool expanded: root.expandedId === row.p.id
            readonly property bool firstOfGroup: row.index === 0 || !!root.rows[row.index - 1].mine !== !!row.p.mine

            width: list.width
            height: content.implicitHeight + Style.spacing.sm * 2
            radius: Style.cornerRadius
            color: row.expanded || hover.containsMouse ? root.selectedBackground : "transparent"

            MouseArea {
              id: hover
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onClicked: root.toggleExpanded(row.p)
            }

            ColumnLayout {
              id: content
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.leftMargin: Style.spacing.rowPaddingX
              anchors.rightMargin: Style.spacing.rowPaddingX
              spacing: Style.spacing.xxs

              PanelSectionHeader {
                visible: row.firstOfGroup
                text: row.p.mine ? "YOUR PLUGINS" : "INSTALLED FROM OTHERS"
              }

              RowLayout {
                Layout.fillWidth: true
                spacing: Style.spacing.sm

                Text {
                  text: Model.healthGlyph(row.p.health)
                  color: root.healthColor(row.p.health)
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.iconSmall
                }

                Text {
                  Layout.fillWidth: true
                  text: row.p.name
                  elide: Text.ElideRight
                  textFormat: Text.PlainText
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                }

                Text {
                  visible: Model.unseen(row.p) > 0
                  text: "󰍡 " + Model.unseen(row.p)
                  color: root.accent
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                }

                // lifecycle: one pip per stage, filled up to where the plugin is
                Row {
                  visible: row.p.mine
                  spacing: Style.space(3)
                  Repeater {
                    model: Model.stageCount()
                    Rectangle {
                      required property int index
                      width: Style.space(7)
                      height: Style.space(7)
                      radius: width / 2
                      color: index < Model.stageStep(row.p.stage) ? root.accent : "transparent"
                      border.width: 1
                      border.color: index < Model.stageStep(row.p.stage) ? root.accent : root.dim
                    }
                  }
                }

                Text {
                  text: Model.stageLabel(row.p.stage)
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  Layout.preferredWidth: Style.space(96)
                  horizontalAlignment: Text.AlignRight
                  elide: Text.ElideLeft
                }
              }

              RowLayout {
                Layout.fillWidth: true
                Layout.leftMargin: Style.space(22)
                spacing: Style.spacing.sm

                Text {
                  Layout.fillWidth: true
                  text: Model.subtitle(row.p)
                  elide: Text.ElideRight
                  textFormat: Text.PlainText
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }

                Text {
                  visible: text !== ""
                  text: Model.statsLine(row.p)
                  textFormat: Text.PlainText
                  color: (row.p.stars || row.p.forks) ? root.foreground : root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }

              // ---- details -----------------------------------------------
              ColumnLayout {
                visible: row.expanded
                Layout.fillWidth: true
                Layout.leftMargin: Style.space(22)
                Layout.topMargin: Style.spacing.xxs
                spacing: Style.spacing.xxs

                Repeater {
                  model: row.p.issues
                  Text {
                    required property var modelData
                    Layout.fillWidth: true
                    text: Model.healthGlyph(modelData.level) + "  " + modelData.text
                    textFormat: Text.PlainText
                    wrapMode: Text.WordWrap
                    color: root.healthColor(modelData.level)
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                  }
                }

                Text {
                  visible: row.p.issues.length === 0
                  text: "Nothing to do. Nice."
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                }

                // what people say: repo comments/issues and upstream Omarchy mentions
                PanelSectionHeader {
                  visible: (row.p.comments || []).length + (row.p.feedback ? row.p.feedback.upstreamMentions.length : 0) > 0
                  text: "FEEDBACK"
                  Layout.topMargin: Style.spacing.xxs
                }

                Repeater {
                  model: (row.p.comments || []).slice(0, 6)
                  Text {
                    required property var modelData
                    Layout.fillWidth: true
                    text: (modelData.new ? "● " : "") + modelData.by + " · " + modelData.where + " · "
                          + Model.relativeTime(modelData.at) + "  " + modelData.text
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    color: modelData.new ? root.accent : root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    MouseArea {
                      anchors.fill: parent
                      cursorShape: Qt.PointingHandCursor
                      onClicked: root.openUrl(parent.modelData.url)
                    }
                  }
                }

                Repeater {
                  model: row.p.feedback ? row.p.feedback.upstreamMentions.slice(0, 5) : []
                  Text {
                    required property var modelData
                    Layout.fillWidth: true
                    text: "Omarchy · " + modelData.state + " · " + modelData.title
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    MouseArea {
                      anchors.fill: parent
                      cursorShape: Qt.PointingHandCursor
                      onClicked: root.openUrl(parent.modelData.url)
                    }
                  }
                }

                Flow {
                  Layout.fillWidth: true
                  Layout.topMargin: Style.spacing.xxs
                  spacing: Style.spacing.xs

                  Button {
                    visible: Model.canFix(row.p)
                    text: "󱚣  Fix with agent"
                    tooltipText: "Open your default agent in this repo with the issues above"
                    bordered: true
                    fontSize: Style.font.caption
                    onClicked: root.fix(row.p)
                  }
                  Button {
                    visible: row.p.mine && row.p.hasTests
                    text: root.testingId === row.p.id ? "Testing…" : "Run tests"
                    bordered: true
                    fontSize: Style.font.caption
                    enabled: root.testingId === ""
                    onClicked: root.runTests(row.p)
                  }
                  Button {
                    visible: !!row.p.tests
                    text: "Test log"
                    bordered: true
                    fontSize: Style.font.caption
                    onClicked: root.openLog(row.p)
                  }
                  Button {
                    visible: Model.repoUrl(row.p) !== ""
                    text: "GitHub"
                    bordered: true
                    fontSize: Style.font.caption
                    onClicked: root.openUrl(Model.repoUrl(row.p))
                  }
                  Button {
                    visible: Model.marketplaceUrl(row.p) !== ""
                    text: "Marketplace #" + (row.p.marketplace && row.p.marketplace.issue ? row.p.marketplace.issue.number : "")
                    bordered: true
                    fontSize: Style.font.caption
                    onClicked: root.openUrl(Model.marketplaceUrl(row.p))
                  }
                  Button {
                    visible: !!row.p.ci && !!row.p.ci.url
                    text: "CI run"
                    bordered: true
                    fontSize: Style.font.caption
                    onClicked: root.openUrl(row.p.ci.url)
                  }
                  Button {
                    text: "Open"
                    tooltipText: row.p.path
                    bordered: true
                    fontSize: Style.font.caption
                    onClicked: root.openFolder(row.p)
                  }
                }
              }
            }
          }
        }

        Text {
          Layout.fillWidth: true
          text: "Click a plugin for details · right-click the bar icon to check GitHub"
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          horizontalAlignment: Text.AlignHCenter
        }
      }
    }
  }
}
