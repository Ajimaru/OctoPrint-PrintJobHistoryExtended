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


class SdCardFileTestCase(unittest.TestCase):
	"""K9 run 3: a file on the Marlin printer's SD card has metadata, but its "analysis" is None."""

	def test_analysisWithoutValueIsNoAnalysis(self):
		plugin = createPlugin()
		self.assertEqual(plugin._readCalculatedFilamentMetaData({"analysis": None, "history": []}), None)
		self.assertEqual(plugin._readCalculatedFilamentMetaData({"analysis": {"filament": None}}), None)
		self.assertEqual(plugin._readCalculatedFilamentMetaData(None), None)
		self.assertEqual(plugin._readCalculatedFilamentMetaData({"analysis": {"filament": {"tool0": {"length": 5.0}}}}),
						 {"tool0": {"length": 5.0}})

	def test_sdCardPrintKeepsTheSpool(self):
		spoolManager = mock.Mock()
		spoolManager.api_getJobFilamentUsage.return_value = None
		spoolManager.api_getLastPrintJobUsage.return_value = None
		spoolManager.api_getExtrusionAmount.return_value = [0.0]
		spoolManager.api_getSelectedSpoolInformations.return_value = [
			{"toolIndex": 0, "databaseId": 31, "spoolName": "Orange", "material": "PLA", "vendor": "Kingroon",
			 "density": 1.23, "diameter": 1.75, "cost": 8.04, "weight": 1000.0}]
		plugin = createPlugin()
		plugin._spoolManagerPluginImplementation = spoolManager
		plugin._spoolManagerPluginImplementationState = "enabled"
		plugin._file_manager = mock.Mock()
		plugin._file_manager.get_metadata.return_value = {"analysis": None, "history": []}
		printJob = createStartedPrintJob()

		plugin._createAndAssignFilamentModel(printJob, {"origin": "printer", "path": "rocket~1.gco"})

		self.assertEqual(printJob.getFilamentModelByToolId("tool0").spoolName, "Orange")
		total = printJob.getFilamentModelByToolId("total")
		self.assertEqual((total.spoolName, total.calculatedLength, total.usedLength), ("Orange", 0.0, 0.0))

	def test_toolWithoutLengthIsSkipped(self):
		spoolManager = mock.Mock()
		spoolManager.api_getJobFilamentUsage.return_value = {"tool0": None, "tool1": {"length": 120.0}}
		spoolManager.api_getLastPrintJobUsage.return_value = None
		spoolManager.api_getExtrusionAmount.return_value = None
		spoolManager.api_getSelectedSpoolInformations.return_value = None
		plugin = createPlugin()
		plugin._spoolManagerPluginImplementation = spoolManager
		plugin._spoolManagerPluginImplementationState = "enabled"
		plugin._file_manager = mock.Mock()
		plugin._file_manager.get_metadata.return_value = None
		printJob = createStartedPrintJob()

		plugin._createAndAssignFilamentModel(printJob, {"origin": "local", "path": "Rocket.gcode"})

		self.assertEqual(printJob.getFilamentModelByToolId("tool0"), None)
		self.assertEqual(printJob.getFilamentModelByToolId("total").calculatedLength, 120.0)


def createSpoolManager(jobFilamentUsage=None, extrusionAmount=None, selectedSpools=None):
	spoolManager = mock.Mock()
	spoolManager.api_getJobFilamentUsage.return_value = jobFilamentUsage
	spoolManager.api_getLastPrintJobUsage.return_value = None
	spoolManager.api_getExtrusionAmount.return_value = extrusionAmount
	spoolManager.api_getSelectedSpoolInformations.return_value = selectedSpools
	return spoolManager


def createPluginWithSpoolManager(spoolManager, metadata=None):
	plugin = createPlugin()
	plugin._spoolManagerPluginImplementation = spoolManager
	plugin._spoolManagerPluginImplementationState = "enabled"
	plugin._file_manager = mock.Mock()
	if (isinstance(metadata, Exception)):
		plugin._file_manager.get_metadata.side_effect = metadata
	else:
		plugin._file_manager.get_metadata.return_value = metadata
	return plugin


