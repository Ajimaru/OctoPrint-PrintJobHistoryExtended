# coding=utf-8
from __future__ import absolute_import

import logging.handlers
import threading
import time
from queue import Queue

import octoprint.plugin
from octoprint.access.permissions import Permissions
from octoprint.events import Events
from octoprint.filemanager import FileDestinations
from octoprint.util.version import get_octoprint_version_string, is_octoprint_compatible

import datetime
import math
import flask
import os
import shutil
import sqlite3
import tempfile
import json
from urllib.request import pathname2url

from octoprint_PrintJobHistoryExtended.common import CSVExportImporter
from octoprint_PrintJobHistoryExtended.models.FilamentModel import FilamentModel
from octoprint_PrintJobHistoryExtended.models.PrintJobModel import PrintJobModel
from octoprint_PrintJobHistoryExtended.models.TemperatureModel import TemperatureModel
from octoprint_PrintJobHistoryExtended.models.CostModel import CostModel
from peewee import DoesNotExist

from .common.SettingsKeys import SettingsKeys
from .common.SlicerSettingsParser import SlicerSettingsParser
from .common.ResetAbleLogFileHandler import ResetAbleLogFileHandler
from .api.PrintJobHistoryExtendedAPI import PrintJobHistoryExtendedAPI
from .api import TransformPrintJob2JSON
from .DatabaseManager import DatabaseManager
from .CameraManager import CameraManager

from octoprint_PrintJobHistoryExtended.common import StringUtils, DateTimeUtils, CameraSettingsMigration
from octoprint_PrintJobHistoryExtended.common import PrintJobUtils

# Identifier this plugin used before it was renamed to "PrintJobHistoryExtended". Both the
# data folder (~/.octoprint/data/<identifier>/) and the settings namespace
# (plugins.<identifier>) are derived from it, so an existing install is only reachable
# under the old name - see _performLegacyMigration().
LEGACY_IDENTIFIER = "PrintJobHistory"

LEGACY_DATABASE_FILE_NAME = "printJobHistory.db"
DATABASE_FILE_NAME = "printJobHistoryExtended.db"

# Records what a migration overwrote, so it can be taken back. One file per migration
# kind, so the database and the settings can be undone independently - they are separate
# actions and undoing one must not silently drop the other's record.
LEGACY_UNDO_FILE_NAMES = {
	"database": "legacy-migration-undo-database.json",
	"settings": "legacy-migration-undo-settings.json",
}

# Written when a migration replaced the database file, removed once the server has come up
# again. The plugin opens its database at startup and keeps the handle, so a file swapped
# underneath it stays invisible until a restart - the marker is what tells the UI to ask
# for one. It has to outlive the process, hence a file rather than an attribute.
LEGACY_RESTART_REQUIRED_FILE_NAME = "legacy-migration-restart-required"

# Everything this plugin logs while a print runs, stored with the job afterwards.
TECHNICAL_LOG_FILE_NAME = "plugin_PrintJobHistoryExtended_singlePrintJob.log"
# MySQL keeps the technical log in a TEXT column, which holds 65,535 bytes. A long print can log
# more - 77 KB in 2.5 hours once a warning repeated every 15 seconds - and then the whole update
# failed: the log was lost and the user got an error popup. Some headroom below the limit.
TECHNICAL_LOG_MAX_BYTES = 60000

# How long after PRINT_STARTED a printer-hosted file is read for the fallback the capture
# needs when the printer is gone by then (see _rememberFileDataAtStartAsync).
REMEMBER_FILE_DATA_DELAY_IN_SECONDS = 30

# Settings that must not be carried over: the two path keys point into the *old* plugin's
# data folder and would send this install back to the legacy database, and the versions
# describe the installed plugin and its settings format rather than a user choice.
LEGACY_SETTINGS_NOT_MIGRATABLE = frozenset([
	SettingsKeys.SETTINGS_KEY_DATABASE_PATH,
	SettingsKeys.SETTINGS_KEY_SNAPSHOT_PATH,
	"installed_version",
	octoprint.plugin.SettingsPlugin.config_version_key,
])


