import pprint
import shutil
import tempfile
import unittest

import peewee

from octoprint_PrintJobHistoryExtended import DatabaseManager, CostModel
from octoprint_PrintJobHistoryExtended.api import TransformPrintJob2JSON, TransformSlicerSettings2JSON
from octoprint_PrintJobHistoryExtended.common import StringUtils
from octoprint_PrintJobHistoryExtended.common import CSVExportImporter
import logging

from octoprint_PrintJobHistoryExtended.models.PrintJobModel import PrintJobModel
from octoprint_PrintJobHistoryExtended.models.FilamentModel import FilamentModel
from octoprint_PrintJobHistoryExtended.services.SlicerSettingsService import SlicerSettingsService


class TestDatabase(unittest.TestCase):

	def setUp(self):
		self.init_database()

	def tearDown(self):
		self.databaseManager.closeDatabase()
		shutil.rmtree(self.databaselocation)

	def _clientOutput(self, message1, message2):
		print(message1)
		print(message2)

	def init_database(self):
		# a throw-away database per test, so the tests need no personal OctoPrint install
		self.databaselocation = tempfile.mkdtemp()
		testLogger = logging.getLogger("testLogger")
		self.databaseManager = DatabaseManager(testLogger, False)
		self.databaseManager.initDatabase(self.databaselocation, self._clientOutput)

	def _insertPrintJob(self, fileName, startDateTime):
		printJob = PrintJobModel()
		printJob.fileName = fileName
		printJob.fileOrigin = "local"
		printJob.printStartDateTime = StringUtils.transformToDateTimeOrNone(startDateTime)
		totalFilament = FilamentModel()
		totalFilament.toolId = "total"
		printJob.addFilamentModel(totalFilament)
		toolFilament = FilamentModel()
		toolFilament.toolId = "tool0"
		printJob.addFilamentModel(toolFilament)
		return self.databaseManager.insertPrintJob(printJob)

	# TimeFrameSelection
	def _test_queryJobs(self):

		# http: // localhost:5000 / plugin / PrintJobHistory / loadPrintJobHistoryByQuery?from=0 & to = 25 & sortColumn = printStartDateTime & sortOrder = desc & filterName = all & startDate = & endDate =
		tableQuery = {
			"from": 0,
			"to": 250,
			"sortColumn": "fileName",
			"sortOrder": "desc",
			"filterName": "all",
			"startDate": "",
			"endDate": "",
		}

		allJobsModels = self.databaseManager.loadPrintJobsByQuery(tableQuery)
		print(allJobsModels)
		allJobsAsList = TransformPrintJob2JSON.transformAllPrintJobModels(allJobsModels)

		pp = pprint.PrettyPrinter(indent=2)
		# pp.pprint(allJobsAsDict)
		jobCount = 0
		for jobItem in allJobsAsList:
			jobCount = jobCount + 1

			print(str(jobCount) + " " + jobItem["fileName"] +" " +str(jobItem["databaseId"]) + "  " + str(jobItem["printStartDateTimeFormatted"]) + "  " + str(jobItem["printEndDateTimeFormatted"]))

		pass

	# Statistic
	def _test_statistics(self):
		tableQuery = {
			"from": 0,
			"to": 100,
			"sortColumn": "filename",
			"sortOrder": "asc",
			"filterName": "all",
			# "startDate": "20.08.2020",
			# "endDate": "20.08.2020",
		}

		stats = self.databaseManager.calculatePrintJobsStatisticByQuery(tableQuery)
		print(stats)
		# allJobsAsList = TransformPrintJob2JSON.transformAllPrintJobModels(allJobsModels)
		#
		# pp = pprint.PrettyPrinter(indent=2)
		# # pp.pprint(allJobsAsDict)
		# for jobItem in allJobsAsList:
		# 	print(str(jobItem["databaseId"]) + "  " + str(jobItem["printStartDateTimeFormatted"]) + "  " + str(jobItem["printEndDateTimeFormatted"]))

		pass

	def _test_loadSelected(self):
		selectedDatabaseIds = "17,3,21,23,20"
		allJobsModels = self.databaseManager.loadSelectedPrintJobs(selectedDatabaseIds)
		print(allJobsModels)
		allJobsAsList = TransformPrintJob2JSON.transformAllPrintJobModels(allJobsModels)

		pp = pprint.PrettyPrinter(indent=2)
		# pp.pprint(allJobsAsDict)
		for jobItem in allJobsAsList:
			print(str(jobItem["databaseId"]) + "  " + str(jobItem["printStartDateTimeFormatted"]) + "  " + str(jobItem["printEndDateTimeFormatted"]))

		pass

	def _test_loadJobSettings(self):
		# selectedDatabaseIds = "17,3,21,23,20"
		# selectedDatabaseIds = "21, 17"
		selectedDatabaseIds = "102, 97"
		allJobsModels = self.databaseManager.loadSelectedPrintJobs(selectedDatabaseIds)
		print(allJobsModels)
		slicerSettingssJobToCompareList = []
		for job in allJobsModels:
			settingsForCompare = SlicerSettingsService.SlicerSettingsJob()
			settingsForCompare.databaseId = job.databaseId
			settingsForCompare.fileName = job.fileName
			settingsForCompare.slicerSettingsAsText = job.slicerSettingsAsText

			# print(settingsForCompare.slicerSettingsAsText)

			slicerSettingssJobToCompareList.append(settingsForCompare)

		slicerService = SlicerSettingsService()
		compareResult = slicerService.compareSlicerSettings(slicerSettingssJobToCompareList, ";(.*)=(.*)\n;   (.*),(.*)")

		compoareResultAsJson = TransformSlicerSettings2JSON.transformSlicerSettingsCompareResult(compareResult)
		print(compoareResultAsJson)
		pass

	def _test_deletePrinjob(self):
		self.databaseManager.deletePrintJob(105)

	def test_csvExportPrinjob(self):
		self.assertIsNotNone(self._insertPrintJob("Rocket.gcode", "12.03.2013 14:45"))
		self.assertIsNotNone(self._insertPrintJob("Benchy.gcode", "13.03.2013 09:10"))

		# selectedDatabaseIds = flask.request.values["databaseIds"]
		# allJobsModels = self._databaseManager.loadSelectedPrintJobs(selectedDatabaseIds)
		allJobsModels = self.databaseManager.loadAllPrintJobs()

		csvLines = list(CSVExportImporter.transform2CSV(allJobsModels))

		self.assertEqual(len(csvLines), 3, "one header line and one line per job expected")
		self.assertIn('"File Name"', csvLines[0])
		# newest job first, like in the table
		self.assertIn('"Benchy.gcode"', csvLines[1])
		self.assertIn('"Rocket.gcode"', csvLines[2])


	def _test_something(self):
		db = peewee.SqliteDatabase(self.databaselocation+'/printJobHistory.db')
		singlePrintJob = PrintJobModel.select().join(FilamentModel).where(PrintJobModel.databaseId == 109).get()
		print(singlePrintJob)
		print(singlePrintJob.fileName)

		singlePrintJob.printFila()
		# for fila in singlePrintJob.filaments:
		# 	print(fila)
		newFilaModel = FilamentModel()
		newFilaModel.material = "MeinMaterial"

		newFilaModel = FilamentModel.create(printJob = singlePrintJob)
		# singlePrintJob.filaments.append(newFilaModel)
		# singlePrintJob.save()
		pass

	def _test_one2oneAsso(self):

		jobAsDict = []
		printJob = self.databaseManager.loadPrintJob(16)

		costs = printJob.getCosts()

		if (costs == None):
			newCostModel = CostModel()
			newCostModel.totalCosts = 1.234

			printJob.setCosts(newCostModel)
		#
		costs = printJob.getCosts()

		costs.totalCosts = 2.567

		printJob.save()
		costs.save()
		print(costs.totalCosts)


		# for costs in allCosts:
		# 	print(costs.totalCosts)
		# 	costsAsDict = costs.__data__
		# 	print(costsAsDict)


		# newCosts = CostModel()
		# newCosts.totalCosts = 123.34
		#
		# printJob.setCost(newCosts);

		# costs = printJob.getCosts()
		# costs.totalCosts = 1234.56


		# self.databaseManager.insertPrintJob(printJob)

		# costModel = CostModel()
		# costModel.printerCost = 1.234
		#
		# printJob.setCost(costModel)
		#
		# self.databaseManager.insertPrintJob(printJob)
		#
		# newPrintModel = self.databaseManager.loadPrintJob(1)
		#
		# print(newPrintModel.costs)
		pass


	def _test_loadPringJobWithAssos(self):
		printJobModel = self.databaseManager.loadPrintJob(1010)
		print(printJobModel.fileName)
		allFilamentModels = printJobModel._getFilamentModelsFromAsso()

		for filamentModel in allFilamentModels:
			print(filamentModel.toolId)

		print("ende")

if __name__ == '__main__':
	print("Start DatabaseManager Test")
	unittest.main()
	print("Finished")

#
# type = "ppostgres"
# host="localhost"
# port="5432"
# databaseName="rainbow_database"
# username = "unicorn_user"
# password = "magical_password"
# result = databaseManager.testConnection(type, host, port, databaseName, username, password)
# print(result)


# printJob = databaseManager.loadPrintJob(1)
# # python2 -> unicode
# # python3 -> str (es gibt kein unicode)
# fileName = printJob.fileName
# convert(fileName)
# fileSize = printJob.fileSize
# convert(fileSize)
# printStartDateTime = printJob.printStartDateTime
# convert(printStartDateTime)
# diameter = printJob.loadFilamentFromAssoziation().diameter
# convert(diameter)

# currentScheme = 1
# targetScheme = 5
#
# databaseManager._upgradeDatabase(currentScheme, targetScheme)