class ConnectorPrintJobTestCase(unittest.TestCase):
	"""Total test on all three printers, 2026-10-06: printer-hosted jobs on U1 (Moonraker) and A1 mini (Bambu)."""

	WHITE_ON_T3 = [{"toolIndex": 3, "databaseId": 110, "spoolName": "White", "material": "PLA", "vendor": "Kingroon",
					"density": 1.24, "diameter": 1.75, "cost": 8.0, "weight": 792.2}]

	def test_odometerToolTheJobDoesNotUseGetsNoRow(self):
		# U1 job printed on T3: the odometer reports 0 for all four heads, tool0 got an empty row
		plugin = createPluginWithSpoolManager(createSpoolManager(jobFilamentUsage={"tool3": {"length": 407.39}},
																  extrusionAmount=[0.0, 0.0, 0.0, 0.0],
																  selectedSpools=self.WHITE_ON_T3))
		printJob = createStartedPrintJob()

		plugin._createAndAssignFilamentModel(printJob, {"origin": "printer", "path": "total_test_1_V2_U1.gcode"})

		self.assertEqual(sorted(f.toolId for f in printJob.getFilamentModels()), ["tool3", "total"])
		tool3 = printJob.getFilamentModelByToolId("tool3")
		self.assertEqual((tool3.calculatedLength, tool3.usedLength, tool3.spoolName), (407.39, 0.0, "White"))

	PETG_ON_T2 = [{"toolIndex": 2, "databaseId": 37, "spoolName": "White", "material": "PETG", "vendor": "OWL-Filament",
				   "density": 1.27, "diameter": 1.75, "cost": 12.9, "weight": 1000.0}]

	def test_spoolOnAToolTheJobDoesNotUseGetsNoRow(self):
		# U1 job 189 printed on T3 with the PETG spool still mounted on T2: empty tool2 row, total "PLA, PETG"
		plugin = createPluginWithSpoolManager(createSpoolManager(jobFilamentUsage={"tool3": {"length": 429.25}},
																  extrusionAmount=[0.0, 0.0, 0.0, 0.0],
																  selectedSpools=self.PETG_ON_T2 + self.WHITE_ON_T3))
		printJob = createStartedPrintJob()

		plugin._createAndAssignFilamentModel(printJob, {"origin": "local", "path": "rocket_PLA_22m54s.gcode"})

		self.assertEqual(sorted(f.toolId for f in printJob.getFilamentModels()), ["tool3", "total"])
		total = printJob.getFilamentModelByToolId("total")
		self.assertEqual((total.spoolName, total.material, total.calculatedLength), ("White", "PLA", 429.25))

	def test_everyToolTheJobUsesKeepsItsRow(self):
		# a two-colour job on T2 and T3, the slicer lists the unused heads with 0
		plugin = createPluginWithSpoolManager(createSpoolManager(
			jobFilamentUsage={"tool0": {"length": 0.0}, "tool2": {"length": 120.0}, "tool3": {"length": 80.0}},
			selectedSpools=self.PETG_ON_T2 + self.WHITE_ON_T3))
		printJob = createStartedPrintJob()

		plugin._createAndAssignFilamentModel(printJob, {"origin": "local", "path": "two_colours.gcode"})

		self.assertEqual(sorted(f.toolId for f in printJob.getFilamentModels()), ["tool2", "tool3", "total"])
		self.assertEqual(printJob.getFilamentModelByToolId("total").material, "PETG, PLA")

	def test_allSpoolsCountWithoutACalculation(self):
		# A1 mini without metadata: nothing tells which spool the job uses
		plugin = createPluginWithSpoolManager(createSpoolManager(selectedSpools=self.PETG_ON_T2 + self.WHITE_ON_T3))
		printJob = createStartedPrintJob()

		plugin._createAndAssignFilamentModel(printJob, {"origin": "printer", "path": "rocket.gcode.3mf"})

		self.assertEqual(sorted(f.toolId for f in printJob.getFilamentModels()), ["tool2", "tool3", "total"])

	def test_allSpoolsCountWhenTheCalculationMatchesNoneOfThem(self):
		# OctoPrint's own analysis files a U1 job under tool0, the spool sits on T3
		plugin = createPluginWithSpoolManager(createSpoolManager(extrusionAmount=[0.0, 0.0, 0.0, 0.0],
																  selectedSpools=self.WHITE_ON_T3),
											  metadata={"analysis": {"filament": {"tool0": {"length": 407.39}}}})
		plugin._spoolManagerPluginImplementation.api_getJobFilamentUsage = None
		del plugin._spoolManagerPluginImplementation.api_getJobFilamentUsage
		printJob = createStartedPrintJob()

		plugin._createAndAssignFilamentModel(printJob, {"origin": "printer", "path": "total_test_1_V2_U1.gcode"})

		self.assertEqual(sorted(f.toolId for f in printJob.getFilamentModels()), ["tool0", "tool3", "total"])
		self.assertEqual(printJob.getFilamentModelByToolId("tool3").spoolName, "White")

	def test_extrusionOutsideTheCalculatedToolsKeepsItsSpool(self):
		# a streamed job on T3, filament pushed through T2 by hand during a pause
		plugin = createPluginWithSpoolManager(createSpoolManager(jobFilamentUsage={"tool3": {"length": 429.25}},
																  extrusionAmount=[0.0, 0.0, 25.0, 410.0],
																  selectedSpools=self.PETG_ON_T2 + self.WHITE_ON_T3))
		printJob = createStartedPrintJob()

		plugin._createAndAssignFilamentModel(printJob, {"origin": "local", "path": "rocket_PLA_22m54s.gcode"})

		self.assertEqual(sorted(f.toolId for f in printJob.getFilamentModels()), ["tool2", "tool3", "total"])
		tool2 = printJob.getFilamentModelByToolId("tool2")
		self.assertEqual((tool2.spoolName, tool2.material, tool2.usedLength), ("White", "PETG", 25.0))
		self.assertGreater(tool2.usedWeight, 0.0)
		total = printJob.getFilamentModelByToolId("total")
		self.assertEqual((total.material, total.usedLength, total.calculatedLength), ("PLA, PETG", 435.0, 429.25))

	def test_reportedToolWithoutUsageGetsNoRow(self):
		spoolManager = createSpoolManager(jobFilamentUsage={"tool3": {"length": 429.25}}, selectedSpools=self.WHITE_ON_T3)
		spoolManager.api_getLastPrintJobUsage.return_value = {
			"apiVersion": 1, "capturedAt": (PRINT_START + datetime.timedelta(minutes=10)).isoformat(), "source": "moonraker",
			"tools": [None, None, {"toolIndex": 2, "usedLength": 0.0, "usedWeight": 0.0, "usedCost": 0.0},
					  {"toolIndex": 3, "usedLength": 179.6, "usedWeight": 0.54, "usedCost": 0.005}]}
		plugin = createPluginWithSpoolManager(spoolManager)
		printJob = createStartedPrintJob()

		plugin._createAndAssignFilamentModel(printJob, {"origin": "local", "path": "rocket_PLA_22m54s.gcode"})

		self.assertEqual(sorted(f.toolId for f in printJob.getFilamentModels()), ["tool3", "total"])
		self.assertEqual(printJob.getFilamentModelByToolId("total").usedLength, 179.6)

	def test_odometerToolThatExtrudedKeepsItsRow(self):
		# an unsliced file printed without a spool selected: the measured length is all there is
		plugin = createPluginWithSpoolManager(createSpoolManager(extrusionAmount=[12.5, 0.0]))
		printJob = createStartedPrintJob()

		plugin._createAndAssignFilamentModel(printJob, {"origin": "local", "path": "Rocket.gcode"})

		self.assertEqual(sorted(f.toolId for f in printJob.getFilamentModels()), ["tool0", "total"])
		self.assertEqual(printJob.getFilamentModelByToolId("tool0").usedLength, 12.5)

	def test_calculatedFilamentFromTheStartWhenThePrinterIsGone(self):
		# A1 mini: connection lost, PrintFailed is captured without a printer storage
		plugin = createPluginWithSpoolManager(createSpoolManager(extrusionAmount=[0.0]),
											  metadata=RuntimeError("No storage configured for destination printer"))
		printJob = createStartedPrintJob()
		printJob.calculatedFilamentAtStart = {"tool0": {"length": 310.0}}

		plugin._createAndAssignFilamentModel(printJob, {"origin": "printer", "path": "rocket.gcode.3mf"})

		self.assertEqual(printJob.getFilamentModelByToolId("total").calculatedLength, 310.0)

	def test_calculatedFilamentOfTheEndWins(self):
		plugin = createPluginWithSpoolManager(createSpoolManager(jobFilamentUsage={"tool0": {"length": 311.2}},
																  extrusionAmount=[0.0]))
		printJob = createStartedPrintJob()
		printJob.calculatedFilamentAtStart = {"tool0": {"length": 99.0}}

		plugin._createAndAssignFilamentModel(printJob, {"origin": "printer", "path": "total_~1.gco"})

		self.assertEqual(printJob.getFilamentModelByToolId("total").calculatedLength, 311.2)

	def test_fileDataIsRememberedAtTheStart(self):
		plugin = createPluginWithSpoolManager(createSpoolManager(jobFilamentUsage={"tool0": {"length": 310.0}}))
		plugin._readFilePreviewImage = mock.Mock(return_value=b"png-bytes")
		printJob = createStartedPrintJob()
		plugin._currentPrintJobModel = printJob

		plugin._rememberFileDataAtStart(printJob, "printer", "rocket.gcode.3mf")

		self.assertEqual(printJob.calculatedFilamentAtStart, {"tool0": {"length": 310.0}})
		self.assertEqual(printJob.previewImageAtStart, b"png-bytes")
		plugin._readFilePreviewImage.assert_called_once_with("printer", "rocket.gcode.3mf")

	def test_startFallsBackToTheFileAnalysis(self):
		plugin = createPluginWithSpoolManager(createSpoolManager(),
											  metadata={"analysis": {"filament": {"tool0": {"length": 397.0}}}})
		plugin._readFilePreviewImage = mock.Mock(return_value=None)
		printJob = createStartedPrintJob()
		plugin._currentPrintJobModel = printJob

		plugin._rememberFileDataAtStart(printJob, "printer", "rocket_PLA_6m53s.gcode")

		self.assertEqual(printJob.calculatedFilamentAtStart, {"tool0": {"length": 397.0}})
		self.assertEqual(printJob.previewImageAtStart, None)

	def test_failingStartReadIsNoError(self):
		plugin = createPluginWithSpoolManager(createSpoolManager())
		plugin._readJobFilamentUsageFromPeer = mock.Mock(side_effect=RuntimeError("boom"))
		printJob = createStartedPrintJob()
		plugin._currentPrintJobModel = printJob

		plugin._rememberFileDataAtStart(printJob, "printer", "a.3mf")

		self.assertEqual(printJob.calculatedFilamentAtStart, None)

	def test_jobOverBeforeTheDelayReadsNothing(self):
		plugin = createPluginWithSpoolManager(createSpoolManager(jobFilamentUsage={"tool0": {"length": 310.0}}))
		plugin._readFilePreviewImage = mock.Mock()
		printJob = createStartedPrintJob()
		plugin._currentPrintJobModel = createStartedPrintJob()  # the next print already

		plugin._rememberFileDataAtStart(printJob, "printer", "rocket.gcode.3mf")

		self.assertEqual(printJob.calculatedFilamentAtStart, None)
		plugin._spoolManagerPluginImplementation.api_getJobFilamentUsage.assert_not_called()
		plugin._readFilePreviewImage.assert_not_called()

	def test_startReadOnlyForPrinterHostedFilesWithAName(self):
		plugin = createPlugin()
		cases = [("local", "Rocket.gcode", False), ("printer", "???", False), ("printer", "", False),
				 ("printer", "rocket.gcode.3mf", True)]
		for origin, path, expectThread in cases:
			printJob = createStartedPrintJob()
			printJob.fileOrigin = origin
			printJob.filePathName = path
			with mock.patch("octoprint_PrintJobHistoryExtended.threading.Thread") as threadClass:
				plugin._rememberFileDataAtStartAsync(printJob)
			self.assertEqual(threadClass.called, expectThread, origin + ":" + path)
			if (expectThread):
				self.assertEqual(threadClass.call_args.kwargs["args"], (printJob, origin, path, 30))
				threadClass.return_value.start.assert_called_once()

	def createAdoptedJob(self):
		# what the Bambu connector reports when it finds the printer already printing
		printJob = createStartedPrintJob()
		printJob.fileOrigin = "printer"
		printJob.fileName = "???"
		printJob.filePathName = "???"
		printJob.fileSize = None
		return printJob

	def test_fileOfAnAdoptedJobIsTakenFromTheEnd(self):
		plugin = createPlugin()
		printJob = self.createAdoptedJob()
		plugin._currentPrintJobModel = printJob
		plugin._capturePrintJobData = mock.Mock()

		plugin._printJobFinished("canceled", {"origin": "printer", "path": "rocket.gcode.3mf",
											  "name": "rocket.gcode.3mf", "size": 270048})

		self.assertEqual((printJob.fileOrigin, printJob.filePathName, printJob.fileName, printJob.fileSize),
						 ("printer", "rocket.gcode.3mf", "rocket.gcode.3mf", 270048))
		self.assertEqual(plugin._capturePrintJobData.call_args[0][1]["path"], "rocket.gcode.3mf")

	def test_adoptedJobWithoutNameInTheEndUsesThePath(self):
		plugin = createPlugin()
		printJob = self.createAdoptedJob()
		plugin._currentPrintJobModel = printJob

		plugin._adoptFileFromEndPayload({"origin": "printer", "path": "cache/rocket.gcode.3mf"})

		self.assertEqual((printJob.filePathName, printJob.fileName), ("cache/rocket.gcode.3mf", "rocket.gcode.3mf"))

	def test_endThatDoesNotKnowTheFileEitherChangesNothing(self):
		plugin = createPlugin()
		printJob = self.createAdoptedJob()
		plugin._currentPrintJobModel = printJob

		plugin._adoptFileFromEndPayload({"origin": "printer", "path": "???", "name": "???"})
		plugin._adoptFileFromEndPayload({"connector": "bambu"})

		self.assertEqual((printJob.filePathName, printJob.fileName), ("???", "???"))

	def test_knownFileIsNotReplacedByTheEnd(self):
		plugin = createPlugin()
		printJob = createStartedPrintJob()
		plugin._currentPrintJobModel = printJob

		plugin._adoptFileFromEndPayload({"origin": "printer", "path": "other.gcode", "name": "other.gcode"})

		self.assertEqual((printJob.fileOrigin, printJob.filePathName), ("local", "folder/Rocket.gcode"))


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
