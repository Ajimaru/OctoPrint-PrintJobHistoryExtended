# coding=utf-8
"""
Tests for adopting the data of a previous "PrintJobHistory" install.

The plugin class cannot be instantiated without a running OctoPrint, so the migration
methods are exercised on a stand-in that provides only what they touch: a data folder, a
settings namespace and a logger.
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from octoprint_PrintJobHistoryExtended import (
    LEGACY_IDENTIFIER,
    LEGACY_DATABASE_FILE_NAME,
    DATABASE_FILE_NAME,
    PrintJobHistoryExtendedPlugin,
)


class FakeSettings:
    def __init__(self, baseFolder, legacyValues=None):
        self._baseFolder = baseFolder
        self._own = {}
        self._namespaces = {LEGACY_IDENTIFIER: dict(legacyValues or {})}
        self.saved = 0

    def getBaseFolder(self, name):
        folder = os.path.join(self._baseFolder, name)
        os.makedirs(folder, exist_ok=True)
        return folder

    def global_get(self, path):
        if path[:1] == ["plugins"] and len(path) == 2:
            return self._namespaces.get(path[1])
        return None

    def get(self, path):
        return self._own.get(path[0])

    def set(self, path, value):
        if value is None:
            self._own.pop(path[0], None)
        else:
            self._own[path[0]] = value

    def save(self):
        self.saved += 1


class FakeLogger:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def exception(self, *a, **k): pass


def createDatabase(path, jobCount):
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE pjh_printjobmodel (databaseId INTEGER PRIMARY KEY, "
        "fileName TEXT, printStartDateTime TEXT, printStatusResult TEXT)"
    )
    connection.execute("CREATE TABLE pjh_pluginmetadatamodel (key TEXT, value TEXT)")
    connection.execute(
        "INSERT INTO pjh_pluginmetadatamodel VALUES ('databaseSchemeVersion', '8')"
    )
    for i in range(jobCount):
        connection.execute(
            "INSERT INTO pjh_printjobmodel (fileName, printStartDateTime, printStatusResult) "
            "VALUES (?, ?, ?)",
            ("job%d.gcode" % i, "2026-01-%02d 10:00:00" % (i + 1), "success"),
        )
    connection.commit()
    connection.close()


class LegacyMigrationTest(unittest.TestCase):

    def setUp(self):
        self.baseFolder = tempfile.mkdtemp()
        self.dataFolder = os.path.join(self.baseFolder, "data", "PrintJobHistoryExtended")
        os.makedirs(self.dataFolder)
        self.legacyFolder = os.path.join(self.baseFolder, "data", LEGACY_IDENTIFIER)
        os.makedirs(self.legacyFolder)

        self.plugin = PrintJobHistoryExtendedPlugin.__new__(PrintJobHistoryExtendedPlugin)
        self.plugin._logger = FakeLogger()
        self.plugin._identifier = "PrintJobHistoryExtended"
        self.plugin._settings = FakeSettings(self.baseFolder)
        self.plugin.get_plugin_data_folder = lambda: self.dataFolder

    def tearDown(self):
        shutil.rmtree(self.baseFolder, ignore_errors=True)

    def legacyDatabase(self, jobCount=3):
        path = os.path.join(self.legacyFolder, LEGACY_DATABASE_FILE_NAME)
        createDatabase(path, jobCount)
        return path

    # ------------------------------------------------------------------ availability

    def test_nothingToMigrateWhenNoLegacyFolder(self):
        shutil.rmtree(self.legacyFolder)
        self.assertFalse(self.plugin._isLegacyMigrationAvailable())

    def test_availableWhenLegacyDatabaseExists(self):
        self.legacyDatabase()
        self.assertTrue(self.plugin._isLegacyMigrationAvailable())

    def test_availableWhenOnlyLegacySettingsExist(self):
        self.plugin._settings = FakeSettings(self.baseFolder, {"currencySymbol": "GBP"})
        self.assertTrue(self.plugin._isLegacyMigrationAvailable())

    # ------------------------------------------------------------------ copying

    def test_migrationCopiesDatabaseUnderNewName(self):
        self.legacyDatabase(jobCount=3)
        result = self.plugin._performLegacyMigration()

        self.assertTrue(result["success"], result["errorMessage"])
        target = os.path.join(self.dataFolder, DATABASE_FILE_NAME)
        self.assertTrue(os.path.isfile(target))
        self.assertTrue(self.plugin._databaseHoldsPrintJobs(target))
        # copy, never move - the old install has to keep working
        self.assertTrue(os.path.isfile(os.path.join(self.legacyFolder, LEGACY_DATABASE_FILE_NAME)))

    def test_migrationCopiesSnapshotFolder(self):
        self.legacyDatabase()
        snapshots = os.path.join(self.legacyFolder, "snapshots")
        os.makedirs(snapshots)
        with open(os.path.join(snapshots, "20260101-120000.jpg"), "w") as handle:
            handle.write("image")

        self.plugin._performLegacyMigration()

        self.assertTrue(
            os.path.isfile(os.path.join(self.dataFolder, "snapshots", "20260101-120000.jpg"))
        )

    def test_fileNamesRestrictsWhatIsCopied(self):
        self.legacyDatabase()
        with open(os.path.join(self.legacyFolder, "printJobHistory-backup-old.db"), "w") as handle:
            handle.write("backup")

        self.plugin._performLegacyMigration(fileNames=[LEGACY_DATABASE_FILE_NAME])

        self.assertTrue(os.path.isfile(os.path.join(self.dataFolder, DATABASE_FILE_NAME)))
        self.assertFalse(os.path.isfile(os.path.join(self.dataFolder, "printJobHistory-backup-old.db")))

    # ------------------------------------------------------------------ conflict guard

    def test_refusesToReplaceDatabaseThatHoldsJobs(self):
        self.legacyDatabase(jobCount=3)
        createDatabase(os.path.join(self.dataFolder, DATABASE_FILE_NAME), jobCount=5)

        result = self.plugin._performLegacyMigration()

        self.assertFalse(result["success"])
        self.assertTrue(result["conflict"])
        # nothing was touched
        target = os.path.join(self.dataFolder, DATABASE_FILE_NAME)
        connection = sqlite3.connect(target)
        self.assertEqual(5, connection.execute("SELECT COUNT(*) FROM pjh_printjobmodel").fetchone()[0])
        connection.close()

    def test_emptyOwnDatabaseIsNotAConflict(self):
        """The plugin creates an empty database on first start - that must not warn."""
        self.legacyDatabase(jobCount=3)
        createDatabase(os.path.join(self.dataFolder, DATABASE_FILE_NAME), jobCount=0)

        result = self.plugin._performLegacyMigration()

        self.assertTrue(result["success"], result["errorMessage"])

    def test_overwriteExistingProceedsAndKeepsReplacedFile(self):
        self.legacyDatabase(jobCount=3)
        createDatabase(os.path.join(self.dataFolder, DATABASE_FILE_NAME), jobCount=5)

        result = self.plugin._performLegacyMigration(overwriteExisting=True)

        self.assertTrue(result["success"], result["errorMessage"])
        replaced = [n for n in os.listdir(self.dataFolder) if ".pre-migration-" in n]
        self.assertEqual(1, len(replaced))

    # ------------------------------------------------------------------ settings

    def test_settingsAreMigratedButPathKeysAreNot(self):
        self.plugin._settings = FakeSettings(self.baseFolder, {
            "currencySymbol": "GBP",
            "databaseFileLocation": "/old/path/printJobHistory.db",
            "snapshotFileLocation": "/old/path/snapshots",
            "installed_version": "1.17.5",
        })
        self.legacyDatabase()

        result = self.plugin._performLegacyMigration(includeSettings=True)

        self.assertTrue(result["settingsMigrated"])
        self.assertEqual("GBP", self.plugin._settings.get(["currencySymbol"]))
        # these would point the new install back at the old plugin's folder
        self.assertIsNone(self.plugin._settings.get(["databaseFileLocation"]))
        self.assertIsNone(self.plugin._settings.get(["snapshotFileLocation"]))
        self.assertIsNone(self.plugin._settings.get(["installed_version"]))

    def test_applyLegacySettingsOnlyWritesSelectedKeys(self):
        self.plugin._settings = FakeSettings(self.baseFolder, {
            "currencySymbol": "GBP",
            "currencyFormat": "%v %s",
        })

        result = self.plugin._applyLegacySettings(["currencySymbol"])

        self.assertTrue(result["success"])
        self.assertEqual("GBP", self.plugin._settings.get(["currencySymbol"]))
        self.assertIsNone(self.plugin._settings.get(["currencyFormat"]))

    # ------------------------------------------------------------------ undo

    def test_undoRestoresReplacedDatabase(self):
        self.legacyDatabase(jobCount=3)
        createDatabase(os.path.join(self.dataFolder, DATABASE_FILE_NAME), jobCount=5)

        self.plugin._performLegacyMigration(overwriteExisting=True)
        undo = self.plugin._undoLegacyMigration("database")

        self.assertTrue(undo["success"], undo["errorMessage"])
        self.assertEqual(1, undo["restoredFiles"])
        connection = sqlite3.connect(os.path.join(self.dataFolder, DATABASE_FILE_NAME))
        self.assertEqual(5, connection.execute("SELECT COUNT(*) FROM pjh_printjobmodel").fetchone()[0])
        connection.close()

    def test_undoRemovesSettingsThatHadNoValueBefore(self):
        self.plugin._settings = FakeSettings(self.baseFolder, {"currencySymbol": "GBP"})
        self.legacyDatabase()

        self.plugin._performLegacyMigration(includeSettings=True)
        self.assertEqual("GBP", self.plugin._settings.get(["currencySymbol"]))

        self.plugin._undoLegacyMigration("settings")
        self.assertIsNone(self.plugin._settings.get(["currencySymbol"]))

    def test_undoWithoutRecordFails(self):
        result = self.plugin._undoLegacyMigration("database")
        self.assertFalse(result["success"])

    def test_bannerRetiresAfterMigrationAndReturnsAfterUndo(self):
        self.legacyDatabase(jobCount=3)
        self.assertFalse(self.plugin._isLegacyMigrationDone())

        self.plugin._performLegacyMigration()
        self.assertTrue(self.plugin._isLegacyMigrationDone())

        self.plugin._undoLegacyMigration("database")
        self.assertFalse(self.plugin._isLegacyMigrationDone())

    # ------------------------------------------------------------------ repeat protection

    def test_secondMigrationIsRefusedAfterTheFirstOne(self):
        """
        Without this the second run copies over the already migrated database. The conflict
        guard below cannot catch it: the plugin holds its database open until it restarts,
        so the freshly copied jobs are not visible to it yet.
        """
        self.legacyDatabase(jobCount=3)
        first = self.plugin._performLegacyMigration()
        self.assertTrue(first["success"])

        second = self.plugin._performLegacyMigration()

        self.assertFalse(second["success"])
        self.assertTrue(second["conflict"])
        self.assertIn("already been migrated", second["errorMessage"])

    def test_undoLiftsTheAlreadyMigratedBlock(self):
        """
        After an undo the "already migrated" block is gone. What may still stand in the way
        is the ordinary conflict guard - if the undo left a database holding jobs behind,
        replacing it stays the user's decision.
        """
        self.legacyDatabase(jobCount=3)
        self.plugin._performLegacyMigration()
        self.plugin._undoLegacyMigration("database")
        self.plugin._undoLegacyMigration("settings")

        again = self.plugin._performLegacyMigration()

        self.assertNotIn("already been migrated", again.get("errorMessage") or "")
        # and with an explicit confirmation it goes through
        confirmed = self.plugin._performLegacyMigration(overwriteExisting=True)
        self.assertTrue(confirmed["success"], confirmed["errorMessage"])

    def test_undoingOnlyTheDataUnblocksMigrationEvenWithMigratedSettings(self):
        """
        Undoing the data while keeping the migrated settings is a legitimate state. The
        remaining settings record must not keep the migration barred.
        """
        self.plugin._settings = FakeSettings(self.baseFolder, {"currencySymbol": "GBP"})
        self.legacyDatabase(jobCount=3)
        createDatabase(os.path.join(self.dataFolder, DATABASE_FILE_NAME), jobCount=0)

        self.plugin._performLegacyMigration(includeSettings=True)
        self.plugin._undoLegacyMigration("database")
        # the settings record is deliberately left in place
        self.assertTrue(self.plugin._isLegacyMigrationUndoAvailable("settings"))

        again = self.plugin._performLegacyMigration(includeSettings=False)

        self.assertTrue(again["success"], again["errorMessage"])

    def test_migrationIsPossibleAgainAfterUndoIntoEmptyInstall(self):
        """The undo put the previous (empty) database back, so a rerun just works."""
        self.legacyDatabase(jobCount=3)
        createDatabase(os.path.join(self.dataFolder, DATABASE_FILE_NAME), jobCount=0)

        self.plugin._performLegacyMigration()
        self.plugin._undoLegacyMigration("database")
        self.plugin._undoLegacyMigration("settings")

        again = self.plugin._performLegacyMigration()

        self.assertTrue(again["success"], again["errorMessage"])

    # ------------------------------------------------------------------ restart marker

    def test_migrationAsksForRestartAndStartupClearsIt(self):
        self.legacyDatabase(jobCount=3)

        result = self.plugin._performLegacyMigration()

        self.assertTrue(result["restartRequired"])
        self.assertTrue(self.plugin._isRestartRequired())

        # what on_after_startup does once the database has been reopened
        self.plugin._setRestartRequired(False)
        self.assertFalse(self.plugin._isRestartRequired())

    def test_settingsOnlyMigrationDoesNotAskForRestart(self):
        """Settings are read live - only a swapped database file needs the restart."""
        self.plugin._settings = FakeSettings(self.baseFolder, {"currencySymbol": "GBP"})
        # legacy folder exists but holds no database
        result = self.plugin._performLegacyMigration(includeSettings=True)

        self.assertTrue(result["success"], result["errorMessage"])
        self.assertFalse(result["restartRequired"])
        self.assertFalse(self.plugin._isRestartRequired())

    def test_undoAsksForRestartToo(self):
        self.legacyDatabase(jobCount=3)
        createDatabase(os.path.join(self.dataFolder, DATABASE_FILE_NAME), jobCount=5)
        self.plugin._performLegacyMigration(overwriteExisting=True)
        self.plugin._setRestartRequired(False)

        undo = self.plugin._undoLegacyMigration("database")

        self.assertTrue(undo["restartRequired"])
        self.assertTrue(self.plugin._isRestartRequired())

    # ------------------------------------------------------------------ preview

    def test_previewReportsJobCountAndSchemeWithoutTouchingTheFile(self):
        path = self.legacyDatabase(jobCount=4)

        preview = self.plugin._readLegacyDatabasePreview(path)

        self.assertTrue(preview["readable"])
        self.assertEqual(4, preview["jobCount"])
        self.assertEqual("8", str(preview["schemeVersion"]))
        self.assertFalse(os.path.exists(path + "-journal"))

    def test_previewOfUnreadableFileIsNotAnError(self):
        path = os.path.join(self.legacyFolder, LEGACY_DATABASE_FILE_NAME)
        with open(path, "w") as handle:
            handle.write("this is not a database")

        preview = self.plugin._readLegacyDatabasePreview(path)

        self.assertFalse(preview["readable"])

    # ------------------------------------------------------------------ classification

    def test_databaseAndSnapshotsArePreselectedBackupsAreNot(self):
        self.legacyDatabase()
        os.makedirs(os.path.join(self.legacyFolder, "snapshots"))
        with open(os.path.join(self.legacyFolder, "printJobHistory-backup-old.db"), "w") as handle:
            handle.write("backup")

        entries = {entry["name"]: entry for entry in self.plugin._getLegacyFileEntries()}

        self.assertTrue(entries[LEGACY_DATABASE_FILE_NAME]["preselected"])
        self.assertTrue(entries["snapshots"]["preselected"])
        self.assertFalse(entries["printJobHistory-backup-old.db"]["preselected"])


if __name__ == "__main__":
    unittest.main()
