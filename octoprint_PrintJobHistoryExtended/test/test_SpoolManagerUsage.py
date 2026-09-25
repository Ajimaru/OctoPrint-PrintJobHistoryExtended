# coding=utf-8
"""
Tests for taking the filament usage and the tool mapping over from SpoolManagerExtended.

The plugin class cannot be instantiated without a running OctoPrint, so the methods are
exercised on an instance created without __init__ that provides only what they touch.
"""
import datetime
import logging
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from peewee import SqliteDatabase

from octoprint_PrintJobHistoryExtended import PrintJobHistoryExtendedPlugin
from octoprint_PrintJobHistoryExtended.DatabaseManager import MODELS
from octoprint_PrintJobHistoryExtended.common.SettingsKeys import SettingsKeys
from octoprint_PrintJobHistoryExtended.models.FilamentModel import FilamentModel
from octoprint_PrintJobHistoryExtended.models.PrintJobModel import PrintJobModel


PRINT_START = datetime.datetime(2026, 9, 24, 19, 5, 0)


class FakeSettings:
	def __init__(self, values=None):
		self._values = dict(values or {})

	def get(self, path):
		return self._values.get(path[0])


class FakeDatabaseManager:
	def __init__(self):
		self.updatedPrintJobs = []

	def loadPrintJob(self, databaseId):
		return PrintJobModel.get_by_id(databaseId)

	def updatePrintJob(self, printJobModel):
		self.updatedPrintJobs.append(printJobModel)
		for filamentModel in printJobModel.getFilamentModels():
			filamentModel.printJob = printJobModel
			filamentModel.save()


def createPlugin(spoolManager=None):
	plugin = PrintJobHistoryExtendedPlugin.__new__(PrintJobHistoryExtendedPlugin)
	plugin._logger = logging.getLogger("test")
	plugin._settings = FakeSettings({SettingsKeys.SETTINGS_KEY_DEFAULT_TOOL_ID: "tool0"})
	plugin._spoolManagerPluginImplementation = spoolManager
	plugin._spoolManagerPluginImplementationState = "enabled" if spoolManager != None else None
	plugin._databaseManager = FakeDatabaseManager()
	plugin._backfillLock = threading.Lock()
	plugin._backfillTargetDatabaseId = None
	plugin._backfillTargetPrintEndDateTime = None
	plugin._file_manager = None
	plugin._sendDataToClient = mock.Mock()
	plugin._recalculateCostsInPlace = mock.Mock()
	return plugin


class DatabaseTestCase(unittest.TestCase):

	def setUp(self):
		self.database = SqliteDatabase(":memory:")
		self.database.bind(MODELS)
		self.database.connect()
		self.database.create_tables(MODELS)

		patcher = mock.patch("octoprint_PrintJobHistoryExtended.TransformPrintJob2JSON.transformPrintJobModel",
							 return_value={})
		patcher.start()
		self.addCleanup(patcher.stop)

	def tearDown(self):
		self.database.close()

	def createStoredPrintJob(self):
		printJob = PrintJobModel()
		printJob.fileOrigin = "printer"
		printJob.fileName = "cover.gcode"
		printJob.filePathName = "cover.gcode"
		printJob.printStartDateTime = PRINT_START
		printJob.printEndDateTime = PRINT_START + datetime.timedelta(hours=4)
		printJob.save()
		for toolId in ["total", "tool3"]:
			filament = FilamentModel()
			filament.toolId = toolId
			filament.printJob = printJob
			filament.calculatedLength = 42117.06
			filament.save()
		return printJob

	def waitFor(self, plugin, printJob):
		plugin._backfillTargetDatabaseId = printJob.get_id()
		plugin._backfillTargetPrintEndDateTime = datetime.datetime.now()


def usageReport(tools, job=None, printStatus="success"):
	return {
		"apiVersion": 1,
		"capturedAt": "2026-09-24T23:37:42.401000",
		"printStatus": printStatus,
		"source": "moonraker",
		"tools": tools,
		"job": job if job != None else {
			"origin": "printer", "path": "cover.gcode", "name": "cover.gcode",
			"printStartDateTime": (PRINT_START + datetime.timedelta(seconds=1)).isoformat()},
	}


