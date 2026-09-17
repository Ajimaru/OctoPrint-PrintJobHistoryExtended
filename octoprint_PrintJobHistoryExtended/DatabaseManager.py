# coding=utf-8
from __future__ import absolute_import

import copy
import datetime
import logging
import os
import re
import shutil
import sqlite3

from octoprint_PrintJobHistoryExtended.WrappedLoggingHandler import WrappedLoggingHandler
from octoprint_PrintJobHistoryExtended.api import TransformPrintJob2JSON
from octoprint_PrintJobHistoryExtended.common import StringUtils
from octoprint_PrintJobHistoryExtended.common.SettingsKeys import SettingsKeys
from octoprint_PrintJobHistoryExtended.models.CostModel import CostModel
from octoprint_PrintJobHistoryExtended.models.FilamentModel import FilamentModel
from octoprint_PrintJobHistoryExtended.models.PrintJobModel import PrintJobModel
from octoprint_PrintJobHistoryExtended.models.PluginMetaDataModel import PluginMetaDataModel
# from octoprint_PrintJobHistoryExtended.models.PrintJobSpoolMapModel import PrintJobSpoolMapModel
from octoprint_PrintJobHistoryExtended.models.TemperatureModel import TemperatureModel
# Explicit imports instead of "from peewee import *": the star-import is what silently made
# PostgresqlDatabase available, and an explicit list makes the supported backends visible.
from peewee import SqliteDatabase, MySQLDatabase, chunked, fn, \
	OperationalError, InterfaceError, IntegrityError, DatabaseError, \
	FloatField, IntegerField, DecimalField, DateTimeField, DateField
from playhouse.pool import PooledMySQLDatabase
from playhouse.shortcuts import model_to_dict


FORCE_CREATE_TABLES = False
SQL_LOGGING = False

CURRENT_DATABASE_SCHEME_VERSION = 9

# Scheme versions below this one can only be migrated on a local SQLite database, because the
# migration scripts for 1..8 are raw sqlite3 scripts (PRAGMA, single-quoted identifiers, the
# SQLite table-rebuild idiom). External databases start at this version.
FIRST_PORTABLE_SCHEME_VERSION = 8

# MySQL error codes / messages that mean "the connection is gone, reconnecting will fix it"
LOST_CONNECTION_ERROR_CODES = (2006, 2013, 2055)
LOST_CONNECTION_MESSAGES = ("server has gone away", "lost connection", "broken pipe")

SAFE_SQL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")

# List all Models
MODELS = [PluginMetaDataModel, PrintJobModel, FilamentModel, TemperatureModel, CostModel]


