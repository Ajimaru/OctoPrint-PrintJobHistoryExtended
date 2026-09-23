# Third-Party Notices

The web interface of this plugin ships the following third-party libraries in
`octoprint_PrintJobHistoryExtended/static/`. Each keeps its own license; the full license
texts are in `3rdPartySoftware/<name>/LICENSE`. Permissively licensed code like this may be
combined into this AGPLv3-licensed work; the combined work remains available under the AGPLv3.

jQuery, Knockout and Bootstrap are provided by OctoPrint itself and are not bundled here.

## Quill

Rich-text editor for the print job notes.

- Version: 2.0.3
- Source: <https://github.com/slab/quill>
- License: BSD-3-Clause, Copyright (c) 2017-2024, Slab; (c) 2014, Jason Chen; (c) 2013, salesforce.com
- Bundled files: `static/js/quill.min.js`, `static/css/quill.snow.css`
- License text: `3rdPartySoftware/quill/LICENSE`

## Day.js

Date parsing and formatting, including the `customParseFormat` plugin.

- Version: 1.11.23
- Source: <https://github.com/iamkun/dayjs>
- License: MIT, Copyright (c) 2018-present, iamkun
- Bundled files: `static/js/dayjs.min.js`, `static/js/plugin/customParseFormat.min.js`
- License text: `3rdPartySoftware/dayjs/LICENSE`

## jQuery DateTimePicker

Date and time picker for the print start/end fields.

- Version: 2.5.x, build from the upstream `master` branch
- Source: <https://github.com/xdan/datetimepicker>
- License: MIT, Copyright (c) 2013 http://xdsoft.net
- Bundled files: `static/js/jquery.datetimepicker.full.min.js`, `static/css/jquery.datetimepicker.min.css`
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