class PrintJobHistoryExtendedPlugin(
	PrintJobHistoryExtendedAPI,
	octoprint.plugin.SettingsPlugin,
	octoprint.plugin.AssetPlugin,
	octoprint.plugin.TemplatePlugin,
	octoprint.plugin.StartupPlugin,
	octoprint.plugin.EventHandlerPlugin,
	octoprint.plugin.SimpleApiPlugin
):

	# How long after a print ended a filament-usage event is still accepted as belonging to
	# it. The measured gap is under a second; anything beyond this did not come from that
	# print and must not rewrite a job the user may already be editing.
	BACKFILL_MAX_AGE_IN_SECONDS = 120
	# Both plugins take the print start from their own PRINT_STARTED handler, a moment
	# apart. Two different prints of the same file are always much further apart than this.
	USAGE_REPORT_START_TOLERANCE_IN_SECONDS = 60

	def initialize(self):
		self._displayLayerProgressPluginImplementation = None
		self._displayLayerProgressPluginImplementationState = None
		self._spoolManagerPluginImplementation = None
		self._spoolManagerPluginImplementationState = None
		self._ultimakerFormatPluginImplementation = None
		self._ultimakerFormatPluginImplementationState = None
		self._prusaSlicerThumbnailsPluginImplementation = None
		self._prusaSlicerThumbnailsPluginImplementationState = None
		self._costEstimationPluginImplementation = None
		self._costEstimationPluginImplementationState = None
		self._tasmotaPluginImplementation = None
		self._tasmotaPluginImplementationState = None
		# SpoolManagerExtended books the filament of a finished job a moment AFTER our own
		# PRINT_DONE handler stored it, so the usage arrives later, by event. These remember
		# which job is waiting for it. In memory only: after a restart no print is pending,
		# and guessing which stored job a late event belongs to is worse than skipping it.
		self._backfillTargetDatabaseId = None
		self._backfillTargetPrintEndDateTime = None
		self._backfillLock = threading.Lock()
		self._printHistoryPluginImplementation = None

		pluginDataBaseFolder = self.get_plugin_data_folder()

		self._logger.info("Start initializing")
		# self.myInfoLogger("Start initializing")

		# Drop the settings dict of an abandoned Postgres attempt. Changing the defaults does
		# not remove it from an existing config.yaml, and it shipped hardcoded credentials that
		# were served to every logged-in client through the settings API.
		try:
			self._settings.remove(["datbaseSettings"])
		except Exception as e:
			self._logger.warning("Could not remove obsolete 'datbaseSettings': " + str(e))

		# DATABASE
		sqlLoggingEnabled = self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_SQL_LOGGING_ENABLED])
		self._databaseManager = DatabaseManager(self._logger, sqlLoggingEnabled)
		self._databaseManager.setInstanceName(self._resolveInstanceName())
		self._databaseManager.initDatabase(pluginDataBaseFolder,
										   self._sendErrorMessageToClient,
										   self._buildDatabaseSettingsFromPluginSettings())

		# CAMERA
		self._cameraManager = CameraManager(self._logger)
		pluginBaseFolder = self._basefolder

		self._cameraManager.initCamera(pluginDataBaseFolder, pluginBaseFolder)

		# Init values for initial settings view-page
		self._settings.set([SettingsKeys.SETTINGS_KEY_DATABASE_PATH], self._databaseManager.getDatabaseFileLocation())
		self._settings.set([SettingsKeys.SETTINGS_KEY_SNAPSHOT_PATH], self._cameraManager.getSnapshotFileLocation())
		self._settings.save()

		# OTHER STUFF
		self._currentPrintJobModel = None
		# The printer asked for the snapshot of the running print (M118) and got one
		self._m118SnapshotTaken = False

		self.alreadyCanceled = False

		self._resetableFileLogHandler = None

		self._logger.info("Done initializing")

	def myInfoLogger(self, message):
		"Automatically log the current function details."
		import inspect, logging
		# Get the previous frame in the stack, otherwise it would
		# be this function!!!
		func = inspect.currentframe().f_back.f_code
		# func.co_firstlineno,
		# func2 = inspect.currentframe().f_code
		# Dump the message + the name of this function to the log.
		self._logger.info("%s:%i - %s" % (
			func.co_name,
			func.co_flags,
			message
		))

	################################################################################################## private functions
	def _sendDataToClient(self, payloadDict):
		self._plugin_manager.send_plugin_message(self._identifier,
												 payloadDict)

	# popupType = 'notice', 'info', 'success', or 'error'.
	def _sendMessageToClient(self, popupType, title, message, hide=False):
		self._sendDataToClient(dict(action="messagePopUp",
									popupType=popupType,
									title=title,
									message=message,
									hide=hide))

	def _sendErrorMessageToClient(self, title, message):
		self._sendDataToClient(dict(action="errorPopUp",
									title=title,
									message=message))

	def _sendMessageConfirmToClient(self, title, message):
		confirmMessageData = {
			"title": title,
			"message": message
		}
		self._sendDataToClient(dict(action="showMessageConfirmDialog",
									confirmMessageData=confirmMessageData))

	def _checkAndLoadThirdPartyPluginInfos(self, sendToClient=False):
		pluginInfo = self._getPluginInformation(SettingsKeys.PLUGIN_SPOOL_MANAGER)
		self._spoolManagerPluginImplementationState = pluginInfo[0]
		self._spoolManagerPluginImplementation = pluginInfo[1]
		spoolManagerCurrentVersion = pluginInfo[2]
		spoolManagerRequiredVersion = pluginInfo[3]

		pluginInfo = self._getPluginInformation(SettingsKeys.PLUGIN_TASMOTA)
		self._tasmotaPluginImplementationState = pluginInfo[0]
		self._tasmotaPluginImplementation = pluginInfo[1]
		tasmotaCurrentVersion = pluginInfo[2]

		# Optional extras. Resolved so the log shows them, but never nagged about.
		pluginInfo = self._getPluginInformation(SettingsKeys.PLUGIN_DISPLAY_LAYER_PROGRESS)
		self._displayLayerProgressPluginImplementationState = pluginInfo[0]
		self._displayLayerProgressPluginImplementation = pluginInfo[1]
		displayLayerCurrentVersion = pluginInfo[2]

		pluginInfo = self._getPluginInformation(SettingsKeys.PLUGIN_ULTIMAKER_FORMAT_PACKAGE)
		self._ultimakerFormatPluginImplementationState = pluginInfo[0]
		self._ultimakerFormatPluginImplementation = pluginInfo[1]
		ultimakerCurrentVersion = pluginInfo[2]

		pluginInfo = self._getPluginInformation(SettingsKeys.PLUGIN_PRUSA_SLICER_THUMNAIL)
		self._prusaSlicerThumbnailsPluginImplementationState = pluginInfo[0]
		self._prusaSlicerThumbnailsPluginImplementation = pluginInfo[1]
		pruseSlicerCurrentVersion = pluginInfo[2]

		pluginInfo = self._getPluginInformation(SettingsKeys.PLUGIN_COST_ESTIMATION)
		self._costEstimationPluginImplementationState = pluginInfo[0]
		self._costEstimationPluginImplementation = pluginInfo[1]
		costPluginCurrentVersion = pluginInfo[2]

		pluginInfo = self._getPluginInformation(SettingsKeys.PLUGIN_PRINT_HISTORY)
		if ("enabled" == pluginInfo[0]):
			self._printHistoryPluginImplementation = pluginInfo[1]
		else:
			self._printHistoryPluginImplementation = None

		self._logger.info("Plugin-State:\n"
						  "| SpoolManagerExtended=" + self._spoolManagerPluginImplementationState + " (" + str(spoolManagerCurrentVersion) + ")\n"
						  "| tasmota=" + self._tasmotaPluginImplementationState + " (" + str(tasmotaCurrentVersion) + ")\n"
						  "| DisplayLayerProgress=" + self._displayLayerProgressPluginImplementationState + " (" + str(displayLayerCurrentVersion) + ")\n"
						  "| UltimakerFormat=" + self._ultimakerFormatPluginImplementationState + " (" + str(ultimakerCurrentVersion) + ")\n"
						  "| PrusaSlicerThumbnail=" + self._prusaSlicerThumbnailsPluginImplementationState + " (" + str(pruseSlicerCurrentVersion) + ")\n"
						  "| costestimation=" + self._costEstimationPluginImplementationState + " (" + str(costPluginCurrentVersion) + ")\n"
						  )

		if sendToClient == True:

			currentPluginVersion = self._plugin_info.version
			lastVersionCheck = self._settings.get([SettingsKeys.SETTINGS_KEY_LAST_PLUGIN_DEPENDENCY_CHECK])
			newPlugiVersionNotifier = False
			if (currentPluginVersion != lastVersionCheck):
				newPlugiVersionNotifier = True

			lastVersionCheck = currentPluginVersion
			self._settings.set([SettingsKeys.SETTINGS_KEY_LAST_PLUGIN_DEPENDENCY_CHECK], lastVersionCheck)
			self._settings.save()

			if (self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_PLUGIN_DEPENDENCY_CHECK]) == True or newPlugiVersionNotifier):

				missingMessage = ""

				if self._spoolManagerPluginImplementation == None:
					missingMessage = missingMessage + "<li><a target='_newTab' href='https://github.com/Ajimaru/OctoPrint-SpoolManagerExtended'>SpoolManagerExtended (" + str(spoolManagerRequiredVersion) + "+)</a> (<b>" + self._spoolManagerPluginImplementationState + "</b>)<br/>needed for filament usage, spool assignment and material cost</li>"

				if self._tasmotaPluginImplementation == None:
					missingMessage = missingMessage + "<li><a target='_newTab' href='https://plugins.octoprint.org/plugins/tasmota/'>Tasmota</a> (<b>" + self._tasmotaPluginImplementationState + "</b>)<br/>needed for measured electricity cost</li>"

				if missingMessage != "":
					missingMessage = "<ul>" + missingMessage + "</ul>"
					self._sendDataToClient(dict(action="missingPlugin",
												message=missingMessage))

	def _checkForMissingFilamentTracking(self):
		# Filament tracking used to be a choice between three plugins. It is now simply on
		# whenever SpoolManagerExtended is present, so the stored value is only ever migrated
		# forward and then left alone.
		currentFilamentTrackingPlugin = self._settings.get([SettingsKeys.SETTINGS_KEY_SELECTED_FILAMENTTRACKER_PLUGIN])
		droppedTrackerName = None

		if (currentFilamentTrackingPlugin == SettingsKeys.LEGACY_KEY_SELECTED_SPOOLMANAGER_PLUGIN):
			currentFilamentTrackingPlugin = SettingsKeys.KEY_SELECTED_SPOOLMANAGER_PLUGIN
		elif (currentFilamentTrackingPlugin == SettingsKeys.LEGACY_KEY_SELECTED_SPOOLMAN_PLUGIN):
			droppedTrackerName = "Spoolman"
			currentFilamentTrackingPlugin = SettingsKeys.KEY_SELECTED_SPOOLMANAGER_PLUGIN
		elif (currentFilamentTrackingPlugin == SettingsKeys.LEGACY_KEY_SELECTED_FILAMENTMANAGER_PLUGIN):
			droppedTrackerName = "FilamentManager"
			currentFilamentTrackingPlugin = SettingsKeys.KEY_SELECTED_SPOOLMANAGER_PLUGIN
		elif (currentFilamentTrackingPlugin != SettingsKeys.KEY_SELECTED_SPOOLMANAGER_PLUGIN):
			currentFilamentTrackingPlugin = SettingsKeys.KEY_SELECTED_NONE_PLUGIN

		if (currentFilamentTrackingPlugin == SettingsKeys.KEY_SELECTED_SPOOLMANAGER_PLUGIN and
			self._isSpoolManagerInstalledAndEnabled() == False):
			currentFilamentTrackingPlugin = SettingsKeys.KEY_SELECTED_NONE_PLUGIN

		self._settings.set([SettingsKeys.SETTINGS_KEY_SELECTED_FILAMENTTRACKER_PLUGIN], currentFilamentTrackingPlugin)
		self._settings.save()

		notifyUser = self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_NO_NOTIFICATION_FILAMENTTRACKERING_PLUGIN_SELECTION]) == False

		# Users of a dropped tracker would otherwise just see their filament data stop appearing.
		if (droppedTrackerName != None):
			self._logger.warning("Filament tracker '" + droppedTrackerName + "' is no longer supported")
			if (notifyUser):
				self._sendMessageToClient("notice", droppedTrackerName + " is no longer supported",
										  "Filament data now comes from SpoolManagerExtended. Install it to keep tracking filament usage and cost.")
		elif (self._isSpoolManagerInstalledAndEnabled() == False):
			self._logger.warning("Filamenttracking not possible, SpoolManagerExtended is not installed/enabled")
			if (notifyUser):
				self._sendMessageToClient("notice", "Filamenttracking not possible!",
										  "SpoolManagerExtended is not installed/enabled", True)

	def _isSpoolManagerInstalledAndEnabled(self):
		return True if self._spoolManagerPluginImplementation != None and self._spoolManagerPluginImplementationState == "enabled" else False

	def _isTasmotaInstalledAndEnabled(self):
		return True if self._tasmotaPluginImplementation != None and self._tasmotaPluginImplementationState == "enabled" else False

	def _isCostEstimationInstalledAndEnabled(self):
		return True if self._costEstimationPluginImplementation != None and self._costEstimationPluginImplementationState == "enabled" else False



	# get the plugin with status information
	# [0] == status-string
	# [1] == implementaiton of the plugin
	# [2] == version of the plugin, as str like 3.3.0
	# [3] == requiredVersion of the plugin, as str like 1.3.0
	def _getPluginInformation(self, pluginInfo):
		pluginKey = pluginInfo["key"]
		requiredVersion = pluginInfo["minVersion"]

		status = None
		implementation = None
		version = None

		if pluginKey in self._plugin_manager.plugins:
			plugin = self._plugin_manager.plugins[pluginKey]
			if plugin != None:
				if (plugin.enabled == True):
					status = "enabled"
					# for OP 1.4.x we need to check agains "icompatible"-attribute
					if (hasattr(plugin, 'incompatible')):
						if (plugin.incompatible == False):
							implementation = plugin.implementation
						else:
							status = "incompatible"
					else:
						# OP 1.3.x
						implementation = plugin.implementation
					pass
				else:
					status = "disabled"
				version = plugin.version
		else:
			status = "missing"

		# Check requiredVersion, if not compatible --> implementation None
		if (requiredVersion != None and version != None):
			canBeUsed = False
			try:
				comparabelVersion = self._get_comparable_version_semantic(version)
				comparabelRequiredVersion = self._get_comparable_version_semantic(requiredVersion)
				canBeUsed = comparabelVersion >= comparabelRequiredVersion
			except (ValueError) as error:
				logging.exception("Something is wrong with the " +pluginKey+ " version numbers")

			if (canBeUsed == False):
				status = "wrong version"
				implementation = None
		return [status, implementation, version, requiredVersion]

	def _get_comparable_version_semantic(self, version_string, force_base=False):
		import semantic_version
		version = semantic_version.Version.coerce(version_string, partial=False)
		if force_base:
			version_string = "{}.{}.{}".format(version.major, version.minor, version.patch)
			version = semantic_version.Version.coerce(version_string, partial=False)

		return version

	# Returns the on-disk path of a print file, or None when there is none.
	# Printer-hosted storage (sd-card, and connectors like Bambu) keeps the file on the
	# printer itself, so path_on_disk raises instead of answering. That is a normal setup,
	# not a failure, hence the quiet info-log for it.
	def _resolveFileOnDisk(self, fileOrigin, filePath):
		if (fileOrigin != FileDestinations.LOCAL):
			self._logger.info("No file on disk for origin '" + str(fileOrigin) + "' (only '" + FileDestinations.LOCAL + "' is stored on disk). Skipping slicer settings.")
			return None
		try:
			return self._file_manager.path_on_disk(fileOrigin, filePath)
		except Exception as e:
			# Even a local file can be gone by now, e.g. deleted right after the print
			self._logger.warning("Could not resolve path on disk for '" + str(filePath) + "': " + str(e))
			return None


	# Returns the file meta data, or None when the storage cannot supply any.
	# Printer storage answers None when the connector reports no metadata capability, so
	# every caller has to cope with a missing dict instead of assuming one.
	def _readFileMetaData(self, fileOrigin, filePath):
		try:
			fileData = self._file_manager.get_metadata(fileOrigin, filePath)
		except Exception as e:
			self._logger.warning("Could not read meta data for '" + str(filePath) + "': " + str(e))
			return None
		if (fileData == None):
			self._logger.info("No meta data available for origin '" + str(fileOrigin) + "' (printer-hosted file or connector without metadata support)")
		return fileData


	# Grabs all informations for the filament attributes
	def _createAndAssignFilamentModel(self, printJob, payload):

		self._logger.info("----- Start reading filament -----")
		filePath = payload.get("path")
		fileData = self._readFileMetaData(payload.get("origin"), filePath)

		# - grab calcualted data for each tool
		# - grap measured data for each tool
		# SpoolManagerExtended knows the printer's PHYSICAL tools; OctoPrint's own analysis
		# files a connector job under tool0 whichever head actually prints it.
		filamentCalculatedDict = self._readJobFilamentUsageFromPeer(payload.get("origin"), filePath)
		if (filamentCalculatedDict == None):
			filamentCalculatedDict = self._readCalculatedFilamentMetaData(fileData)
		# The printer storage is gone when the connection dropped (see _rememberFileDataAtStart)
		if (filamentCalculatedDict == None and printJob.calculatedFilamentAtStart != None):
			self._logger.info("Taking the calculated filament read at the start of the print: " + str(printJob.calculatedFilamentAtStart))
			filamentCalculatedDict = printJob.calculatedFilamentAtStart
		# Preferred source: usage SpoolManagerExtended captured while booking the finished job.
		# It survives the odometer reset and covers printer-storage prints - but only once
		# the peer has actually booked, which is why the reader verifies the snapshot age.
		lastPrintJobUsage = self._readLastPrintJobUsage(printJob.printStartDateTime)
		filamentExtrusionArray = None
		if (lastPrintJobUsage == None):
			filamentExtrusionArray = self._readMeasuredFilament()
		selectedSpoolDataDict = self._getSelectedSpools()
		# isMultiToolPrint = len(filamentCalculatedDict) > 1

		# - add always "total"
		calculatedTotalLength = 0.0

		totalFilamentModel = FilamentModel()
		totalFilamentModel.toolId = "total"
		printJob.addFilamentModel(totalFilamentModel)

		allToolIds = self._collectToolIdsOfPrintJob(filamentCalculatedDict, selectedSpoolDataDict)

		for toolId in allToolIds:
			filamentModel = printJob.getFilamentModelByToolId(toolId)
			if (filamentModel == None):
				filamentModel = FilamentModel()
				filamentModel.toolId = toolId
				printJob.addFilamentModel(filamentModel)

			# - assign calculated values
			if (filamentCalculatedDict != None and toolId in filamentCalculatedDict):
				toolAnalysis = filamentCalculatedDict[toolId]
				calculatedLength = None
				if (isinstance(toolAnalysis, dict)):
					calculatedLength = StringUtils.transformToFloatOrNone(toolAnalysis.get("length"))
				# not needed calculatedVolumne = filamentCalculatedDict[toolId]["volume"]

				if (calculatedLength != None):
					filamentModel.calculatedLength = calculatedLength
					calculatedTotalLength = calculatedTotalLength + calculatedLength

			# Assign SpoolData (e.g. Name), no longer gated on calculatedLength > 0: a
			# printer-hosted print has no calculated length at all, and an unknown length is
			# no reason to forget which spool was mounted.
			self._assignSelectedSpool(filamentModel, selectedSpoolDataDict)

		totalFilamentModel.calculatedLength = calculatedTotalLength

		# - assign measured values
		usedTotalLength = None
		usedTotaWeight = None
		usedTotalCost = None

		if (lastPrintJobUsage != None):
			self._logger.info("Using per-job usage from SpoolManagerExtended (source '" + str(lastPrintJobUsage.get("source")) + "')")
			usedTotalLength = 0.0
			usedTotaWeight = 0.0
			usedTotalCost = 0.0
			for toolUsage in lastPrintJobUsage.get("tools", []):
				if (toolUsage == None):
					continue
				toolId = "tool" + str(toolUsage["toolIndex"])
				filamentModel = printJob.getFilamentModelByToolId(toolId)
				if (filamentModel == None):
					# Like the backfill: a tool the job never used gets no row
					if (StringUtils.transformToFloatOrZero(toolUsage.get("usedLength")) == 0):
						continue
					filamentModel = FilamentModel()
					filamentModel.toolId = toolId
					printJob.addFilamentModel(filamentModel)
					self._assignSelectedSpool(filamentModel, selectedSpoolDataDict)

				filamentModel.usedLength = toolUsage.get("usedLength")
				filamentModel.usedWeight = toolUsage.get("usedWeight")
				filamentModel.usedCost = toolUsage.get("usedCost")

				usedTotalLength = usedTotalLength + StringUtils.transformToFloatOrZero(filamentModel.usedLength)
				usedTotaWeight = usedTotaWeight + StringUtils.transformToFloatOrZero(filamentModel.usedWeight)
				usedTotalCost = usedTotalCost + StringUtils.transformToFloatOrZero(filamentModel.usedCost)

				self._logger.info(toolId + ": usedLength='"+str(filamentModel.usedLength)+"'; usedWeight='"+str(filamentModel.usedWeight)+"'; usedCost='"+str(filamentModel.usedCost)+"'")

		elif (filamentExtrusionArray != None):
			usedTotalLength = 0.0
			usedTotaWeight = 0.0
			usedTotalCost = 0.0
			for toolIndex, usedLength in enumerate(filamentExtrusionArray):
				toolId = "tool" + str(toolIndex)
				filamentModel = printJob.getFilamentModelByToolId(toolId)
				if (filamentModel == None):
					# The odometer reports every tool of the printer profile, and 0 for a job
					# the printer hosts itself. A tool with no calculated length, no spool and
					# nothing extruded is not part of this job: a U1 job printed on T3 used
					# to get an empty tool0 row next to its tool3 row.
					if (StringUtils.transformToFloatOrZero(usedLength) == 0):
						continue
					# Extruded outside the calculated tools (manual extrusion during a pause, a
					# purge on another head): the spool mounted there is what it came from
					filamentModel = FilamentModel()
					filamentModel.toolId = toolId
					printJob.addFilamentModel(filamentModel)
					self._assignSelectedSpool(filamentModel, selectedSpoolDataDict)

				filamentModel.toolId = toolId
				filamentModel.usedLength = usedLength
				usedTotalLength = usedTotalLength + usedLength

				filamentModel.usedWeight = self._calculateFilamentWeightForLength(usedLength,
																				  filamentModel.diameter,
																				  filamentModel.density)
				usedTotaWeight = usedTotaWeight + filamentModel.usedWeight

				if (filamentModel.spoolCost != None and
					filamentModel.weight != None and
					filamentModel.usedWeight != None):
					filamentModel.usedCost = (filamentModel.spoolCost / filamentModel.weight) * filamentModel.usedWeight
					usedTotalCost = usedTotalCost + filamentModel.usedCost

				self._logger.info(toolId + ": usedLength='"+str(usedLength)+"'; usedWeight='"+str(filamentModel.usedWeight)+"'; usedCost='"+str(filamentModel.usedCost)+"'")

			# The odometer reads zero for a job the printer streams itself (it never passes
			# through OctoPrint), and also when SpoolManagerExtended booked - and reset - before
			# us. Either way its own report of the job is what fills this in afterwards.
			if (usedTotalLength == 0.0 and calculatedTotalLength > 0):
				self._logger.info(
					"The odometer measured no filament although the file needs " + str(calculatedTotalLength) +
					"mm (printer-hosted job, or SpoolManagerExtended booked this job first). "
					"Taking the usage over from SpoolManagerExtended's report once it has booked the job.")

		if (usedTotalLength != None):
			totalFilamentModel.usedLength = usedTotalLength
			totalFilamentModel.usedWeight = usedTotaWeight
			totalFilamentModel.usedCost = usedTotalCost

			self._logger.info("total: usedTotalLength='"+str(usedTotalLength)+"'; usedTotaWeight='"+str(usedTotaWeight)+"'; usedTotalCost='"+str(usedTotalCost)+"'")

		# - assign all spool informations to total, also of a tool only the measurement found
		allSpoolNames = ""
		allVendors = ""
		allMaterials = ""
		for filamentModel in printJob.getFilamentModels(withoutTotal=True):
			allSpoolNames = self._appendUniqueToCommaList(allSpoolNames, filamentModel.spoolName)
			allVendors = self._appendUniqueToCommaList(allVendors, filamentModel.vendor)
			allMaterials = self._appendUniqueToCommaList(allMaterials, filamentModel.material)
		totalFilamentModel.spoolName = allSpoolNames
		totalFilamentModel.vendor = allVendors
		totalFilamentModel.material = allMaterials

	def _assignSelectedSpool(self, filamentModel, selectedSpoolDataDict):
		if (selectedSpoolDataDict == None or (filamentModel.toolId in selectedSpoolDataDict) == False):
			return
		spoolData = selectedSpoolDataDict[filamentModel.toolId]

		filamentModel.spoolName = spoolData["spoolName"]
		filamentModel.vendor = spoolData["vendor"]
		filamentModel.material = spoolData["material"]
		filamentModel.diameter = spoolData["diameter"]
		filamentModel.density = spoolData["density"]

		filamentModel.spoolCost = spoolData["spoolCost"]
		filamentModel.weight = spoolData["weight"]


	# The tools that get a row at the capture. A tool is worth recording when the slicer
	# calculated something for it OR when a spool is selected on it: printer-hosted prints
	# (Bambu and other connectors) have no file meta data at all, so only the calculated dict
	# is missing - the spool data is there and used to be thrown away, which left the whole
	# dialog blank.
	# Once the calculation names the tools the job uses (length > 0), every other tool is
	# not part of it, whether a spool is merely mounted there or the slicer listed it with
	# nothing: U1 job 189 printed on T3 got an empty tool2 row for the PETG spool on T2, and
	# its total row read "PLA, PETG". All tools still count without such a calculation, or
	# when the selected spools sit only on other tools - OctoPrint's own analysis files a
	# connector job under tool0 whichever head prints it, so it cannot tell which one is right.
	def _collectToolIdsOfPrintJob(self, filamentCalculatedDict, selectedSpoolDataDict):
		allCalculatedToolIds = []
		allUsedToolIds = []
		if (filamentCalculatedDict != None):
			for toolId in filamentCalculatedDict:
				allCalculatedToolIds.append(toolId)
				toolAnalysis = filamentCalculatedDict[toolId]
				if (isinstance(toolAnalysis, dict) and StringUtils.transformToFloatOrZero(toolAnalysis.get("length")) > 0):
					allUsedToolIds.append(toolId)

		allSpoolToolIds = []
		if (selectedSpoolDataDict != None):
			allSpoolToolIds = list(selectedSpoolDataDict)

		allToolIds = []
		for toolId in allCalculatedToolIds + allSpoolToolIds:
			if ((toolId in allToolIds) == False):
				allToolIds.append(toolId)

		if (len(allUsedToolIds) == 0):
			return allToolIds
		isSpoolOnUsedTool = any(toolId in allSpoolToolIds for toolId in allUsedToolIds)
		if (len(allSpoolToolIds) > 0 and isSpoolOnUsedTool == False):
			return allToolIds

		allUnusedToolIds = [toolId for toolId in allToolIds if toolId not in allUsedToolIds]
		if (len(allUnusedToolIds) > 0):
			self._logger.info("No row for " + str(allUnusedToolIds) + ", the job uses only " + str(allUsedToolIds))
		return allUsedToolIds

	# read the total extrusion of each tool, like this
	# return [123.123, 234.234, 0, 0]
	def _readMeasuredFilament(self):
		if (self._isSpoolManagerInstalledAndEnabled() == False):
			self._logger.info("There is no plugin for filament tracking available. Installed and Enabled?")
			return None

		result = None
		try:
			result = self._spoolManagerPluginImplementation.api_getExtrusionAmount()
		except Exception as e:
			self._logger.error("Could not read extrusion amount from SpoolManagerExtended: " + str(e))
		return result

	# per-tool usage already booked by SpoolManagerExtended, including its sliced-metadata
	# fallback for printers whose extrusion the odometer never sees (Bambu and friends).
	# Returns None when the peer plugin does not offer the newer API yet.
	def _readLastPrintJobUsage(self, printStartDateTime=None):
		if (self._isSpoolManagerInstalledAndEnabled() == False):
			return None
		if (hasattr(self._spoolManagerPluginImplementation, "api_getLastPrintJobUsage") == False):
			return None

		usage = None
		try:
			usage = self._spoolManagerPluginImplementation.api_getLastPrintJobUsage()
		except Exception as e:
			self._logger.error("Could not read last print job usage from SpoolManagerExtended: " + str(e))
			return None

		if (usage == None):
			return None
		if (usage.get("apiVersion", 0) < 1):
			self._logger.warning("SpoolManagerExtended reports an unsupported usage apiVersion, ignoring it")
			return None
		if (self._isUsageFromThisPrintJob(usage, printStartDateTime) == False):
			return None
		return usage


	# The snapshot only becomes this job's once SpoolManagerExtended has booked it, and
	# OctoPrint guarantees no order between two plugins' PRINT_DONE handlers. Running first
	# means reading the PREVIOUS job's snapshot, which would silently overwrite good values
	# with stale ones - worse than having no snapshot at all, because the fallback path is
	# then skipped. A snapshot captured before this print even started cannot be ours.
	def _isUsageFromThisPrintJob(self, usage, printStartDateTime):
		if (printStartDateTime == None):
			return True

		capturedAt = usage.get("capturedAt")
		if (StringUtils.isEmpty(capturedAt) == True):
			self._logger.warning("SpoolManagerExtended usage has no 'capturedAt', cannot tell whether it belongs to this print job. Falling back to the odometer.")
			return False

		try:
			capturedAtDateTime = datetime.datetime.fromisoformat(capturedAt)
		except Exception as e:
			self._logger.warning("Could not parse 'capturedAt' '" + str(capturedAt) + "' from SpoolManagerExtended: " + str(e))
			return False

		if (capturedAtDateTime < printStartDateTime):
			self._logger.info("SpoolManagerExtended has not booked this print job yet (snapshot from '" + str(capturedAt) + "' is older than this print's start). Reading the odometer instead.")
			return False
		return True

	# dict of this
	# {u'tool4': {u'volume': 185.20129656279946, u'length': 76997.75167999369},
	#  u'tool3': {u'volume': 0.0, u'length': 0.0}, u'tool2': {u'volume': 0.0, u'length': 0.0},
	#  u'tool1': {u'volume': 0.0, u'length': 0.0}, u'tool0': {u'volume': 0.0, u'length': 0.0}}

	# The sliced usage per PHYSICAL tool, as SpoolManagerExtended resolves it - same shape as
	# OctoPrint's analysis["filament"]. Returns None when the peer does not offer it (older
	# version, not installed) or knows nothing about the job; the caller then falls back to
	# OctoPrint's analysis.
	def _readJobFilamentUsageFromPeer(self, origin, path):
		if (self._isSpoolManagerInstalledAndEnabled() == False):
			return None
		if (hasattr(self._spoolManagerPluginImplementation, "api_getJobFilamentUsage") == False):
			return None

		try:
			usage = self._spoolManagerPluginImplementation.api_getJobFilamentUsage(origin, path)
		except Exception as e:
			self._logger.error("Could not read the job's filament usage from SpoolManagerExtended: " + str(e))
			return None

		if (usage == None or len(usage) == 0):
			return None
		self._logger.info("Calculated filament per tool from SpoolManagerExtended: " + str(usage))
		return usage

	def _readCalculatedFilamentMetaData(self, fileData):
		filamentAnalyseDict = None
		# No meta data at all (printer-hosted file) is not the same as meta data without a
		# filament analysis - both end up without a calculated length, though. A file on a
		# Marlin printer's SD card carries the key with None as its value (K9, 2026-10-03).
		analysis = fileData.get("analysis") if isinstance(fileData, dict) else None
		if (isinstance(analysis, dict) and isinstance(analysis.get("filament"), dict)):
			filamentAnalyseDict = analysis["filament"]
		if (filamentAnalyseDict == None):
			self._logger.info("There is no calculated filament data in meta-file")
		return filamentAnalyseDict

	# read the selected tools
	# return  {
	# 'tool0': {'databaseId': 4711, 'spoolName': 'NewSpool', 'weight': 2000.0, 'spoolCost': 123.2, 'material': 'PLA', 'vendor: 'MaterMost', 'density': 4.25, 'diameter': 1.75, },
	# 'tool1': {}
	# },
	def _getSelectedSpools(self):
		if (self._isSpoolManagerInstalledAndEnabled() == False):
			self._logger.info("There is no plugin for spool selection. Installed and Enabled?")
			return None

		self._logger.info("Try reading filament from SpoolManagerExtended...")
		try:
			selectedSpoolInformations = self._spoolManagerPluginImplementation.api_getSelectedSpoolInformations()
		except Exception as e:
			self._logger.error("Could not read selected spools from SpoolManagerExtended: " + str(e))
			return None

		if (selectedSpoolInformations == None):
			return None

		result = {}
		for spoolData in selectedSpoolInformations:
			if (spoolData == None):
				continue
			toolId = "tool" + str(spoolData["toolIndex"])
			result[toolId] = {
				"databaseId": spoolData["databaseId"],
				"spoolName": spoolData["spoolName"],
				"material": spoolData["material"],
				"vendor": spoolData["vendor"],
				"density": spoolData["density"],
				"diameter": spoolData["diameter"],
				"spoolCost": spoolData["cost"],
				"weight": spoolData["weight"]
			}
			self._logger.info(
				" reading for '" + toolId + "',  Spool: '" + str(spoolData["spoolName"]) + "', Material: '" + str(spoolData["material"]) + "', Vendor: '" + str(spoolData["vendor"]) + "'")
		return result

	# The "total" row lists every distinct spool / vendor / material of the job as one string.
	def _appendUniqueToCommaList(self, currentList, value):
		if (value == None or value == ""):
			return currentList
		if ((value in currentList) == True):
			return currentList
		if (currentList != ""):
			return currentList + ", " + value
		return currentList + value


	def _calculateFilamentWeightForLength(self, usedLength, diameter, density):
		result = 0.0
		if (usedLength != None and diameter != None and density != None):
			radius = diameter / 2.0
			volume = usedLength * math.pi * radius * radius / 1000.0
			result = volume * density
		return result


	def _updatePrintJobModelWithLayerHeightInfos(self, dlpPayload):
		# DisplayLayerProgress reports the height while no print is running as well, e.g. when
		# the Z axis is jogged by hand. There is no job to attribute it to then.
		if (self._currentPrintJobModel == None):
			return
		totalLayers = dlpPayload["totalLayer"]
		currentLayer = dlpPayload["currentLayer"]
		self._currentPrintJobModel.printedLayers = currentLayer + " / " + totalLayers

		totalHeight = dlpPayload["totalHeightFormatted"]
		currentHeight = dlpPayload["currentHeightFormatted"]
		self._currentPrintJobModel.printedHeight = currentHeight + " / " + totalHeight


	def _createPrintJobModel(self, payload):
		self._currentPrintJobModel = PrintJobModel()
		self._currentPrintJobModel.printStartDateTime = datetime.datetime.now()
		# Per instance, because the attribute is declared on the class - a leftover from the
		# previous job would otherwise be attributed to this one.
		self._currentPrintJobModel.printTemperatures = None
		self._currentPrintJobModel.calculatedFilamentAtStart = None
		self._currentPrintJobModel.previewImageAtStart = None

		self._currentPrintJobModel.fileOrigin = payload["origin"]
		self._currentPrintJobModel.fileName = payload["name"]
		self._currentPrintJobModel.filePathName = payload["path"]

		# self._file_manager.path_on_disk()
		# OctoPrint only puts "owner" in the payload when the job actually has one, and
		# "user" only when a user triggered the action. A print started at the printer
		# itself - the normal case for a connector printer like Bambu - has neither.
		# Leave the name empty then instead of inventing one: the edit dialog fills in the
		# current user on save, and a placeholder name would defeat that and claim someone
		# printed this who did not.
		userName = payload.get("owner")
		if (StringUtils.isEmpty(userName) == True):
			userName = payload.get("user")
		self._currentPrintJobModel.userName = userName if StringUtils.isNotEmpty(userName) else None
		self._currentPrintJobModel.fileSize = payload["size"]

		# readTemperatureFromPrinter
		# because temperature is 0 at the beginning, we need to wait a couple of seconds (maybe 3)
		self._readAndAssignCurrentTemperatureDelayed(self._currentPrintJobModel)


	# Reading the temperature once, a fixed delay after the start, is a guess about how long
	# the printer needs. A Bambu calibrates first and ramps its target up in steps, so after
	# 60 seconds the nozzle target was still 140 instead of the 220 it printed with.
	# The highest target of the print is no better: the A1 mini purges its nozzle at 250
	# before printing PLA at 220, and the job was stored with 250. So every sample counts
	# the target it saw, and the target held for most of the print is the print temperature
	# (see _resolvePrintTemperatures). Works the same on Marlin, Klipper and connector printers.
	#
	# Every tool the printer reports is tracked, not just one: which tools the job actually
	# used is only known at the end (see _resolveTemperatureToolIds).
	def _trackPrintTemperaturesAsync(self, printer, printJobModel, delayInSeconds, intervalInSeconds):
		time.sleep(delayInSeconds)

		samplesBySensor = {}
		while (self._currentPrintJobModel is printJobModel):
			try:
				currentTemps = printer.get_current_temperatures()
				if (currentTemps != None):
					self._collectTemperatureSample(samplesBySensor, currentTemps)
			except Exception as e:
				self._logger.warning("Could not read the current temperature: " + str(e))

			# Publish after every sample, not just at the end: PRINT_DONE can arrive while
			# this thread sleeps, and a job stored in that window would get no temperature.
			# A fresh dict, so the capture never sees one this thread is still changing.
			if (len(samplesBySensor) > 0):
				printJobModel.printTemperatures = self._resolvePrintTemperatures(samplesBySensor)

			if (printer.is_printing() == False and printer.is_paused() == False):
				break
			time.sleep(intervalInSeconds)

		# Park the result on the model instead of writing it: temperatures are only persisted
		# by insertPrintJob, and this thread can still be running when the job is stored.
		# _capturePrintJobData picks it up at the one moment where it is guaranteed to count.
		printTemperatures = self._resolvePrintTemperatures(samplesBySensor)
		self._logger.info("Print temperatures (the target held longest): " + str(printTemperatures))
		printJobModel.printTemperatures = printTemperatures


	# samplesBySensor: {"tool0": {"count": 12, "targets": {220.0: (10, 12), 250.0: (2, 2)},
	# "highestActual": 221.4}, ...} - per target how many samples saw it, and the number of
	# the last sample that did.
	def _collectTemperatureSample(self, samplesBySensor, currentTemps):
		for sensorName in currentTemps:
			# The Moonraker connector files every Klipper heater it has no name for under None,
			# bed included, and logged a warning every 15 s - enough to overflow the technical log.
			if (isinstance(sensorName, str) == False):
				continue
			if ((sensorName == "bed" or sensorName.startswith("tool")) == False):
				continue

			samples = samplesBySensor.setdefault(sensorName, {"count": 0, "targets": {}, "highestActual": None})
			samples["count"] = samples["count"] + 1
			# The target is what the job asked for; actual only reaches it, and overshoots.
			# A target of 0 is a heater that is off - a parked head, the cool-down after the
			# last layer - and says nothing about the print temperature.
			target = currentTemps[sensorName].get("target")
			if (target != None and target != 0):
				seenCount = samples["targets"].get(target, (0, 0))[0]
				samples["targets"][target] = (seenCount + 1, samples["count"])
				continue
			actual = currentTemps[sensorName].get("actual")
			if (actual != None and (samples["highestActual"] == None or actual > samples["highestActual"])):
				samples["highestActual"] = actual


	# The target seen in the most samples; on a tie the later one, because a purge or a
	# calibration step comes before the print, not after it. Actual only for a sensor that
	# never reported a target at all, and then the highest one, as before.
	def _resolvePrintTemperatures(self, samplesBySensor):
		printTemperatures = {}
		for sensorName, samples in samplesBySensor.items():
			targets = samples["targets"]
			if (len(targets) > 0):
				printTemperatures[sensorName] = max(targets, key=lambda target: targets[target])
			elif (samples["highestActual"] != None):
				printTemperatures[sensorName] = samples["highestActual"]
		return printTemperatures


	def _readAndAssignCurrentTemperatureDelayed(self, printJobModel):
		# The delay setting keeps its meaning: nothing is sampled before it has passed, so a
		# printer that reports garbage right after the start is still skipped.
		delayInSeconds = self._settings.get_int([SettingsKeys.SETTINGS_KEY_DELAY_READING_TEMPERATURE_FROM_PRINTER])
		thread = threading.Thread(name='TrackPrintTemperatures',
								  target=self._trackPrintTemperaturesAsync,
								  args=(self._printer, printJobModel, delayInSeconds, 15,))
		thread.daemon = True
		thread.start()
		pass


	# The tools whose temperature belongs to this job: the ones it has a calculated length
	# for. A parked head on a tool changer is heated too, and recording that one as "the"
	# nozzle temperature is wrong. Without any calculated data (unsliced file, no analysis)
	# there is nothing to go by, so the configured default tool is used as before.
	def _resolveTemperatureToolIds(self, printJobModel):
		toolIds = []
		for filamentModel in printJobModel.getFilamentModels(withoutTotal=True):
			calculatedLength = StringUtils.transformToFloatOrZero(filamentModel.calculatedLength)
			if (calculatedLength > 0 and (filamentModel.toolId in toolIds) == False):
				toolIds.append(filamentModel.toolId)
		if (len(toolIds) == 0):
			toolIds.append(self._settings.get([SettingsKeys.SETTINGS_KEY_DEFAULT_TOOL_ID]))  # "tool0"
		return sorted(toolIds)


	# One bed and ONE nozzle row: the dialog shows and edits a single nozzle temperature and
	# writes it back to every tool row. Of the tools the job used, the hottest reported one
	# is the nozzle temperature of the job. "-" when the printer reported none of them -
	# some connectors only report their first extruder, and an honest "unknown" beats
	# another head's value.
	def _addTemperaturesToPrintModel(self, printJobModel, printTemperatures, toolIds):
		nozzleToolId = toolIds[0]
		nozzleTemperature = None
		for toolId in toolIds:
			temperature = printTemperatures.get(toolId)
			if (temperature != None and (nozzleTemperature == None or temperature > nozzleTemperature)):
				nozzleToolId = toolId
				nozzleTemperature = temperature
		if (nozzleTemperature == None):
			self._logger.info("The printer reported no temperature for " + str(toolIds) + " during this print")

		bedTemperature = printTemperatures.get("bed")
		for (sensorName, temperature) in [("bed", bedTemperature), (nozzleToolId, nozzleTemperature)]:
			tempModel = TemperatureModel()
			tempModel.sensorName = sensorName
			tempModel.sensorValue = temperature if temperature != None else "-"
			printJobModel.addTemperatureModel(tempModel)


	# SpoolManagerExtended books the per-tool usage of a finished job a moment AFTER our own
	# PRINT_DONE handler has stored it, so the stored usedLength is 0 on printers whose
	# extrusion the odometer never sees. This event carries the booked numbers.
	def _onSpoolUsageBookedByPeer(self, payload):
		if (payload == None):
			return

		# A SpoolManagerExtended that reports the whole job (print_job_usage_booked, shipped
		# together with api_getJobFilamentUsage) makes this event redundant, and wrong for a
		# job that was paused: it only carries what was booked since the last pause, while
		# the report adds up the whole job. Applied after the report it would replace the
		# job's total with that last part.
		if (self._doesPeerReportWholePrintJobs() == True):
			self._logger.info("Ignoring spool usage event for tool '" + str(payload.get("toolId")) + "' (status '" + str(payload.get("printStatus")) + "'), SpoolManagerExtended reports the whole print job separately")
			return

		# The same event is fired for a spool change in the MIDDLE of a print (the
		# selectSpool endpoint commits the odometer before switching), and there is no
		# printStatus then. Booking that as a job end would count the partial usage twice
		# once the print really finishes. A SpoolManagerExtended without the usage fields
		# does not send printStatus either, so this doubles as the version check.
		printStatus = payload.get("printStatus")
		if (printStatus == None):
			self._logger.info("Ignoring spool usage event without printStatus (spool change during a print, or a SpoolManagerExtended that does not report per-job usage yet)")
			return

		# A selected tool the job never used reports 0.0, not None, and fires an event like
		# any other. Storing that would add empty tool rows to a multi-tool job.
		usedLength = StringUtils.transformToFloatOrNone(payload.get("usedLength"))
		if (usedLength == None or usedLength == 0.0):
			self._logger.info("Ignoring spool usage event for tool '" + str(payload.get("toolId")) + "' without used filament")
			return

		databaseId = self._readBackfillTarget("spool usage for tool '" + str(payload.get("toolId")) + "'")
		if (databaseId == None):
			return

		try:
			self._backfillFilamentUsage(databaseId, payload)
		except Exception as e:
			self._logger.exception("Could not backfill the filament usage of print job '" + str(databaseId) + "': " + str(e))


	# SpoolManagerExtended's report of a whole job end, sent once it has booked the job -
	# whichever of the two PRINT_DONE handlers ran first. It carries every tool at once plus
	# the job it belongs to, so it neither depends on the handler order nor on timing to be
	# attributed. The per-tool event above still arrives too (and from older versions only
	# that one); both assign the same values, so the order they come in does not matter.
	def _onPrintJobUsageBookedByPeer(self, payload):
		if (payload == None):
			return
		if (payload.get("apiVersion", 0) < 1):
			self._logger.warning("SpoolManagerExtended reports an unsupported usage apiVersion, ignoring its print job report")
			return

		# null = no spool on that tool, or nothing booked for it. A zero length would add
		# an empty row for a tool the job never used.
		allToolUsages = []
		for toolUsage in (payload.get("tools") or []):
			if (toolUsage == None):
				continue
			usedLength = StringUtils.transformToFloatOrNone(toolUsage.get("usedLength"))
			if (usedLength == None or usedLength == 0.0):
				continue
			allToolUsages.append(toolUsage)

		if (len(allToolUsages) == 0):
			self._logger.info("SpoolManagerExtended booked no filament for this print job (status '" + str(payload.get("printStatus")) + "')")
			return

		databaseId = self._readBackfillTarget("a print job usage report")
		if (databaseId == None):
			return

		try:
			self._backfillPrintJobUsage(databaseId, payload, allToolUsages)
		except Exception as e:
			self._logger.exception("Could not backfill the filament usage of print job '" + str(databaseId) + "': " + str(e))


	# The job still waiting for its usage, or None when there is none or the report came too
	# late to belong to it.
	def _readBackfillTarget(self, whatArrived):
		with self._backfillLock:
			databaseId = self._backfillTargetDatabaseId
			printEndDateTime = self._backfillTargetPrintEndDateTime

		if (databaseId == None):
			self._logger.info("Received " + whatArrived + ", but no print job is waiting for it")
			return None

		if (printEndDateTime != None):
			ageInSeconds = (datetime.datetime.now() - printEndDateTime).total_seconds()
			if (ageInSeconds > PrintJobHistoryExtendedPlugin.BACKFILL_MAX_AGE_IN_SECONDS):
				self._logger.warning("Received " + whatArrived + " '" + str(ageInSeconds) + "' seconds after the print ended, too late to belong to it. Ignoring it.")
				return None
		return databaseId


	# The report names its job. A mismatch means it belongs to another print than the one
	# waiting, and writing it would corrupt that one. A report without the job (older
	# version) is accepted: the waiting window is then the only check, as for the per-tool
	# event.
	def _isUsageReportForPrintJob(self, job, printJobModel):
		if (job == None):
			return True

		reportedPath = job.get("path")
		if (StringUtils.isNotEmpty(reportedPath) and StringUtils.isNotEmpty(printJobModel.filePathName)
			and reportedPath != printJobModel.filePathName):
			self._logger.warning("Usage report is for '" + str(reportedPath) + "', but the waiting print job is '" + str(printJobModel.filePathName) + "'. Ignoring it.")
			return False

		reportedStart = job.get("printStartDateTime")
		if (StringUtils.isNotEmpty(reportedStart) and printJobModel.printStartDateTime != None):
			try:
				reportedStartDateTime = datetime.datetime.fromisoformat(reportedStart)
			except Exception as e:
				self._logger.warning("Could not parse 'printStartDateTime' '" + str(reportedStart) + "' of the usage report: " + str(e))
				return True
			differenceInSeconds = abs((reportedStartDateTime - printJobModel.printStartDateTime).total_seconds())
			if (differenceInSeconds > PrintJobHistoryExtendedPlugin.USAGE_REPORT_START_TOLERANCE_IN_SECONDS):
				self._logger.warning("Usage report is for a print started at '" + str(reportedStart) + "', but the waiting print job started at '" + str(printJobModel.printStartDateTime) + "'. Ignoring it.")
				return False
		return True


	def _backfillPrintJobUsage(self, databaseId, payload, allToolUsages):
		printJobModel = self._databaseManager.loadPrintJob(databaseId)
		if (printJobModel == None):
			self._logger.warning("Could not backfill filament usage, print job '" + str(databaseId) + "' is gone")
			return
		if (self._isUsageReportForPrintJob(payload.get("job"), printJobModel) == False):
			return

		for toolUsage in allToolUsages:
			toolId = "tool" + str(toolUsage.get("toolIndex"))
			filamentModel = self._assignToolUsage(printJobModel, toolId, toolUsage)
			self._assignToolSpools(filamentModel, toolUsage)
			self._logger.info("Backfilled " + toolId + " of print job '" + str(databaseId) + "' from the job report: usedLength='" + str(filamentModel.usedLength) + "'; usedWeight='" + str(filamentModel.usedWeight) + "'; usedCost='" + str(filamentModel.usedCost) + "' (source '" + str(toolUsage.get("source", payload.get("source"))) + "')")

		self._storeBackfilledPrintJob(databaseId, printJobModel)


	# One event per tool, so this patches exactly one tool and then re-derives everything
	# that depends on all of them. Values are assigned, never accumulated, so receiving the
	# same event twice changes nothing.
	def _backfillFilamentUsage(self, databaseId, payload):
		toolId = "tool" + str(payload.get("toolId"))

		printJobModel = self._databaseManager.loadPrintJob(databaseId)
		if (printJobModel == None):
			self._logger.warning("Could not backfill filament usage, print job '" + str(databaseId) + "' is gone")
			return

		filamentModel = self._assignToolUsage(printJobModel, toolId, payload)
		self._logger.info("Backfilled " + toolId + " of print job '" + str(databaseId) + "': usedLength='" + str(filamentModel.usedLength) + "'; usedWeight='" + str(filamentModel.usedWeight) + "'; usedCost='" + str(filamentModel.usedCost) + "' (source '" + str(payload.get("source")) + "')")

		self._storeBackfilledPrintJob(databaseId, printJobModel)


	# Assigns one tool's booked usage (usedLength, usedWeight, usedCost) to the job.
	def _assignToolUsage(self, printJobModel, toolId, usage):
		# Always through getFilamentModelByToolId: it forces the per-instance model dict to
		# be loaded before anything is changed.
		filamentModel = printJobModel.getFilamentModelByToolId(toolId)
		if (filamentModel == None):
			filamentModel = FilamentModel()
			filamentModel.toolId = toolId
			printJobModel.addFilamentModel(filamentModel)

		filamentModel.usedLength = StringUtils.transformToFloatOrNone(usage.get("usedLength"))
		filamentModel.usedWeight = StringUtils.transformToFloatOrNone(usage.get("usedWeight"))
		filamentModel.usedCost = StringUtils.transformToFloatOrNone(usage.get("usedCost"))

		# usedWeight stays None when the spool carries no diameter/density. We may still be
		# able to derive it from the spool data we captured before the peer booked - but only
		# when both are known: _calculateFilamentWeightForLength returns 0.0 for a missing
		# one, and storing 0.0 would claim "used nothing" where the truth is "unknown".
		if (filamentModel.usedWeight == None and filamentModel.usedLength != None and
			filamentModel.diameter != None and filamentModel.density != None):
			filamentModel.usedWeight = self._calculateFilamentWeightForLength(filamentModel.usedLength,
																			  filamentModel.diameter,
																			  filamentModel.density)

		return filamentModel


	# The spool(s) the report booked the tool's usage on. Several when the spool was changed
	# during the job: the row then names all of them, in the order they were used. A row the
	# capture could not fill takes the spool from the report.
	def _assignToolSpools(self, filamentModel, toolUsage):
		allSpools = [spool for spool in (toolUsage.get("spools") or []) if spool != None]
		if (len(allSpools) > 1):
			allSpoolNames = ""
			allVendors = ""
			allMaterials = ""
			for spool in allSpools:
				allSpoolNames = self._appendUniqueToCommaList(allSpoolNames, spool.get("spoolName"))
				allVendors = self._appendUniqueToCommaList(allVendors, spool.get("vendor"))
				allMaterials = self._appendUniqueToCommaList(allMaterials, spool.get("material"))
			filamentModel.spoolName = allSpoolNames
			filamentModel.vendor = allVendors
			filamentModel.material = allMaterials
			return

		if (StringUtils.isEmpty(filamentModel.spoolName) and StringUtils.isNotEmpty(toolUsage.get("spoolName"))):
			filamentModel.spoolName = toolUsage.get("spoolName")
			filamentModel.vendor = toolUsage.get("vendor")
			filamentModel.material = toolUsage.get("material")
			if (filamentModel.diameter == None):
				filamentModel.diameter = StringUtils.transformToFloatOrNone(toolUsage.get("diameter"))
			if (filamentModel.density == None):
				filamentModel.density = StringUtils.transformToFloatOrNone(toolUsage.get("density"))

	# SpoolManagerExtended sends print_job_usage_booked once per job end;
	# api_getJobFilamentUsage came with it, so its presence tells the two versions apart.

	def _doesPeerReportWholePrintJobs(self):
		if (self._isSpoolManagerInstalledAndEnabled() == False):
			return False
		return hasattr(self._spoolManagerPluginImplementation, "api_getJobFilamentUsage")

	def _storeBackfilledPrintJob(self, databaseId, printJobModel):
		self._recalculateTotalFilamentModel(printJobModel)
		self._recalculateCostsInPlace(printJobModel)

		self._databaseManager.updatePrintJob(printJobModel)

		# Refresh the table and let an open dialog drop its "still measuring" hint. The
		# dialog is not re-shown: it has no dirty tracking, so that would throw away
		# anything the user has typed meanwhile.
		self._sendDataToClient(dict(action="reloadTableItems"))
		self._sendDataToClient(dict(action="filamentUsageArrived",
									databaseId=databaseId,
									printJobItem=TransformPrintJob2JSON.transformPrintJobModel(printJobModel, self._file_manager)))


	# Re-derived from scratch on every backfill so that several per-tool events, in any
	# order, converge on the same totals.
	def _recalculateTotalFilamentModel(self, printJobModel):
		totalFilamentModel = printJobModel.getFilamentModelByToolId("total")
		if (totalFilamentModel == None):
			# A capture that failed half way stored the job without its "total" row - the
			# only one the table and the dialog show. The usage would be invisible without it.
			totalFilamentModel = FilamentModel()
			totalFilamentModel.toolId = "total"
			printJobModel.addFilamentModel(totalFilamentModel)

		usedTotalLength = None
		usedTotalWeight = 0.0
		usedTotalCost = 0.0
		allSpoolNames = ""
		allVendors = ""
		allMaterials = ""
		for filamentModel in printJobModel.getFilamentModels(withoutTotal=True):
			allSpoolNames = self._appendUniqueToCommaList(allSpoolNames, filamentModel.spoolName)
			allVendors = self._appendUniqueToCommaList(allVendors, filamentModel.vendor)
			allMaterials = self._appendUniqueToCommaList(allMaterials, filamentModel.material)
			if (filamentModel.usedLength == None):
				continue
			if (usedTotalLength == None):
				usedTotalLength = 0.0
			usedTotalLength = usedTotalLength + StringUtils.transformToFloatOrZero(filamentModel.usedLength)
			usedTotalWeight = usedTotalWeight + StringUtils.transformToFloatOrZero(filamentModel.usedWeight)
			usedTotalCost = usedTotalCost + StringUtils.transformToFloatOrZero(filamentModel.usedCost)

		if (usedTotalLength != None):
			totalFilamentModel.usedLength = usedTotalLength
			totalFilamentModel.usedWeight = usedTotalWeight
			totalFilamentModel.usedCost = usedTotalCost

		# Like the capture: the "total" row lists every spool of the job. A report may have
		# named another spool (changed during the job) or filled a row the capture left empty.
		if (allSpoolNames != ""):
			totalFilamentModel.spoolName = allSpoolNames
			totalFilamentModel.vendor = allVendors
			totalFilamentModel.material = allMaterials


	# _addCostsToPrintModel() cannot be reused for a backfill: it always builds a NEW
	# CostModel, and CostModel.printJob is unique, so the second row would fail and roll the
	# whole transaction back - losing the usage as well. Printer and electricity cost are
	# pure functions of the unchanged print window and come out identical.
	def _recalculateCostsInPlace(self, printJobModel):
		printTimeInSeconds = DateTimeUtils.calcDurationInSeconds(printJobModel.printEndDateTime,
																 printJobModel.printStartDateTime)
		allFilamentModels = printJobModel.getFilamentModels(withoutTotal=True)
		costData = self._calculateCostData(allFilamentModels, printTimeInSeconds,
										   printJobModel.printStartDateTime, printJobModel.printEndDateTime)

		costModel = printJobModel.getCosts()
		if (costModel == None):
			costModel = CostModel()
			printJobModel.setCosts(costModel)

		costModel.totalCosts = costData["totalCosts"]
		costModel.filamentCost = costData["filamentCost"]
		costModel.electricityCost = costData["electricityCost"]
		costModel.electricityKwh = costData["electricityKwh"]
		costModel.printerCost = costData["printerCost"]
		costModel.costSource = costData["costSource"]
		costModel.withDefaultSpoolValues = costData["withDefaultSpoolValues"]
		self._logger.info("Recalculated costs after backfill: " + str(costData))


	def _addCostsToPrintModel(self, printJobModel):

		self._logger.info("----- Start reading costs -----")

		printTimeInSeconds = DateTimeUtils.calcDurationInSeconds(printJobModel.printEndDateTime, printJobModel.printStartDateTime)
		allFilamentModels = printJobModel.getFilamentModels(withoutTotal=True)
		costData = self._calculateCostData(allFilamentModels, printTimeInSeconds,
										   printJobModel.printStartDateTime, printJobModel.printEndDateTime)

		costModel = CostModel()
		costModel.totalCosts = costData["totalCosts"]
		costModel.filamentCost = costData["filamentCost"]
		costModel.electricityCost = costData["electricityCost"]
		costModel.electricityKwh = costData["electricityKwh"]
		costModel.printerCost = costData["printerCost"]
		costModel.costSource = costData["costSource"]
		costModel.withDefaultSpoolValues = costData["withDefaultSpoolValues"]

		printJobModel.setCosts(costModel)
		self._logger.info("Adding costs: " + str(costData))


	# Printer cost used to be read out of the CostEstimation plugin's settings on every
	# calculation. Those values are now owned here, so carry them over once instead of
	# making the user retype what they already configured.
	def _importCostEstimationSettingsOnce(self):
		if (self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_COSTESTIMATION_IMPORTED]) == True):
			return
		if (self._isCostEstimationInstalledAndEnabled() == False):
			return

		try:
			foreignSettings = self._costEstimationPluginImplementation._settings
			importedKeys = [
				(SettingsKeys.SETTINGS_KEY_PRINTER_PURCHASE_PRICE, "priceOfPrinter"),
				(SettingsKeys.SETTINGS_KEY_PRINTER_LIFESPAN_HOURS, "lifespanOfPrinter"),
				(SettingsKeys.SETTINGS_KEY_PRINTER_MAINTENANCE_PER_HOUR, "maintenanceCosts"),
				(SettingsKeys.SETTINGS_KEY_ELECTRICITY_COST_PER_KWH, "costOfElectricity"),
				(SettingsKeys.SETTINGS_KEY_CURRENCY_SYMBOL, "currency"),
				(SettingsKeys.SETTINGS_KEY_CURRENCY_FORMAT, "currencyFormat")
			]
			for ownKey, foreignKey in importedKeys:
				value = foreignSettings.get([foreignKey])
				if (StringUtils.isNotEmpty(value)):
					self._settings.set([ownKey], value)
					self._logger.info("Imported '" + foreignKey + "' from CostEstimation as '" + ownKey + "'")
		except Exception as e:
			self._logger.error("Could not import settings from CostEstimation: " + str(e))
			return

		self._settings.set_boolean([SettingsKeys.SETTINGS_KEY_COSTESTIMATION_IMPORTED], True)
		self._settings.save()
		self._sendMessageToClient("info", "Cost settings imported",
								  "Printer cost settings were taken over from the CostEstimation plugin. This plugin no longer needs it.")

	# Tasmota has no notion of "this plug powers the printer", so the user has to pick one.
	# Its plugs are identified by the pair (ip, idx); the label is only for display.
	def _readTasmotaPlugs(self):
		if (self._isTasmotaInstalledAndEnabled() == False):
			return []

		result = []
		try:
			configuredPlugs = self._settings.global_get(["plugins", "tasmota", "arrSmartplugs"])
			if (configuredPlugs == None):
				return []
			for plug in configuredPlugs:
				plugIp = plug.get("ip")
				plugIdx = str(plug.get("idx", "1"))
				if (StringUtils.isEmpty(plugIp)):
					continue
				label = plug.get("label")
				if (StringUtils.isEmpty(label)):
					label = plugIp
				result.append({
					"ip": plugIp,
					"idx": plugIdx,
					"label": label + " (" + plugIp + ":" + plugIdx + ")"
				})
		except Exception as e:
			self._logger.warning("Could not read Tasmota plug list: " + str(e))
			return []
		return result

	# Measuring electricity needs samples inside the print window, but the Tasmota plugin
	# polls only every few minutes and ships with polling switched off entirely.
	def _checkTasmotaSetup(self):
		if (self._isTasmotaInstalledAndEnabled() == False):
			return
		plugIp = self._settings.get([SettingsKeys.SETTINGS_KEY_TASMOTA_PLUG_IP])
		if (StringUtils.isEmpty(plugIp)):
			return

		# The plug is stored by its address and does not follow a change in Tasmota's own
		# list. The settings then show "None" while the old address is still asked for, and
		# every print ends without electricity cost and without a hint why (K9, 2026-10-03).
		allPlugIps = [plug["ip"] for plug in self._readTasmotaPlugs()]
		if ((plugIp in allPlugIps) == False):
			self._logger.warning("The Tasmota plug '" + str(plugIp) + "' selected for electricity measurement is not configured in the Tasmota plugin (configured: " + str(allPlugIps) + ")")
			self._sendMessageToClient("notice", "Tasmota plug not found",
									  "The plug " + str(plugIp) + " selected for measuring electricity is no longer configured in the Tasmota plugin. Select the printer's plug again in the Print Job History Extended settings.")
			return

		if (self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_NO_NOTIFICATION_TASMOTA_POLLING]) == True):
			return

		# Note these are the snake_case keys Tasmota's defaults and its polling timer use.
		pollingEnabled = self._settings.global_get(["plugins", "tasmota", "polling_enabled"])
		pollingInterval = self._settings.global_get(["plugins", "tasmota", "polling_interval"])

		if (pollingEnabled != True):
			self._logger.warning("Tasmota polling is disabled, electricity cost cannot be measured")
			self._sendMessageToClient("notice", "Tasmota polling is disabled",
									  "Enable polling in the Tasmota plugin settings, otherwise no electricity cost can be recorded.")
		elif (StringUtils.transformToFloatOrZero(pollingInterval) > 1):
			self._logger.info("Tasmota polling interval is '" + str(pollingInterval) + "' minutes, short prints may not be measurable")
			self._sendMessageToClient("notice", "Tasmota polling interval is coarse",
									  "Polling every " + str(pollingInterval) + " minutes means short prints record too few samples. A 1 minute interval is recommended.")

	# Energy consumed during the print window, in kWh, or None when it cannot be determined.
	#
	# The Tasmota plugin exposes no helper API, so its energy database is read directly. Its
	# 'total' column is the plug's cumulative kWh counter, which makes the consumption of any
	# window the difference between the last and the first sample inside it.
	def _readMeasuredEnergyKwh(self, printStartDateTime, printEndDateTime):
		if (printStartDateTime is None or printEndDateTime is None):
			return None
		if (self._isTasmotaInstalledAndEnabled() == False):
			return None

		plugIp = self._settings.get([SettingsKeys.SETTINGS_KEY_TASMOTA_PLUG_IP])
		plugIdx = self._settings.get([SettingsKeys.SETTINGS_KEY_TASMOTA_PLUG_IDX])
		if (StringUtils.isEmpty(plugIp) or StringUtils.isEmpty(plugIdx)):
			self._logger.info("No Tasmota plug selected, skipping electricity measurement")
			return None

		databaseLocation = os.path.join(self.get_plugin_data_folder(), "..", "tasmota", "energy_data.db")
		if (os.path.isfile(databaseLocation) == False):
			self._logger.info("Tasmota energy database not found at '" + databaseLocation + "'")
			return None

		# Tasmota timestamps are naive local time, and so are our print timestamps, so they
		# compare directly. Converting either side would break across a DST change.
		fromTimestamp = printStartDateTime.strftime("%Y-%m-%d %H:%M:%S.%f")
		toTimestamp = printEndDateTime.strftime("%Y-%m-%d %H:%M:%S.%f")

		connection = None
		try:
			connection = sqlite3.connect("file:" + pathname2url(os.path.abspath(databaseLocation)) + "?mode=ro",
										 uri=True, timeout=2.0)
			row = connection.execute(
				"SELECT MIN(total), MAX(total), COUNT(*) FROM energy_data "
				"WHERE ip = ? AND idx = ? AND timestamp BETWEEN ? AND ?",
				(plugIp, str(plugIdx), fromTimestamp, toTimestamp)).fetchone()
		except Exception as e:
			self._logger.warning("Could not read Tasmota energy database: " + str(e))
			return None
		finally:
			if (connection != None):
				connection.close()

		if (row is None or row[0] is None or row[1] is None):
			self._logger.info("No Tasmota energy samples recorded during the print for plug '" + str(plugIp) + ":" + str(plugIdx) + "'")
			return None

		# A single sample carries no difference, so it says nothing about consumption.
		sampleCount = row[2]
		if (sampleCount < 2):
			self._logger.info("Only " + str(sampleCount) + " Tasmota energy sample(s) during the print, too few to measure. Lower the Tasmota polling interval.")
			return None

		consumedKwh = row[1] - row[0]
		# The plug's counter restarts at zero when it is reset, which makes the window meaningless.
		if (consumedKwh < 0):
			self._logger.warning("Tasmota energy counter decreased during the print, ignoring the measurement")
			return None

		self._logger.info("Measured '" + str(consumedKwh) + "' kWh from " + str(sampleCount) + " Tasmota samples")
		return consumedKwh

	def _calculateCostData(self, allFilamentModels, printTimeInSeconds, printStartDateTime=None, printEndDateTime=None):
		#             var costData = {
		#                 totalCosts:
		#                 filamentCost: filamentCost,
		#                 electricityCost: electricityCost,
		#                 electricityKwh: electricityKwh,
		#                 printerCost: printerCost,
		#                 costSource: costSource,
		# 				  withDefaultSpoolValues
		#             }

		withDefaultSpoolValues = False
		printTimeInHours = printTimeInSeconds / 3600

		# calc: electricityCost, from energy the Tasmota plugin actually measured.
		# No estimation fallback on purpose: a guessed number is worse than an empty field.
		electricityCost = None
		electricityKwh = None
		costSource = "none"
		measuredKwh = self._readMeasuredEnergyKwh(printStartDateTime, printEndDateTime)
		if (measuredKwh is None):
			self._logger.info("No measured energy data available, electricity cost stays empty")
		else:
			costPerKwh = StringUtils.transformToFloatOrNone(
				self._settings.get([SettingsKeys.SETTINGS_KEY_ELECTRICITY_COST_PER_KWH]))
			electricityKwh = measuredKwh
			costSource = "tasmota"
			if (costPerKwh is None):
				self._logger.error("Measured '"+str(measuredKwh)+"' kWh, but no electricity price is configured")
			else:
				electricityCost = measuredKwh * costPerKwh
				self._logger.info("electricityCost '"+str(electricityCost)+"' = measured '"+str(measuredKwh)+"' kWh * costPerKwh '"+str(costPerKwh)+"'")

		# calc: printerCost
		printerCost = None
		priceOfPrinter = StringUtils.transformToFloatOrNone(
			self._settings.get([SettingsKeys.SETTINGS_KEY_PRINTER_PURCHASE_PRICE]))
		lifespanOfPrinter = StringUtils.transformToFloatOrNone(
			self._settings.get([SettingsKeys.SETTINGS_KEY_PRINTER_LIFESPAN_HOURS]))
		maintenancePerHour = StringUtils.transformToFloatOrNone(
			self._settings.get([SettingsKeys.SETTINGS_KEY_PRINTER_MAINTENANCE_PER_HOUR]))
		wearMultiplier = StringUtils.transformToFloatOrNone(
			self._settings.get([SettingsKeys.SETTINGS_KEY_PRINTER_WEAR_MULTIPLIER]))
		if (wearMultiplier is None):
			wearMultiplier = 1.0

		if (priceOfPrinter is None or lifespanOfPrinter is None or maintenancePerHour is None):
			self._logger.error(
				"Could not calculate printerCost, because purchasePrice or lifespanOfPrinter or maintenancePerHour is none")
		else:
			# 	value_when_true if condition else value_when_false
			depreciationPerHour = priceOfPrinter / lifespanOfPrinter if lifespanOfPrinter > 0 else 0
			printerCost = (depreciationPerHour + maintenancePerHour) * wearMultiplier * printTimeInHours
			self._logger.info("printerCost '"+str(printerCost)+"' = (depreciationPerHour '"+str(depreciationPerHour)+"' + maintenancePerHour '"+str(maintenancePerHour)+"') * wearMultiplier '"+str(wearMultiplier)+"' * printTimeInHours '"+str(printTimeInHours)+"'" )

		# calc: filamentCost
		filamentCost = None
		if (allFilamentModels == None):
			self._logger.error("No measured/needed filament present. FilamentCost calculation not possible. Maybe metadata not assigned in json file.")
		else:

			# Material cost per tool comes from SpoolManagerExtended, which knows the spool
			# price and the weight consumed. usedCost is already per-job; only sum it here.
			for filamentModel in allFilamentModels:
				toolId = filamentModel.toolId # tool0

				if (filamentModel.usedCost != None):
					filamentCost = StringUtils.transformToFloatOrZero(filamentCost) + filamentModel.usedCost
					self._logger.info("filamentCost for '"+toolId+"' = '"+str(filamentModel.usedCost)+"'")
					continue

				# No spool cost booked - fall back to the spool price and the weight we measured.
				usedLength = filamentModel.usedLength
				if (usedLength == None or usedLength == 0.0):
					self._logger.info("No filament calculation for '"+toolId+"', because usedLength is 0")
					continue

				if (filamentModel.spoolCost is None or filamentModel.weight is None or
					filamentModel.diameter is None or filamentModel.density is None):
					self._logger.info("No filament cost for '"+toolId+"', spool data is incomplete")
					withDefaultSpoolValues = True
					continue

				costPerWeight = filamentModel.spoolCost / filamentModel.weight
				volumeWeight = self._calculateFilamentWeightForLength(usedLength, filamentModel.diameter, filamentModel.density)
				filamentCost = StringUtils.transformToFloatOrZero(filamentCost) + (costPerWeight * volumeWeight)
				self._logger.info("filamentCost '"+str(filamentCost)+"' = costPerWeight '"+str(costPerWeight)+"' * volumeWeight '"+str(volumeWeight)+"'")

		# calc: totalCost

		totalCost = StringUtils.transformToFloatOrZero(filamentCost) + StringUtils.transformToFloatOrZero(electricityCost) + StringUtils.transformToFloatOrZero(printerCost)
		self._logger.info("totalcost = '"+str(totalCost)+"'")

		costData = dict(
						totalCosts=totalCost,
						filamentCost=filamentCost,
						electricityCost=electricityCost,
						electricityKwh=electricityKwh,
			 			printerCost=printerCost,
						costSource=costSource,
						withDefaultSpoolValues=withDefaultSpoolValues
		)
		return costData

	def _printJobStarted(self, payload):
		self._resetableFileLogHandler.resetLog()
		self._resetableFileLogHandler.startLogging()

		self._logger.info("PrintJob '" + payload["name"] + "' started!")

		self.alreadyCanceled = False
		self._m118SnapshotTaken = False

		# A new print invalidates the previous backfill window: a late usage event must
		# neither land on the job before last nor on the job now running.
		with self._backfillLock:
			self._backfillTargetDatabaseId = None
			self._backfillTargetPrintEndDateTime = None

		self._createPrintJobModel(payload)
		# The event thread lives for the whole print; a connection it kept would be long
		# dead by the time the job is captured.
		self._databaseManager.releaseThreadConnection()

		self._rememberFileDataAtStartAsync(self._currentPrintJobModel)


	# A printer-hosted file can only be read while the printer is connected. A job that fails
	# BECAUSE the connection dropped is captured after its printer storage is gone: A1 mini,
	# 2026-10-06, stored without its calculated 310 mm and without a preview. So both are read
	# once at the start as well, and the capture falls back to them. In a thread of its own:
	# the preview of a file on a Bambu printer means downloading the whole 3MF first.
	# A local file stays readable, and a job the connector adopted without knowing its file
	# ("???") has nothing to read yet.
	# Not right at the start: SpoolManagerExtended downloads and parses a Bambu 3MF it has
	# not seen yet in its own PrintStarted handler, and a second download of the same file at
	# the same moment could fail on the printer's FTPS. Once it is done, this is a cache hit.
	def _rememberFileDataAtStartAsync(self, printJobModel):
		if (printJobModel.fileOrigin == FileDestinations.LOCAL or self._isPlaceholderFilePath(printJobModel.filePathName)):
			return
		thread = threading.Thread(name="RememberFileDataAtStart",
								  target=self._rememberFileDataAtStart,
								  args=(printJobModel, printJobModel.fileOrigin, printJobModel.filePathName,
										REMEMBER_FILE_DATA_DELAY_IN_SECONDS))
		thread.daemon = True
		thread.start()


	def _rememberFileDataAtStart(self, printJobModel, fileOrigin, filePath, delayInSeconds=0):
		time.sleep(delayInSeconds)
		# Over already: the capture has read the file itself, or found it gone
		if (self._currentPrintJobModel is not printJobModel):
			return
		try:
			calculatedFilament = self._readJobFilamentUsageFromPeer(fileOrigin, filePath)
			if (calculatedFilament == None):
				calculatedFilament = self._readCalculatedFilamentMetaData(self._readFileMetaData(fileOrigin, filePath))
			printJobModel.calculatedFilamentAtStart = calculatedFilament
			printJobModel.previewImageAtStart = self._readFilePreviewImage(fileOrigin, filePath)
		except Exception as e:
			self._logger.warning("Could not read the file data at the start of the print: " + str(e))


	# The Bambu connector adopts a print it finds already running as path and name "???"
	def _isPlaceholderFilePath(self, filePath):
		return PrintJobUtils.isPlaceholderFilePath(filePath)

	#### print job finished
	# printStatus = "success", "failed", "canceled"
	def _printJobFinished(self, printStatus, payload):
		self._logger.info("PrintJob finished!")

		# Without a start there is no job to complete: OctoPrint restarted during the print,
		# or a connector reported the end of the same job a second time.
		if (self._currentPrintJobModel == None):
			self._logger.warning("Print job ended with status '" + printStatus + "', but no print job is running. Nothing to capture.")
			return

		self._adoptFileFromEndPayload(payload)
		payload = self._completePayloadFromJobStart(payload)

		# Start the capture without a connection left over on this thread (a stale one
		# fails with "server has gone away" before the retry in insertPrintJob is reached),
		# and do not leave one behind for the next print either.
		self._databaseManager.releaseThreadConnection()
		try:
			self._capturePrintJobData(printStatus, payload)
		finally:
			self._databaseManager.releaseThreadConnection()
			# The job is over, whatever the capture made of it. Anything still arriving for
			# it (a repeated end event, the layer reports of a hand-jogged Z axis) must not
			# land on it, nor be stored as another job.
			self._currentPrintJobModel = None


	# The opposite case: the start knew no file, the end does. After a reconnect the Bambu
	# connector found the printer still printing and fired PrintStarted for "???"; by the
	# end it had learned the real file from the printer (A1 mini, 2026-10-06). The job then
	# takes the file over from the end event instead of being stored as "???".
	def _adoptFileFromEndPayload(self, payload):
		printJobModel = self._currentPrintJobModel
		if (payload == None or self._isPlaceholderFilePath(printJobModel.filePathName) == False):
			return
		endPath = payload.get("path")
		if (self._isPlaceholderFilePath(endPath)):
			return

		self._logger.info("The print job started without its file ('" + str(printJobModel.filePathName) + "'), taking '" + str(endPath) + "' from the end event")
		printJobModel.filePathName = endPath
		endName = payload.get("name")
		printJobModel.fileName = endName if self._isPlaceholderFilePath(endName) == False else os.path.basename(endPath)
		if (payload.get("origin") != None):
			printJobModel.fileOrigin = payload.get("origin")
		if (payload.get("size") != None):
			printJobModel.fileSize = payload.get("size")


	# The end event is supposed to name the file, but after a printer error OctoPrint
	# 2.0.0rc5 fires PrintFailed with the payload of the Disconnected event instead:
	# {"connector": "serial"}, no origin, path, name or time. The thread that sends it
	# reads a local variable which the same function reassigns for the Disconnected event
	# right afterwards. The file is the one recorded at the start either way, so it is
	# taken from there. A copy, because every other plugin gets the same dict.
	def _completePayloadFromJobStart(self, payload):
		completedPayload = dict(payload) if payload != None else {}
		printJobModel = self._currentPrintJobModel
		missingKeys = []
		for key, startValue in [("origin", printJobModel.fileOrigin),
								("path", printJobModel.filePathName),
								("name", printJobModel.fileName),
								("size", printJobModel.fileSize)]:
			if (completedPayload.get(key) == None):
				completedPayload[key] = startValue
				missingKeys.append(key)
		if (len(missingKeys) > 0):
			self._logger.warning("The end event carries no " + ", ".join(missingKeys) + " (payload " + str(payload) + "), taking it from the start of the print job")
		return completedPayload

	def _capturePrintJobData(self, printStatus, payload):
		captureMode = self._settings.get([SettingsKeys.SETTINGS_KEY_CAPTURE_PRINTJOBHISTORY_MODE])
		self._logger.info("Print result:" + printStatus + ", CaptureMode:" + captureMode)

		if (captureMode == SettingsKeys.KEY_CAPTURE_PRINTJOBHISTORY_MODE_NONE):
			self._logger.info("PrintJob not captured, because it is not enabled in plugin settings")
			return None

		captureThePrint = False
		if (captureMode == SettingsKeys.KEY_CAPTURE_PRINTJOBHISTORY_MODE_ALWAYS):
			captureThePrint = True
		else:
			# check status is neccessary
			if (printStatus == "success"):
				captureThePrint = True

		# Acknowledging an error on the printer makes some connectors report a start and a
		# done within a few seconds. That is indistinguishable from a real print here, so it
		# used to be stored as a successful one - complete with the after-print dialog.
		# Filtering on duration is the only signal that separates the two.
		if (captureThePrint == True):
			minimumDuration = StringUtils.transformToFloatOrNone(
				self._settings.get([SettingsKeys.SETTINGS_KEY_MINIMUM_PRINT_DURATION_IN_SECONDS]))
			if (minimumDuration != None and minimumDuration > 0 and self._currentPrintJobModel != None
				and self._currentPrintJobModel.printStartDateTime != None):
				durationInSeconds = (datetime.datetime.now() - self._currentPrintJobModel.printStartDateTime).total_seconds()
				if (durationInSeconds < minimumDuration):
					self._logger.info("PrintJob not captured, because it only lasted '" + str(round(durationInSeconds, 1)) + "' seconds (minimum is '" + str(minimumDuration) + "')")
					captureThePrint = False

		databaseId = None
		payLoadForClient = None
		# capture the print
		if (captureThePrint == True):
			self._logger.info("----- Start capturing print job data... -----")

			# - Core Data
			self._currentPrintJobModel.printEndDateTime = datetime.datetime.now()
			self._currentPrintJobModel.duration = (
					self._currentPrintJobModel.printEndDateTime - self._currentPrintJobModel.printStartDateTime).total_seconds()
			self._currentPrintJobModel.printStatusResult = printStatus

			# Everything between here and insertPrintJob is enrichment: each step may fail on
			# its own, the print job record itself must always be stored.

			# - Slicer Settings
			# Check the expressions first: the parser needs a real file on disk, and a
			# printer-hosted print has none. Without expressions there is nothing to read
			# anyway, so the file manager is not touched at all in the default config.
			try:
				slicerSettingsExpressions = self._settings.get([SettingsKeys.SETTINGS_KEY_SLICERSETTINGS_KEYVALUE_EXPRESSION])
				if (slicerSettingsExpressions != None and len(slicerSettingsExpressions) != 0):
					selectedFile = self._resolveFileOnDisk(payload.get("origin"), payload.get("path"))
					if (selectedFile != None):
						slicerSettings = SlicerSettingsParser(self._logger).extractSlicerSettings(selectedFile, slicerSettingsExpressions)
						if (slicerSettings.settingsAsText != None and len(slicerSettings.settingsAsText) != 0):
							self._currentPrintJobModel.slicerSettingsAsText = slicerSettings.settingsAsText
			except Exception as e:
				self._logger.exception("Could not read slicer settings, storing the print job without them: " + str(e))

			# - Image / Thumbnail
			# Enrichment only: a print that happened must be recorded even when we cannot
			# illustrate it, so the image step never takes the whole job down.
			try:
				self._grabImage(payload)
			except Exception as e:
				self._logger.exception("Could not grab an image, storing the print job without one: " + str(e))

			# - FilamentInformations e.g. length
			# Depends on file meta data and on the SpoolManagerExtended plugin. Both are
			# optional, the print job record is not.
			try:
				self._createAndAssignFilamentModel(self._currentPrintJobModel, payload)
			except Exception as e:
				self._logger.exception("Could not read filament informations, storing the print job without them: " + str(e))

			# - Temperatures, the targets held while printing (see _trackPrintTemperaturesAsync)
			# After the filament: which tools the job used comes from there.
			try:
				printTemperatures = self._currentPrintJobModel.printTemperatures
				if (printTemperatures != None):
					toolIds = self._resolveTemperatureToolIds(self._currentPrintJobModel)
					self._addTemperaturesToPrintModel(self._currentPrintJobModel, printTemperatures, toolIds)
				else:
					self._logger.warning("No temperature was tracked during this print, storing none")
			except Exception as e:
				self._logger.exception("Could not add temperatures, storing the print job without them: " + str(e))

			# - Costs
			try:
				self._addCostsToPrintModel(self._currentPrintJobModel)
			except Exception as e:
				self._logger.exception("Could not calculate costs, storing the print job without them: " + str(e))

			# store everything in the database
			self._logger.info("----- Try storing printjob model ----")
			databaseId = self._databaseManager.insertPrintJob(self._currentPrintJobModel)
			if (databaseId == None):
				self._logger.error("PrintJob not captured, see previous error log!")
				return None

			# This is the job SpoolManagerExtended's usage event will refer to
			with self._backfillLock:
				self._backfillTargetDatabaseId = databaseId
				self._backfillTargetPrintEndDateTime = self._currentPrintJobModel.printEndDateTime
			printJobItem = None
			if self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_SHOW_PRINTJOB_DIALOG_AFTER_PRINT]):

				self._settings.set_int([SettingsKeys.SETTINGS_KEY_SHOW_PRINTJOB_DIALOG_AFTER_PRINT_JOB_ID], databaseId)
				self._settings.save()

				# inform client to show job edit dialog
				printJobModel = self._databaseManager.loadPrintJob(databaseId)

				# check the correct status (redundent code, see event client_open)
				printJobItem = None
				showDisplayAfterPrintMode = self._settings.get(
					[SettingsKeys.SETTINGS_KEY_SHOWPRINTJOBDIALOGAFTERPRINT_MODE])
				printJobModelStatus = printJobModel.printStatusResult

				if (showDisplayAfterPrintMode == SettingsKeys.KEY_SHOWPRINTJOBDIALOGAFTERPRINT_MODE_SUCCESSFUL):
					# show only when succesfull
					if ("success" == printJobModelStatus):
						printJobItem = TransformPrintJob2JSON.transformPrintJobModel(printJobModel, self._file_manager)
				elif (showDisplayAfterPrintMode == SettingsKeys.KEY_SHOWPRINTJOBDIALOGAFTERPRINT_MODE_FAILED):
					if ("failed" == printJobModelStatus or "canceled" == printJobModelStatus):
						printJobItem = TransformPrintJob2JSON.transformPrintJobModel(printJobModel, self._file_manager)

				else:
					# always
					printJobItem = TransformPrintJob2JSON.transformPrintJobModel(printJobModel, self._file_manager)

			# inform client for a reload (and show dialog)
			# SpoolManagerExtended books the filament a moment after us, so the job is stored
			# without it. Tell the client to expect it, otherwise the dialog just shows zeros
			# with no hint that anything is still coming.
			payLoadForClient = {
				"action": "printFinished",
				"printJobItem": printJobItem,  # if present then the editor dialog is shown
				"filamentUsagePending": self._isFilamentUsageStillExpected()
			}
			# self._sendDataToClient(payLoadForClient)
			self._logger.info("----- ... End PrintJob captured! -----")
		else:
			self._logger.info("----- ... PrintJob not captured, because not activated! -----")

		# capture the technical log and send updated model to browser
		self._resetableFileLogHandler.stopLogging()
		if (databaseId != None):
			techLog = self._resetableFileLogHandler.readLogContent()
			lastPrintJobModel = self._databaseManager.loadPrintJob(databaseId)
			lastPrintJobModel.technicalLog = StringUtils.shortenInTheMiddle(
				techLog, TECHNICAL_LOG_MAX_BYTES,
				"[... {omitted} bytes left out, the complete log is in " + TECHNICAL_LOG_FILE_NAME + " until the next print starts ...]\n")
			self._databaseManager.updatePrintJob(lastPrintJobModel)
			if (payload != None):
				if (payLoadForClient["printJobItem"] != None):
					printJobItem = TransformPrintJob2JSON.transformPrintJobModel(lastPrintJobModel, self._file_manager)
					payLoadForClient["printJobItem"] = printJobItem
				self._sendDataToClient(payLoadForClient)
			pass

		return databaseId


	# True while we expect SpoolManagerExtended to report the per-job usage by event. Only
	# meaningful when its event actually carries that usage - an older version never sends
	# it, and then waiting for it would be a promise we cannot keep.
	def _isFilamentUsageStillExpected(self):
		if (self._isSpoolManagerInstalledAndEnabled() == False):
			return False
		with self._backfillLock:
			return self._backfillTargetDatabaseId != None


	# The image step of a finished print. The work itself runs in a thread of its own: the
	# preview of a file on a Bambu printer is only there once the whole 3MF was downloaded
	# from the printer, and a webcam can be slow as well. Neither may hold up the event
	# thread, which still has to hand PRINT_DONE on to SpoolManagerExtended.
	def _grabImage(self, payload):
		self._logger.info("----- Start grab Image/thumbnail... -----")
		imageSource = self._settings.get([SettingsKeys.SETTINGS_KEY_IMAGE_SOURCE])
		if (imageSource == SettingsKeys.KEY_IMAGE_SOURCE_NONE):
			self._logger.info("No image should be taken")
			return

		# The printer asked for this snapshot while printing, at the moment the gcode chose
		# for it. A snapshot from the end of the print would only be a worse one.
		if (imageSource == SettingsKeys.KEY_IMAGE_SOURCE_CAMERA and self._m118SnapshotTaken == True):
			self._logger.info("Keeping the snapshot the printer asked for with M118")
			return

		snapshotFilename = CameraManager.buildSnapshotFilename(self._currentPrintJobModel.printStartDateTime)
		thread = threading.Thread(name="GrabPrintJobImage",
								  target=self._grabImageInBackground,
								  args=(imageSource, snapshotFilename, payload.get("origin"), payload.get("path"),
										self._currentPrintJobModel.previewImageAtStart))
		thread.daemon = True
		thread.start()


	# Takes the image from the chosen source and, when that one has none to give, from the
	# other one: an entry with the second best image is worth more than one without any.
	def _grabImageInBackground(self, imageSource, snapshotFilename, fileOrigin, filePath, previewImageAtStart=None):
		try:
			webcam = ("webcam", lambda: self._cameraManager.takeSnapshot(snapshotFilename))
			filePreview = ("file preview", lambda: self._takePreviewImage(snapshotFilename, fileOrigin, filePath, previewImageAtStart))
			if (imageSource == SettingsKeys.KEY_IMAGE_SOURCE_CAMERA):
				imageSources = [webcam, filePreview]
			else:
				imageSources = [filePreview, webcam]

			for sourceName, takeImage in imageSources:
				if (takeImage() == True):
					self._logger.info("Image of the print job taken from the " + sourceName)
					self._sendDataToClient(dict(action="printJobImageUpdated",
												snapshotFilename=snapshotFilename))
					return
				self._logger.info("No image from the " + sourceName)
			self._logger.warning("The print job is stored without an image")
		except Exception as e:
			self._logger.exception("Could not grab an image for the print job: " + str(e))


	# The preview the slicer embedded into the print file. Since 2.0 OctoPrint extracts it
	# itself, and for a printer-hosted file the connector fetches it from the printer
	# (Moonraker, Bambu). Local files uploaded before, and UFP packages, only have the one a
	# thumbnail plugin stored - hence that one as the second choice.
	# A preview read at the start of the print is the same image, and comes first: it needs
	# no second download from the printer, and it is there even when the printer is not.
	def _takePreviewImage(self, snapshotFilename, fileOrigin, filePath, previewImageAtStart=None):
		if (previewImageAtStart != None):
			try:
				self._logger.info("Taking the preview image read at the start of the print")
				if (self._cameraManager.storeThumbnail(snapshotFilename, previewImageAtStart) == True):
					return True
			except Exception as e:
				self._logger.warning("Could not store the preview image read at the start of the print: " + str(e))
		if (self._takeFilePreviewImage(snapshotFilename, fileOrigin, filePath) == True):
			return True
		return self._takePluginThumbnailImage(snapshotFilename, fileOrigin, filePath)


	def _takeFilePreviewImage(self, snapshotFilename, fileOrigin, filePath):
		imageData = self._readFilePreviewImage(fileOrigin, filePath)
		if (imageData == None):
			return False
		try:
			return self._cameraManager.storeThumbnail(snapshotFilename, imageData)
		except Exception as e:
			self._logger.warning("Could not take the preview image of '" + str(filePath) + "': " + str(e))
			return False


	# The bytes of the largest preview the storage has for the file, or None
	def _readFilePreviewImage(self, fileOrigin, filePath):
		try:
			# Truthiness on purpose: connectors answer has_thumbnail() with the thumbnail
			# list or a folder name rather than a bool.
			if (not self._file_manager.capabilities(fileOrigin).thumbnails
				or not self._file_manager.has_thumbnail(fileOrigin, filePath)):
				self._logger.info("No preview image in '" + str(filePath) + "' (origin '" + str(fileOrigin) + "')")
				return None

			# without a size hint every storage answers with its largest preview
			thumbnail = self._file_manager.read_thumbnail(fileOrigin, filePath)
			if (thumbnail == None):
				self._logger.info("The storage delivered no preview image for '" + str(filePath) + "'")
				return None

			thumbnailInfo, thumbnailHandle = thumbnail
			try:
				chunks = []
				while True:
					chunk = thumbnailHandle.read()
					if (chunk == None or len(chunk) == 0):
						break
					chunks.append(chunk)
			finally:
				thumbnailHandle.close()

			self._logger.info("Read preview image '" + str(thumbnailInfo.name) + "' (" + str(thumbnailInfo.sizehint) + ") of '" + str(filePath) + "'")
			return b"".join(chunks)
		except Exception as e:
			self._logger.warning("Could not take the preview image of '" + str(filePath) + "': " + str(e))
			return None


	# The preview a thumbnail plugin (Slicer Thumbnails, Cura Thumbnails) stored for the file
	# and referenced in its metadata
	def _takePluginThumbnailImage(self, snapshotFilename, fileOrigin, filePath):
		metadata = self._readFileMetaData(fileOrigin, filePath)
		if (metadata == None or not metadata.get("thumbnail")):
			self._logger.info("No thumbnail plugin stored a preview image for '" + str(filePath) + "'")
			return False
		try:
			return self._cameraManager.takePluginThumbnail(snapshotFilename, metadata["thumbnail"])
		except Exception as e:
			self._logger.warning("Could not take the preview image a thumbnail plugin stored: " + str(e))
			return False


	#######################################################################################   OP - HOOKs
	from queue import Queue
	# from logging.handlers import QueueHandler
	# from logging.handlers import QueueListener

	def on_after_startup(self):
		# the database was reopened as part of this startup, so whatever a migration
		# replaced is now actually in use - the restart it asked for has happened
		self._setRestartRequired(False)

		# check if needed plugins were available
		self._checkAndLoadThirdPartyPluginInfos(False) # don't inform the client, because client is maybe not opened

		self._importCostEstimationSettingsOnce()

		# helpers = self._plugin_manager.get_helpers("multicam")
		# if helpers and "get_webcam_profiles" in helpers:
		# 	get_webcam_profiles = helpers["get_webcam_profiles"]
		#
		# 	self.camProfiles = get_webcam_profiles()

		# que = Queue(10)
		# queue_handler = MyQueueHandler(que)
		#
		# root = logging.getLogger()
		# root.addHandler(queue_handler)
		# self._technicalLoggingHandler = MyHandler()
		# listener = logging.handlers.QueueListener(que, self._technicalLoggingHandler)
		# listener.start()

		logFilename = os.path.join(self._settings.getBaseFolder("logs"), TECHNICAL_LOG_FILE_NAME)
		formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
		self._resetableFileLogHandler = ResetAbleLogFileHandler(logFilename, "octoprint.plugins.PrintJobHistoryExtended")
		self._resetableFileLogHandler.setFormatter(formatter)
		# self._resetableFileLogHandler.assignLoggerNameToCapture("octoprint.plugins.PrintJobHistoryExtended")
		# add to current plugin logger
		pluginLogger = logging.getLogger(self._logger.name)
		pluginLogger.addHandler(self._resetableFileLogHandler)

		self._logger.info("on after startup done")
		pass




	# Receiving commands from the printer: "M118 //action:pjhTakeSnapshot" in the gcode lets
	# the print decide when the snapshot is taken, e.g. once the bed has moved to the front.
	# Only printers that talk to OctoPrint directly (serial) send action commands.
	# Terminal: !!DEBUG:send //action:pjhTakeSnapshot
	def on_receivedActionHook(self, comm, line, action, *args, **kwargs):
		if (action != "pjhTakeSnapshot"):
			return
		self._logger.info("Received \"pjhTakeSnapshot\" action from printer")

		if (self._settings.get([SettingsKeys.SETTINGS_KEY_IMAGE_SOURCE]) != SettingsKeys.KEY_IMAGE_SOURCE_CAMERA
			or self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_TAKE_SNAPSHOT_ON_M118_COMMAND]) != True):
			self._logger.info("Take Snapshot via M118 command is not activated in plugin settings")
			return

		# Read once: the job ends on the event thread, which resets it to None meanwhile
		printJobModel = self._currentPrintJobModel
		if (printJobModel == None or printJobModel.printStartDateTime == None):
			self._logger.error("M118 command detected, but there is no printjob started!")
			return

		printStartDateTime = printJobModel.printStartDateTime

		def snapshotTaken(success):
			# only for the print that asked, should the next one have started meanwhile
			currentPrintJobModel = self._currentPrintJobModel
			if (success == True and currentPrintJobModel != None
				and currentPrintJobModel.printStartDateTime == printStartDateTime):
				self._m118SnapshotTaken = True

		self._logger.info("M118 command enabled for taking snapshot. Try to capture image!")
		self._cameraManager.takeSnapshotAsync(CameraManager.buildSnapshotFilename(printStartDateTime),
											  None,
											  snapshotTaken)

	def additional_permissions_hook(self):
		from octoprint.access import ADMIN_GROUP
		from octoprint.access import USER_GROUP
		return [
			{
				"key": "VIEW",
				"name": "View print job history",
				"description": "Allows to read the print job history, its statistics, "
				               "snapshots, reports and CSV exports",
				"default_groups": [ADMIN_GROUP, USER_GROUP],
				# Exactly one role. OctoPrint's allows() demands *every* need a permission
				# carries, so listing a second role would AND the two together: verified on the
				# running instance, a holder of either role alone then satisfies neither. That
				# also rules out expressing "EDIT implies VIEW" through roles or a "permissions"
				# key - it would only widen EDIT_JOB's own need set. The read routes therefore
				# accept VIEW *or* a write permission, see _canViewHistory in the API mixin.
				"roles": ["view_job"],
				# Deliberately not dangerous. OctoPrint strips dangerous permissions from the
				# guests group, which would block the read-only sharing this permission exists
				# for (OllisGit #220).
			},
			{
				"key": "DELETE_JOB",
				"name": "Delete print jobs",
				"description": "Allows to delete print jobs from history",
				"default_groups": [ADMIN_GROUP, USER_GROUP],
				"roles": ["print_operator"],
			},
			{
				"key": "EDIT_JOB",
				"name": "Edit print jobs",
				"description": "Allows to edit print jobs",
				"default_groups": [ADMIN_GROUP, USER_GROUP],
				"roles": ["print_operator"],
			},
		]

	# Main Event-Handler
	def on_event(self, event, payload):

		# print("****************************")
		# print(event)
		# print("****************************")
		# WebBrowser opened
		# TODO when OP 1.8.0 is release, implement FileMoved event
		if ("FileMoved" == event):
			print("******* MOVED ***********")

		if Events.CLIENT_OPENED == event:

			# - Check if all needed Plugins are available, if not modale dialog to User
			self._checkAndLoadThirdPartyPluginInfos(True)

			# Send plugin storage information
			# - Storage
			if (hasattr(self, "_databaseManager") == True):
				databaseFileLocation = self._databaseManager.getDatabaseFileLocation()
				snapshotFileLocation = self._cameraManager.getSnapshotFileLocation()
				currencySymbol = self._settings.get([SettingsKeys.SETTINGS_KEY_CURRENCY_SYMBOL])
				currencyFormat = self._settings.get([SettingsKeys.SETTINGS_KEY_CURRENCY_FORMAT])
				self._sendDataToClient(dict(action="initalData",
											databaseFileLocation=databaseFileLocation,
											snapshotFileLocation=snapshotFileLocation,
											isPrintHistoryPluginAvailable=self._printHistoryPluginImplementation != None,
											isSpoolManagerInstalled = self._isSpoolManagerInstalledAndEnabled(),
											isTasmotaInstalled = self._isTasmotaInstalledAndEnabled(),
											tasmotaPlugs = self._readTasmotaPlugs(),
											currencySymbol = currencySymbol,
											currencyFormat = currencyFormat,
											))

			# - Show last Print-Dialog
			if self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_SHOW_PRINTJOB_DIALOG_AFTER_PRINT]):
				printJobId = self._settings.get_int([SettingsKeys.SETTINGS_KEY_SHOW_PRINTJOB_DIALOG_AFTER_PRINT_JOB_ID])
				if (not printJobId == None):
					try:
						printJobModel = self._databaseManager.loadPrintJob(printJobId)

						# check the correct status
						printJobItem = None
						showDisplayAfterPrintMode = self._settings.get([SettingsKeys.SETTINGS_KEY_SHOWPRINTJOBDIALOGAFTERPRINT_MODE])
						printJobModelStatus = printJobModel.printStatusResult

						if (showDisplayAfterPrintMode == SettingsKeys.KEY_SHOWPRINTJOBDIALOGAFTERPRINT_MODE_SUCCESSFUL):
							# show only when succesfull
							if ("success" == printJobModelStatus):
								printJobItem = TransformPrintJob2JSON.transformPrintJobModel(printJobModel, self._file_manager)
						elif (showDisplayAfterPrintMode == SettingsKeys.KEY_SHOWPRINTJOBDIALOGAFTERPRINT_MODE_FAILED):
							if ("failed" == printJobModelStatus or "canceled" == printJobModelStatus):
								printJobItem = TransformPrintJob2JSON.transformPrintJobModel(printJobModel, self._file_manager)
						else:
							# always
							printJobItem = TransformPrintJob2JSON.transformPrintJobModel(printJobModel, self._file_manager)

						payload = {
							"action": "showPrintJobDialogAfterClientConnection",
							"printJobItem": printJobItem
						}
						self._sendDataToClient(payload)
					except DoesNotExist as e:
						self._settings.remove([SettingsKeys.SETTINGS_KEY_SHOW_PRINTJOB_DIALOG_AFTER_PRINT_JOB_ID])

			# The setting holds the dict built by _sendMessageConfirmToClient, so it is indexed,
			# not attribute-accessed. Nothing writes this setting yet; the pending-message path
			# is here for a producer that stores a message to be confirmed after a restart.
			messageConfirmData = self._settings.get([SettingsKeys.SETTINGS_KEY_MESSAGE_CONFIRM_DATA])
			if (messageConfirmData != None):
				self._sendMessageConfirmToClient(messageConfirmData.get("title"), messageConfirmData.get("message"))

			self._checkForMissingFilamentTracking()
			self._checkTasmotaSetup()

		elif Events.PRINT_STARTED == event:
			self._printJobStarted(payload)

		elif "DisplayLayerProgress_layerChanged" == event or event == "DisplayLayerProgress_heightChanged":
			self._updatePrintJobModelWithLayerHeightInfos(payload)

		elif Events.PRINT_DONE == event:
			self._printJobFinished("success", payload)
		elif Events.PRINT_FAILED == event:
			if self.alreadyCanceled == False:
				self._printJobFinished("failed", payload)
		elif Events.PRINT_CANCELLED == event:
			self.alreadyCanceled = True
			self._printJobFinished("canceled", payload)

		elif "plugin_spoolmanagerextended_spool_weight_updated_after_print" == event:
			self._onSpoolUsageBookedByPeer(payload)
		elif "plugin_spoolmanagerextended_print_job_usage_booked" == event:
			self._onPrintJobUsageBookedByPeer(payload)
		pass


	def _buildDatabaseSettingsFromPluginSettings(self):
		"""Read the flat database settings into a DatabaseSettings carrier."""
		databaseSettings = DatabaseManager.DatabaseSettings()
		databaseSettings.useExternal = self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_DATABASE_USE_EXTERNAL])
		databaseSettings.type = self._settings.get([SettingsKeys.SETTINGS_KEY_DATABASE_TYPE])
		databaseSettings.host = self._settings.get([SettingsKeys.SETTINGS_KEY_DATABASE_HOST])
		# The port has no default, so it can be empty/None until the user fills it in.
		databasePort = self._settings.get_int([SettingsKeys.SETTINGS_KEY_DATABASE_PORT])
		databaseSettings.port = databasePort if databasePort != None else 3306
		databaseSettings.name = self._settings.get([SettingsKeys.SETTINGS_KEY_DATABASE_NAME])
		databaseSettings.user = self._settings.get([SettingsKeys.SETTINGS_KEY_DATABASE_USER])
		databaseSettings.password = self._settings.get([SettingsKeys.SETTINGS_KEY_DATABASE_PASSWORD])
		return databaseSettings

	def _resolveInstanceName(self):
		"""Name identifying this OctoPrint instance in a shared database.

		Derived from OctoPrint's own instance name (or the hostname) on first use and then
		persisted: a name that keeps being re-derived would change with the hostname and
		orphan this instance's print jobs.
		"""
		instanceName = self._settings.get([SettingsKeys.SETTINGS_KEY_INSTANCE_NAME])
		if (StringUtils.isEmpty(instanceName) == False):
			return instanceName

		instanceName = self._settings.global_get(["appearance", "name"])
		if (StringUtils.isEmpty(instanceName) == True):
			import socket
			instanceName = socket.gethostname()

		self._settings.set([SettingsKeys.SETTINGS_KEY_INSTANCE_NAME], instanceName)
		self._settings.save()
		self._logger.info("Resolved instance name to '" + str(instanceName) + "'")
		return instanceName

	def on_settings_save(self, data):
		databaseSettingsBefore = str(self._buildDatabaseSettingsFromPluginSettings())

		# default save function
		octoprint.plugin.SettingsPlugin.on_settings_save(self, data)

		# reinitialize some fields
		sqlLoggingEnabled = self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_SQL_LOGGING_ENABLED])
		self._databaseManager.showSQLLogging(sqlLoggingEnabled)

		self._databaseManager.setInstanceName(self._settings.get([SettingsKeys.SETTINGS_KEY_INSTANCE_NAME]))

		# Reconnect when the database configuration changed, so the user does not have to
		# restart the server after entering the connection details.
		newDatabaseSettings = self._buildDatabaseSettingsFromPluginSettings()
		if (str(newDatabaseSettings) != databaseSettingsBefore):
			self._logger.info("Database settings changed, reconnecting: " + str(newDatabaseSettings))
			pluginDataBaseFolder = self.get_plugin_data_folder()
			self._databaseManager.closeDatabase()
			self._databaseManager.initDatabase(pluginDataBaseFolder,
											   self._sendErrorMessageToClient,
											   newDatabaseSettings)

	def get_settings_restricted_paths(self):
		# Without this the database password is part of the settings payload sent to every
		# logged-in browser session.
		return {
			"admin": [
				[SettingsKeys.SETTINGS_KEY_DATABASE_PASSWORD]
			]
		}



	# Declared explicitly: OctoPrint's default is going to switch from False to True, and it
	# warns on every start as long as a plugin relies on it.
	def is_api_protected(self):
		return True

	# Serves the defaults for the "Reset Settings" button in the settings dialog. Read-only
	# on purpose: the reset happens in the dialog and only takes effect once the user saves.
	# The former "resetSettings" action wrote the defaults on a plain GET, database
	# connection included.
	def on_api_get(self, request):
		if not Permissions.SETTINGS.can():
			flask.abort(403)

		action = request.values.get("action")
		if "getDefaultSettings" == action:
			return flask.jsonify(self.get_settings_defaults())

		flask.abort(400, description="Unknown action: " + str(action))


	##~~ SettingsPlugin mixin
	def get_settings_defaults(self):
		settings = dict(
			installed_version=self._plugin_version
		)
		## General
		settings[SettingsKeys.SETTINGS_KEY_PLUGIN_DEPENDENCY_CHECK] = True
		settings[SettingsKeys.SETTINGS_KEY_SHOW_PRINTJOB_DIALOG_AFTER_PRINT] = True
		settings[SettingsKeys.SETTINGS_KEY_SHOW_PRINTJOB_DIALOG_AFTER_PRINT_JOB_ID] = None
		settings[SettingsKeys.SETTINGS_KEY_SHOWPRINTJOBDIALOGAFTERPRINT_MODE] = SettingsKeys.KEY_SHOWPRINTJOBDIALOGAFTERPRINT_MODE_SUCCESSFUL
		settings[SettingsKeys.SETTINGS_KEY_CAPTURE_PRINTJOBHISTORY_MODE] = SettingsKeys.KEY_CAPTURE_PRINTJOBHISTORY_MODE_SUCCESSFUL
		settings[SettingsKeys.SETTINGS_KEY_MINIMUM_PRINT_DURATION_IN_SECONDS] = 30
		# settings[SettingsKeys.SETTINGS_KEY_SELECTED_FILAMENTTRACKER_PLUGIN] = SettingsKeys.KEY_SELECTED_SPOOLMANAGER_PLUGIN
		settings[SettingsKeys.SETTINGS_KEY_SELECTED_FILAMENTTRACKER_PLUGIN] = SettingsKeys.KEY_SELECTED_NONE_PLUGIN
		settings[SettingsKeys.SETTINGS_KEY_NO_NOTIFICATION_FILAMENTTRACKERING_PLUGIN_SELECTION] = False

		settings[SettingsKeys.SETTINGS_KEY_SLICERSETTINGS_KEYVALUE_EXPRESSION] = ";(.*)=(.*)\n;   (.*),(.*)"
		settings[SettingsKeys.SETTINGS_KEY_SINGLE_PRINTJOB_REPORT_TEMPLATENAME] = SettingsKeys.SETTINGS_DEFAULT_VALUE_SINGLE_PRINTJOB_REPORT_TEMPLATENAME
		settings[SettingsKeys.SETTINGS_KEY_MULTI_PRINTJOB_REPORT_TEMPLATENAME] = SettingsKeys.SETTINGS_DEFAULT_VALUE_MULTI_PRINTJOB_REPORT_TEMPLATENAME

		settings[SettingsKeys.SETTINGS_KEY_CURRENCY_SYMBOL] = "€"
		settings[SettingsKeys.SETTINGS_KEY_CURRENCY_FORMAT] = "%v %s"

		## Printer running cost. No made-up numbers: the purchase price and lifespan are
		## specific to the machine, so they stay empty until the user fills them in.
		settings[SettingsKeys.SETTINGS_KEY_PRINTER_PURCHASE_PRICE] = ""
		settings[SettingsKeys.SETTINGS_KEY_PRINTER_LIFESPAN_HOURS] = ""
		settings[SettingsKeys.SETTINGS_KEY_PRINTER_MAINTENANCE_PER_HOUR] = ""
		# 1.0 means "no extra wear assumed" - see the settings help text.
		settings[SettingsKeys.SETTINGS_KEY_PRINTER_WEAR_MULTIPLIER] = 1.0
		settings[SettingsKeys.SETTINGS_KEY_COSTESTIMATION_IMPORTED] = False

		## Electricity
		settings[SettingsKeys.SETTINGS_KEY_TASMOTA_PLUG_IP] = ""
		settings[SettingsKeys.SETTINGS_KEY_TASMOTA_PLUG_IDX] = ""
		settings[SettingsKeys.SETTINGS_KEY_ELECTRICITY_COST_PER_KWH] = ""
		settings[SettingsKeys.SETTINGS_KEY_NO_NOTIFICATION_TASMOTA_POLLING] = False

		## Camera
		# The preview needs no hardware, and the webcam still stands in for files without one
		settings[SettingsKeys.SETTINGS_KEY_IMAGE_SOURCE] = SettingsKeys.KEY_IMAGE_SOURCE_THUMBNAIL
		settings[SettingsKeys.SETTINGS_KEY_TAKE_SNAPSHOT_ON_M118_COMMAND] = False

		## Temperature
		settings[SettingsKeys.SETTINGS_KEY_DEFAULT_TOOL_ID] = "tool0"
		settings[SettingsKeys.SETTINGS_KEY_DELAY_READING_TEMPERATURE_FROM_PRINTER] = 60

		## Export / Import
		settings[SettingsKeys.SETTINGS_KEY_IMPORT_CSV_MODE] = SettingsKeys.KEY_IMPORTCSV_MODE_APPEND

		## Database backend
		settings[SettingsKeys.SETTINGS_KEY_DATABASE_USE_EXTERNAL] = False
		settings[SettingsKeys.SETTINGS_KEY_DATABASE_TYPE] = SettingsKeys.KEY_DATABASE_TYPE_SQLITE
		# No defaults for host/port/name: a pre-filled value looks like a working configuration
		# and hides which fields the user still has to supply.
		settings[SettingsKeys.SETTINGS_KEY_DATABASE_HOST] = ""
		settings[SettingsKeys.SETTINGS_KEY_DATABASE_PORT] = ""
		settings[SettingsKeys.SETTINGS_KEY_DATABASE_NAME] = ""
		settings[SettingsKeys.SETTINGS_KEY_DATABASE_USER] = ""
		settings[SettingsKeys.SETTINGS_KEY_DATABASE_PASSWORD] = ""
		settings[SettingsKeys.SETTINGS_KEY_INSTANCE_NAME] = ""

		## Debugging
		settings[SettingsKeys.SETTINGS_KEY_SQL_LOGGING_ENABLED] = False

		## Other stuff
		settings[SettingsKeys.SETTINGS_KEY_MESSAGE_CONFIRM_DATA] = None
		settings[SettingsKeys.SETTINGS_KEY_LAST_PLUGIN_DEPENDENCY_CHECK] = None


		# ## Storage
		# if (hasattr(self,"_databaseManager") == True):
		# 	settings[SettingsKeys.SETTINGS_KEY_DATABASE_PATH] = self._databaseManager.getDatabaseFileLocation()
		# 	settings[SettingsKeys.SETTINGS_KEY_SNAPSHOT_PATH] = self._cameraManager.getSnapshotFileLocation()
		# else:
		# 	settings[SettingsKeys.SETTINGS_KEY_DATABASE_PATH] = ""
		# 	settings[SettingsKeys.SETTINGS_KEY_SNAPSHOT_PATH] = ""

		return settings


	# 1: the camera switches collapsed into one image source
	def get_settings_version(self):
		return 1


	# Called by OctoPrint before the plugin's settings are used, also for installs that
	# never had a settings version (current is None then).
	def on_settings_migrate(self, target, current):
		if (current == None or current < 1):
			self._migrateCameraSettings()


	def _migrateCameraSettings(self):
		# Only what is stored counts: the old keys have no defaults any more, so a key the
		# user never changed reads as None and translates with its old default.
		storedValues = {}
		for key in CameraSettingsMigration.LEGACY_CAMERA_DEFAULTS:
			value = self._settings.get([key])
			if (value != None):
				storedValues[key] = value

		translatedValues = CameraSettingsMigration.translateCameraSettings(storedValues)
		if (translatedValues is storedValues):
			return

		imageSource = translatedValues[SettingsKeys.SETTINGS_KEY_IMAGE_SOURCE]
		self._settings.set([SettingsKeys.SETTINGS_KEY_IMAGE_SOURCE], imageSource)
		for key in CameraSettingsMigration.OBSOLETE_CAMERA_KEYS:
			if (key in storedValues):
				self._settings.remove([key])
		self._logger.info("Migrated the camera settings " + str(storedValues) + " to image source '" + str(imageSource) + "'")


	##~~ TemplatePlugin mixin
	def get_template_configs(self):
		return [
			# No data_bind on the tab config. OctoPrint merges it into the same data-bind as
			# allowBindings on the tab's outer div, and that pair decides whether knockout
			# descends into the subtree at all - adding "visible" there left the dialogs in
			# modal-dialogs-printJobHistoryExtended unbound, so every permission binding and
			# every click handler inside them silently stopped working (ko.dataFor returned
			# null). The tab gates itself from inside its own template instead.
			dict(type="tab", name="Print Job History Extended"),
			dict(type="settings", custom_bindings=True, name="Print Job History Extended")
		]


	def is_template_autoescaped(self):
		# Without this OctoPrint wraps the UI templates in "autoesc false" and logs a warning
		# on every start; OctoPrint 2.1 enforces autoescaping anyway. The UI templates hold only
		# plain-text _() strings, and the report templates go through render_template_string,
		# which is autoescaped by the app regardless of this flag.
		return True


	def get_template_vars(self):
		# the banner listens to legacyMigrationPending: something to migrate, not migrated yet
		migrationAvailable = self._isLegacyMigrationAvailable()
		return dict(
			legacyMigrationAvailable=migrationAvailable,
			legacyMigrationPending=(migrationAvailable and not self._isLegacyMigrationDone()),
			legacySettingsAvailable=self._hasLegacySettings()
		)


	##~~ AssetPlugin mixin
	def get_assets(self):
		# Define your plugin's asset files to automatically include in the
		# core UI here.
		return dict(
			js=[
				"js/PrintJobHistoryExtended.js",
				"js/PrintJobHistoryExtended-APIClient.js",
				"js/PrintJobHistoryExtended-PluginCheckDialog.js",
				"js/PrintJobHistoryExtended-MessageConfirmDialog.js",
				"js/PrintJobHistoryExtended-EditJobDialog.js",
				"js/PrintJobHistoryExtended-ImportDialog.js",
				"js/PrintJobHistoryExtended-StatisticDialog.js",
				"js/PrintJobHistoryExtended-SettingsCompareDialog.js",
				"js/PrintJobHistoryExtended-ComponentFactory.js",
				"js/PrintJobHistoryExtended-LegacyMigration.js",
				"js/quill.min.js",
				"js/dayjs.min.js",
				"js/plugin/customParseFormat.min.js",
				"js/jquery.datetimepicker.full.min.js",
				"js/TableItemHelper.js",
				"js/ResetSettingsUtilV3.js"],
			css=[
				"css/PrintJobHistoryExtended.css",
				"css/jquery.datetimepicker.min.css",
				"css/quill.snow.css"],
			less=["less/PrintJobHistoryExtended.less"]
		)


	##~~ Softwareupdate hook
	def get_update_information(self):
		# Define the configuration for your plugin to use with the Software Update
		# Plugin here. See https://github.com/foosel/OctoPrint/wiki/Plugin:-Software-Update
		# for details.
		return dict(
			PrintJobHistoryExtended=dict(
				displayName="Print Job History Extended",
				displayVersion=self._plugin_version,

				# version check: github repository
				type="github_release",

				user="Ajimaru",
				repo="OctoPrint-PrintJobHistoryExtended",
				current=self._plugin_version,

				# Note the key is "commitish", not "comittish": a misspelled key is silently
				# ignored, which makes the branch restriction have no effect at all.
				stable_branch=dict(
					name="Only Release",
					branch="main",
					commitish=["main"]
				),
				prerelease_branches=[
					dict(
						name="Release & Pre-Release",
						branch="main",
						commitish=["main"],
					)
				],

				# force_base is deliberately NOT set: it would compare only the base version
				# ("2.0.0a1" -> "2.0.0"), so neither 2.0.0a2 nor the final 2.0.0 would ever be
				# offered as an update. OctoPrint defaults it to False, which compares the full
				# PEP 440 version and orders a1 < a2 < 2.0.0 correctly.

				# update method: pip
				pip="https://github.com/Ajimaru/OctoPrint-PrintJobHistoryExtended/releases/download/{target_version}/main.zip"
			)
		)


	##~~ Legacy migration (data of a previous "PrintJobHistory" install)

	def _getLegacyDataFolder(self):
		"""
		Path of the data folder left behind under the plugin's previous identifier, or
		None if there is none. Pure lookup, no side effects.
		"""
		legacyDataFolder = os.path.join(self._settings.getBaseFolder("data"), LEGACY_IDENTIFIER)
		if not os.path.isdir(legacyDataFolder):
			return None
		return legacyDataFolder


	def _hasLegacySettings(self):
		return bool(self._settings.global_get(["plugins", LEGACY_IDENTIFIER]))


	def _getLegacySettings(self):
		"""
		The settings of the previous install, in this plugin's current format. The old camera
		switches are translated on the way: copied as they are, they would be dead keys that
		no longer control anything.
		"""
		return CameraSettingsMigration.translateCameraSettings(
			self._settings.global_get(["plugins", LEGACY_IDENTIFIER]))


	def _isLegacyMigrationAvailable(self):
		"""Whether there is anything worth migrating from a previous install."""
		legacyDataFolder = self._getLegacyDataFolder()
		if legacyDataFolder is not None:
			if os.path.isfile(os.path.join(legacyDataFolder, LEGACY_DATABASE_FILE_NAME)):
				return True
		return self._hasLegacySettings()


	def _databaseHoldsPrintJobs(self, databaseFile):
		"""
		Whether the SQLite file holds at least one print job.

		Read directly via sqlite3 rather than through the DatabaseManager: the plugin keeps
		its own database open, and this has to inspect a file that may not be the connected
		one. Anything unreadable counts as "no jobs" - an unusable file is not worth
		guarding against being replaced.
		"""
		if not os.path.isfile(databaseFile):
			return False
		try:
			connection = sqlite3.connect(databaseFile)
			try:
				return connection.execute("SELECT COUNT(*) FROM pjh_printjobmodel").fetchone()[0] > 0
			finally:
				connection.close()
		except Exception:
			self._logger.exception("Could not read '" + str(databaseFile) + "', treating it as empty")
			return False


	def _readLegacyDatabasePreview(self, databaseFile, jobLimit=10):
		"""
		Summary of what the legacy database holds, for the confirmation dialog.

		Opened read-only (sqlite3 URI mode=ro): the file still belongs to the old plugin,
		which may well be running, and a preview must not create a journal next to it.
		"""
		preview = {
			"readable": False,
			"jobCount": 0,
			"schemeVersion": None,
			"firstJobDate": None,
			"lastJobDate": None,
			"jobs": [],
			"moreJobs": 0,
		}
		if not os.path.isfile(databaseFile):
			return preview

		try:
			uri = "file:%s?mode=ro" % pathname2url(databaseFile)
			connection = sqlite3.connect(uri, uri=True)
			try:
				preview["jobCount"] = connection.execute("SELECT COUNT(*) FROM pjh_printjobmodel").fetchone()[0]
				dateRow = connection.execute(
					"SELECT MIN(printStartDateTime), MAX(printStartDateTime) FROM pjh_printjobmodel"
				).fetchone()
				if dateRow is not None:
					preview["firstJobDate"] = dateRow[0]
					preview["lastJobDate"] = dateRow[1]
				for row in connection.execute(
					"SELECT fileName, printStartDateTime, printStatusResult FROM pjh_printjobmodel "
					"ORDER BY printStartDateTime DESC LIMIT ?", (jobLimit,)
				):
					preview["jobs"].append({
						"fileName": row[0],
						"printStartDateTime": row[1],
						"printStatusResult": row[2],
					})
				preview["moreJobs"] = max(0, preview["jobCount"] - len(preview["jobs"]))
				try:
					schemeRow = connection.execute(
						"SELECT value FROM pjh_pluginmetadatamodel WHERE key = 'databaseSchemeVersion'"
					).fetchone()
					if schemeRow is not None:
						preview["schemeVersion"] = schemeRow[0]
				except Exception:
					# a database without the metadata table is still worth previewing
					pass
				preview["readable"] = True
			finally:
				connection.close()
		except Exception:
			# An unreadable file simply cannot be summarised; the dialog says so while
			# still offering the copy.
			self._logger.exception("Could not read legacy database '" + str(databaseFile) + "'")

		return preview


	def _classifyLegacyFile(self, entryName):
		"""
		What kind of entry this is, which decides whether the dialog preselects it. Unlike
		caches, snapshots are user data that cannot be regenerated, so they are preselected
		together with the database itself.
		"""
		if entryName == LEGACY_DATABASE_FILE_NAME:
			return "database"
		if entryName == "snapshots":
			return "snapshots"
		if entryName.startswith("printJobHistory-backup") or entryName.endswith(".csv"):
			return "backup"
		return "other"


	def _getLegacyFileEntries(self):
		"""Entries of the legacy data folder, annotated for the migration dialog."""
		legacyDataFolder = self._getLegacyDataFolder()
		if legacyDataFolder is None:
			return []

		entries = []
		for entryName in sorted(os.listdir(legacyDataFolder)):
			path = os.path.join(legacyDataFolder, entryName)
			kind = self._classifyLegacyFile(entryName)
			try:
				if os.path.isdir(path):
					size = sum(
						os.path.getsize(os.path.join(root, name))
						for root, _dirs, files in os.walk(path)
						for name in files
					)
				else:
					size = os.path.getsize(path)
			except OSError:
				size = 0
			entries.append({
				"name": entryName,
				"kind": kind,
				"size": size,
				"isDirectory": os.path.isdir(path),
				"preselected": kind in ("database", "snapshots"),
			})
		return entries


	def _getUndoFilePath(self, undoKind):
		return os.path.join(self.get_plugin_data_folder(), LEGACY_UNDO_FILE_NAMES[undoKind])


	def _getRestartRequiredFilePath(self):
		return os.path.join(self.get_plugin_data_folder(), LEGACY_RESTART_REQUIRED_FILE_NAME)


	def _isRestartRequired(self):
		return os.path.isfile(self._getRestartRequiredFilePath())


	def _setRestartRequired(self, required):
		path = self._getRestartRequiredFilePath()
		try:
			if required:
				with open(path, "w") as marker:
					marker.write(datetime.datetime.now().isoformat(timespec="seconds"))
			elif os.path.isfile(path):
				os.remove(path)
		except Exception:
			# Only drives a hint in the UI - never worth failing the surrounding action for.
			self._logger.exception("Could not update the restart-required marker")


	def _isLegacyMigrationUndoAvailable(self, undoKind):
		return os.path.isfile(self._getUndoFilePath(undoKind))


	def _isLegacyMigrationDone(self):
		"""
		Whether a migration has already run. Used to retire the banner: the legacy folder is
		deliberately kept (we copy, never move), so its mere presence would keep the hint up
		forever. An undo brings the banner back, which is the point.
		"""
		return any(self._isLegacyMigrationUndoAvailable(kind) for kind in LEGACY_UNDO_FILE_NAMES)


	def _writeUndoRecord(self, undoKind, replacedFiles, previousSettings):
		record = {
			"timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
			"replacedFiles": replacedFiles,
			"previousSettings": previousSettings,
		}
		try:
			with open(self._getUndoFilePath(undoKind), "w") as undoFile:
				json.dump(record, undoFile, indent=2)
		except Exception:
			# Losing the undo record must not fail the migration itself - the data is
			# already copied at this point, and the legacy folder is still intact.
			self._logger.exception("Could not write the migration undo record")


	def _captureSettingsForUndo(self, keys):
		"""
		Current value of each key before it is overwritten. A key without a value of its own
		is recorded as None, so the undo removes it again rather than writing a value the
		user never had.
		"""
		captured = {}
		for key in keys:
			try:
				captured[key] = self._settings.get([key])
			except Exception:
				captured[key] = None
		return captured


	def _performLegacyMigration(self, overwriteExisting=False, includeSettings=True, fileNames=None):
		"""
		Copies data and (optionally) settings of a previous PrintJobHistory install into
		this plugin's own data folder / settings namespace. Triggered by the user, never
		automatically - it touches user data.

		The legacy folder is left untouched (copy, not move), so the old install stays
		usable. Files that would be overwritten are kept as "<name>.pre-migration-<stamp>"
		and recorded for _undoLegacyMigration().
		"""
		def failure(errorMessage, conflict=False):
			return {
				"success": False,
				"errorMessage": errorMessage,
				"conflict": conflict,
				"copiedFiles": 0,
				"settingsMigrated": False,
			}

		legacyDataFolder = self._getLegacyDataFolder()
		legacySettings = self._getLegacySettings()

		if legacyDataFolder is None and not legacySettings:
			return failure("No previous PrintJobHistory installation found. Nothing to migrate.")

		# A second run would copy the same data over the already migrated database - and
		# since the plugin keeps that database open until it restarts, the conflict guard
		# below cannot see the jobs it already holds. Undo first, then migrate again.
		#
		# Only the database record blocks here, not _isLegacyMigrationDone(): undoing the
		# data while keeping the migrated settings is a legitimate state, and it must not
		# leave the migration permanently barred.
		if self._isLegacyMigrationUndoAvailable("database") and not overwriteExisting:
			return failure(
				"This installation has already been migrated. Undo the previous migration "
				"first if you want to run it again.",
				conflict=True
			)

		newDataFolder = self.get_plugin_data_folder()
		databaseIsSelected = (
			legacyDataFolder is not None
			and os.path.isfile(os.path.join(legacyDataFolder, LEGACY_DATABASE_FILE_NAME))
			and (fileNames is None or LEGACY_DATABASE_FILE_NAME in fileNames)
		)

		# Only a database holding actual jobs is worth protecting. The plugin creates an
		# empty one on first start, so testing for the file alone would confront every user
		# with a data-loss warning that does not apply to them.
		if databaseIsSelected and not overwriteExisting:
			if self._databaseHoldsPrintJobs(os.path.join(newDataFolder, DATABASE_FILE_NAME)):
				return failure(
					"This installation already has its own database with print jobs in it. "
					"Migrating would replace it with the one from the previous PrintJobHistory install.",
					conflict=True
				)

		timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
		replacedFiles = []
		copiedFiles = 0
		try:
			if legacyDataFolder is not None:
				if not os.path.exists(newDataFolder):
					os.makedirs(newDataFolder)
				for entryName in os.listdir(legacyDataFolder):
					if fileNames is not None and entryName not in fileNames:
						continue
					sourcePath = os.path.join(legacyDataFolder, entryName)
					# the database is the one file that changes its name on the way over
					targetName = DATABASE_FILE_NAME if entryName == LEGACY_DATABASE_FILE_NAME else entryName
					targetPath = os.path.join(newDataFolder, targetName)

					# keep whatever is about to be replaced, so the undo has something to put back
					if os.path.exists(targetPath) and not os.path.isdir(targetPath):
						backupName = "%s.pre-migration-%s" % (targetName, timestamp)
						os.rename(targetPath, os.path.join(newDataFolder, backupName))
						replacedFiles.append({"name": targetName, "backupName": backupName})

					if os.path.isdir(sourcePath):
						shutil.copytree(sourcePath, targetPath, dirs_exist_ok=True)
					else:
						shutil.copy2(sourcePath, targetPath)
					copiedFiles += 1
				self._logger.info(
					"Migrated %s entries from '%s' to '%s' (originals kept)"
					% (copiedFiles, legacyDataFolder, newDataFolder)
				)
		except Exception as e:
			self._logger.exception("Legacy data migration failed")
			return failure("Could not copy the data folder: " + str(e))

		settingsMigrated = False
		previousSettings = {}
		try:
			if includeSettings and legacySettings:
				migratableKeys = [k for k in legacySettings.keys() if k not in LEGACY_SETTINGS_NOT_MIGRATABLE]
				previousSettings = self._captureSettingsForUndo(migratableKeys)
				for key in migratableKeys:
					self._settings.set([key], legacySettings[key])
				self._settings.save()
				settingsMigrated = True
				self._logger.info(
					"Migrated settings from 'plugins.%s' to 'plugins.%s'"
					% (LEGACY_IDENTIFIER, self._identifier)
				)
		except Exception as e:
			self._logger.exception("Legacy settings migration failed")
			return failure("Data was copied, but the settings could not be migrated: " + str(e))

		# Written whenever something was actually migrated, not only when files were
		# replaced: migrating into an empty install overwrites nothing, but it still has to
		# count as done - otherwise the banner would never retire for exactly the users the
		# migration is meant for.
		if copiedFiles or replacedFiles or previousSettings:
			self._writeUndoRecord("database", replacedFiles, {})
			if settingsMigrated:
				self._writeUndoRecord("settings", [], previousSettings)

		# The database this plugin has open is now a different file on disk; until the
		# server restarts it keeps serving the old one, so the migrated jobs would not show
		# up and the user would think the migration failed.
		restartRequired = databaseIsSelected
		if restartRequired:
			self._setRestartRequired(True)

		return {
			"success": True,
			"errorMessage": None,
			"conflict": False,
			"copiedFiles": copiedFiles,
			"settingsMigrated": settingsMigrated,
			"restartRequired": restartRequired,
		}


	def _undoLegacyMigration(self, undoKind):
		"""
		Puts back what the named migration replaced: the saved files and the settings values
		it overwrote. Keys that had no value before are removed again. The two kinds are
		independent.
		"""
		undoFilePath = self._getUndoFilePath(undoKind)
		if not os.path.isfile(undoFilePath):
			return {"success": False, "errorMessage": "There is nothing to undo.", "restoredFiles": 0, "restoredSettings": 0}

		try:
			with open(undoFilePath) as undoFile:
				record = json.load(undoFile)
		except Exception as e:
			self._logger.exception("Could not read the migration undo record")
			return {"success": False, "errorMessage": "Could not read the undo record: " + str(e), "restoredFiles": 0, "restoredSettings": 0}

		dataFolder = self.get_plugin_data_folder()
		restoredFiles = 0
		try:
			for entry in record.get("replacedFiles", []):
				backupPath = os.path.join(dataFolder, entry["backupName"])
				targetPath = os.path.join(dataFolder, entry["name"])
				if not os.path.isfile(backupPath):
					continue
				if os.path.exists(targetPath):
					os.remove(targetPath)
				os.rename(backupPath, targetPath)
				restoredFiles += 1
		except Exception as e:
			self._logger.exception("Restoring the replaced files failed")
			return {"success": False, "errorMessage": "Could not restore the files: " + str(e), "restoredFiles": restoredFiles, "restoredSettings": 0}

		restoredSettings = 0
		try:
			previousSettings = record.get("previousSettings", {})
			for key, value in previousSettings.items():
				# None means "had no value of its own" - set(None) makes OctoPrint drop the
				# key again, which is exactly the state we are restoring
				self._settings.set([key], value)
				restoredSettings += 1
			if previousSettings:
				self._settings.save()
		except Exception as e:
			self._logger.exception("Restoring the previous settings failed")
			return {"success": False, "errorMessage": "Files were restored, but the settings were not: " + str(e), "restoredFiles": restoredFiles, "restoredSettings": restoredSettings}

		try:
			os.remove(undoFilePath)
		except OSError:
			self._logger.exception("Could not remove the migration undo record")

		# Same reasoning as after a migration: the database file changed underneath the open
		# handle, so what the plugin serves and what is on disk only line up after a restart.
		restartRequired = restoredFiles > 0
		if restartRequired:
			self._setRestartRequired(True)

		self._logger.info(
			"Undid the last migration: %s file(s), %s setting(s) restored" % (restoredFiles, restoredSettings)
		)
		return {
			"success": True,
			"errorMessage": None,
			"restoredFiles": restoredFiles,
			"restoredSettings": restoredSettings,
			"restartRequired": restartRequired,
		}


	def _getLegacySettingsComparison(self):
		"""
		Per-key comparison of the old plugin's settings against this install's effective
		values, for the settings tab of the migration dialog.

		OctoPrint only stores settings that differ from their default, so the legacy
		namespace holds a handful of keys while the plugin knows dozens. Only the stored
		ones are worth showing here - the rest are identical defaults on both sides.
		"""
		legacySettings = self._getLegacySettings() or {}
		comparison = []
		for key in sorted(legacySettings.keys()):
			if key in LEGACY_SETTINGS_NOT_MIGRATABLE:
				continue
			legacyValue = legacySettings.get(key)
			try:
				currentValue = self._settings.get([key])
			except Exception:
				currentValue = None
			comparison.append({
				"key": key,
				"legacyValue": legacyValue,
				"legacyValueText": StringUtils.to_native_str(legacyValue) if legacyValue is not None else "",
				"currentValue": currentValue,
				"currentValueText": StringUtils.to_native_str(currentValue) if currentValue is not None else "",
				"differs": legacyValue != currentValue,
			})
		return comparison


	def _applyLegacySettings(self, keys):
		"""Writes only the named keys from the legacy namespace into this plugin's own."""
		legacySettings = self._getLegacySettings() or {}
		selectedValues = {}
		for key in keys:
			if key in LEGACY_SETTINGS_NOT_MIGRATABLE:
				continue
			if key in legacySettings:
				selectedValues[key] = legacySettings[key]

		if not selectedValues:
			return {"success": False, "errorMessage": "None of the selected settings exist in the previous installation.", "appliedSettings": 0}

		try:
			previousSettings = self._captureSettingsForUndo(selectedValues.keys())
			for key, value in selectedValues.items():
				self._settings.set([key], value)
			self._settings.save()
			self._writeUndoRecord("settings", [], previousSettings)
		except Exception as e:
			self._logger.exception("Applying legacy settings failed")
			return {"success": False, "errorMessage": "Could not apply the settings: " + str(e), "appliedSettings": 0}

		return {"success": True, "errorMessage": None, "appliedSettings": len(selectedValues)}


	# Increase upload-size (default 100kb) for uploading images
	def bodysize_hook(self, current_max_body_sizes, *args, **kwargs):
		return [("POST", r"/upload/", 20 * 1024 * 1024)]  # size in bytes


	# A commented-out route_hook registering a "mysnapshot" UrlProxyHandler used to sit here.
	# It was never enabled, yet the edit dialog requested that route to freeze the webcam
	# picture during the shutter animation, so every "take picture" logged a 404 and the
	# freeze never happened. The request has been removed; reviving the proxy would also mean
	# porting it off the global "webcam.snapshot" setting, which OctoPrint 2.0 only keeps as
	# a deprecated compatibility overlay.


