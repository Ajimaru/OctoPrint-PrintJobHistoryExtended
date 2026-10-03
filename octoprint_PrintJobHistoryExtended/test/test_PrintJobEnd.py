# coding=utf-8
"""
Tests for the end of a print job when the end event is incomplete, and for saving a job the
capture could not complete.

K9 (Marlin), 2026-10-03: an emergency stop during a pause made OctoPrint 2.0.0rc5 fire
PrintFailed with the payload of the Disconnected event ({"connector": "serial"}). The
capture failed on payload["path"], stored the job without any filament row, and the job
could then not be saved in the dialog either.
"""
import datetime
import logging
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from peewee import SqliteDatabase

from octoprint_PrintJobHistoryExtended import PrintJobHistoryExtendedPlugin
from octoprint_PrintJobHistoryExtended.DatabaseManager import DatabaseManager, MODELS
from octoprint_PrintJobHistoryExtended.common.SettingsKeys import SettingsKeys
from octoprint_PrintJobHistoryExtended.models.CostModel import CostModel
from octoprint_PrintJobHistoryExtended.models.FilamentModel import FilamentModel
from octoprint_PrintJobHistoryExtended.models.PrintJobModel import PrintJobModel
from octoprint_PrintJobHistoryExtended.models.TemperatureModel import TemperatureModel


PRINT_START = datetime.datetime(2026, 10, 3, 12, 22, 39)


def createPlugin():
	plugin = PrintJobHistoryExtendedPlugin.__new__(PrintJobHistoryExtendedPlugin)
	plugin._logger = logging.getLogger("test")
	plugin._databaseManager = mock.Mock()
	plugin._backfillLock = threading.Lock()
	plugin._currentPrintJobModel = None
	return plugin


def createStartedPrintJob():
	printJob = PrintJobModel()
	printJob.printStartDateTime = PRINT_START
	printJob.fileOrigin = "local"
	printJob.fileName = "Rocket.gcode"
	printJob.filePathName = "folder/Rocket.gcode"
	printJob.fileSize = 1354541
	return printJob


class EndPayloadTestCase(unittest.TestCase):

	def test_disconnectPayloadIsCompletedFromTheStart(self):
		plugin = createPlugin()
		plugin._currentPrintJobModel = createStartedPrintJob()
		disconnectPayload = {"connector": "serial"}

		payload = plugin._completePayloadFromJobStart(disconnectPayload)

		self.assertEqual(payload, {"connector": "serial", "origin": "local", "path": "folder/Rocket.gcode",
								   "name": "Rocket.gcode", "size": 1354541})
		# every other plugin gets the same dict
		self.assertEqual(disconnectPayload, {"connector": "serial"})

	def test_completePayloadIsLeftAsItIs(self):
		plugin = createPlugin()
		plugin._currentPrintJobModel = createStartedPrintJob()
		endPayload = {"origin": "printer", "path": "other.gcode", "name": "other.gcode", "size": 5, "time": 12.5}

		self.assertEqual(plugin._completePayloadFromJobStart(endPayload), endPayload)

	def test_endIsCapturedWithTheCompletedPayloadAndClosesTheJob(self):
		plugin = createPlugin()
		plugin._currentPrintJobModel = createStartedPrintJob()
		plugin._capturePrintJobData = mock.Mock()

		plugin._printJobFinished("failed", {"connector": "serial"})

		printStatus, payload = plugin._capturePrintJobData.call_args[0]
		self.assertEqual((printStatus, payload["origin"], payload["path"]), ("failed", "local", "folder/Rocket.gcode"))
		self.assertEqual(plugin._currentPrintJobModel, None)

	def test_jobIsClosedEvenWhenTheCaptureFails(self):
		plugin = createPlugin()
		plugin._currentPrintJobModel = createStartedPrintJob()
		plugin._capturePrintJobData = mock.Mock(side_effect=RuntimeError("boom"))

		with self.assertRaises(RuntimeError):
			plugin._printJobFinished("success", {})
		self.assertEqual(plugin._currentPrintJobModel, None)

	def test_endWithoutRunningJobCapturesNothing(self):
		# a repeated end event of a connector, or OctoPrint restarted during the print
		plugin = createPlugin()
		plugin._capturePrintJobData = mock.Mock()

		plugin._printJobFinished("success", {"origin": "local", "path": "Rocket.gcode"})

		plugin._capturePrintJobData.assert_not_called()


class LayerInfoTestCase(unittest.TestCase):

	def test_layerReportWithoutRunningJobIsIgnored(self):
		# DisplayLayerProgress reports the height of a hand-jogged Z axis as well
		plugin = createPlugin()
		plugin._updatePrintJobModelWithLayerHeightInfos({"totalLayer": "-", "currentLayer": "-",
														  "totalHeightFormatted": "-", "currentHeightFormatted": "30.0"})

	def test_layerReportOfTheRunningJobIsKept(self):
		plugin = createPlugin()
		plugin._currentPrintJobModel = createStartedPrintJob()
		plugin._updatePrintJobModelWithLayerHeightInfos({"totalLayer": "120", "currentLayer": "12",
														  "totalHeightFormatted": "24.0", "currentHeightFormatted": "2.4"})
		self.assertEqual(plugin._currentPrintJobModel.printedLayers, "12 / 120")
		self.assertEqual(plugin._currentPrintJobModel.printedHeight, "2.4 / 24.0")