TOOL3_USAGE = {"toolIndex": 3, "usedLength": 5737.0, "usedWeight": 14.5, "usedCost": 0.21, "source": "moonraker"}


class PrintJobUsageReportTestCase(DatabaseTestCase):

	def test_reportFillsTheWaitingJob(self):
		plugin = createPlugin()
		printJob = self.createStoredPrintJob()
		self.waitFor(plugin, printJob)

		plugin._onPrintJobUsageBookedByPeer(usageReport([None, None, None, TOOL3_USAGE], printStatus="canceled"))

		stored = PrintJobModel.get_by_id(printJob.get_id())
		self.assertEqual(stored.getFilamentModelByToolId("tool3").usedLength, 5737.0)
		self.assertEqual(stored.getFilamentModelByToolId("total").usedLength, 5737.0)
		self.assertEqual(stored.getFilamentModelByToolId("tool0"), None)
		self.assertEqual(len(plugin._databaseManager.updatedPrintJobs), 1)

	def test_reportForAnotherFileIsIgnored(self):
		plugin = createPlugin()
		printJob = self.createStoredPrintJob()
		self.waitFor(plugin, printJob)

		job = {"origin": "printer", "path": "other.gcode", "name": "other.gcode", "printStartDateTime": None}
		plugin._onPrintJobUsageBookedByPeer(usageReport([None, None, None, TOOL3_USAGE], job=job))

		self.assertEqual(plugin._databaseManager.updatedPrintJobs, [])

	def test_reportForAnotherPrintOfTheSameFileIsIgnored(self):
		plugin = createPlugin()
		printJob = self.createStoredPrintJob()
		self.waitFor(plugin, printJob)

		job = {"origin": "printer", "path": "cover.gcode", "name": "cover.gcode",
			   "printStartDateTime": (PRINT_START - datetime.timedelta(hours=5)).isoformat()}
		plugin._onPrintJobUsageBookedByPeer(usageReport([None, None, None, TOOL3_USAGE], job=job))

		self.assertEqual(plugin._databaseManager.updatedPrintJobs, [])

	def test_emptyReportChangesNothing(self):
		plugin = createPlugin()
		printJob = self.createStoredPrintJob()
		self.waitFor(plugin, printJob)

		plugin._onPrintJobUsageBookedByPeer(usageReport([None, None, None, None], printStatus="canceled"))

		self.assertEqual(plugin._databaseManager.updatedPrintJobs, [])

	def test_reportWithoutWaitingJobIsIgnored(self):
		plugin = createPlugin()
		self.createStoredPrintJob()

		plugin._onPrintJobUsageBookedByPeer(usageReport([None, None, None, TOOL3_USAGE]))

		self.assertEqual(plugin._databaseManager.updatedPrintJobs, [])

	def test_perToolEventAndReportConverge(self):
		plugin = createPlugin()
		printJob = self.createStoredPrintJob()
		self.waitFor(plugin, printJob)

		perToolEvent = {"toolId": 3, "usedLength": 5737.0, "usedWeight": 14.5, "usedCost": 0.21,
						"source": "moonraker", "printStatus": "canceled"}
		plugin._onSpoolUsageBookedByPeer(perToolEvent)
		plugin._onPrintJobUsageBookedByPeer(usageReport([None, None, None, TOOL3_USAGE], printStatus="canceled"))

		stored = PrintJobModel.get_by_id(printJob.get_id())
		self.assertEqual(stored.getFilamentModelByToolId("tool3").usedLength, 5737.0)
		self.assertEqual(stored.getFilamentModelByToolId("total").usedLength, 5737.0)