# If you want your plugin to be registered within OctoPrint under a different name than what you defined in setup.py
# ("OctoPrint-PluginSkeleton"), you may define that here. Same goes for the other metadata derived from setup.py that
# can be overwritten via __plugin_xyz__ control properties. See the documentation for that.
# Name is used in the left Settings-Menue
__plugin_name__ = "Print Job History Extended"
__plugin_pythoncompat__ = ">=3.11,<3.15"

# setup.py's OctoPrint requirement only guards pip. A plugin folder carried over from a 1.x
# install bypasses it entirely and would fail later in confusing ways instead:
# __plugin_check__ is called before the plugin is loaded and refuses the load when it
# returns False. Note ">=2.0.0" already accepts the 2.0.0 release candidates.
OCTOPRINT_COMPAT = ">=2.0.0"


def __plugin_check__():
	if is_octoprint_compatible(OCTOPRINT_COMPAT):
		return True

	logging.getLogger("octoprint.plugins." + __name__).error(
		"Print Job History Extended requires OctoPrint %s, but this is OctoPrint %s. "
		"The plugin will not be loaded. Please update OctoPrint.",
		OCTOPRINT_COMPAT,
		get_octoprint_version_string(),
	)
	return False


def __plugin_load__():
	global __plugin_implementation__
	__plugin_implementation__ = PrintJobHistoryExtendedPlugin()

	global __plugin_hooks__
	__plugin_hooks__ = {
		# "octoprint.server.http.routes": __plugin_implementation__.route_hook,
		"octoprint.comm.protocol.action": __plugin_implementation__.on_receivedActionHook,
		"octoprint.access.permissions": __plugin_implementation__.additional_permissions_hook,
		"octoprint.server.http.bodysize": __plugin_implementation__.bodysize_hook,
		"octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information
	}

