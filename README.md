# OctoPrint-PrintJobHistoryExtended

[![Version](https://img.shields.io/badge/dynamic/json.svg?color=brightgreen&label=version&url=https://api.github.com/repos/Ajimaru/OctoPrint-PrintJobHistoryExtended/releases&query=$[0].name)]()
[![Released](https://img.shields.io/badge/dynamic/json.svg?color=brightgreen&label=released&url=https://api.github.com/repos/Ajimaru/OctoPrint-PrintJobHistoryExtended/releases&query=$[0].published_at)]()
![GitHub Releases (by Release)](https://img.shields.io/github/downloads/Ajimaru/OctoPrint-PrintJobHistoryExtended/latest/total.svg)

The OctoPrint-Plugin stores all print-job information of a print in a local database.
This information is collected from OctoPrint itself, but also from other plugins. See [below](#Optional-Plugins) for more information about these plugins.

## Included features

- [x] Print result (success, fail, cancel)
- [x] Start/End datetime -> duration
- [x] Temperatures Bed/Extruder -> HINT: Only single Extruder-Temperature is currently collected. Selectable Tool
- [x] Username
- [x] Filename, filesize
- [x] Note (WYSIWYG-Editor)
- [x] Image (single Image)
- [x] Printed Layers/Height
- [x] Spoolname
- [x] Material
- [x] Used/Calculated length
- [x] Used weight
- [x] Filament cost
- [x] Slicer Settings (look [here](https://github.com/Ajimaru/OctoPrint-PrintJobHistoryExtended/wiki/Slicer-Settings) for "how to use it")
- [x] Export all data from PrintHistory-Plugin as CSV

### UI features

- [x] List all printjobs
- [x] Edit single printjob
- [x] Add single printjob
- [x] Capture/Upload Image
- [x] Filter history table
- [x] Sort history table
- [x] Table column visibility
- [x] Capture image after print
- [x] Take Thumbnail from [Cura Thumbnails](https://plugins.octoprint.org/plugins/UltimakerFormatPackage/) and [PrusaSlicer Thumbnails](https://plugins.octoprint.org/plugins/prusaslicerthumbnails/)
- [x] Export all printjobs as CSV
- [x] Import printjobs from CSV
- [x] Compare Slicer-Settings

### Not included

- No report diagramms

## Optional Plugins

- [PreHeat](https://plugins.octoprint.org/plugins/preheat/)
    - Starting Temperature
- [CostEstimation](https://plugins.octoprint.org/plugins/costestimation/)
    - Added the estimated costs to a print job
- [SpoolManager](https://plugins.octoprint.org/plugins/SpoolManager/)
    - Spool Management
- [FillamentManager](https://plugins.octoprint.org/plugins/filamentmanager/)
    - Spool - Informations
- [DisplayLayerProgress](https://plugins.octoprint.org/plugins/DisplayLayerProgress/)
    - Layer and Height
- [Cura-Thumbnails](https://plugins.octoprint.org/plugins/UltimakerFormatPackage/)
    - Thumbnail
- [PrusaSlicer-Thumbnail](https://plugins.octoprint.org/plugins/prusaslicerthumbnails/)
    - Thumbnail

## Screenshots
![plugin-tab](screenshots/plugin-tab.png "Plugin-Tab")
![editPrintJob-dialog](screenshots/editPrintJob-dialog.png "EditPrintJob-Dialog")
![changePrintStatus-dialog](screenshots/editPrintJob-changeStatus-dialog.png "Change print status")
![statistics-dialog](screenshots/statistics-dialog.png "Print Statistics")
![plugin-settings](screenshots/plugin-settings.png "Plugin-Settings")
![missingplugins-dialog](screenshots/missingPlugins-dialog.png "MissingPlugins-Dialog")

## Setup

Install via the bundled [Plugin Manager](http://docs.octoprint.org/en/master/bundledplugins/pluginmanager.html)
or manually using this URL:

    https://github.com/Ajimaru/OctoPrint-PrintJobHistoryExtended/releases/latest/download/main.zip

## Migrating from PrintJobHistory

This plugin uses its own identifier, so it can be installed **alongside** the original
[PrintJobHistory](https://github.com/vojtakaniok/OctoPrint-PrintJobHistory) plugin. Both
keep separate databases, snapshots and settings.

To adopt the data of an existing PrintJobHistory install, open
**Settings → Print Job History Extended**. While there is something to migrate, a banner
offers a migration dialog; afterwards it stays reachable under the **Storage** tab.

The dialog shows what the old database holds and lets you pick what to copy — the database
and snapshots are preselected, backups are not. Settings can be copied along, except those
pointing at the old plugin's data folder.

Good to know:

- Files are **copied, never moved** — the original plugin keeps working with its own data.
- If this install already has print jobs, the migration stops and asks before replacing
  anything. Replaced files are kept as `<name>.pre-migration-<timestamp>`.
- Each migration can be **undone** (data and settings separately) from the same dialog.
- The plugin holds its database open while running, so a migration ends with a prompt to
  **restart OctoPrint** — the copied print jobs only appear afterwards.

## Roadmap

TBD. Critical bug fixes for starters. Submit issues to the repo [here](https://github.com/Ajimaru/OctoPrint-PrintJobHistoryExtended/issues).

## Versions

see [Release-Overview](https://github.com/Ajimaru/OctoPrint-PrintJobHistoryExtended/releases/)