class SaveEditedPrintJobTestCase(unittest.TestCase):
	"""The dialog's save, against a real (SQLite) DatabaseManager."""

	def setUp(self):
		self.directory = tempfile.mkdtemp()
		# a file, not :memory: - every connection scope closes the connection
		self.database = SqliteDatabase(os.path.join(self.directory, "printJobHistory.db"))
		self.database.bind(MODELS)
		self.database.create_tables(MODELS)
		self.database.close()

		self.databaseManager = DatabaseManager(logging.getLogger("test"), False)
		self.databaseManager._database = self.database
		self.databaseManager.sendErrorMessageToClient = mock.Mock()
		self.plugin = createPlugin()

	def tearDown(self):
		if (self.database.is_closed() == False):
			self.database.close()
		shutil.rmtree(self.directory)

	def storeJobWithoutFilament(self):
		printJob = createStartedPrintJob()
		printJob.printEndDateTime = PRINT_START + datetime.timedelta(minutes=20)
		printJob.save()
		for sensorName, sensorValue in [("bed", "50.0"), ("tool0", "210.0")]:
			temperature = TemperatureModel()
			temperature.sensorName = sensorName
			temperature.sensorValue = sensorValue
			temperature.printJob = printJob
			temperature.save()
		self.database.close()
		return printJob.get_id()

	def dialogJson(self, **values):
		jsonData = {"userName": "pi", "fileName": "Rocket.gcode",
					"printStartDateTimeFormatted": "03.10.2026 12:22", "printEndDateTimeFormatted": "03.10.2026 12:42",
					"duration": 1207, "printStatusResult": "failed", "noteText": "", "noteDeltaFormat": None,
					"noteHtml": "", "printedLayers": "-", "printedHeight": "-",
					"vendor": "Kingroon", "spoolName": "Orange", "material": "PLA",
					"usedLengthFormatted": "0.65", "calculatedLengthFormatted": "", "usedWeight": 1.93, "usedCost": 0.02}
		jsonData.update(values)
		return jsonData

	def save(self, databaseId, jsonData):
		printJob = self.databaseManager.loadPrintJob(databaseId)
		self.plugin._updatePrintJobFromJson(printJob, jsonData)
		self.databaseManager.updatePrintJob(printJob, None, withTemperatures=True)
		return self.databaseManager.loadPrintJob(databaseId)

	def test_jobWithoutFilamentCanBeSaved(self):
		databaseId = self.storeJobWithoutFilament()

		stored = self.save(databaseId, self.dialogJson(temperatureBed="50.0", temperatureNozzle="210.0"))

		total = stored.getFilamentModelByToolId("total")
		self.assertEqual((total.spoolName, total.usedLength, total.usedWeight), ("Orange", 650.0, 1.93))
		self.databaseManager.sendErrorMessageToClient.assert_not_called()

	def test_editedTemperaturesAreStored(self):
		databaseId = self.storeJobWithoutFilament()

		stored = self.save(databaseId, self.dialogJson(temperatureBed="55", temperatureNozzle="205"))

		temperatures = sorted((t.sensorName, t.sensorValue) for t in stored.getTemperatureModels())
		self.assertEqual(temperatures, [("bed", "55"), ("tool0", "205")])

	def test_temperatureMissingFromTheRequestKeepsTheStoredOne(self):
		databaseId = self.storeJobWithoutFilament()

		stored = self.save(databaseId, self.dialogJson(temperatureNozzle="205"))

		temperatures = sorted((t.sensorName, t.sensorValue) for t in stored.getTemperatureModels())
		self.assertEqual(temperatures, [("bed", "50.0"), ("tool0", "205")])
		self.databaseManager.sendErrorMessageToClient.assert_not_called()

	def storeCapturedJob(self):
		# job 147 as captured, before anyone opened the dialog
		databaseId = self.storeJobWithoutFilament()
		printJob = self.databaseManager.loadPrintJob(databaseId)
		total = FilamentModel()
		total.toolId = "total"
		total.usedLength = 562.66241
		total.calculatedLength = 1087.44914
		total.usedWeight = 1.66463
		total.usedCost = 0.0133837
		printJob.addFilamentModel(total)
		costs = CostModel()
		costs.filamentCost = 0.0133837
		costs.electricityCost = 0.00175
		costs.printerCost = 0.03788
		costs.totalCosts = 0.0530137
		printJob.setCosts(costs)
		self.databaseManager.updatePrintJob(printJob)
		return databaseId

	def roundedDialogJson(self, **values):
		# what the dialog sends back without any change: metres, two decimals, the minute
		roundedValues = dict(usedLengthFormatted="0.56", calculatedLengthFormatted="1.09", usedWeight="1.66",
							 usedCost="0.01", totalCosts="0.05", filamentCost="0.01", electricityCost="0.00",
							 printerCost="0.04", printStartDateTimeFormatted="03.10.2026 12:22")
		roundedValues.update(values)
		return self.dialogJson(**roundedValues)

	def test_untouchedValuesKeepTheirPrecision(self):
		databaseId = self.storeCapturedJob()

		stored = self.save(databaseId, self.roundedDialogJson())

		total = stored.getFilamentModelByToolId("total")
		self.assertEqual((total.usedLength, total.calculatedLength, total.usedWeight, total.usedCost),
						 (562.66241, 1087.44914, 1.66463, 0.0133837))
		costs = stored.getCosts()
		self.assertEqual((costs.filamentCost, costs.electricityCost, costs.printerCost), (0.0133837, 0.00175, 0.03788))
		self.assertAlmostEqual(costs.totalCosts, 0.0530137)
		self.assertEqual(stored.printStartDateTime, PRINT_START)

	def test_changedValuesAreTakenOver(self):
		databaseId = self.storeCapturedJob()

		stored = self.save(databaseId, self.roundedDialogJson(usedLengthFormatted="0.60", electricityCost="0.10",
															   otherCost="1.50", otherCostLabel="Glue"))

		self.assertEqual(stored.getFilamentModelByToolId("total").usedLength, 600.0)
		costs = stored.getCosts()
		self.assertEqual((costs.electricityCost, costs.otherCost, costs.filamentCost), (0.10, 1.50, 0.0133837))
		# added up from the parts as stored, not from the rounded ones the dialog shows
		self.assertAlmostEqual(costs.totalCosts, 0.0133837 + 0.10 + 0.03788 + 1.50)

	def test_untouchedStartTimeIsNoChange(self):
		self.assertEqual(self.plugin._readDateTimeFromJson("start", {"start": "03.10.2026 12:22"}, PRINT_START), PRINT_START)
		self.assertEqual(self.plugin._readDateTimeFromJson("start", {"start": "03.10.2026 12:25"}, PRINT_START),
						 datetime.datetime(2026, 10, 3, 12, 25))
		self.assertEqual(self.plugin._readDateTimeFromJson("start", {"start": "03.10.2026 12:22"}, None),
						 datetime.datetime(2026, 10, 3, 12, 22))

	def test_otherUpdatesLeaveTheTemperaturesAlone(self):
		databaseId = self.storeJobWithoutFilament()
		printJob = self.databaseManager.loadPrintJob(databaseId)
		for temperature in printJob.getTemperatureModels():
			temperature.sensorValue = "999"

		self.databaseManager.updatePrintJob(printJob)

		stored = self.databaseManager.loadPrintJob(databaseId)
		self.assertEqual(sorted(t.sensorValue for t in stored.getTemperatureModels()), ["210.0", "50.0"])


