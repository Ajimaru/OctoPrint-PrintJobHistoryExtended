import logging
import unittest
from unittest import mock

from peewee import SqliteDatabase, OperationalError

from octoprint_PrintJobHistoryExtended.DatabaseManager import DatabaseManager, MODELS
from octoprint_PrintJobHistoryExtended.models.CostModel import CostModel
from octoprint_PrintJobHistoryExtended.models.FilamentModel import FilamentModel
from octoprint_PrintJobHistoryExtended.models.PrintJobModel import PrintJobModel
from octoprint_PrintJobHistoryExtended.models.TemperatureModel import TemperatureModel


class PrintJobModelTestCase(unittest.TestCase):
	"""A print job that is still being captured must not touch the database.

	The capture used to read the (empty) backrefs of the unsaved job outside any
	connection scope; with a dead MySQL connection that failed with "server has gone
	away" and the print job was never stored.
	"""

	def setUp(self):
		self.database = SqliteDatabase(":memory:")
		self.database.bind(MODELS)
		self.database.connect()
		self.database.create_tables(MODELS)

	def tearDown(self):
		self.database.close()

	def test_unsavedJobDoesNotQueryTheDatabase(self):
		printJob = PrintJobModel()
		goneAway = OperationalError(2006, "MySQL server has gone away")
		with mock.patch.object(self.database, "execute_sql", side_effect=goneAway):
			totalFilament = FilamentModel()
			totalFilament.toolId = "total"
			printJob.addFilamentModel(totalFilament)
			self.assertIs(printJob.getFilamentModelByToolId("total"), totalFilament)
			self.assertEqual(printJob.getFilamentModelByToolId("tool0"), None)
			self.assertEqual(list(printJob.getFilamentModels(withoutTotal=True)), [])
			self.assertEqual(printJob.getTemperatureModels(), [])
			self.assertEqual(printJob.getCosts(), None)

		# A fresh unsaved job, where the very first access is a getter
		with mock.patch.object(self.database, "execute_sql", side_effect=goneAway):
			self.assertEqual(list(PrintJobModel().getFilamentModels()), [])

	def test_savedJobStillLoadsItsChildren(self):
		printJob = PrintJobModel()
		printJob.fileName = "benchy.gcode"
		printJob.save()

		filament = FilamentModel()
		filament.toolId = "tool0"
		filament.printJob = printJob
		filament.save()
		temperature = TemperatureModel()
		temperature.sensorName = "bed"
		temperature.sensorValue = "60"
		temperature.printJob = printJob
		temperature.save()
		cost = CostModel()
		cost.totalCosts = 1.5
		cost.printJob = printJob
		cost.save()

		loadedJob = PrintJobModel.get_by_id(printJob.get_id())
		self.assertEqual(loadedJob.getFilamentModelByToolId("tool0").get_id(), filament.get_id())
		self.assertEqual([t.get_id() for t in loadedJob.getTemperatureModels()], [temperature.get_id()])
		self.assertEqual(loadedJob.getCosts().get_id(), cost.get_id())


class ReleaseThreadConnectionTestCase(unittest.TestCase):

	def setUp(self):
		self.databaseManager = DatabaseManager(logging.getLogger("test"), False)
		self.database = SqliteDatabase(":memory:")
		self.databaseManager._database = self.database

	def tearDown(self):
		self.database.close()

	def test_closesAConnectionHeldOutsideAScope(self):
		self.database.connect()
		self.databaseManager.releaseThreadConnection()
		self.assertTrue(self.database.is_closed())

	def test_keepsTheConnectionInsideAScope(self):
		with self.databaseManager._connectionScope():
			self.databaseManager.releaseThreadConnection()
			self.assertFalse(self.database.is_closed())
		self.assertTrue(self.database.is_closed())

	def test_keepsTheConnectionDuringATransaction(self):
		self.database.connect()
		with self.database.atomic():
			self.databaseManager.releaseThreadConnection()
			self.assertFalse(self.database.is_closed())

	def test_withoutDatabaseIsANoOp(self):
		self.databaseManager._database = None
		self.databaseManager.releaseThreadConnection()


if __name__ == "__main__":
	unittest.main()
