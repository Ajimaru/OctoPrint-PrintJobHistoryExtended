# OctoPrint-PrintJobHistoryExtended

[![License][badge-license]](LICENSE.txt)
[![Python][badge-python]](https://python.org)
[![OctoPrint][badge-octoprint]](https://octoprint.org)
[![Latest Release][badge-release]](https://github.com/Ajimaru/OctoPrint-PrintJobHistoryExtended/releases/latest)
[![Pre-release][badge-prerelease]](https://github.com/Ajimaru/OctoPrint-PrintJobHistoryExtended/releases)
[![Downloads][badge-downloads]](https://github.com/Ajimaru/OctoPrint-PrintJobHistoryExtended/releases)
[![Made with Love][badge-love]](https://github.com/Ajimaru/OctoPrint-PrintJobHistoryExtended)

[badge-license]: https://img.shields.io/github/license/Ajimaru/OctoPrint-PrintJobHistoryExtended?style=flat-square
[badge-python]: https://img.shields.io/badge/python-3.11%2B-blue.svg?style=flat-square
[badge-octoprint]: https://img.shields.io/badge/OctoPrint-2.0.0%2B-blue.svg?style=flat-square
[badge-release]: https://img.shields.io/github/v/release/Ajimaru/OctoPrint-PrintJobHistoryExtended?style=flat-square
[badge-prerelease]: https://img.shields.io/github/v/release/Ajimaru/OctoPrint-PrintJobHistoryExtended?include_prereleases&label=prerelease&style=flat-square
[badge-downloads]: https://img.shields.io/github/downloads/Ajimaru/OctoPrint-PrintJobHistoryExtended/total.svg?style=flat-square
[badge-love]: https://img.shields.io/badge/made_with-%E2%9D%A4%EF%B8%8F-ff69b4?style=flat-square

An OctoPrint plugin that records every print job — result, duration, temperatures, filament
use, cost, slicer settings and an image — and stores it in a database. Data is collected from
OctoPrint itself and, where available, from other plugins; see
[Optional plugins](#optional-plugins).

## Disclaimer

> [!CAUTION]
> **About the codebase.** This is a personal project, in an early development stage,
> and I make no guarantees about its functionality or safety.
> It has not been fully tested and should not be used in production environments.
> **Use at your own risk.**

> [!NOTE]
> **About this project.** I built this for my own printer setup with AI,
> and if it helps others, even better.
> I have tested it to the best of my knowledge and ability, and every change is backed
> by an automated test suite that runs in CI.
> Disclosed here per the OctoPrint plugin guidelines. Issues and PRs are welcome.

## Origin

This plugin is a fork descending from:

- [OllisGit/OctoPrint-PrintJobHistory](https://github.com/OllisGit/OctoPrint-PrintJobHistory) — original project
- [vojtakaniok/OctoPrint-PrintJobHistory](https://github.com/vojtakaniok/OctoPrint-PrintJobHistory) — fork this repository is based on

This fork is maintained by [Ajimaru](https://github.com/Ajimaru).

## Requirements

- **OctoPrint 2.0.0 or newer** — at the time of writing only available as a release
  candidate. The OctoPrint 1.x branch is **not supported**: pip refuses the installation,
  and the plugin declines to load on a 1.x core.
- **Python 3.11 or newer** (up to 3.14).

## Installation

> [!WARNING]
> Create a backup of your OctoPrint instance before installing this plugin. If you already
> use the original PrintJobHistory plugin, read
> [Migrating from PrintJobHistory](#migrating-from-printjobhistory) first.

This URL always installs the **latest version**, and updates an existing installation
in place:

```text
https://github.com/Ajimaru/OctoPrint-PrintJobHistoryExtended/archive/main.zip
```

### Via OctoPrint's Plugin Manager

*Settings* → *Plugin Manager* → *Get More…* → *… from URL*, paste the URL and
click *Install*, then restart OctoPrint.

### Via pip

Use the pip of the Python environment OctoPrint runs under, then restart OctoPrint:

```bash
pip install --upgrade https://github.com/Ajimaru/OctoPrint-PrintJobHistoryExtended/archive/main.zip
```

### Installing a specific version

All released versions are listed on the
[releases page](https://github.com/Ajimaru/OctoPrint-PrintJobHistoryExtended/releases).

## Features

Recorded per print job:

- Result (success, fail, cancel), start and end time, and the resulting duration
- Bed and tool temperatures — one selectable tool is recorded
- User, filename and file size
- A note, written in a rich-text editor
- A single image: a webcam snapshot or the slicer thumbnail from the file
- Printed layers and height
- Spool name, material, used and calculated length, used weight, and filament cost
- Cost breakdown: filament, electricity (measured, when a Tasmota plug reports it),
  printer wear and free-form other costs
- Slicer settings, with a side-by-side comparison between two jobs

In the user interface:

- List, filter, sort the print job table, and choose which columns it shows
- Add, edit and delete single print jobs, and capture or upload an image
- Statistics across the recorded jobs
- Re-select a recorded job's file for printing, when the file is still available
- CSV export and import, including export of data from the PrintHistory plugin

Storage and access:

- SQLite by default, or an external MySQL/MariaDB database
- Separate OctoPrint permissions for viewing, editing and deleting print jobs

> [!NOTE]
> Report diagrams are not included.

## Optional plugins

Two plugins unlock data this plugin cannot collect on its own. If either is missing, a
one-time hint points at it; the check can be switched off in the settings.

- [SpoolManagerExtended](https://github.com/Ajimaru/OctoPrint-SpoolManagerExtended) (2.0.0a1+) — filament usage, spool assignment and material cost
- [Tasmota](https://plugins.octoprint.org/plugins/tasmota/) — measured electricity cost

These are used when present and never asked for:

- [DisplayLayerProgress](https://plugins.octoprint.org/plugins/DisplayLayerProgress/) (1.26.0+) — printed layers and height
- [Cura Thumbnails](https://plugins.octoprint.org/plugins/UltimakerFormatPackage/) — preview image
- [PrusaSlicer Thumbnails](https://plugins.octoprint.org/plugins/prusaslicerthumbnails/) — preview image
- [CostEstimation](https://plugins.octoprint.org/plugins/costestimation/) — its cost settings are imported once, so they do not have to be entered again
- [PrintHistory](https://plugins.octoprint.org/plugins/printhistory/) — import source for its recorded jobs

> [!NOTE]
> Filament tracking is no longer a choice between plugins: it is simply on whenever
> SpoolManagerExtended is installed. Configurations still naming FilamentManager or
> Spoolman are migrated forward automatically.

## Screenshots

![Plugin tab](screenshots/plugin-tab.png "The print job table")
![Edit print job](screenshots/editPrintJob-dialog.png "Editing a single print job")
![Change print status](screenshots/editPrintJob-changeStatus-dialog.png "Changing a print's status")
![Statistics](screenshots/statistics-dialog.png "Print statistics")
![Settings](screenshots/plugin-settings.png "Plugin settings")

## Migrating from PrintJobHistory

This plugin uses its own identifier, so it can be installed **alongside** the original
[PrintJobHistory](https://github.com/vojtakaniok/OctoPrint-PrintJobHistory) plugin. Both
keep separate databases, snapshots and settings.

To adopt the data of an existing PrintJobHistory install, open
*Settings* → *Print Job History Extended*. While there is something to migrate, a banner
offers a migration dialog; afterwards it stays reachable under the *Storage* tab.

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

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).

## License

AGPLv3 — see [LICENSE.txt](LICENSE.txt).

The bundled JavaScript libraries (Quill, Day.js, jQuery DateTimePicker) keep their own
licenses — see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