class JobFilamentUsageTestCase(unittest.TestCase):

	def test_usesThePhysicalToolsFromSpoolManager(self):
		spoolManager = mock.Mock()
		spoolManager.api_getJobFilamentUsage.return_value = {"tool3": {"length": 42117.06, "volume": 101.3}}
		plugin = createPlugin(spoolManager)

		usage = plugin._readJobFilamentUsageFromPeer("printer", "cover.gcode")

		self.assertEqual(usage, {"tool3": {"length": 42117.06, "volume": 101.3}})
		spoolManager.api_getJobFilamentUsage.assert_called_once_with("printer", "cover.gcode")

	def test_olderSpoolManagerFallsBack(self):
		spoolManager = mock.Mock(spec=["api_getSelectedSpoolInformations"])
		plugin = createPlugin(spoolManager)
		self.assertEqual(plugin._readJobFilamentUsageFromPeer("printer", "cover.gcode"), None)

	def test_unknownJobFallsBack(self):
		spoolManager = mock.Mock()
		spoolManager.api_getJobFilamentUsage.return_value = None
		plugin = createPlugin(spoolManager)
		self.assertEqual(plugin._readJobFilamentUsageFromPeer("printer", "cover.gcode"), None)

	def test_failingSpoolManagerFallsBack(self):
		spoolManager = mock.Mock()
		spoolManager.api_getJobFilamentUsage.side_effect = RuntimeError("boom")
		plugin = createPlugin(spoolManager)
		self.assertEqual(plugin._readJobFilamentUsageFromPeer("printer", "cover.gcode"), None)

	def test_withoutSpoolManager(self):
		plugin = createPlugin()
		self.assertEqual(plugin._readJobFilamentUsageFromPeer("printer", "cover.gcode"), None)


class TemperatureTestCase(unittest.TestCase):

	def createJob(self, calculatedLengthByTool):
		printJob = PrintJobModel()
		printJob.filamentModelsByToolId = {}
		for toolId, calculatedLength in calculatedLengthByTool.items():
			filament = FilamentModel()
			filament.toolId = toolId
			filament.calculatedLength = calculatedLength
			printJob.addFilamentModel(filament)
		return printJob

	def storedTemperatures(self, printJob):
		return [(t.sensorName, t.sensorValue) for t in printJob.getTemperatureModels()]

	def test_collectsEveryReportedTool(self):
		plugin = createPlugin()
		highest = {}
		plugin._collectHighestTemperatures(highest, {"bed": {"target": 90.0}, "tool0": {"target": 0, "actual": 54.0},
													 "chamber": {"target": 40.0}})
		plugin._collectHighestTemperatures(highest, {"bed": {"target": 60.0}, "tool0": {"target": 0, "actual": 49.0},
													 "tool1": {"target": 250.0}})
		self.assertEqual(highest, {"bed": 90.0, "tool0": 54.0, "tool1": 250.0})

	def test_usedToolThePrinterDoesNotReportIsUnknown(self):
		# A tool changer printing on its 4th head while the connector only reports the first:
		# the parked head's 54 degrees must not be stored as the nozzle temperature.
		plugin = createPlugin()
		printJob = self.createJob({"total": 42117.06, "tool3": 42117.06})

		toolIds = plugin._resolveTemperatureToolIds(printJob)
		plugin._addTemperaturesToPrintModel(printJob, {"bed": 90.0, "tool0": 54.0}, toolIds)

		self.assertEqual(self.storedTemperatures(printJob), [("bed", 90.0), ("tool3", "-")])

	def test_hottestUsedToolIsTheNozzleTemperature(self):
		plugin = createPlugin()
		printJob = self.createJob({"total": 300.0, "tool0": 100.0, "tool1": 200.0, "tool2": 0.0})

		toolIds = plugin._resolveTemperatureToolIds(printJob)
		plugin._addTemperaturesToPrintModel(printJob, {"bed": 60.0, "tool0": 215.0, "tool1": 245.0, "tool2": 260.0}, toolIds)

		self.assertEqual(toolIds, ["tool0", "tool1"])
		self.assertEqual(self.storedTemperatures(printJob), [("bed", 60.0), ("tool1", 245.0)])

	def test_withoutCalculatedDataTheDefaultToolIsUsed(self):
		plugin = createPlugin()
		printJob = self.createJob({"total": None})

		toolIds = plugin._resolveTemperatureToolIds(printJob)
		plugin._addTemperaturesToPrintModel(printJob, {"bed": 60.0, "tool0": 215.0}, toolIds)

		self.assertEqual(self.storedTemperatures(printJob), [("bed", 60.0), ("tool0", 215.0)])


if __name__ == "__main__":
	unittest.main()