class TasmotaSetupTestCase(unittest.TestCase):

	def createPlugin(self, plugIp):
		plugin = createPlugin()
		plugin._tasmotaPluginImplementation = object()
		plugin._tasmotaPluginImplementationState = "enabled"
		pluginSettings = {SettingsKeys.SETTINGS_KEY_TASMOTA_PLUG_IP: plugIp,
						  SettingsKeys.SETTINGS_KEY_NO_NOTIFICATION_TASMOTA_POLLING: False}
		tasmotaSettings = {"arrSmartplugs": [{"ip": "192.168.1.134", "idx": "1", "label": "MainPower"},
											 {"ip": "192.168.1.85", "idx": "1", "label": "PrinterPower"}],
						   "polling_enabled": True, "polling_interval": 1}
		plugin._settings = mock.Mock()
		plugin._settings.get.side_effect = lambda path: pluginSettings.get(path[0])
		plugin._settings.get_boolean.side_effect = lambda path: pluginSettings.get(path[0])
		plugin._settings.global_get.side_effect = lambda path: tasmotaSettings.get(path[2])
		plugin._sendMessageToClient = mock.Mock()
		return plugin

	def test_plugNoLongerConfiguredInTasmotaIsReported(self):
		# K9: the plug the measurement asked for had left Tasmota's list a week before
		plugin = self.createPlugin("192.168.1.99")

		plugin._checkTasmotaSetup()

		popupType, title, message = plugin._sendMessageToClient.call_args[0]
		self.assertEqual((popupType, title), ("notice", "Tasmota plug not found"))
		self.assertIn("192.168.1.99", message)

	def test_configuredPlugWithPollingIsQuiet(self):
		plugin = self.createPlugin("192.168.1.134")

		plugin._checkTasmotaSetup()

		plugin._sendMessageToClient.assert_not_called()


if __name__ == "__main__":
	unittest.main()
