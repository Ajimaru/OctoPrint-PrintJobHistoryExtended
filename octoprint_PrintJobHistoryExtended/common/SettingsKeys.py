# coding=utf-8
from __future__ import absolute_import

class SettingsKeys():

	# Filament, spool assignment and material cost all come from this one plugin.
	PLUGIN_SPOOL_MANAGER = { "key": "SpoolManagerExtended", "minVersion": "2.0.0a1"}
	# Measured electricity data. Read straight from its SQLite file, so no version floor applies.
	PLUGIN_TASMOTA = { "key": "tasmota", "minVersion": None}
	# Optional extras: used when present, never nagged about.
	PLUGIN_DISPLAY_LAYER_PROGRESS = { "key": "DisplayLayerProgress", "minVersion": "1.26.0"}
	PLUGIN_ULTIMAKER_FORMAT_PACKAGE = { "key": "UltimakerFormatPackage", "minVersion": "1.0.0"}
	PLUGIN_PRUSA_SLICER_THUMNAIL = { "key": "prusaslicerthumbnails", "minVersion": "1.0.0"}
	# Legacy import source only.
	PLUGIN_PRINT_HISTORY = { "key": "printhistory", "minVersion": None}
	# Kept solely to import its settings once; see SETTINGS_KEY_COSTESTIMATION_IMPORTED.
	PLUGIN_COST_ESTIMATION = { "key": "costestimation", "minVersion": None}

	## General
	SETTINGS_KEY_PLUGIN_DEPENDENCY_CHECK = "pluginCheckActivated"
	SETTINGS_KEY_SHOW_PRINTJOB_DIALOG_AFTER_PRINT = "showPrintJobDialogAfterPrint"
	SETTINGS_KEY_SHOW_PRINTJOB_DIALOG_AFTER_PRINT_JOB_ID = "showPrintJobDialogAfterPrint_jobId"

	SETTINGS_KEY_SHOWPRINTJOBDIALOGAFTERPRINT_MODE = "showPrintJobDialogAfterPrintMode"
	KEY_SHOWPRINTJOBDIALOGAFTERPRINT_MODE_ALWAYS = "always"
	KEY_SHOWPRINTJOBDIALOGAFTERPRINT_MODE_SUCCESSFUL = "successful"
	KEY_SHOWPRINTJOBDIALOGAFTERPRINT_MODE_FAILED = "failed"

	SETTINGS_KEY_CAPTURE_PRINTJOBHISTORY_MODE = "capturePrintJobHistoryMode"
	KEY_CAPTURE_PRINTJOBHISTORY_MODE_NONE = "none"
	KEY_CAPTURE_PRINTJOBHISTORY_MODE_ALWAYS = "always"
	KEY_CAPTURE_PRINTJOBHISTORY_MODE_SUCCESSFUL = "successful"

	# Filament tracking is no longer a choice between plugins, it is simply on when
	# SpoolManagerExtended is present. The setting survives only to migrate old configs.
	SETTINGS_KEY_SELECTED_FILAMENTTRACKER_PLUGIN = "selectedFilamentTrackerPlugin"
	KEY_SELECTED_SPOOLMANAGER_PLUGIN = "SpoolManagerExtended"
	KEY_SELECTED_NONE_PLUGIN = "none"
	# Values written by earlier releases, only ever read during migration.
	LEGACY_KEY_SELECTED_SPOOLMANAGER_PLUGIN = "SpoolManager Plugin"
	LEGACY_KEY_SELECTED_SPOOLMAN_PLUGIN = "Spoolman Plugin"
	LEGACY_KEY_SELECTED_FILAMENTMANAGER_PLUGIN = "FilamentManager Plugin"
	SETTINGS_KEY_NO_NOTIFICATION_FILAMENTTRACKERING_PLUGIN_SELECTION = "noNotificationTrackingPluginSelection"


	SETTINGS_KEY_CURRENCY_SYMBOL = "currencySymbol"
	SETTINGS_KEY_CURRENCY_FORMAT = "currencyFormat"

	## Printer running cost. Owned by this plugin since 1.18; previously read out of
	## the CostEstimation plugin's settings.
	SETTINGS_KEY_PRINTER_PURCHASE_PRICE = "printerPurchasePrice"
	SETTINGS_KEY_PRINTER_LIFESPAN_HOURS = "printerLifespanHours"
	SETTINGS_KEY_PRINTER_MAINTENANCE_PER_HOUR = "printerMaintenancePerHour"
	SETTINGS_KEY_PRINTER_WEAR_MULTIPLIER = "printerWearMultiplier"
	SETTINGS_KEY_COSTESTIMATION_IMPORTED = "costEstimationSettingsImported"

	## Electricity, measured through the Tasmota plugin's energy database
	SETTINGS_KEY_TASMOTA_PLUG_IP = "tasmotaPlugIp"
	SETTINGS_KEY_TASMOTA_PLUG_IDX = "tasmotaPlugIdx"
	SETTINGS_KEY_ELECTRICITY_COST_PER_KWH = "electricityCostPerKwh"
	SETTINGS_KEY_NO_NOTIFICATION_TASMOTA_POLLING = "noNotificationTasmotaPolling"

	SETTINGS_KEY_SLICERSETTINGS_KEYVALUE_EXPRESSION = "slicerSettingsKeyValueExpression"
	SETTINGS_KEY_SINGLE_PRINTJOB_REPORT_TEMPLATENAME = "singlePrintJobTemplateName"
	SETTINGS_KEY_MULTI_PRINTJOB_REPORT_TEMPLATENAME = "multiPrintJobTemplateName"
	SETTINGS_DEFAULT_VALUE_SINGLE_PRINTJOB_REPORT_TEMPLATENAME = "defaultSinglePrintJobReport"
	SETTINGS_DEFAULT_VALUE_MULTI_PRINTJOB_REPORT_TEMPLATENAME = "defaultMultiPrintJobReport"

	## Camera
	SETTINGS_KEY_TAKE_SNAPSHOT_AFTER_PRINT = "takeSnapshotAfterPrint"
	SETTINGS_KEY_TAKE_PLUGIN_THUMBNAIL_AFTER_PRINT = "takePluginThumbnailAfterPrint"
	SETTINGS_KEY_TAKE_SNAPSHOT_ON_M118_COMMAND = "takeSnapshotOnM118Commnd"
	SETTINGS_KEY_TAKE_SNAPSHOT_ON_GCODE_COMMAND = "takeSnapshotOnGCodeCommnd"
	SETTINGS_KEY_TAKE_SNAPSHOT_GCODE_COMMAND_PATTERN = "takeSnapshotGCodeCommndPattern"
	SETTINGS_KEY_PREFERED_IMAGE_SOURCE = "preferedImageSource"
	KEY_PREFERED_IMAGE_SOURCE_THUMBNAIL = "thumbnail"
	KEY_PREFERED_IMAGE_SOURCE_CAMERA = "camera"

	# Temperatrue
	SETTINGS_KEY_DEFAULT_TOOL_ID = "defaultTemperatureToolId"
	SETTINGS_KEY_DELAY_READING_TEMPERATURE_FROM_PRINTER = "delayReadingTemperatureFromPrinter"

	## Export / Import
	SETTINGS_KEY_IMPORT_CSV_MODE = "importCSVMode"
	KEY_IMPORTCSV_MODE_REPLACE = "replace"
	KEY_IMPORTCSV_MODE_APPEND = "append"

	## Storage
	SETTINGS_KEY_DATABASE_PATH = "databaseFileLocation"
	SETTINGS_KEY_SNAPSHOT_PATH = "snapshotFileLocation"

	## Database backend.
	# These are deliberately flat keys instead of a nested dict: OctoPrint only returns the
	# changed sub-keys of a nested settings dict on a partial save, which silently drops the
	# rest of the values.
	SETTINGS_KEY_DATABASE_USE_EXTERNAL = "useExternal"
	SETTINGS_KEY_DATABASE_TYPE = "databaseType"
	SETTINGS_KEY_DATABASE_HOST = "databaseHost"
	SETTINGS_KEY_DATABASE_PORT = "databasePort"
	SETTINGS_KEY_DATABASE_NAME = "databaseName"
	SETTINGS_KEY_DATABASE_USER = "databaseUser"
	SETTINGS_KEY_DATABASE_PASSWORD = "databasePassword"

	KEY_DATABASE_TYPE_SQLITE = "sqlite"
	KEY_DATABASE_TYPE_MYSQL = "mysql"

	## Identifies this OctoPrint instance when several of them share one external database
	SETTINGS_KEY_INSTANCE_NAME = "instanceName"

	## Debugging
	SETTINGS_KEY_SQL_LOGGING_ENABLED = "sqlLoggingEnabled"

	# Other stuff
	SETTINGS_KEY_MESSAGE_CONFIRM_DATA = "messageConfirmData"
	SETTINGS_KEY_LAST_PLUGIN_DEPENDENCY_CHECK = "lastPluginDependencyCheck"
