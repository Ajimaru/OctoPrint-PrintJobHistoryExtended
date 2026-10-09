# Third-Party Notices

The web interface of this plugin ships the following third-party libraries in
`octoprint_PrintJobHistoryExtended/static/`. Each keeps its own license; the full license
texts are in `3rdPartySoftware/<name>/LICENSE`. Permissively licensed code like this may be
combined into this AGPLv3-licensed work; the combined work remains available under the AGPLv3.

jQuery, Knockout and Bootstrap are provided by OctoPrint itself and are not bundled here.

Each entry records the upstream artifact and the SHA-256 of the file as vendored, so the
bundled bytes can be verified against the upstream release at any time. Minified builds of
Day.js and jQuery DateTimePicker carry no version banner, which is why the hash is recorded
rather than relied upon from the file itself.

## Quill

Rich-text editor for the print job notes.

- Version: 2.0.3
- Source: <https://github.com/slab/quill>
- License: BSD-3-Clause, Copyright (c) 2017-2024, Slab; (c) 2014, Jason Chen; (c) 2013, salesforce.com
- Bundled files: `static/js/quill.min.js`, `static/css/quill.snow.css`
- Upstream artifacts: <https://cdn.jsdelivr.net/npm/quill@2.0.3/dist/quill.min.js>,
  <https://cdn.jsdelivr.net/npm/quill@2.0.3/dist/quill.snow.css>
- SHA-256 `static/js/quill.min.js`: `13bebff59d2c91f50fec42d5b72757b2b11acce0be8d9b5c994959000d754fcd`
- SHA-256 `static/css/quill.snow.css`: `99302d78bcda5f34450fbeb84c0848a0bb70b62dca555af0f6e0ead4a8f624d2`
- Verified against upstream 2.0.3: the stylesheet is byte-identical once comments are
  stripped; the script differs only by a trailing `sourceMappingURL` comment the upstream
  build appends.
- License text: `3rdPartySoftware/quill/LICENSE`

## Day.js

Date parsing and formatting, including the `customParseFormat` plugin.

- Version: 1.11.23
- Source: <https://github.com/iamkun/dayjs>
- License: MIT, Copyright (c) 2018-present, iamkun
- Bundled files: `static/js/dayjs.min.js`, `static/js/plugin/customParseFormat.min.js`
- Upstream artifacts: <https://cdn.jsdelivr.net/npm/dayjs@1.11.23/dayjs.min.js>,
  <https://cdn.jsdelivr.net/npm/dayjs@1.11.23/plugin/customParseFormat.min.js>
- SHA-256 `static/js/dayjs.min.js`: `0198dd0b1f760cded169c7e7ff7eaf56bc36c4c22c7c9b7c683e59437ed8700e`
- SHA-256 `static/js/plugin/customParseFormat.min.js`: `b199a58d0fbbdc519a072eb9b140a2ca3d4775ea1b054a0c0e5aeed86603c941`
- Verified against upstream 1.11.23: `dayjs.min.js` is byte-identical, the plugin file
  differs only by a trailing newline.
- License text: `3rdPartySoftware/dayjs/LICENSE`

## jQuery DateTimePicker

Date and time picker for the print start/end fields.

- Version: 2.5.x, build from the upstream `master` branch
- Source: <https://github.com/xdan/datetimepicker>
- License: MIT, Copyright (c) 2013 http://xdsoft.net
- Bundled files: `static/js/jquery.datetimepicker.full.min.js`, `static/css/jquery.datetimepicker.min.css`
- SHA-256 `static/js/jquery.datetimepicker.full.min.js`: `144a847a5588dd6a2e14ea365563ffb897ecd72f0a27ef852e1d8b6ea73c4899`
- SHA-256 `static/css/jquery.datetimepicker.min.css`: `0ce4bd5ba351f8d15ed5f521104d0f18a63f7ee6db5029ce7d38ded89303c376`
- These builds carry no version banner and the exact upstream commit was not recorded, so
  the version above cannot be verified from the files. The latest upstream release is 2.5.21
  and the project is effectively unmaintained; re-vendoring from a pinned release is tracked
  as follow-up work.
- License text: `3rdPartySoftware/jquery-datetimepicker/LICENSE`

The "full" build embeds two further libraries:

### php-date-formatter

- Source: <https://github.com/kartik-v/php-date-formatter>
- License: BSD-3-Clause, Copyright (c) 2014 - 2025, Kartik Visweswaran, Krajee.com
- License text: `3rdPartySoftware/php-date-formatter/LICENSE`

### jQuery Mousewheel

- Version: 3.1.12 (this is the `version:"3.1.12"` string inside the build, not the DateTimePicker version)
- Source: <https://github.com/jquery/jquery-mousewheel>
- License: MIT, Copyright OpenJS Foundation and other contributors
- License text: `3rdPartySoftware/jquery-mousewheel/LICENSE`

## OctoPrint plugin helper scripts

`static/js/TableItemHelper.js` and `static/js/ResetSettingsUtilV3.js` originate from
OllisGit's OctoPrint plugins, where they are shared between plugins as plain source files
without a separate license header. They are not third-party libraries in the npm sense, but
they are not original to this plugin either. They are covered by the AGPLv3 of the project
they came from, which is the same license as this plugin, so no separate license text applies.
