# coding=utf-8
from __future__ import absolute_import

import logging.handlers
import threading
import time
from queue import Queue

import octoprint.plugin
from octoprint.events import Events

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

from octoprint_PrintJobHistoryExtended.common import StringUtils, DateTimeUtils

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

# Settings that must not be carried over: the two path keys point into the *old* plugin's
# data folder and would send this install back to the legacy database, and the version
# describes the installed plugin rather than a user choice.
LEGACY_SETTINGS_NOT_MIGRATABLE = frozenset([
	SettingsKeys.SETTINGS_KEY_DATABASE_PATH,
	SettingsKeys.SETTINGS_KEY_SNAPSHOT_PATH,
	"installed_version",
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

	def initialize(self):
		self._preHeatPluginImplementation = None
		self._preHeatPluginImplementationState = None
		self._filamentManagerPluginImplementation = None
		self._filamentManagerPluginImplementationState = None
		self._displayLayerProgressPluginImplementation = None
		self._displayLayerProgressPluginImplementationState = None
		self._spoolManagerPluginImplementation = None
		self._spoolManagerPluginImplementationState = None
		self._spoolmanPluginImplementation = None
		self._spoolmanPluginImplementationState = None
		self._ultimakerFormatPluginImplementation = None
		self._ultimakerFormatPluginImplementationState = None
		self._prusaSlicerThumbnailsPluginImplementation = None
		self._prusaSlicerThumbnailsPluginImplementationState = None
		self._costEstimationPluginImplementation = None
		self._costEstimationPluginImplementationState = None
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

		self._cameraManager.initCamera(pluginDataBaseFolder, pluginBaseFolder, self._settings)

		# Init values for initial settings view-page
		self._settings.set([SettingsKeys.SETTINGS_KEY_DATABASE_PATH], self._databaseManager.getDatabaseFileLocation())
		self._settings.set([SettingsKeys.SETTINGS_KEY_SNAPSHOT_PATH], self._cameraManager.getSnapshotFileLocation())
		self._settings.save()

		# OTHER STUFF
		self._currentPrintJobModel = None

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

	def _sendReloadTableToClient(self, shouldSend=True):
		if (shouldSend == True):
			payload = {
				"action": "reloadTableItems"
			}
			self._sendDataToClient(payload)

	def _sendMessageConfirmToClient(self, title, message):
		confirmMessageData = {
			"title": title,
			"message": message
		}
		self._sendDataToClient(dict(action="showMessageConfirmDialog",
									confirmMessageData=confirmMessageData))

	def _checkAndLoadThirdPartyPluginInfos(self, sendToClient=False):
		pluginInfo = self._getPluginInformation(SettingsKeys.PLUGIN_PREHEAT)
		self._preHeatPluginImplementationState = pluginInfo[0]
		self._preHeatPluginImplementation = pluginInfo[1]
		preHeatCurrentVersion = pluginInfo[2]
		preHeatRequiredVersion = pluginInfo[3]

		pluginInfo = self._getPluginInformation(SettingsKeys.PLUGIN_FILAMENT_MANAGER)
		self._filamentManagerPluginImplementationState = pluginInfo[0]
		self._filamentManagerPluginImplementation = pluginInfo[1]
		filamentManagerCurrentVersion = pluginInfo[2]
		filamentManagerRequiredVersion = pluginInfo[3]

		pluginInfo = self._getPluginInformation(SettingsKeys.PLUGIN_DISPLAY_LAYER_PROGRESS)
		self._displayLayerProgressPluginImplementationState = pluginInfo[0]
		self._displayLayerProgressPluginImplementation = pluginInfo[1]
		displayLayerCurrentVersion = pluginInfo[2]
		displayLayerRequiredVersion = pluginInfo[3]

		pluginInfo = self._getPluginInformation(SettingsKeys.PLUGIN_SPOOL_MANAGER)
		self._spoolManagerPluginImplementationState = pluginInfo[0]
		self._spoolManagerPluginImplementation = pluginInfo[1]
		spoolManagerCurrentVersion = pluginInfo[2]
		spoolManagerRequiredVersion = pluginInfo[3]

		pluginInfo = self._getPluginInformation(SettingsKeys.PLUGIN_SPOOLMAN)
		self._spoolmanPluginImplementationState = pluginInfo[0]
		self._spoolmanPluginImplementation = pluginInfo[1]
		spoolmanCurrentVersion = pluginInfo[2]
		spoolmanRequiredVersion = pluginInfo[3]

		pluginInfo = self._getPluginInformation(SettingsKeys.PLUGIN_ULTIMAKER_FORMAT_PACKAGE)
		self._ultimakerFormatPluginImplementationState = pluginInfo[0]
		self._ultimakerFormatPluginImplementation = pluginInfo[1]
		ultimakerCurrentVersion = pluginInfo[2]
		ultimakerRequiredVersion = pluginInfo[3]

		pluginInfo = self._getPluginInformation(SettingsKeys.PLUGIN_PRUSA_SLICER_THUMNAIL)
		self._prusaSlicerThumbnailsPluginImplementationState = pluginInfo[0]
		self._prusaSlicerThumbnailsPluginImplementation = pluginInfo[1]
		pruseSlicerCurrentVersion = pluginInfo[2]
		pruseSlicerRequiredVersion = pluginInfo[3]

		pluginInfo = self._getPluginInformation(SettingsKeys.PLUGIN_COST_ESTIMATION)
		self._costEstimationPluginImplementationState = pluginInfo[0]
		self._costEstimationPluginImplementation = pluginInfo[1]
		costPluginCurrentVersion = pluginInfo[2]
		costPluginRequiredVersion = pluginInfo[3]

		pluginInfo = self._getPluginInformation(SettingsKeys.PLUGIN_PRINT_HISTORY)
		if ("enabled" == pluginInfo[0]):
			self._printHistoryPluginImplementation = pluginInfo[1]
		else:
			self._printHistoryPluginImplementation = None
		printHistoryCurrentVersion = pluginInfo[2]
		printHistoryRequiredVersion = pluginInfo[3]

		self._logger.info("Plugin-State:\n"
						  "| PreHeat=" + self._preHeatPluginImplementationState + " (" + str(preHeatCurrentVersion) + ")\n"
						  "| filamentmanager=" + self._filamentManagerPluginImplementationState + " (" + str(filamentManagerCurrentVersion) + ")\n"
						  "| DisplayLayerProgress=" + self._displayLayerProgressPluginImplementationState + " (" + str(displayLayerCurrentVersion) + ")\n"
						  "| SpoolManager=" + self._spoolManagerPluginImplementationState + " (" + str(spoolManagerCurrentVersion) + ")\n"
                          "| Spoolman=" + self._spoolmanPluginImplementationState + " (" + str(spoolmanCurrentVersion) + ")\n"
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

				if self._preHeatPluginImplementation == None:
					missingMessage = missingMessage + "<li><a target='_newTab' href='https://plugins.octoprint.org/plugins/preheat/'>PreHeat Button (" + str(preHeatRequiredVersion) + "+)</a> (<b>" + self._preHeatPluginImplementationState + "</b>)</li>"

				# if at least one filemant tracker is installed, then don't inform the user about the missing other plugin
				if (self._spoolManagerPluginImplementation == None and self._filamentManagerPluginImplementation == None and self._spoolmanPluginImplementation == None):
					missingMessage = missingMessage + "<li><a target='_newTab' href='https://plugins.octoprint.org/plugins/SpoolManager/'>SpoolManager (" + str(spoolManagerRequiredVersion) + "+)</a> (<b>" + self._spoolManagerPluginImplementationState + "</b>)<br/><b>or</b></li>"
					missingMessage = missingMessage + "<li><a target='_newTab' href='https://plugins.octoprint.org/plugins/Spoolman/'>Spoolman (" + str(spoolmanRequiredVersion) + "+)</a> (<b>" + self._spoolmanPluginImplementationState + "</b>)<br/><b>or</b></li>"
					missingMessage = missingMessage + "<li><a target='_newTab' href='https://plugins.octoprint.org/plugins/filamentmanager/'>FilamentManager (" + str(filamentManagerRequiredVersion) + "+)</a> (<b>" + self._filamentManagerPluginImplementationState + "</b>)</li>"

				if self._displayLayerProgressPluginImplementation == None:
					missingMessage = missingMessage + "<li><a target='_newTab' href='https://plugins.octoprint.org/plugins/DisplayLayerProgress/'>DisplayLayerProgress (" + str(displayLayerRequiredVersion) + "+)</a> (<b>" + self._displayLayerProgressPluginImplementationState + "</b>)</li>"

				if self._ultimakerFormatPluginImplementation == None:
					missingMessage = missingMessage + "<li><a target='_newTab' href='https://plugins.octoprint.org/plugins/UltimakerFormatPackage/'>Cura Thumbnails (" + str(ultimakerRequiredVersion) + "+)</a> (<b>" + self._ultimakerFormatPluginImplementationState + "</b>)</li>"

				if self._prusaSlicerThumbnailsPluginImplementation == None:
					missingMessage = missingMessage + "<li><a target='_newTab' href='https://plugins.octoprint.org/plugins/prusaslicerthumbnails/'>PrusaSlicer Thumbnails (" + str(pruseSlicerRequiredVersion) + "+)</a> (<b>" + self._prusaSlicerThumbnailsPluginImplementationState + "</b>)</li>"

				if self._costEstimationPluginImplementation == None:
					missingMessage = missingMessage + "<li><a target='_newTab' href='https://plugins.octoprint.org/plugins/costestimation/'>CostEstimation (" + str(costPluginRequiredVersion) +"+)</a> (<b>" + self._costEstimationPluginImplementationState + "</b>)</li>"
					# missingMessage = missingMessage + "<li><a target='_newTab' href='https://plugins.octoprint.org/plugins/costestimation/'>CostEstimation</a> (<b>Wrong Version, expected: 3.4.0+ current: " + currentVersion + "</b>)</li>"

				if missingMessage != "":
					missingMessage = "<ul>" + missingMessage + "</ul>"
					self._sendDataToClient(dict(action="missingPlugin",
												message=missingMessage))

	def _checkForMissingFilamentTracking(self):

		currentFilamentTrackingPlugin = self._settings.get([SettingsKeys.SETTINGS_KEY_SELECTED_FILAMENTTRACKER_PLUGIN])
		# check if the current tracking plugin is known
		if (currentFilamentTrackingPlugin != SettingsKeys.KEY_SELECTED_NONE_PLUGIN and
			currentFilamentTrackingPlugin != SettingsKeys.KEY_SELECTED_SPOOLMANAGER_PLUGIN and
			currentFilamentTrackingPlugin != SettingsKeys.KEY_SELECTED_SPOOLMAN_PLUGIN and
			currentFilamentTrackingPlugin != SettingsKeys.KEY_SELECTED_FILAMENTMANAGER_PLUGIN):
			# unknown -> set to none
			self._settings.set([SettingsKeys.SETTINGS_KEY_SELECTED_FILAMENTTRACKER_PLUGIN],
							   SettingsKeys.KEY_SELECTED_NONE_PLUGIN)
			self._settings.save()

		if ( (currentFilamentTrackingPlugin == SettingsKeys.KEY_SELECTED_SPOOLMANAGER_PLUGIN) and
			 (self._isSpoolManagerInstalledAndEnabled() == False) ):
			self._settings.set([SettingsKeys.SETTINGS_KEY_SELECTED_FILAMENTTRACKER_PLUGIN],
							   SettingsKeys.KEY_SELECTED_NONE_PLUGIN)
			self._settings.save()

		if ( (currentFilamentTrackingPlugin == SettingsKeys.KEY_SELECTED_SPOOLMAN_PLUGIN) and
			(self._isSpoolmanInstalledAndEnabled() == False) ):
			self._settings.set([SettingsKeys.SETTINGS_KEY_SELECTED_FILAMENTTRACKER_PLUGIN],
							   SettingsKeys.KEY_SELECTED_NONE_PLUGIN)
			self._settings.save()

		if ( (currentFilamentTrackingPlugin == SettingsKeys.KEY_SELECTED_FILAMENTMANAGER_PLUGIN) and
			 (self._isFilamentManagerInstalledAndEnabled() == False) ):
			self._settings.set([SettingsKeys.SETTINGS_KEY_SELECTED_FILAMENTTRACKER_PLUGIN], SettingsKeys.KEY_SELECTED_NONE_PLUGIN)
			self._settings.save()

		notifyUser = self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_NO_NOTIFICATION_FILAMENTTRACKERING_PLUGIN_SELECTION]) == False
		if ( (self._isSpoolManagerInstalledAndEnabled() == True or self._isSpoolmanInstalledAndEnabled() == True or self._isFilamentManagerInstalledAndEnabled() == True) and
			 (self._settings.get([SettingsKeys.SETTINGS_KEY_SELECTED_FILAMENTTRACKER_PLUGIN]) == SettingsKeys.KEY_SELECTED_NONE_PLUGIN) ):
				# Plugins installed, but currently 'none' is selected
				self._logger.warning("Filamentracking is disabled, but some plugins are installed!");
				if (notifyUser):
					self._sendMessageToClient("notice", "Filamenttracking is possible!", "Select an tracking plugin in settings", True)
		else:
			if (self._settings.get([SettingsKeys.SETTINGS_KEY_SELECTED_FILAMENTTRACKER_PLUGIN]) == SettingsKeys.KEY_SELECTED_NONE_PLUGIN):
				if (notifyUser):
					self._sendMessageToClient("notice", "Filamenttracking not possible!",
											  "No tracking plugin is installed/enabled", True)

	def _isSpoolManagerInstalledAndEnabled(self):
		return True if self._spoolManagerPluginImplementation != None and self._spoolManagerPluginImplementationState == "enabled" else False

	def _isSpoolmanInstalledAndEnabled(self):
		return True if self._spoolmanPluginImplementation != None and self._spoolmanPluginImplementationState == "enabled" else False

	def _isFilamentManagerInstalledAndEnabled(self):
		return True if self._filamentManagerPluginImplementation != None and self._filamentManagerPluginImplementationState == "enabled" else False

	def _isCostEstimationInstalledAndEnabled(self):
		return True if self._costEstimationPluginImplementation != None and self._costEstimationPluginImplementationState == "enabled" else False

	def _isPreHeatInstalledAndEnabled(self):
		return True if self._preHeatPluginImplementation != None and self._preHeatPluginImplementationState == "enabled" else False



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

	# Grabs all informations for the filament attributes
	def _createAndAssignFilamentModel(self, printJob, payload):

		self._logger.info("----- Start reading filament -----")
		filePath = payload["path"]
		fileData = self._file_manager.get_metadata(payload["origin"], filePath)

		# - grab calcualted data for each tool
		# - grap measured data for each tool
		filamentCalculatedDict = self._readCalculatedFilamentMetaData(fileData)
		filamentExtrusionArray = self._readMeasuredFilament()
		selectedSpoolDataDict = self._getSelectedSpools()
		# isMultiToolPrint = len(filamentCalculatedDict) > 1

		# - add always "total"
		calculatedTotalLength = 0.0

		totalFilamentModel = FilamentModel()
		totalFilamentModel.toolId = "total"
		printJob.addFilamentModel(totalFilamentModel)

		# - assign all spool informations to total
		allSpoolNames = ""
		allVendors = ""
		allMaterials = ""

		# - assign calculated values
		if (filamentCalculatedDict != None):

			for toolId in filamentCalculatedDict:
				filamentModel = FilamentModel()
				filamentModel.toolId = toolId

				calculatedLength = filamentCalculatedDict[toolId]["length"]
				# not needed calculatedVolumne = filamentCalculatedDict[toolId]["volume"]

				filamentModel.calculatedLength = calculatedLength
				calculatedTotalLength = calculatedTotalLength + calculatedLength
				printJob.addFilamentModel(filamentModel)

				# Assign SpoolData (e.g. Name) for the calculated tools (only for calc-lenght > 0)
				# get spool data, if available
				if (calculatedLength > 0 and selectedSpoolDataDict != None and toolId in selectedSpoolDataDict):
					spoolData = selectedSpoolDataDict[toolId]

					filamentModel.spoolName = spoolData["spoolName"]
					filamentModel.vendor = spoolData["vendor"]
					filamentModel.material = spoolData["material"]
					filamentModel.diameter = spoolData["diameter"]
					filamentModel.density = spoolData["density"]

					filamentModel.spoolCost = spoolData["spoolCost"]
					filamentModel.weight = spoolData["weight"]

					if (filamentModel.spoolName != None and (filamentModel.spoolName in allSpoolNames) == False):
						if (allSpoolNames != ""):
							allSpoolNames = allSpoolNames + ", "
						allSpoolNames = allSpoolNames + filamentModel.spoolName

					if (filamentModel.vendor != None and (filamentModel.vendor in allVendors) == False):
						if (allVendors != ""):
							allVendors = allVendors + ", "
						allVendors = allVendors + filamentModel.vendor

					if (filamentModel.material != None and (filamentModel.material in allMaterials) == False):
						if (allMaterials != ""):
							allMaterials = allMaterials + ", "
						allMaterials = allMaterials + filamentModel.material
				pass

		totalFilamentModel.calculatedLength = calculatedTotalLength
		totalFilamentModel.spoolName = allSpoolNames
		totalFilamentModel.vendor = allVendors
		totalFilamentModel.material = allMaterials

		# - assign measured values
		if (filamentExtrusionArray != None):
			usedTotalLength = 0.0
			usedTotaWeight = 0.0
			usedTotalCost = 0.0
			toolIndex = 0
			for usedLength in filamentExtrusionArray:
				toolId = "tool" + str(toolIndex)
				filamentModel = printJob.getFilamentModelByToolId(toolId)
				if (filamentModel == None):
					filamentModel = FilamentModel()
					filamentModel.toolId = toolId
					printJob.addFilamentModel(filamentModel)

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

				toolIndex = toolIndex + 1
			# - add total values
			filamentModel = printJob.getFilamentModelByToolId("total")
			if (filamentModel == None):
				filamentModel = FilamentModel()
				filamentModel.toolId = "total"
				printJob.addFilamentModel(filamentModel)

			totalFilamentModel.usedLength = usedTotalLength
			totalFilamentModel.usedWeight = usedTotaWeight
			totalFilamentModel.usedCost = usedTotalCost

			self._logger.info("total: usedTotalLength='"+str(usedTotalLength)+"'; usedTotaWeight='"+str(usedTotaWeight)+"'; usedTotalCost='"+str(usedTotalCost)+"'")


	# read the total extrusion of each tool, like this
	# return [123.123, 234.234, 0, 0]
	def _readMeasuredFilament(self):
		result = None
		# get some data from the selected filament tracker plugin
		filamentTrackerPlugin = self._settings.get([SettingsKeys.SETTINGS_KEY_SELECTED_FILAMENTTRACKER_PLUGIN])
		if (SettingsKeys.KEY_SELECTED_SPOOLMANAGER_PLUGIN == filamentTrackerPlugin):
			# get data from SPOOLMANAGER
			try:
				result = self._spoolManagerPluginImplementation.api_getExtrusionAmount()
			except:
				self._logger.warning("You don't use the latest SpoolManager Version 1.4+")
			if (result == None):
				# try the old way
				result = self._spoolManagerPluginImplementation.myFilamentOdometer.getExtrusionAmount()
			pass
		elif (SettingsKeys.KEY_SELECTED_SPOOLMAN_PLUGIN == filamentTrackerPlugin):
			# get data from SPOOLMAN

			# TODO: should be replaced with a proper exposed function call
			# Currently, we rely on the fact, that the PRINT_DONE event of this plugin is executed before Spoolman.
			# Spoolman clears the extrusionAmount after handling the PRINT_DONE event.
			peek_stats_helpers = self._spoolmanPluginImplementation.lastPrintOdometerLoad.send(False)
			current_extrusion_stats = peek_stats_helpers['get_current_extrusion_stats']()
			result = current_extrusion_stats['extrusionAmount'][:]

			pass
		elif (SettingsKeys.KEY_SELECTED_FILAMENTMANAGER_PLUGIN == filamentTrackerPlugin):
			# myFilamentOdometer, since FilamentManager V1.7.2
			result = self._filamentManagerPluginImplementation.myFilamentOdometer.getExtrusionAmount()
			pass
		else:
			self._logger.info("There is no plugin for filament tracking available. Installed and Enabled?")
		return result

	# dict of this
	# {u'tool4': {u'volume': 185.20129656279946, u'length': 76997.75167999369},
	#  u'tool3': {u'volume': 0.0, u'length': 0.0}, u'tool2': {u'volume': 0.0, u'length': 0.0},
	#  u'tool1': {u'volume': 0.0, u'length': 0.0}, u'tool0': {u'volume': 0.0, u'length': 0.0}}

	def _readCalculatedFilamentMetaData(self, fileData):
		filamentAnalyseDict = None
		if "analysis" in fileData:
			if "filament" in fileData["analysis"]:
				filamentAnalyseDict = fileData["analysis"]["filament"]
		if (filamentAnalyseDict == None):
			self._logger.info("There is no calculated filament data in meta-file")
		return filamentAnalyseDict

	# read the selected tools
	# return  {
	# 'tool0': {'databaseId': 4711, 'spoolName': 'NewSpool', 'weight': 2000.0, 'spoolCost': 123.2, 'material': 'PLA', 'vendor: 'MaterMost', 'density': 4.25, 'diameter': 1.75, },
	# 'tool1': {}
	# },
	def _getSelectedSpools(self):
		result = None
		# get some data from the selected filament tracker plugin
		filamentTrackerPlugin = self._settings.get([SettingsKeys.SETTINGS_KEY_SELECTED_FILAMENTTRACKER_PLUGIN])
		if (SettingsKeys.KEY_SELECTED_SPOOLMANAGER_PLUGIN == filamentTrackerPlugin):
			self._logger.info("Try reading filament from SpoolManager...")
			# get data from SPOOLMANAGER
			selectedSpoolInformations = self._spoolManagerPluginImplementation.api_getSelectedSpoolInformations()
			if (selectedSpoolInformations != None):
				result = {}
				for spoolData in selectedSpoolInformations:
					if (spoolData != None):
						toolId = "tool" + str(spoolData["toolIndex"])
						databaseId = spoolData["databaseId"]
						spoolName = spoolData["spoolName"]
						weight = spoolData["weight"]
						spoolCost = spoolData["cost"]

						material = spoolData["material"]
						vendor = spoolData["vendor"]
						density = spoolData["density"]
						diameter = spoolData["diameter"]

						result[toolId] = {
							"databaseId": databaseId,
							"spoolName": spoolName,
							"material": material,
							"vendor":  vendor,
							"density": density,
							"diameter":  diameter,
							"spoolCost": spoolCost,
							"weight": weight
						}
						self._logger.info(
							" reading for '" + toolId + "',  Spool: '" + spoolName + "', Material: '" + material + "', Vendor: '" + vendor + "'")
			pass
		elif (SettingsKeys.KEY_SELECTED_SPOOLMAN_PLUGIN == filamentTrackerPlugin):
			self._logger.info("Try reading filament from Spoolman...")
			# get data from SPOOLMAN

			# TODO: should be replaced with a proper exposed function call
			# Especially getting the selected spools via a protected _settings variable is BAD
			spools_available = self._spoolmanPluginImplementation.getSpoolmanConnector().handleGetSpoolsAvailable()
			selected_spool_ids = self._spoolmanPluginImplementation._settings.get(['selectedSpoolIds'])

			spool_by_id = {str(spool['id']): spool for spool in spools_available['data']['spools']}

			spools = {}
			for tool_id, spool_id in selected_spool_ids.items():
				spool = spool_by_id.get(spool_id['spoolId'])
				if spool:
					spools[tool_id] = spool
				else:
					spools[tool_id] = None

			result = {}
			for toolId, spoolData in spools.items():
				if (spoolData != None):
					toolId = "tool" + toolId
					spoolName = spoolData["filament"]["name"]
					weight = spoolData["filament"]["weight"]
					spoolCost = spoolData["price"]

					material = spoolData["filament"]["material"]
					vendor = spoolData["filament"]["vendor"]["name"]
					density = spoolData["filament"]["density"]
					diameter = spoolData["filament"]["diameter"]

					result[toolId] = {
						"spoolName": spoolName,
						"material": material,
						"vendor":  vendor,
						"density": density,
						"diameter":  diameter,
						"spoolCost": spoolCost,
						"weight": weight
					}
					self._logger.info(
						" reading for '" + toolId + "',  Spool: '" + spoolName + "', Material: '" + material + "', Vendor: '" + vendor + "'")
			pass
		elif (SettingsKeys.KEY_SELECTED_FILAMENTMANAGER_PLUGIN == filamentTrackerPlugin):
			self._logger.info("Try reading filament from FilamentManager...")
			# myFilamentOdometer, since FilamentManager V1.7.2
			selectedSpools = self._filamentManagerPluginImplementation.filamentManager.get_all_selections(self._filamentManagerPluginImplementation.client_id)
			if selectedSpools != None and len(selectedSpools) > 0:
				result = {}
				for currentSpoolData in selectedSpools:
					toolId = "tool" + str(currentSpoolData["tool"])
					spoolData = currentSpoolData["spool"]
					databaseId = spoolData["id"]
					spoolName = spoolData["name"]
					weight = spoolData["weight"]
					spoolCost = spoolData["cost"]

					profileData = spoolData["profile"]
					material = profileData["material"]
					vendor = profileData["vendor"]
					density = profileData["density"]
					diameter = profileData["diameter"]

					result[toolId] = {
						"databaseId": databaseId,
						"spoolName": spoolName,
					    "material": material,
					    "vendor":  vendor,
					    "density": density,
					  	"diameter":  diameter,
						"spoolCost": spoolCost,
						"weight": weight
					}
					self._logger.info(" reading for '"+toolId+"',  Spool: '"+spoolName+"', Material: '"+material+"', Vendor: '"+vendor+"'")
					pass
			pass
		else:
			self._logger.info("There is no plugin for spool selection. Installed and Enabled?")
		return result

	def _calculateFilamentWeightForLength(self, usedLength, diameter, density):
		result = 0.0
		if (usedLength != None and diameter != None and density != None):
			radius = diameter / 2.0
			volume = usedLength * math.pi * radius * radius / 1000.0
			result = volume * density
		return result


	def _updatePrintJobModelWithLayerHeightInfos(self, dlpPayload):
		totalLayers = dlpPayload["totalLayer"]
		currentLayer = dlpPayload["currentLayer"]
		self._currentPrintJobModel.printedLayers = currentLayer + " / " + totalLayers

		totalHeight = dlpPayload["totalHeightFormatted"]
		currentHeight = dlpPayload["currentHeightFormatted"]
		self._currentPrintJobModel.printedHeight = currentHeight + " / " + totalHeight


	def _createPrintJobModel(self, payload):
		self._currentPrintJobModel = PrintJobModel()
		self._currentPrintJobModel.printStartDateTime = datetime.datetime.now()

		self._currentPrintJobModel.fileOrigin = payload["origin"]
		self._currentPrintJobModel.fileName = payload["name"]
		self._currentPrintJobModel.filePathName = payload["path"]

		# self._file_manager.path_on_disk()
		if "owner" in payload:
			self._currentPrintJobModel.userName = payload["owner"]
		else:
			self._currentPrintJobModel.userName = "John Doe"
		self._currentPrintJobModel.fileSize = payload["size"]

		tempFound = False
		toolId = self._settings.get([SettingsKeys.SETTINGS_KEY_DEFAULT_TOOL_ID])
		tempTool = -1
		tempBed = 0

		shouldReadTemperatureFromPreHeat = self._settings.get_boolean(
			[SettingsKeys.SETTINGS_KEY_TAKE_TEMPERATURE_FROM_PREHEAT])
		if (shouldReadTemperatureFromPreHeat == True):
			self._logger.info("Try reading Temperature from PreHeat-Plugin...")

			if (self._preHeatPluginImplementation != None):
				path_on_disk = octoprint.server.fileManager.path_on_disk(self._currentPrintJobModel.fileOrigin,
																		 self._currentPrintJobModel.filePathName)

				preHeatTemperature = self._preHeatPluginImplementation.read_temperatures_from_file(path_on_disk)
				if preHeatTemperature != None:
					if "bed" in preHeatTemperature:
						tempBed = preHeatTemperature["bed"]
						tempFound = True
					if toolId in preHeatTemperature:
						tempTool = preHeatTemperature[toolId]  # "tool0"
						tempFound = True
					else:
						self._logger.warning(
							"... PreHeat-Temperatures does not include default Extruder-Tool '" + toolId + "'")
				pass
			else:
				self._logger.warning("... PreHeat Button Plugin not installed/enabled")

		if (tempFound == True):
			self._logger.info(
				"... Temperature found '" + str(tempBed) + "' Tool '" + toolId + "' '" + str(tempTool) + "'")
			self._addTemperatureToPrintModel(self._currentPrintJobModel, tempBed, toolId, tempTool)
		else:
			# readTemperatureFromPrinter
			# because temperature is 0 at the beginning, we need to wait a couple of seconds (maybe 3)
			self._readAndAssignCurrentTemperatureDelayed(self._currentPrintJobModel)


	def _readCurrentTemperatureFromPrinterAsync(self, printer, printJobModel, addTemperatureToPrintModel):
		dealyInSeconds = self._settings.get_int([SettingsKeys.SETTINGS_KEY_DELAY_READING_TEMPERATURE_FROM_PRINTER])
		time.sleep(dealyInSeconds)

		currentTemps = printer.get_current_temperatures()
		if (currentTemps != None and "bed" in currentTemps and "tool0" in currentTemps):
			tempBed = currentTemps["bed"]["target"]
			# Maybe an other tool should be used.
			toolId = self._settings.get([SettingsKeys.SETTINGS_KEY_DEFAULT_TOOL_ID])  # "tool0"
			tempTool = -1
			try:
				tempTool = currentTemps[toolId]["target"]
			except Exception as e:
				self._logger.error("Could not read temperature from Tool '" + toolId + "'", e)

			self._logger.info(
				"Temperature read from Printer Bed: '" + str(tempBed) +
				"' Tool " + toolId + ": '" + str(tempTool) + "' after a delay of '"+str(dealyInSeconds)+"' seconds")
			addTemperatureToPrintModel(printJobModel, tempBed, toolId, tempTool)


	def _readAndAssignCurrentTemperatureDelayed(self, printJobModel):
		thread = threading.Thread(name='ReadCurrentTemperature',
								  target=self._readCurrentTemperatureFromPrinterAsync,
								  args=(self._printer, printJobModel, self._addTemperatureToPrintModel,))
		thread.daemon = True
		thread.start()
		pass


	def _addTemperatureToPrintModel(self, printJobModel, bedTemp, toolId, toolTemp):
		tempModel = TemperatureModel()
		tempModel.sensorName = "bed"
		# tempModel.sensorValue = bedTemp if bedTemp != None else"-"
		tempModel.sensorValue = bedTemp if bedTemp != None else "-"
		printJobModel.addTemperatureModel(tempModel)

		tempModel = TemperatureModel()
		tempModel.sensorName = toolId  # "tool0"
		tempModel.sensorValue = toolTemp if toolTemp != None else "-"
		printJobModel.addTemperatureModel(tempModel)


	def _addCostsToPrintModel(self, printJobModel):

		self._logger.info("----- Start reading costs -----")
		#             var costData = {
		#                 filename: filename,
		#                 filepath: filepath,
		#                 costResult: costResult,
		#                 filamentCost: filamentCost,
		#                 electricityCost: electricityCost,
		#                 printerCost: printerCost,
		#                 otherCostLabel: otherCostLabel,
		#                 otherCost: otherCost,
		#             }
		if (self._isCostEstimationInstalledAndEnabled()):
			# self._logger.info("Try reading Costs from CostEstimation-Plugin")
			# costData = self._costEstimationPluginImplementation.api_getCurrentCostsValues()

			# TODO own cost calculation
			printTimeInSeconds = DateTimeUtils.calcDurationInSeconds(printJobModel.printEndDateTime, printJobModel.printStartDateTime)
			allFilamentModels = printJobModel.getFilamentModels(withoutTotal=True)
			self._logger.info("Try calculating Costs with CostEstimation-Plugin settings")
			costData = self._calculateCostData(allFilamentModels , printTimeInSeconds)

			totalCosts = costData["totalCosts"]	# float = 11.96
			filamentCost = costData["filamentCost"] # float = 0.06002333...
			electricityCost = costData["electricityCost"] # float = 0.0213454...
			printerCost = costData["printerCost"] # float = 11.87653993...
			# otherCostLabel = costData["otherCostLabel"] # str = Delivery
			# otherCost = costData["otherCost"] # float = 11.87653993...
			withDefaultSpoolValues = costData["withDefaultSpoolValues"] # boolean

			costModel = CostModel()
			costModel.totalCosts = totalCosts
			costModel.filamentCost = filamentCost
			costModel.electricityCost = electricityCost
			costModel.printerCost = printerCost
			# costModel.otherCostLabel = otherCostLabel
			# costModel.otherCost = otherCost
			costModel.withDefaultSpoolValues = withDefaultSpoolValues

			printJobModel.setCosts(costModel)
			if ("filename" in costData):
				costData.pop("filename")
			if ("filepath" in costData):
				costData.pop("filepath")
			self._logger.info("Adding costs from CostEstimation-Plugin: "+ str(costData))
		else:
			self._logger.info("Costs could not captured, because CostEstimation-Plugin not installed/enabled")

		pass


	def _calculateCostData(self, allFilamentModels, printTimeInSeconds):
		#             var costData = {
		#                 totalCosts:
		#                 filamentCost: filamentCost,
		#                 electricityCost: electricityCost,
		#                 printerCost: printerCost,
		# 				  withDefaultSpoolValues
		#             }

		withDefaultSpoolValues = False
		printTimeInHours = printTimeInSeconds / 3600

		# read cost-settings froom Cost-Plugin
		currencySymbol = self._costEstimationPluginImplementation._settings.get(["currency"]) # Euro
		currencyFormat = self._costEstimationPluginImplementation._settings.get(["currencyFormat"]) # Euro

		# calc: electricityCost
		electricityCost = None
		powerConsumption = self._costEstimationPluginImplementation._settings.get(["powerConsumption"]) # kW
		costOfElectricity = self._costEstimationPluginImplementation._settings.get(["costOfElectricity"]) # Euro/kWh

		powerConsumption = StringUtils.transformToFloatOrNone(powerConsumption)
		costOfElectricity = StringUtils.transformToFloatOrNone(costOfElectricity)
		if (powerConsumption is None or costOfElectricity is None):
			self._logger.error(
				"Could not calculate electricityCost, because powerConsumption or costOfElectricity is none")
		else:
			costPerHour = powerConsumption * costOfElectricity
			electricityCost = costPerHour * printTimeInHours
			self._logger.info("costPerHour '"+str(costPerHour)+"' = powerConsumption '"+str(powerConsumption)+"' * costOfElectricity '"+str(costOfElectricity)+"'" )
			self._logger.info("electricityCost '"+str(electricityCost)+"' = costPerHour '"+str(costPerHour)+"' * printTimeInHours '"+str(printTimeInHours)+"'" )

		# calc: printerCost
		printerCost = None
		priceOfPrinter = self._costEstimationPluginImplementation._settings.get(["priceOfPrinter"]) # Euro
		lifespanOfPrinter = self._costEstimationPluginImplementation._settings.get(["lifespanOfPrinter"]) # h
		maintenanceCosts = self._costEstimationPluginImplementation._settings.get(["maintenanceCosts"]) # Euro/h

		priceOfPrinter = StringUtils.transformToFloatOrNone(priceOfPrinter)
		lifespanOfPrinter = StringUtils.transformToFloatOrNone(lifespanOfPrinter)
		maintenancePerHour = StringUtils.transformToFloatOrNone(maintenanceCosts)

		if (priceOfPrinter is None or lifespanOfPrinter is None or maintenancePerHour is None):
			self._logger.error(
				"Could not calculate printerCost, because purchasePrice or lifespanOfPrinter or maintenancePerHour is none")
		else:
			# 	value_when_true if condition else value_when_false
			depreciationPerHour = priceOfPrinter / lifespanOfPrinter if lifespanOfPrinter > 0 else 0
			printerCost = (depreciationPerHour + maintenancePerHour) * printTimeInHours
			self._logger.info("printerCost '"+str(printerCost)+"' = (depreciationPerHour '"+str(depreciationPerHour)+"' + maintenancePerHour '"+str(maintenancePerHour)+"') * printTimeInHours '"+str(printTimeInHours)+"'" )

		# calc: filamentCost
		filamentCost = None
		# - do we have measured filament values or should we use default values
		costOfFilament = None
		weightOfFilament = None
		densityOfFilament = None
		diameterOfFilament = None
		if (allFilamentModels == None):
			#  filament default values
			self._logger.error("No measured/needed filament present. FilamentCost calculation not possible. Maybe metadata not assigned in json file.")
		else:

			# do we have fillamennt-informations for cost-calculation, if not use default filament parameters
			# we need:
			# 1. used filament (per tool)
			# 2. diameterOfFilament
			# 3. densityOfFilament
			# 4. spooldata (costOfFilament, weightOfFilament)
			# if 1 is not available -> skip
			# if 2,3 or 4 not available use default values from cost-plugin

			if (allFilamentModels[0].usedLength is None):
				self._logger.error(
					"No used filament present. FilamentCost calculation not possible. Maybe no filament tracker, like SpoolManager installed.")
			else:
				calcWithDefaultValuesPossible = None
				self._logger.info("Trying to read default filament values, for 'fallback-calculation'")
				default_costOfFilament = self._costEstimationPluginImplementation._settings.get(["costOfFilament"])  # Euro
				default_weightOfFilament = self._costEstimationPluginImplementation._settings.get(["weightOfFilament"])  # g
				default_densityOfFilament = self._costEstimationPluginImplementation._settings.get(["densityOfFilament"])  # g/cm3
				default_diameterOfFilament = self._costEstimationPluginImplementation._settings.get(["diameterOfFilament"])  # mm

				default_costOfFilament = StringUtils.transformToFloatOrNone(default_costOfFilament)
				default_weightOfFilament = StringUtils.transformToFloatOrNone(default_weightOfFilament)
				default_densityOfFilament = StringUtils.transformToFloatOrNone(default_densityOfFilament)
				default_diameterOfFilament = StringUtils.transformToFloatOrNone(default_diameterOfFilament)
				if (default_costOfFilament is None or default_weightOfFilament is None or default_densityOfFilament is None or default_diameterOfFilament is None):
					self._logger.warning(
						"Fallback calculation for filamentCost not possible, because Default costOfFilament or weightOfFilament or densityOfFilament is diameterOfFilament none")
					calcWithDefaultValuesPossible = False
				else:
					calcWithDefaultValuesPossible = True

				for filamentModel in allFilamentModels:
					usedLength = filamentModel.usedLength
					toolId = filamentModel.toolId # tool0
					diameterOfFilament = filamentModel.diameter
					densityOfFilament = filamentModel.density
					costOfFilament = filamentModel.spoolCost
					weightOfFilament = filamentModel.weight

					if (usedLength == None or usedLength == 0.0):
						self._logger.info("No filament calculation for '"+toolId+"', because usedLength is 0")
						continue

					if (diameterOfFilament is None):
						if (calcWithDefaultValuesPossible):
							self._logger.info("No filament diameter from filamenttracker, using default '"+str(default_diameterOfFilament)+"'")
							diameterOfFilament = default_diameterOfFilament
							withDefaultSpoolValues = True
						else:
							self._logger.info(
								"No filament diameter from filamenttracker or as default value")
							continue
					if (densityOfFilament is None):
						if (calcWithDefaultValuesPossible):
							self._logger.info("No filament densityOfFilament from filamenttracker, using default '"+str(default_densityOfFilament)+"'")
							densityOfFilament = default_densityOfFilament
							withDefaultSpoolValues = True
						else:
							self._logger.info(
								"No filament densityOfFilament from filamenttracker or as default value")
							continue
					if (costOfFilament is None):
						if (calcWithDefaultValuesPossible):
							self._logger.info("No filament costOfFilament from filamenttracker, using default '"+str(default_costOfFilament)+"'")
							costOfFilament = default_costOfFilament
							withDefaultSpoolValues = True
						else:
							self._logger.info(
								"No filament costOfFilament from filamenttracker or as default value")
							continue
					if (weightOfFilament is None):
						if (calcWithDefaultValuesPossible):
							self._logger.info("No filament weightOfFilament from filamenttracker, using default '"+str(default_weightOfFilament)+"'")
							weightOfFilament = default_weightOfFilament
							withDefaultSpoolValues = True
						else:
							self._logger.info(
								"No filament weightOfFilament from filamenttracker or as default value")
							continue

					self._logger.info("Filament cost calculation for '" + toolId + "'")
					# calculated used cost of this curretn tool
					costPerWeight =  costOfFilament / weightOfFilament
					self._logger.info("Cost per weight '"+str(costPerWeight)+"' =  costOfFilament '"+str(costOfFilament)+"' / weightOfFilament '"+str(weightOfFilament)+"'")
					volumeWeight =  self._calculateFilamentWeightForLength(usedLength, diameterOfFilament, densityOfFilament)
					filamentCost = StringUtils.transformToFloatOrZero(filamentCost) + (costPerWeight * volumeWeight)
					self._logger.info("filamentCost '"+str(filamentCost)+"' = costPerWeight '"+str(costPerWeight)+"' * volumeWeight '"+str(volumeWeight)+"'")
					pass

		# calc: totalCost

		totalCost = StringUtils.transformToFloatOrZero(filamentCost) + StringUtils.transformToFloatOrZero(electricityCost) + StringUtils.transformToFloatOrZero(printerCost)
		self._logger.info("totalcost = '"+str(totalCost)+"'")

		costData = dict(
						totalCosts=totalCost,
						# filamentCost=filamentCost,
						filamentCost=filamentCost,
						electricityCost=electricityCost,# done
			 			printerCost=printerCost,
						withDefaultSpoolValues=withDefaultSpoolValues
		)
		return costData

	def _printJobStarted(self, payload):
		self._resetableFileLogHandler.resetLog()
		self._resetableFileLogHandler.startLogging()

		self._logger.info("PrintJob '" + payload["name"] + "' started!")

		self.alreadyCanceled = False
		self._createPrintJobModel(payload)

	#### print job finished
	# printStatus = "success", "failed", "canceled"
	def _printJobFinished(self, printStatus, payload):
		self._logger.info("PrintJob finished!")

		self._capturePrintJobData(printStatus, payload)

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

			# - Slicer Settings
			selectedFilename = payload.get("path")
			selectedFile = self._file_manager.path_on_disk(payload.get("origin"), selectedFilename)
			slicerSettingsExpressions = self._settings.get([SettingsKeys.SETTINGS_KEY_SLICERSETTINGS_KEYVALUE_EXPRESSION])
			if (slicerSettingsExpressions != None and len(slicerSettingsExpressions) != 0):
				slicerSettings = SlicerSettingsParser(self._logger).extractSlicerSettings(selectedFile, slicerSettingsExpressions)
				if (slicerSettings.settingsAsText != None and len(slicerSettings.settingsAsText) != 0):
					self._currentPrintJobModel.slicerSettingsAsText = slicerSettings.settingsAsText

			# - Image / Thumbnail
			self._grabImage(payload)

			# - FilamentInformations e.g. length
			self._createAndAssignFilamentModel(self._currentPrintJobModel, payload)

			# - Costs
			self._addCostsToPrintModel(self._currentPrintJobModel)

			# store everything in the database
			self._logger.info("----- Try storing printjob model ----")
			databaseId = self._databaseManager.insertPrintJob(self._currentPrintJobModel)
			if (databaseId == None):
				self._logger.error("PrintJob not captured, see previous error log!")
				return None
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
			payLoadForClient = {
				"action": "printFinished",
				"printJobItem": printJobItem  # if present then the editor dialog is shown
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
			lastPrintJobModel.technicalLog = techLog
			self._databaseManager.updatePrintJob(lastPrintJobModel)
			if (payload != None):
				if (payLoadForClient["printJobItem"] != None):
					printJobItem = TransformPrintJob2JSON.transformPrintJobModel(lastPrintJobModel, self._file_manager)
					payLoadForClient["printJobItem"] = printJobItem
				self._sendDataToClient(payLoadForClient)
			pass

		return databaseId


	def _grabImage(self, payload):
		self._logger.info("----- Start grab Image/thumbnail... -----")
		isCameraPresent = self._cameraManager.isCamaraSnahotURLPresent()

		takeSnapshotAfterPrint = self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_TAKE_SNAPSHOT_AFTER_PRINT])
		takeSnapshotOnGCode = self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_TAKE_SNAPSHOT_ON_GCODE_COMMAND])
		takeSnapshotOnM118Code = self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_TAKE_SNAPSHOT_ON_M118_COMMAND])
		takeThumbnailAfterPrint = self._settings.get_boolean(
			[SettingsKeys.SETTINGS_KEY_TAKE_PLUGIN_THUMBNAIL_AFTER_PRINT])

		preferedSnapshot = self._settings.get(
			[SettingsKeys.SETTINGS_KEY_PREFERED_IMAGE_SOURCE]) == SettingsKeys.KEY_PREFERED_IMAGE_SOURCE_CAMERA
		preferedThumbnail = self._settings.get(
			[SettingsKeys.SETTINGS_KEY_PREFERED_IMAGE_SOURCE]) == SettingsKeys.KEY_PREFERED_IMAGE_SOURCE_THUMBNAIL

		isThumbnailPresent = self._isThumbnailPresent(payload)

		# - No Image
		if (takeSnapshotAfterPrint == False and takeSnapshotOnGCode == False and takeSnapshotOnM118Code == False and takeThumbnailAfterPrint == False):
			self._logger.info("No image should be taken")
			return
		# - Only Thumbnail
		if (takeThumbnailAfterPrint == True and takeSnapshotAfterPrint == False and takeSnapshotOnGCode == False and takeSnapshotOnM118Code == False):
			# Try to take the thumbnail
			self._logger.info("Try to take thumbnail, because afterprint/gcode not selected")
			self._takeThumbnailImage(payload)
			return
		if (takeThumbnailAfterPrint == True and isThumbnailPresent == True and preferedThumbnail == True):
			self._logger.info("Try to take thumbnail, because thumbnail is present and prefered")
			self._takeThumbnailImage(payload)
			return
		# - Only Camera
		if ((takeSnapshotAfterPrint == True) and takeThumbnailAfterPrint == False):
			if (isCameraPresent == False):
				self._logger.info("Camera Snapshot is selected but no camera url is available")
				return
			self._logger.info("Try capturing snapshot asyc from camera, because thumbnail not selected")
			self._cameraManager.takeSnapshotAsync(
				CameraManager.buildSnapshotFilename(self._currentPrintJobModel.printStartDateTime),
				self._sendErrorMessageToClient,
				self._sendReloadTableToClient
			)

			return
		# - Camera
		if (isCameraPresent == True and takeSnapshotAfterPrint == True):
			self._logger.info("Try capturing snapshot asyc")
			self._cameraManager.takeSnapshotAsync(
				CameraManager.buildSnapshotFilename(self._currentPrintJobModel.printStartDateTime),
				self._sendErrorMessageToClient,
				self._sendReloadTableToClient
			)


	def _isThumbnailPresent(self, payload):
		return self._takeThumbnailImage(payload, storeImage=False)


	def _takeThumbnailImage(self, payload, storeImage=True):
		self._logger.info("Try reading Thumbnail")
		thumbnailPresent = False
		metadata = self._file_manager.get_metadata(payload["origin"], payload["path"])
		# check if available
		if ("thumbnail" in metadata):
			thumbnailPresent = self._cameraManager.takePluginThumbnail(
				CameraManager.buildSnapshotFilename(self._currentPrintJobModel.printStartDateTime),
				metadata["thumbnail"],
				storeImage=storeImage
			)
		else:
			self._logger.warning("Thumbnail not found in print metadata")

		if (thumbnailPresent == False):
			self._logger.warning("Thumbnail not found for cameraManager")
		else:
			self._logger.info("Thumbnail was captured from metadata")

		return thumbnailPresent


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

		logFilename = os.path.join(self._settings.getBaseFolder("logs"), "plugin_PrintJobHistoryExtended_singlePrintJob.log")
		formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
		self._resetableFileLogHandler = ResetAbleLogFileHandler(logFilename, "octoprint.plugins.PrintJobHistoryExtended")
		self._resetableFileLogHandler.setFormatter(formatter)
		# self._resetableFileLogHandler.assignLoggerNameToCapture("octoprint.plugins.PrintJobHistoryExtended")
		# add to current plugin logger
		pluginLogger = logging.getLogger(self._logger.name)
		pluginLogger.addHandler(self._resetableFileLogHandler)

		self._logger.info("on after startup done")
		pass




	# Listen to all  g-code which where already sent to the printer (thread: comm.sending_thread)
	def on_sentGCodeHook(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
		# take snapshot an gcode command
		if (self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_TAKE_SNAPSHOT_ON_GCODE_COMMAND])):
			gcodePattern = self._settings.get([SettingsKeys.SETTINGS_KEY_TAKE_SNAPSHOT_GCODE_COMMAND_PATTERN])
			if (gcodePattern != None and len(gcodePattern.strip()) != 0):
				commandAsString = StringUtils.to_native_str(cmd)
				if (commandAsString.startswith(gcodePattern)):
					self._logger.info("M117 message for taking snapshot detected. Try to capture image!")
					self._cameraManager.takeSnapshotAsync(
						CameraManager.buildSnapshotFilename(self._currentPrintJobModel.printStartDateTime),
						self._sendErrorMessageToClient
					)
				pass
		pass

	# Receiving commands from the printer
	# Terminal: !!DEBUG:send //action:pjhTakeSnapshot
	def on_receivedActionHook(self, comm, line, action, *args, **kwargs):
		if (action == "pjhTakeSnapshot"):
			self._logger.info("Received \"pjhTakeSnapshot\" action from printer")

			if (self._settings.get_boolean(
				[SettingsKeys.SETTINGS_KEY_TAKE_SNAPSHOT_ON_M118_COMMAND])):
				self._logger.info("M118 command enabled for taking snapshot. Try to capture image!")
				if (self._currentPrintJobModel != None and self._currentPrintJobModel.printStartDateTime != None):

					if (self._cameraManager.isCamaraSnahotURLPresent() == True):
						# Try to take the snapshot
						self._cameraManager.takeSnapshotAsync(
							CameraManager.buildSnapshotFilename(self._currentPrintJobModel.printStartDateTime),
							self._sendErrorMessageToClient)
					else:
						self._logger.error("Camera Snapshot is selected, but no camera url is available")
				else:
					self._logger.error("M118 command detected, but there is no printjob started!")
			else:
				self._logger.info("Take Snapshot via M118 command is not activated in plugin settings")

		return

	def additional_permissions_hook(self):
		from octoprint.access import ADMIN_GROUP
		from octoprint.access import USER_GROUP
		return [
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
				# Eval currencySymbol
				currencySymbol = self._settings.get([SettingsKeys.SETTINGS_KEY_CURRENCY_SYMBOL])
				currencyFormat = self._settings.get([SettingsKeys.SETTINGS_KEY_CURRENCY_FORMAT])
				if (self._isCostEstimationInstalledAndEnabled()):
					currencySymbol = self._costEstimationPluginImplementation._settings.get(["currency"])
					currencyFormat = self._costEstimationPluginImplementation._settings.get(["currencyFormat"])
				self._sendDataToClient(dict(action="initalData",
											databaseFileLocation=databaseFileLocation,
											snapshotFileLocation=snapshotFileLocation,
											isPrintHistoryPluginAvailable=self._printHistoryPluginImplementation != None,
											isSpoolManagerInstalled = self._isSpoolManagerInstalledAndEnabled(),
											isSpoolmanInstalled = self._isSpoolmanInstalledAndEnabled(),
											isFilamentManagerInstalled = self._isFilamentManagerInstalledAndEnabled(),
											isCostEstimationPluginAvailable = self._isCostEstimationInstalledAndEnabled(),
											isPreHeatPluginAvailable = self._isPreHeatInstalledAndEnabled(),
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

			messageConfirmData = self._settings.get([SettingsKeys.SETTINGS_KEY_MESSAGE_CONFIRM_DATA])
			if (messageConfirmData != None):
				self._sendMessageConfirmToClient(messageConfirmData.title, messageConfirmData.message)

			self._checkForMissingFilamentTracking()

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



	# to allow the frontend to trigger an GET call
	def on_api_get(self, request):
		if len(request.values) != 0:
			action = request.values["action"]

			# deceide if you want the reset function in you settings dialog
			if "isResetSettingsEnabled" == action:
				return flask.jsonify(enabled="true")

			if "resetSettings" == action:
				self._settings.set([], self.get_settings_defaults())
				self._settings.save()
				return flask.jsonify(self.get_settings_defaults())
		pass


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
		# settings[SettingsKeys.SETTINGS_KEY_SELECTED_FILAMENTTRACKER_PLUGIN] = SettingsKeys.KEY_SELECTED_SPOOLMANAGER_PLUGIN
		settings[SettingsKeys.SETTINGS_KEY_SELECTED_FILAMENTTRACKER_PLUGIN] = SettingsKeys.KEY_SELECTED_NONE_PLUGIN
		settings[SettingsKeys.SETTINGS_KEY_NO_NOTIFICATION_FILAMENTTRACKERING_PLUGIN_SELECTION] = False

		settings[SettingsKeys.SETTINGS_KEY_SLICERSETTINGS_KEYVALUE_EXPRESSION] = ";(.*)=(.*)\n;   (.*),(.*)"
		settings[SettingsKeys.SETTINGS_KEY_SINGLE_PRINTJOB_REPORT_TEMPLATENAME] = SettingsKeys.SETTINGS_DEFAULT_VALUE_SINGLE_PRINTJOB_REPORT_TEMPLATENAME
		settings[SettingsKeys.SETTINGS_KEY_MULTI_PRINTJOB_REPORT_TEMPLATENAME] = SettingsKeys.SETTINGS_DEFAULT_VALUE_MULTI_PRINTJOB_REPORT_TEMPLATENAME

		settings[SettingsKeys.SETTINGS_KEY_CURRENCY_SYMBOL] = "€"
		settings[SettingsKeys.SETTINGS_KEY_CURRENCY_FORMAT] = "%v %s"


		## Camera
		settings[SettingsKeys.SETTINGS_KEY_TAKE_SNAPSHOT_AFTER_PRINT] = True
		settings[SettingsKeys.SETTINGS_KEY_TAKE_PLUGIN_THUMBNAIL_AFTER_PRINT] = True
		settings[SettingsKeys.SETTINGS_KEY_TAKE_SNAPSHOT_ON_M118_COMMAND] = False
		settings[SettingsKeys.SETTINGS_KEY_TAKE_SNAPSHOT_ON_GCODE_COMMAND] = False
		settings[SettingsKeys.SETTINGS_KEY_TAKE_SNAPSHOT_GCODE_COMMAND_PATTERN] = "M117 Snap"
		settings[SettingsKeys.SETTINGS_KEY_PREFERED_IMAGE_SOURCE] = SettingsKeys.KEY_PREFERED_IMAGE_SOURCE_THUMBNAIL

		## Temperature
		settings[SettingsKeys.SETTINGS_KEY_DEFAULT_TOOL_ID] = "tool0"
		settings[SettingsKeys.SETTINGS_KEY_TAKE_TEMPERATURE_FROM_PREHEAT] = True
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


	##~~ TemplatePlugin mixin
	def get_template_configs(self):
		return [
			dict(type="tab", name="Print Job History Extended"),
			dict(type="settings", custom_bindings=True, name="Print Job History Extended")
		]


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
			# "js/ulog.full.min.js",
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

				stable_branch=dict(
					name="Only Release",
					branch="main",
					comittish=["main"]
				),
				prerelease_branches=[
					# dict(
					# 	name="Release & Candidate",
					# 	branch="pre-release",
					# 	comittish=["pre-release", "main"],
					# ),
					dict(
						name="Release & in development",
						branch="dev",
						comittish=["dev", "main"],
					)
				],

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
		legacySettings = self._settings.global_get(["plugins", LEGACY_IDENTIFIER])

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
		legacySettings = self._settings.global_get(["plugins", LEGACY_IDENTIFIER]) or {}
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
		legacySettings = self._settings.global_get(["plugins", LEGACY_IDENTIFIER]) or {}
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


	# # For Streaming I need a special ResponseHandler
	# def route_hook(self, server_routes, *args, **kwargs):
	# 	from octoprint.server.util.tornado import LargeResponseHandler, UrlProxyHandler, path_validation_factory
	# 	from octoprint.util import is_hidden_path
	#
	# 	return [
	# 		# (r'myvideofeed', StreamHandler, dict(url=self._settings.global_get(["webcam", "snapshot"]),
	# 		# 									 as_attachment=True)),
	# 		(r"mysnapshot", UrlProxyHandler, dict(url=self._settings.global_get(["webcam", "snapshot"]),
	# 											 as_attachment=True))
	# 	]


# If you want your plugin to be registered within OctoPrint under a different name than what you defined in setup.py
# ("OctoPrint-PluginSkeleton"), you may define that here. Same goes for the other metadata derived from setup.py that
# can be overwritten via __plugin_xyz__ control properties. See the documentation for that.
# Name is used in the left Settings-Menue
__plugin_name__ = "PrintJobHistoryExtended"
__plugin_pythoncompat__ = ">=3.11,<3.15"

def __plugin_load__():
	global __plugin_implementation__
	__plugin_implementation__ = PrintJobHistoryExtendedPlugin()

	global __plugin_hooks__
	__plugin_hooks__ = {
		# "octoprint.server.http.routes": __plugin_implementation__.route_hook,
		# "octoprint.comm.protocol.gcode.sent": __plugin_implementation__.on_sentGCodeHook,
		"octoprint.comm.protocol.gcode.sending": __plugin_implementation__.on_sentGCodeHook,
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