# # filamentAnalyseDict = fileData["analysis"]["filament"]
# filamentAnalyseDict = {u'tool4': {u'volume': 185.20129656279946, u'length': 76997.75167999369},
#  u'tool3': {u'volume': 0.0, u'length': 0.0}, u'tool2': {u'volume': 0.0, u'length': 0.0},
#  u'tool1': {u'volume': 0.0, u'length': 0.0}, u'tool0': {u'volume': 0.0, u'length': 0.0}}
# #
# print (len(filamentAnalyseDict))
# for toolId in filamentAnalyseDict:
# 	# print(toolId)
# 	length = filamentAnalyseDict[toolId]["length"]
# 	volumne = filamentAnalyseDict[toolId]["volume"]
# 	print(toolId + " " + str(length))

class MyHandler:
	"""
	A simple handler for logging events. It runs in the listener process and
	dispatches events to loggers based on the name in the received record,
	which then get dispatched, by the logging system, to the handlers
	configured for those loggers.
	"""
	def __init__(self):
		self.technicalLog = ""

	def resetTechnicalLog(self):
		self.technicalLog = ""

	def getTechnicalLog(self):
		return self.technicalLog

	def handle(self, record):
		# if record.name == "root":
		#     logger = logging.getLogger()
		# else:
		#     logger = logging.getLogger(record.name)
		#
		# if logger.isEnabledFor(record.levelno):
		#     # The process name is transformed just to show that it's the listener
		#     # doing the logging to files and console
		#     record.processName = '%s (for %s)' % (current_process().name, record.processName)
		#     logger.handle(record)

		# BOOOOMMM "AttributeError: 'LogRecord' object has no attribute 'asctime'"
		# print("AAAAA****************************")
		#print(record)
		# asctime = record.asctime #2022-01-22 14:50:09,729
		name = record.name #octoprint.plugins.SpoolManager
		module = record.module # SpoolManagerAPI
		levelname = record.levelname # DEBUG
		message = record.message # API Load all spool

		if (name.startswith("octoprint.plugins.PrintJobHistoryExtended")):
			self.technicalLog = self.technicalLog + message + "\n"

		# print("EEEEE****************************")
		pass

class MyQueueHandler(logging.handlers.QueueHandler):

	def enqueue(self, record):
		"""
		Enqueue a record.

		The base implementation uses put_nowait. You may want to override
		this method if you want to use blocking, timeouts or custom queue
		implementations.
		"""
		# print("*** Current len: " + str (self.queue.qsize()))
		if (self.queue.full()):
			print("Something wrong with the listener, because loggging queue is full. No new log-record is added.")
		else:
			self.queue.put_nowait(record)