class DatabaseManager(object):

	class DatabaseSettings:
		"""Carrier for everything needed to open a database connection.

		Deliberately a plain object and not the plugin settings themselves, because the
		connection test and the local -> external copy both need to swap in a candidate
		configuration and restore the previous one afterwards.
		"""

		def __init__(self):
			self.baseFolder = ""
			self.fileLocation = ""
			self.useExternal = False
			self.type = "sqlite"
			self.name = ""
			self.host = ""
			self.port = 3306
			self.user = ""
			self.password = ""

		def __str__(self):
			# The password is masked on purpose: this object is logged to octoprint.log.
			return (
				"DatabaseSettings[useExternal=" + str(self.useExternal) +
				", type=" + str(self.type) +
				", host=" + str(self.host) +
				", port=" + str(self.port) +
				", name=" + str(self.name) +
				", user=" + str(self.user) +
				", password=" + ("***" if self.password else "") +
				", fileLocation=" + str(self.fileLocation) + "]"
			)


	def __init__(self, parentLogger, sqlLoggingEnabled):

		self.sqlLoggingEnabled = sqlLoggingEnabled
		self._logger = logging.getLogger(parentLogger.name + "." + self.__class__.__name__)
		self._sqlLogger = logging.getLogger(parentLogger.name + "." + self.__class__.__name__ + ".SQL")

		self._database = None
		self._databaseFileLocation = None
		self._sendDataToClient = None
		self._databaseSettings = DatabaseManager.DatabaseSettings()
		self._instanceName = ""
		# True when an external database needs a migration that we refuse to run automatically
		self._schemeUpgradeNeeded = False

	################################################################################################## private functions

	def _isMissingTableError(self, errorMessage):
		"""Does this error mean "the table is not there yet"? The wording differs per backend."""
		return (errorMessage.startswith("no such table")		# SQLite
				or "doesn't exist" in errorMessage)			# MySQL, error 1146

	def _createOrUpgradeSchemeIfNecessary(self):
		schemeVersionFromDatabaseModel = None
		try:
			schemeVersionFromDatabaseModel = PluginMetaDataModel.get(PluginMetaDataModel.key == PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION)
			pass
		except Exception as e:
			errorMessage = str(e)
			if self._isMissingTableError(errorMessage):

				self._logger.info("Create database-table, because didn't exists")
				self._createDatabaseTables()
			else:
				self._logger.error(str(e))

		if not schemeVersionFromDatabaseModel == None:
			currentDatabaseSchemeVersion = int(schemeVersionFromDatabaseModel.value)
			if (currentDatabaseSchemeVersion < CURRENT_DATABASE_SCHEME_VERSION):
				# An external database can be shared by several OctoPrint instances. Letting
				# each of them start an ALTER TABLE on it at boot is a corruption path, so the
				# migration is deferred to an explicit button in the settings.
				if (self._databaseSettings.useExternal == True):
					self._logger.warning(
						"Database-scheme upgrade needed, but it is not done automatically for an "
						"external database. Use the 'Upgrade database scheme' button in the plugin "
						"settings (Storage tab).")
					self._schemeUpgradeNeeded = True
					return

				# evautate upgrade steps (from 1-2 , 1...6)
				self._logger.info("We need to upgrade the database scheme from: '" + str(currentDatabaseSchemeVersion) + "' to: '" + str(CURRENT_DATABASE_SCHEME_VERSION) + "'")

				try:
					self.backupDatabaseFile(self._databasePath)
					self._upgradeDatabase(currentDatabaseSchemeVersion, CURRENT_DATABASE_SCHEME_VERSION)
				except Exception as e:
					self._logger.error("Error during database upgrade!!!!")
					self._logger.exception(e)
					return
				self._logger.info("Database-scheme successfully upgraded.")
				self._schemeUpgradeNeeded = False
			else:
				self._schemeUpgradeNeeded = False
		pass


	def upgradeDatabaseScheme(self):
		"""Run the pending migration explicitly. Used for external databases, where the
		automatic upgrade at startup is refused on purpose."""
		result = {
			"success": False,
			"errorMessage": None,
			"schemeVersion": None
		}
		try:
			schemeVersionModel = PluginMetaDataModel.get(
				PluginMetaDataModel.key == PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION)
			currentDatabaseSchemeVersion = int(schemeVersionModel.value)

			if (currentDatabaseSchemeVersion >= CURRENT_DATABASE_SCHEME_VERSION):
				result["success"] = True
				result["schemeVersion"] = currentDatabaseSchemeVersion
				return result

			self._upgradeDatabase(currentDatabaseSchemeVersion, CURRENT_DATABASE_SCHEME_VERSION)

			schemeVersionModel = PluginMetaDataModel.get(
				PluginMetaDataModel.key == PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION)
			result["schemeVersion"] = int(schemeVersionModel.value)
			result["success"] = True
			self._schemeUpgradeNeeded = False
		except Exception as e:
			result["errorMessage"] = str(e)
			self._logger.error("Error during manual database upgrade")
			self._logger.exception(e)
		return result

	def _upgradeDatabase(self,currentDatabaseSchemeVersion, targetDatabaseSchemeVersion):

		migrationFunctions = [self._upgradeFrom1To2,
							  self._upgradeFrom2To3,
							  self._upgradeFrom3To4,
							  self._upgradeFrom4To5,
							  self._upgradeFrom5To6,
							  self._upgradeFrom6To7,
							  self._upgradeFrom7To8,
							  self._upgradeFrom8To9,
							  self._upgradeFrom9To10
							  ]

		# Migrations 1..8 are raw sqlite3 scripts (PRAGMA, single-quoted identifiers, the
		# SQLite table-rebuild idiom) and cannot run against MySQL.
		if (self._databaseSettings.useExternal == True and currentDatabaseSchemeVersion < FIRST_PORTABLE_SCHEME_VERSION):
			raise DatabaseError(
				"Upgrades from scheme version " + str(currentDatabaseSchemeVersion) +
				" are not supported for external databases. Migrate the local database first, "
				"then copy it to the external database.")

		for migrationMethodIndex in range(currentDatabaseSchemeVersion -1, targetDatabaseSchemeVersion -1):
			self._logger.info("Database migration from '" + str(migrationMethodIndex + 1) + "' to '" + str(migrationMethodIndex + 2) + "'")
			migrationFunctions[migrationMethodIndex]()
			pass
		pass

	def _upgradeFrom9To10(self):
		self._logger.info(" Starting 9 -> 10")
		self._logger.info(" Successfully 9 -> 10")
		pass

	def _upgradeFrom8To9(self):
		self._logger.info(" Starting 8 -> 9")
		# What is changed:
		# - PrintJobModel:
		# 	- Add Column: instanceName
		#
		# First migration that runs through peewee instead of a raw sqlite3 script, so it
		# works on SQLite and MySQL alike. The column check makes it idempotent, which matters
		# because several OctoPrint instances may point at the same external database and each
		# of them will reach this code.
		tableName = self._assertSafeSQLIdentifier("pjh_printjobmodel")
		columnNames = [column.name for column in self._database.get_columns(tableName)]
		if ("instanceName" in columnNames):
			self._logger.info("  column 'instanceName' already present, skipping ALTER TABLE")
		else:
			self._database.execute_sql("ALTER TABLE " + tableName + " ADD COLUMN instanceName VARCHAR(255)")

		# Rows that existed before this migration came from this instance's own local database,
		# so they can be attributed. On an external database a NULL may belong to any instance,
		# so it is left alone.
		if (self._databaseSettings.useExternal == False and StringUtils.isEmpty(self._instanceName) == False):
			updatedRows = PrintJobModel.update(instanceName=self._instanceName).where(
				PrintJobModel.instanceName.is_null()).execute()
			self._logger.info("  assigned instance name '" + self._instanceName + "' to " + str(updatedRows) + " existing print jobs")

		PluginMetaDataModel.update(value="9").where(
			PluginMetaDataModel.key == PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION).execute()

		self._logger.info(" Successfully 8 -> 9")
		pass

	def _upgradeFrom7To8(self):
		self._logger.info(" Starting 7 -> 8")
		# What is changed:
		# - PrintJobModel:
		# 	- Add Column: technicalLog

		connection = sqlite3.connect(self._databaseFileLocation)
		cursor = connection.cursor()

		sql = """
		PRAGMA foreign_keys=off;
		BEGIN TRANSACTION;

			ALTER TABLE 'pjh_printjobmodel' ADD 'technicalLog' TEXT;

				UPDATE 'pjh_pluginmetadatamodel' SET value=8 WHERE key='databaseSchemeVersion';
		COMMIT;
		PRAGMA foreign_keys=on;
		"""
		cursor.executescript(sql)

		connection.close()

		self._logger.info(" Successfully 7 -> 8")
		pass

	def _upgradeFrom6To7(self):
		self._logger.info(" Starting 6 -> 7")

		connection = sqlite3.connect(self._databaseFileLocation)
		cursor = connection.cursor()

		## Changeset
		# - NEW CostModel
		# - Droping costUnit, because now there is a general plugin-setting

		# 			ALTER TABLE "pjh_filamentmodel" DROP COLUMN "spoolCostUnit"; Not working for DB Release < 3.30.0 (offical 3.35.0)
		sql = """
		PRAGMA foreign_keys=off;
		BEGIN TRANSACTION;

			CREATE TABLE "pjh_costmodel" ("databaseId" INTEGER NOT NULL PRIMARY KEY,
			"created" DATETIME NOT NULL,
			"printJob_id" INTEGER NOT NULL,
			"totalCosts" REAL,
			"filamentCost" REAL,
			"electricityCost" REAL,
			"printerCost" REAL,
			"otherCostLabel" VARCHAR(255),
			"otherCost" REAL,
			"withDefaultSpoolValues" INTEGER,
			FOREIGN KEY ("printJob_id") REFERENCES "pjh_printjobmodel" ("databaseId") ON DELETE CASCADE);

			UPDATE 'pjh_pluginmetadatamodel' SET value=7 WHERE key='databaseSchemeVersion';
		COMMIT;
		PRAGMA foreign_keys=on;
		"""
		cursor.executescript(sql)

		connection.close()

		self._logger.info(" Successfully 6 -> 7")
		pass

	def _upgradeFrom5To6(self):
		self._logger.info(" Starting 5 -> 6")
		# What is changed:
		# - FilamentModel:
		# 	- renameing:
		# 		profileVendor -> vendor
		# 		spoolWeight -> weight
		#   (ALTER TABLE spo_spoolmodel RENAME COLUMN encloserTemperature to enclosureTemperature; not working SQLite did not support the ALTER TABLE RENAME COLUMN syntax before version 3.25.0.
		# 	see https://www.sqlitetutorial.net/sqlite-rename-column/#:~:text=SQLite%20did%20not%20support%20the,the%20version%20lower%20than%203.25.)

		connection = sqlite3.connect(self._databaseFileLocation)
		cursor = connection.cursor()

		sql = """
		PRAGMA foreign_keys=off;
		BEGIN TRANSACTION;

			UPDATE 'pjh_filamentmodel' SET toolId='total' where toolId is NULL;

			UPDATE 'pjh_pluginmetadatamodel' SET value=6 WHERE key='databaseSchemeVersion';
		COMMIT;
		PRAGMA foreign_keys=on;
		"""
		cursor.executescript(sql)

		connection.close()
		self._logger.info(" Successfully 5 -> 6")
		pass

	def _upgradeFrom4To5(self):
		self._logger.info(" Starting 4 -> 5")
		# What is changed:
		# - FilamentModel:
		# 	- renameing:
		# 		profileVendor -> vendor
		# 		spoolWeight -> weight
		#   (ALTER TABLE spo_spoolmodel RENAME COLUMN encloserTemperature to enclosureTemperature; not working SQLite did not support the ALTER TABLE RENAME COLUMN syntax before version 3.25.0.
		# 	see https://www.sqlitetutorial.net/sqlite-rename-column/#:~:text=SQLite%20did%20not%20support%20the,the%20version%20lower%20than%203.25.)

		connection = sqlite3.connect(self._databaseFileLocation)
		cursor = connection.cursor()

		sql = """
		PRAGMA foreign_keys=off;
		BEGIN TRANSACTION;

			ALTER TABLE 'pjh_filamentmodel' RENAME TO 'pjh_filamentmodel_old';

			CREATE TABLE "pjh_filamentmodel" (
				"databaseId" INTEGER NOT NULL PRIMARY KEY,
				"created" DATETIME NOT NULL,
				"printJob_id" INTEGER NOT NULL,
				"vendor" VARCHAR(255),
				"diameter" REAL,
				"density" REAL,
				"material" VARCHAR(255),
				"spoolName" VARCHAR(255),
				"spoolCost" REAL,
				"spoolCostUnit" VARCHAR(255),
				"weight" REAL,
				"usedLength" REAL,
				"calculatedLength" REAL,
				"usedWeight" REAL,
				"usedCost" REAL,
				'toolId' VARCHAR(255),
				FOREIGN KEY ("printJob_id") REFERENCES "pjh_printjobmodel" ("databaseId") ON DELETE CASCADE);

			INSERT INTO 'pjh_filamentmodel'
			(databaseId, created, printJob_id, vendor, diameter, density, material, spoolName, spoolCost, spoolCostUnit, weight, usedLength, calculatedLength, usedWeight, usedCost, toolId)
			 SELECT databaseId, created, printJob_id, profileVendor, diameter, density, material, spoolName, spoolCost, spoolCostUnit, spoolWeight, usedLength, calculatedLength, usedWeight, usedCost, toolId
			 FROM 'pjh_filamentmodel_old';

			DROP TABLE 'pjh_filamentmodel_old';

			UPDATE 'pjh_pluginmetadatamodel' SET value=5 WHERE key='databaseSchemeVersion';
		COMMIT;
		PRAGMA foreign_keys=on;
		"""
		cursor.executescript(sql)

		connection.close()
		self._logger.info(" Successfully 4 -> 5")
		pass

	def _upgradeFrom3To4(self):
		self._logger.info(" Starting 3 -> 4")
		# What is changed:
		# - FilamentModel:
		# 	- add toolId = CharField(null=True) # since V4	--> old values must be total, because no information about single tool

		connection = sqlite3.connect(self._databaseFileLocation)
		cursor = connection.cursor()

		sql = """
		PRAGMA foreign_keys=off;
		BEGIN TRANSACTION;

			ALTER TABLE 'pjh_filamentmodel' ADD 'toolId' VARCHAR(255);
			UPDATE 'pjh_filamentmodel' SET toolId='total';

			UPDATE 'pjh_pluginmetadatamodel' SET value=4 WHERE key='databaseSchemeVersion';
		COMMIT;
		PRAGMA foreign_keys=on;
		"""
		cursor.executescript(sql)

		connection.close()
		self._logger.info(" Successfully 3 -> 4")
		pass

	def _upgradeFrom2To3(self):
		self._logger.info(" Starting 2 -> 3")
		# What is changed:
		# - PrintJobModel:
		# 	- Add Column: slicerSettingsAsText

		connection = sqlite3.connect(self._databaseFileLocation)
		cursor = connection.cursor()

		sql = """
		PRAGMA foreign_keys=off;
		BEGIN TRANSACTION;

			ALTER TABLE 'pjh_printjobmodel' ADD 'slicerSettingsAsText' TEXT;

				UPDATE 'pjh_pluginmetadatamodel' SET value=3 WHERE key='databaseSchemeVersion';
		COMMIT;
		PRAGMA foreign_keys=on;
		"""
		cursor.executescript(sql)

		connection.close()
		self._logger.info(" Successfully 2 -> 3")
		pass

	def _upgradeFrom1To2(self):
		self._logger.info(" Starting 1 -> 2")
		# What is changed:
		# - PrintJobModel: Add Column fileOrigin
		# - FilamentModel: Several ColumnTypes were wrong
		connection = sqlite3.connect(self._databaseFileLocation)
		cursor = connection.cursor()

		sql = """
		PRAGMA foreign_keys=off;
		BEGIN TRANSACTION;

			ALTER TABLE 'pjh_printjobmodel' ADD 'fileOrigin' VARCHAR(255);

			ALTER TABLE 'pjh_filamentmodel' RENAME TO 'pjh_filamentmodel_old';
			CREATE TABLE "pjh_filamentmodel" (
				"databaseId" INTEGER NOT NULL PRIMARY KEY,
				"created" DATETIME NOT NULL,
				"printJob_id" INTEGER NOT NULL,
				"profileVendor" VARCHAR(255),
				"diameter" REAL,
				"density" REAL,
				"material" VARCHAR(255),
				"spoolName" VARCHAR(255),
				"spoolCost" VARCHAR(255),
				"spoolCostUnit" VARCHAR(255),
				"spoolWeight" REAL,
				"usedLength" REAL,
				"calculatedLength" REAL,
				"usedWeight" REAL,
				"usedCost" REAL,
				FOREIGN KEY ("printJob_id") REFERENCES "pjh_printjobmodel" ("databaseId") ON DELETE CASCADE);

				INSERT INTO 'pjh_filamentmodel' (databaseId, created, printJob_id, profileVendor, diameter, density, material, spoolName, spoolCost, spoolCostUnit, spoolWeight, usedLength, calculatedLength, usedWeight, usedCost)
				  SELECT databaseId, created, printJob_id, profileVendor, diameter, density, material, spoolName, spoolCost, spoolCostUnit, spoolWeight, usedLength, calculatedLength, usedWeight, usedCost
				  FROM 'pjh_filamentmodel_old';

				DROP TABLE 'pjh_filamentmodel_old';

				UPDATE 'pjh_pluginmetadatamodel' SET value=2 WHERE key='databaseSchemeVersion';
		COMMIT;
		PRAGMA foreign_keys=on;
		"""
		cursor.executescript(sql)

		connection.close()
		pass



	def _createDatabaseTables(self):
		self._database.connect(reuse_if_open=True)
		# MySQL/InnoDB really enforces the foreign keys (SQLite does not by default), so the
		# child tables have to be dropped first. peewee's drop_tables sorts by dependency and
		# drops in reverse, which is correct here; safe=True tolerates tables that do not exist yet.
		self._database.drop_tables(MODELS, safe=True)
		self._database.create_tables(MODELS)

		PluginMetaDataModel.create(key=PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION, value=CURRENT_DATABASE_SCHEME_VERSION)
		self._database.close()
		self._logger.info("Database tables created")





	################################################################################################### public functions

	def _assertSafeSQLIdentifier(self, identifier):
		"""Guard for identifiers that end up in a raw SQL string instead of a bound parameter."""
		if not SAFE_SQL_IDENTIFIER_PATTERN.match(str(identifier)):
			raise ValueError("Unsafe SQL identifier: '" + str(identifier) + "'")
		return identifier


	def _isLostConnection(self, exception):
		"""True when the exception means the connection died and a reconnect would help."""
		errorArgs = getattr(exception, "args", None)
		if (errorArgs and len(errorArgs) > 0 and errorArgs[0] in LOST_CONNECTION_ERROR_CODES):
			return True
		errorMessage = str(exception).lower()
		for lostConnectionMessage in LOST_CONNECTION_MESSAGES:
			if (lostConnectionMessage in errorMessage):
				return True
		return False


	def _executeWithRetry(self, operation, operationName):
		"""Run a database operation, reconnecting once if the connection was lost.

		Only safe for single statements and for operations wrapped in their own atomic()
		block: the server rolls an interrupted transaction back, so the retry is a clean
		re-run rather than a partial duplicate. Never use this around a multi-statement
		sequence that is not inside a transaction.
		"""
		try:
			return operation()
		except (OperationalError, InterfaceError) as e:
			if (not self._isLostConnection(e)):
				raise
			self._logger.warning("Lost database connection during '" + operationName + "', reconnecting...")
			try:
				self.closeDatabase()
			except Exception:
				pass	# the connection is gone anyway
			self.connectToDatabase()
			return operation()


	def _sanitizeRowForStrictDatabase(self, model, rowDict):
		"""Make a row read from SQLite acceptable to MySQL.

		SQLite does not enforce column types, so historic rows can hold an empty string in a
		numeric column. MySQL rejects that with "Incorrect double value: ''". Empty strings in
		numeric and date fields therefore become NULL, which is what they always meant.
		"""
		for field in model._meta.sorted_fields:
			fieldName = field.name
			if (fieldName not in rowDict):
				continue
			value = rowDict[fieldName]
			if (isinstance(value, str) == False or value.strip() != ""):
				continue
			if (isinstance(field, (FloatField, IntegerField, DecimalField, DateTimeField, DateField))):
				rowDict[fieldName] = None
		return rowDict


	def _buildDatabaseConnection(self):
		"""Create the peewee database object matching the current settings."""
		if (self._databaseSettings.useExternal == False):
			# check_same_thread is an sqlite3 kwarg. peewee forwards unknown kwargs straight
			# to the driver, so it must never reach the MySQL branch.
			return SqliteDatabase(self._databaseSettings.fileLocation, check_same_thread=False)

		databaseType = self._databaseSettings.type
		if (databaseType != SettingsKeys.KEY_DATABASE_TYPE_MYSQL):
			raise ValueError("Unsupported external database type: '" + str(databaseType) + "'")

		# Pooled, because stale_timeout recycles connections before MySQL's wait_timeout (or a
		# router's NAT timeout) drops them, which is the usual "MySQL server has gone away".
		# charset is set explicitly: MySQL's default "utf8" is 3-byte and cannot store emoji
		# in file names or notes.
		return PooledMySQLDatabase(
			self._databaseSettings.name,
			user=self._databaseSettings.user,
			password=self._databaseSettings.password,
			host=self._databaseSettings.host,
			port=int(self._databaseSettings.port),
			max_connections=8,
			stale_timeout=280,
			charset="utf8mb4"
		)


	def connectToDatabase(self, sendErrorPopUp=True):
		"""(Re)build the connection from the current settings and bind the models to it."""
		try:
			self._database = self._buildDatabaseConnection()
			DatabaseManager.db = self._database
			self._database.bind(MODELS)
			self._database.connect(reuse_if_open=True)
			return True
		except Exception as e:
			self._logger.error("Could not connect to database " + str(self._databaseSettings))
			self._logger.exception(e)
			if (sendErrorPopUp == True and self.sendErrorMessageToClient != None):
				self.sendErrorMessageToClient("PJH-DatabaseManager",
											  "Could not connect to the database. See OctoPrint.log for details!")
			return False


	def closeDatabase(self):
		if (self._database != None):
			try:
				self._database.close()
			except Exception as e:
				self._logger.warning("Could not close database connection: " + str(e))


	def isExternalDatabase(self):
		return self._databaseSettings.useExternal == True


	def isSchemeUpgradeNeeded(self):
		return self._schemeUpgradeNeeded


	def getDatabaseSettings(self):
		# A copy, because callers back up / mutate / restore these settings. With a shared
		# object the "backup" would be mutated too and the restore would be a no-op.
		return copy.copy(self._databaseSettings)


	def assignNewDatabaseSettings(self, databaseSettings):
		self._databaseSettings = databaseSettings
		if (databaseSettings.useExternal == False):
			self._databaseFileLocation = databaseSettings.fileLocation


	def setInstanceName(self, instanceName):
		self._instanceName = instanceName if instanceName != None else ""


	def getInstanceName(self):
		return self._instanceName


	def testDatabaseConnection(self, databaseSettings):
		"""Try the given settings without disturbing the live connection.

		Returns None on success, otherwise an error message.
		"""
		backupDatabaseSettings = self._databaseSettings
		backupDatabase = self._database
		errorMessage = None
		try:
			self._databaseSettings = databaseSettings
			testDatabase = self._buildDatabaseConnection()
			testDatabase.connect(reuse_if_open=True)
			testDatabase.close()
		except Exception as e:
			errorMessage = str(e)
			self._logger.warning("Test database-connection failed: " + errorMessage)
		finally:
			# Restore first, then rebind: the models are global, so a failed test must not
			# leave them pointing at the candidate database.
			self._databaseSettings = backupDatabaseSettings
			self._database = backupDatabase
			if (self._database != None):
				self._database.bind(MODELS)
				DatabaseManager.db = self._database
		return errorMessage


	def _readSchemeVersionAndJobCount(self):
		"""Read scheme version and job count from the currently bound database."""
		schemeVersion = None
		jobCount = None
		try:
			schemeVersionModel = PluginMetaDataModel.get(
				PluginMetaDataModel.key == PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION)
			schemeVersion = int(schemeVersionModel.value)
			jobCount = PrintJobModel.select().count()
		except Exception as e:
			if (self._isMissingTableError(str(e)) == False):
				raise
		return (schemeVersion, jobCount)


	def loadDatabaseMetaInformations(self, databaseSettings=None):
		"""Report scheme version and job count for the local and (if configured) external database.

		databaseSettings: candidate external settings to probe, or None to use the stored ones.
		"""
		result = {
			"success": True,
			"errorMessage": None,
			"schemeVersionFromPlugin": CURRENT_DATABASE_SCHEME_VERSION,
			"localSchemeVersion": None,
			"localJobCount": None,
			"externalSchemeVersion": None,
			"externalJobCount": None,
			"schemeUpgradeNeeded": self._schemeUpgradeNeeded,
			"instanceName": self._instanceName
		}

		backupDatabaseSettings = self._databaseSettings
		backupDatabase = self._database

		externalSettings = databaseSettings if databaseSettings != None else copy.copy(backupDatabaseSettings)

		try:
			# --- local SQLite
			localSettings = copy.copy(backupDatabaseSettings)
			localSettings.useExternal = False
			localSettings.type = SettingsKeys.KEY_DATABASE_TYPE_SQLITE
			localSettings.fileLocation = self._databaseFileLocation
			try:
				self._databaseSettings = localSettings
				if (self.connectToDatabase(sendErrorPopUp=False) == True):
					(result["localSchemeVersion"], result["localJobCount"]) = self._readSchemeVersionAndJobCount()
				self.closeDatabase()
			except Exception as e:
				self._logger.warning("Could not read local database meta data: " + str(e))

			# --- external database, only when one is configured
			if (externalSettings.useExternal == True):
				try:
					self._databaseSettings = externalSettings
					if (self.connectToDatabase(sendErrorPopUp=False) == True):
						(result["externalSchemeVersion"], result["externalJobCount"]) = self._readSchemeVersionAndJobCount()
					else:
						result["success"] = False
						result["errorMessage"] = "Could not connect to the external database."
					self.closeDatabase()
				except Exception as e:
					result["success"] = False
					result["errorMessage"] = str(e)
					self._logger.warning("Could not read external database meta data: " + str(e))
		finally:
			# Always restore the live connection, whatever happened above.
			self._databaseSettings = backupDatabaseSettings
			self._database = backupDatabase
			if (self._database != None):
				self._database.bind(MODELS)
				DatabaseManager.db = self._database

		return result


	# datapasePath '/Users/o0632/Library/Application Support/OctoPrint/data/PrintJobHistoryExtended'
	def initDatabase(self, databasePath, sendErrorMessageToClient, databaseSettings=None):
		self._logger.info("Init DatabaseManager")
		self.sendErrorMessageToClient = sendErrorMessageToClient
		self._databasePath = databasePath
		self._databaseFileLocation = os.path.join(databasePath, "printJobHistoryExtended.db")

		if (databaseSettings == None):
			databaseSettings = DatabaseManager.DatabaseSettings()
		databaseSettings.baseFolder = databasePath
		databaseSettings.fileLocation = self._databaseFileLocation
		self._databaseSettings = databaseSettings

		if (databaseSettings.useExternal == True):
			self._logger.info("Using external database: " + str(databaseSettings))
		else:
			self._logger.info("Using database in: " + str(self._databaseFileLocation))

		import logging
		logger = logging.getLogger('peewee')
		# we need only the single logger without parent
		logger.parent = None
		# logger.addHandler(logging.StreamHandler())
		# activate SQL logging on PEEWEE side and on PLUGIN side

		# logger.setLevel(logging.DEBUG)
		# self._sqlLogger.setLevel(logging.DEBUG)
		self.showSQLLogging(self.sqlLoggingEnabled)

		wrappedHandler = WrappedLoggingHandler(self._sqlLogger)
		logger.addHandler(wrappedHandler)

		self._createDatabase(FORCE_CREATE_TABLES)

		pass

	def showSQLLogging(self, enabled):
		import logging
		logger = logging.getLogger('peewee')

		if (enabled):
			logger.setLevel(logging.DEBUG)
			self._sqlLogger.setLevel(logging.DEBUG)
		else:
			logger.setLevel(logging.ERROR)
			self._sqlLogger.setLevel(logging.ERROR)


	def backupDatabaseFile(self, backupFolder):
		# A file copy only makes sense for the local SQLite file. An external database is
		# backed up with the server's own tools (mysqldump).
		if (self._databaseSettings.useExternal == True):
			self._logger.warning("No database file backup created, because an external database is in use.")
			return None

		now = datetime.datetime.now()
		currentDate = now.strftime("%Y%m%d-%H%M")
		currentSchemeVersion = "unknown"
		try:
			currentSchemeVersion = PluginMetaDataModel.get(PluginMetaDataModel.key == PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION)
			if (currentSchemeVersion != None):
				currentSchemeVersion = str(currentSchemeVersion.value)
		except Exception as e:
			self._logger.exception("Could not read databasescheme version:" + str(e))
		backupDatabaseFileName = "printJobHistoryExtended-backup-"+currentDate+"-V"+currentSchemeVersion +".db"
		backupDatabaseFilePath = os.path.join(backupFolder, backupDatabaseFileName)
		if not os.path.exists(backupDatabaseFilePath):
			shutil.copy(self._databaseFileLocation, backupDatabaseFilePath)
			self._logger.info("Backup of printjobhistoryextended database created '"+backupDatabaseFilePath+"'")
		else:
			self._logger.warning("Backup of printjobhistoryextended database ('" + backupDatabaseFilePath + "') is already present. No backup created.")
		return backupDatabaseFilePath


	def _createDatabase(self, forceCreateTables):
		if (self.connectToDatabase() == False):
			return

		if forceCreateTables:
			self._logger.info("Creating new database-tables, because FORCE == TRUE!")
			self._createDatabaseTables()
		else:
			# check, if we need an scheme upgrade
			self._logger.info("Check if database-scheme upgrade needed.")
			self._createOrUpgradeSchemeIfNecessary()
		self._logger.info("Done DatabaseManager.createDatabase")


	def copyPrintJobDataToExternalDatabase(self, databaseSettings):
		"""Copy every print job (and its filaments, temperatures and costs) from the local
		SQLite database into the configured external database.

		The print job ids are NOT preserved: on a shared database instance A's id 47 would
		collide with instance B's. MySQL assigns new ids and the child rows are remapped onto
		them.

		The copy is additive: the external database is never emptied, because it may already
		hold the print jobs of other OctoPrint instances. Only the tables that do not exist
		yet are created.

		Snapshot images are files on this instance and are not copied.
		"""
		result = {
			"success": False,
			"errorMessage": None,
			"copiedJobCount": 0,
			"totalJobCountAfterCopy": None
		}

		backupDatabaseSettings = self._databaseSettings
		backupDatabase = self._database

		try:
			# ---------- phase A: read everything from the local SQLite database
			localSettings = copy.copy(backupDatabaseSettings)
			localSettings.useExternal = False
			localSettings.type = SettingsKeys.KEY_DATABASE_TYPE_SQLITE
			localSettings.fileLocation = self._databaseFileLocation
			self._databaseSettings = localSettings

			if (self.connectToDatabase(sendErrorPopUp=False) == False):
				result["errorMessage"] = "Could not connect to the local database."
				return result

			# Materialise before closing: a peewee select() is lazy and would otherwise be
			# evaluated against the external database.
			# recurse=False keeps the foreign keys as plain ids instead of nested objects.
			allJobs = [self._sanitizeRowForStrictDatabase(PrintJobModel, model_to_dict(job, backrefs=False, recurse=False))
					   for job in PrintJobModel.select()]
			allFilaments = [self._sanitizeRowForStrictDatabase(FilamentModel, model_to_dict(filament, backrefs=False, recurse=False))
							for filament in FilamentModel.select()]
			allTemperatures = [self._sanitizeRowForStrictDatabase(TemperatureModel, model_to_dict(temperature, backrefs=False, recurse=False))
							   for temperature in TemperatureModel.select()]
			allCosts = [self._sanitizeRowForStrictDatabase(CostModel, model_to_dict(cost, backrefs=False, recurse=False))
						for cost in CostModel.select()]
			self.closeDatabase()

			self._logger.info("Copying " + str(len(allJobs)) + " print jobs to the external database")

			# ---------- phase B: write them to the external database
			externalSettings = copy.copy(databaseSettings)
			externalSettings.useExternal = True
			self._databaseSettings = externalSettings

			if (self.connectToDatabase(sendErrorPopUp=False) == False):
				result["errorMessage"] = "Could not connect to the external database."
				return result

			# NOT _createDatabaseTables(): that drops the tables first, which would delete the
			# print jobs of every other instance sharing this database. safe=True creates only
			# what is missing and leaves existing data untouched.
			self._database.create_tables(MODELS, safe=True)
			if (PluginMetaDataModel.get_or_none(
					PluginMetaDataModel.key == PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION) == None):
				PluginMetaDataModel.create(key=PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION,
										   value=CURRENT_DATABASE_SCHEME_VERSION)

			# Refuse a second copy from the same instance: the jobs carry no stable external id,
			# so copying again would simply duplicate all of them.
			existingOwnJobCount = PrintJobModel.select().where(
				PrintJobModel.instanceName == self._instanceName).count()
			if (existingOwnJobCount > 0):
				result["errorMessage"] = (
					"The external database already contains " + str(existingOwnJobCount) +
					" print job(s) for instance '" + self._instanceName + "'. Copying again would "
					"create duplicates. Delete those jobs first if you really want to copy again.")
				return result

			# One transaction for the whole copy: InnoDB actually enforces the foreign keys,
			# so a half-finished copy would leave orphaned child rows behind.
			with self._database.atomic():
				printJobIdMapping = {}
				for jobDict in allJobs:
					oldDatabaseId = jobDict.pop("databaseId")
					if (StringUtils.isEmpty(jobDict.get("instanceName")) == True):
						jobDict["instanceName"] = self._instanceName
					newDatabaseId = PrintJobModel.insert(jobDict).execute()
					printJobIdMapping[oldDatabaseId] = newDatabaseId

				for (childRows, childModel) in ((allFilaments, FilamentModel),
												(allTemperatures, TemperatureModel),
												(allCosts, CostModel)):
					remappedRows = []
					for childRow in childRows:
						childRow.pop("databaseId", None)
						oldPrintJobId = childRow.get("printJob")
						if (oldPrintJobId not in printJobIdMapping):
							# Orphaned row in the source database, nothing to attach it to.
							continue
						childRow["printJob"] = printJobIdMapping[oldPrintJobId]
						remappedRows.append(childRow)

					# Chunked, so a few thousand rows do not exceed max_allowed_packet.
					for rowBatch in chunked(remappedRows, 100):
						childModel.insert_many(list(rowBatch)).execute()

				result["copiedJobCount"] = len(printJobIdMapping)

			result["totalJobCountAfterCopy"] = PrintJobModel.select().count()

			self.closeDatabase()
			result["success"] = True
			self._logger.info("Copied " + str(result["copiedJobCount"]) + " print jobs to the external database")

		except Exception as e:
			result["errorMessage"] = str(e)
			self._logger.error("Could not copy print job data to the external database")
			self._logger.exception(e)
		finally:
			# Restore the live connection whatever happened.
			try:
				self.closeDatabase()
			except Exception:
				pass
			self._databaseSettings = backupDatabaseSettings
			self._database = backupDatabase
			if (self._database != None):
				self._database.bind(MODELS)
				DatabaseManager.db = self._database

		return result


	def getDatabase(self):
		return self._database

	def getDatabaseFileLocation(self):
		return self._databaseFileLocation

	def reCreateDatabase(self):
		self._logger.info("ReCreating Database")
		self._createDatabase(True)

	def insertPrintJob(self, printJobModel):
		return self._executeWithRetry(lambda: self._insertPrintJob(printJobModel), "insertPrintJob")

	def _insertPrintJob(self, printJobModel):
		databaseId = None
		# Identify the originating instance, so a shared database stays attributable.
		if (StringUtils.isEmpty(printJobModel.instanceName) == True):
			printJobModel.instanceName = self._instanceName
		with self._database.atomic() as transaction:  # Opens new transaction.
			try:
				printJobModel.save()
				databaseId = printJobModel.get_id()
				# save all relations
				# - Filament
				for filamentModel in printJobModel.getFilamentModels():
					filamentModel.printJob = printJobModel
					filamentModel.save()
				# - Temperature
				for temperatureModel in printJobModel.getTemperatureModels():
					temperatureModel.printJob = printJobModel
					temperatureModel.save()
				# - Costs
				if (printJobModel.getCosts() != None):
					printJobModel.getCosts().save()
			except Exception as e:
				# Because this block of code is wrapped with "atomic", a
				# new transaction will begin automatically after the call
				# to rollback().
				transaction.rollback()
				databaseId = None
				self._logger.exception("Could not insert printJob into database:" + str(e))

				self.sendErrorMessageToClient("PJH-DatabaseManager", "Could not insert the printjob into the database. See OctoPrint.log for details!")
			pass

		return databaseId

	def updatePrintJob(self, printJobModel, rollbackHandler = None):
		return self._executeWithRetry(lambda: self._updatePrintJob(printJobModel, rollbackHandler), "updatePrintJob")

	def _updatePrintJob(self, printJobModel, rollbackHandler = None):
		with self._database.atomic() as transaction:  # Opens new transaction.
			try:
				printJobModel.save()
				databaseId = printJobModel.get_id()
				# save all relations
				# - Filament
				for filamentModel in printJobModel.getFilamentModels():
					filamentModel.save()

				# # - Temperature not needed for an update
				# for temperatureModel in printJobModel.getTemperatureModels():
				# 	temperatureModel.printJob = printJobModel
				# 	temperatureModel.save()

				# - Costs
				if (printJobModel.getCosts() != None):
					printJobModel.getCosts().save()
			except Exception as e:
				# Because this block of code is wrapped with "atomic", a
				# new transaction will begin automatically after the call
				# to rollback().
				transaction.rollback()
				self._logger.exception("Could not update printJob into database:" + str(e))
				# The parameter is optional, so calling it unconditionally would raise a
				# TypeError that hides the actual database error.
				if (rollbackHandler != None):
					rollbackHandler()
				self.sendErrorMessageToClient("PJH-DatabaseManager", "Could not update the printjob ('"+ printJobModel.fileName +"') into the database. See OctoPrint.log for details!")
			pass

	#
	def calculatePrintJobsStatisticByQuery(self, tableQuery):

		printJobCount = 0
		duration = 0
		length = 0.0
		weight = 0.0
		fileSize = 0
		statusDict = dict()
		materialDict = dict()
		spoolDict = dict()
		firstDate = None
		lastDate = None
		newTableQuery = tableQuery.copy()
		newTableQuery["sortColumn"] = "printStartDateTime"
		newTableQuery["sortOrder"] = "asc"
		newTableQuery["from"] = 0
		newTableQuery["to"] = 999999
		allJobModels = self.loadPrintJobsByQuery(newTableQuery)

		for job in allJobModels:
			printJobCount = printJobCount + 1
			if (firstDate == None):
				firstDate = job.printStartDateTime
			if (job.printEndDateTime != None):
				lastDate = job.printEndDateTime
			tempJobFileSize = job.fileSize
			if (tempJobFileSize == None):
				tempJobFileSize = 0
			fileSize = fileSize + tempJobFileSize
			duration = duration + job.duration
			statusResult = job.printStatusResult

			if (statusResult in statusDict):
				currentCount = statusDict[statusResult]
				currentCount = currentCount + 1
				statusDict[statusResult] = currentCount
			else:
				statusDict[statusResult] = 1

			allFilaments = job.getFilamentModels()
			if allFilaments != None:
				for filla in allFilaments:
					if filla.toolId == "total":
						# exclude totals, otherwise everything is counted twice
						continue
					if (StringUtils.isEmpty(filla.usedLength) == False):
						length = length + filla.usedLength
					if (StringUtils.isEmpty(filla.usedWeight) == False):
						weight = weight + filla.usedWeight

					if (StringUtils.isEmpty(filla.spoolName) == False):
						# spoolsSet.add(filla.spoolName)
						if (filla.spoolName in spoolDict):
							currentCount = spoolDict[filla.spoolName]
							currentCount = currentCount + 1
							spoolDict[filla.spoolName] = currentCount
						else:
							spoolDict[filla.spoolName] = 1

					if (StringUtils.isEmpty(filla.material) == False):
						# materialsSet.add(filla.material)
						if (filla.material in materialDict):
							currentCount = materialDict[filla.material]
							currentCount = currentCount + 1
							materialDict[filla.material] = currentCount
						else:
							materialDict[filla.material] = 1

		# do formatting
		queryString = self._buildQueryString(tableQuery)
		lastDateString = ""
		if (lastDate != None):
			lastDateString = lastDate.strftime('%d.%m.%Y %H:%M')
		fromToString = firstDate.strftime('%d.%m.%Y %H:%M') + " - " + lastDateString
		durationString = StringUtils.secondsToText(duration)
		lengthString = self._buildLengthString(length)
		weightString = self._buildWeightString(weight)
		statusString = self._buildStatusString(statusDict)
		materialString = self._buildDictlString(materialDict)
		spoolString = self._buildDictlString(spoolDict)
		fileSizeString = StringUtils.get_formatted_size(fileSize)
		return {
			"printJobCount": printJobCount,
			"query": queryString,
			"fromToDate": fromToString,
			"duration": durationString,
			"usedLength": lengthString,
			"usedWeight": weightString,
			"fileSize": fileSizeString,
			"printStatus": statusString,
		    "material": materialString,
			"spools": spoolString
		}

	def _buildLengthString(self, length):
		lengthString = StringUtils.formatFloatSave("{:.02f}", TransformPrintJob2JSON.convertMM2M(length), "-")
		if (lengthString != "-"):
			lengthString = lengthString + "m"
		return lengthString

	def _buildWeightString(self, weight):
		weightString = StringUtils.formatFloatSave("{:.02f}", weight, "-")
		if (weightString != "-"):
			weightString = weightString + "g"
		return weightString

	def _buildQueryString(self, tableQuery):
		result = ""
		if ("startDate" in tableQuery):
			startDate = tableQuery["startDate"]
			endDate = tableQuery["endDate"]
			if (len(startDate) > 0 and len(endDate) > 0):
				# EndDay + 1
				# startDateTime = datetime.datetime.strptime(startDate, "%d.%m.%Y")
				# endDateTime = datetime.datetime.strptime(endDate, "%d.%m.%Y") + datetime.timedelta(days=1)
				result = "Timeframe: " + startDate + " - " + endDate + ","

		if (len(result)== 0):
			result = "Timeframe: all,"

		if (tableQuery["filterName"] == "onlySuccess"):
			result = result + " Filter: only successful"
		if (tableQuery["filterName"] == "onlyFailed"):
			result = result + " Filter: only failed"
		if (tableQuery["filterName"] == "all"):
			result = result + " Filter: all"

		return result

	def _buildStatusString(self, statusDict):
		result = ""
		if ("success" in statusDict):
			result = "success(" + str(statusDict["success"]) + ")"
		if ("canceled" in statusDict):
			if (len(result) != 0):
				result += ", "
			result = result + "canceled(" + str(statusDict["canceled"]) + ")"
		if ("failed" in statusDict):
			if (len(result) != 0):
				result += ", "
			result = result + "failed(" + str(statusDict["failed"]) + ")"

		return result

	def _buildDictlString(self, myDict):
		result = ""

		keys = myDict.keys()
		for key in keys:
			count = myDict[key]
			if (len(result) != 0):
				result = result + ", "
			result = result + key + "(" + str(count) + ")"

		if (len(result) == 0):
			result = "-"
		return result


	def countPrintJobsByQuery(self, tableQuery):

		# filterName = tableQuery["filterName"]

		myQuery = PrintJobModel.select()
		myQuery = self._addTableQueryToSelect(myQuery, tableQuery)
		# self._addTableQueryToSelect(myQuery, tableQuery)
		# if (filterName == "onlySuccess"):
		# 	myQuery = myQuery.where(PrintJobModel.printStatusResult == "success")
		# elif (filterName == "onlyFailed"):
		# 	myQuery = myQuery.where(PrintJobModel.printStatusResult != "success")

		return self._executeWithRetry(lambda: myQuery.count(), "countPrintJobsByQuery")


	def loadPrintJobsByQuery(self, tableQuery):
		offset = int(tableQuery["from"])
		limit = int(tableQuery["to"])
		# sortColumn = tableQuery["sortColumn"]
		# sortOrder = tableQuery["sortOrder"]
		# filterName = tableQuery["filterName"]

		# dont use join "Kartesischs-Produkt" myQuery = PrintJobModel.select().join(FilamentModel).offset(offset).limit(limit)
		myQuery = PrintJobModel.select().offset(offset).limit(limit)
		myQuery = self._addTableQueryToSelect(myQuery, tableQuery)
		# if (filterName == "onlySuccess"):
		# 	myQuery = myQuery.where(PrintJobModel.printStatusResult == "success")
		# elif (filterName == "onlyFailed"):
		# 	myQuery = myQuery.where(PrintJobModel.printStatusResult != "success")
		#
		# if ("printStartDateTime" == sortColumn):
		# 	if ("desc" == sortOrder):
		# 		myQuery = myQuery.order_by(PrintJobModel.printStartDateTime.desc())
		# 	else:
		# 		myQuery = myQuery.order_by(PrintJobModel.printStartDateTime)
		# if ("fileName" == sortColumn):
		# 	if ("desc" == sortOrder):
		# 		myQuery = myQuery.order_by(PrintJobModel.fileName.desc())
		# 	else:
		# 		myQuery = myQuery.order_by(PrintJobModel.fileName)
		# if ("startDate" in tableQuery):
		# 	startDate = tableQuery["startDate"]
		# 	endDate = tableQuery["endDate"]
		# 	if (len(startDate) > 0 and len(endDate) > 0):
		# 		# EndDay + 1
		# 		startDateTime = datetime.datetime.strptime(startDate, "%d.%m.%Y")
		# 		endDateTime = datetime.datetime.strptime(endDate, "%d.%m.%Y") + datetime.timedelta(days=1)
		#
		# 		# myQuery = myQuery.where( (( PrintJobModel.printStartDateTime == startDate) | ( PrintJobModel.printStartDateTime >  startDate))
		# 		# 						 &
		# 		# 						 ((PrintJobModel.printStartDateTime == endDate) | ( PrintJobModel.printStartDateTime <  startDate)) )
		# 		myQuery = myQuery.where( ( ( PrintJobModel.printStartDateTime > startDateTime) & ( PrintJobModel.printStartDateTime < endDateTime))
		# 								 )

		return myQuery



	def _addTableQueryToSelect(self, myQuery, tableQuery):

		sortColumn = tableQuery["sortColumn"]
		sortOrder = tableQuery["sortOrder"]
		filterName = tableQuery["filterName"]

		# - status
		if (filterName == "onlySuccess"):
			myQuery = myQuery.where(PrintJobModel.printStatusResult == "success")
		elif (filterName == "onlyFailed"):
			myQuery = myQuery.where(PrintJobModel.printStatusResult != "success")
		# -sorting
		if ("printStartDateTime" == sortColumn):
			if ("desc" == sortOrder):
				myQuery = myQuery.order_by(PrintJobModel.printStartDateTime.desc())
			else:
				myQuery = myQuery.order_by(PrintJobModel.printStartDateTime)
		if ("fileName" == sortColumn):
			if ("desc" == sortOrder):
				myQuery = myQuery.order_by(fn.Lower(PrintJobModel.fileName).desc())
			else:
				myQuery = myQuery.order_by(fn.Lower(PrintJobModel.fileName))
		# - date range
		if ("startDate" in tableQuery):
			startDate = tableQuery["startDate"]
			endDate = tableQuery["endDate"]
			if (len(startDate) > 0 and len(endDate) > 0):
				# EndDay + 1
				startDateTime = datetime.datetime.strptime(startDate, "%d.%m.%Y")
				endDateTime = datetime.datetime.strptime(endDate, "%d.%m.%Y") + datetime.timedelta(days=1)

				# myQuery = myQuery.where( (( PrintJobModel.printStartDateTime == startDate) | ( PrintJobModel.printStartDateTime >  startDate))
				# 						 &
				# 						 ((PrintJobModel.printStartDateTime == endDate) | ( PrintJobModel.printStartDateTime <  startDate)) )
				myQuery = myQuery.where( ( ( PrintJobModel.printStartDateTime > startDateTime) & ( PrintJobModel.printStartDateTime < endDateTime))
										 )
		# - search query (only filename)
		if ("searchQuery" in tableQuery):
			searchQueryValue = tableQuery["searchQuery"]
			if (len(searchQueryValue) > 0):
				myQuery = myQuery.where(PrintJobModel.fileName.contains(searchQueryValue))
				pass
		# - instance (only relevant when several OctoPrint instances share one database).
		#   Absent or "all" means: show everything.
		if ("instanceName" in tableQuery):
			instanceNameValue = tableQuery["instanceName"]
			if (StringUtils.isEmpty(instanceNameValue) == False and instanceNameValue != "all"):
				myQuery = myQuery.where(PrintJobModel.instanceName == instanceNameValue)
				pass
		return myQuery


	def loadKnownInstanceNames(self):
		"""Distinct instance names present in the database, for the filter UI."""
		def doLoad():
			allNames = []
			for job in PrintJobModel.select(PrintJobModel.instanceName).distinct():
				if (StringUtils.isEmpty(job.instanceName) == False):
					allNames.append(job.instanceName)
			return sorted(allNames)
		return self._executeWithRetry(doLoad, "loadKnownInstanceNames")


	def loadSelectedPrintJobs(self, selectedDatabaseIds):
		selectedDatabaseIdsSplitted = selectedDatabaseIds.split(',')
		databaseArray = []

		for dbId in selectedDatabaseIdsSplitted:
			databaseArray.append(dbId)

		return PrintJobModel.select().where(PrintJobModel.databaseId << databaseArray).order_by(PrintJobModel.printStartDateTime.desc())


	def loadAllPrintJobs(self):
		return PrintJobModel.select().order_by(PrintJobModel.printStartDateTime.desc())

		# return PrintJobModel.select().offset(offset).limit(limit).order_by(PrintJobModel.printStartDateTime.desc())
		# all = PrintJobModel.select().join(FilamentModel).switch(PrintJobModel).join(TemperatureModel).order_by(PrintJobModel.printStartDateTime.desc())
		# allDict = all.dicts()
		# result = prefetch(allJobsQuery, FilamentModel)
		# return result
		# return allDict

	def loadPrintJob(self, databaseId):
		databaseIdAsInt = StringUtils.transformToIntOrNone(databaseId)
		if (databaseIdAsInt == None):
			self._logger.error("Could not load PrintJob, because not a valid databaseId '"+str(databaseId)+"' maybe not a number")
			return None
		return self._executeWithRetry(lambda: PrintJobModel.get_or_none(databaseIdAsInt), "loadPrintJob")

	def deletePrintJob(self, databaseId):
		return self._executeWithRetry(lambda: self._deletePrintJob(databaseId), "deletePrintJob")

	def _deletePrintJob(self, databaseId):
		databaseIdAsInt = StringUtils.transformToIntOrNone(databaseId)
		if (databaseIdAsInt == None):
			self._logger.error("Could not delete PrintJob, because not a valid databaseId '"+str(databaseId)+"' maybe not a number")
			return None

		with self._database.atomic() as transaction:  # Opens new transaction.
			try:
				# first delete relations
				n = FilamentModel.delete().where(FilamentModel.printJob == databaseIdAsInt).execute()
				n = TemperatureModel.delete().where(TemperatureModel.printJob == databaseIdAsInt).execute()
				n = CostModel.delete().where(CostModel.printJob == databaseIdAsInt).execute()

				PrintJobModel.delete_by_id(databaseIdAsInt)
			except Exception as e:
				# Because this block of code is wrapped with "atomic", a
				# new transaction will begin automatically after the call
				# to rollback().
				transaction.rollback()
				self._logger.exception("Could not delete printJob from database:" + str(e))

				self.sendErrorMessageToClient("PJH-DatabaseManager", "Could not delete the printjob ('"+ str(databaseId) +"') from the database. See OctoPrint.log for details!")
			pass
