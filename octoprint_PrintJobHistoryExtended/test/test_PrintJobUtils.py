# coding=utf-8
"""
Tests for deciding whether a stored print job can be selected for printing again.

The job in the database holds a path, but two different forms of it are needed: the
storage-relative one, which is the only thing OctoPrint's file manager accepts, and the
absolute one on disk, which is what a person reads in a tooltip or an error message. Mixing
the two is what broke "Select for printing" on OctoPrint 2.0: 1.x still resolved an absolute
path back to its relative form, 2.0 joins it onto the storage root instead and ends up with a
doubled path that exists nowhere.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from octoprint_PrintJobHistoryExtended.common import PrintJobUtils

LOCAL = "local"
PRINTER = "printer"


class FakeStorageError(Exception):
	pass


class FakeFileManager:
	"""
	The few file manager methods this code uses, with the same path arithmetic as
	OctoPrint's LocalFileStorage, so the tests exercise the real resolution rules.
	"""

	def __init__(self, basefolder, pathOnDiskError=None, pathInStorageError=None):
		self._basefolder = basefolder
		self._pathOnDiskError = pathOnDiskError
		self._pathInStorageError = pathInStorageError
		self.pathOnDiskCalls = []
		self.pathInStorageCalls = []

	def path_on_disk(self, origin, path):
		self.pathOnDiskCalls.append((origin, path))
		if (self._pathOnDiskError != None):
			raise self._pathOnDiskError
		return os.path.join(self._basefolder, path)

	def path_in_storage(self, origin, path):
		self.pathInStorageCalls.append((origin, path))
		if (self._pathInStorageError != None):
			raise self._pathInStorageError
		if (path.startswith(self._basefolder)):
			path = path[len(self._basefolder):]
		path = path.replace(os.path.sep, "/")
		while (path.startswith("/")):
			path = path[1:]
		return path


class PrintJobUtilsTest(unittest.TestCase):

	def setUp(self):
		self._basefolder = tempfile.mkdtemp()
		self._fileManager = FakeFileManager(self._basefolder)

	def tearDown(self):
		shutil.rmtree(self._basefolder, ignore_errors=True)

	def _createFile(self, relativePath):
		fullPath = os.path.join(self._basefolder, relativePath)
		folder = os.path.dirname(fullPath)
		if (len(folder) > 0 and os.path.exists(folder) == False):
			os.makedirs(folder)
		with open(fullPath, "w") as f:
			f.write("; a print job\n")
		return fullPath

	######################################################################################## LOCAL
	def test_localFileIsReprintable(self):
		fullPath = self._createFile("toppery/nutki.gcode")

		result = PrintJobUtils.isPrintJobReprintable(self._fileManager, LOCAL, "toppery/nutki.gcode", "nutki.gcode")

		self.assertTrue(result["isRePrintable"])
		self.assertEqual("toppery/nutki.gcode", result["storagePath"])
		self.assertEqual(fullPath, result["fullFileLocation"])
		self.assertEqual(None, result["notReprintableReason"])

	def test_localFileMissing(self):
		result = PrintJobUtils.isPrintJobReprintable(self._fileManager, LOCAL, "toppery/gone.gcode", "gone.gcode")

		self.assertFalse(result["isRePrintable"])
		self.assertEqual("notFound", result["notReprintableReason"])
		# still filled, so the error message can name the place that was searched
		self.assertEqual("toppery/gone.gcode", result["storagePath"])

	def test_absolutePathInDatabaseIsNormalized(self):
		# The regression this whole change is about: older rows hold the absolute path.
		# Handing that to the file manager used to produce <basefolder>/<basefolder>/...
		fullPath = self._createFile("toppery/nutki.gcode")

		result = PrintJobUtils.isPrintJobReprintable(self._fileManager, LOCAL, fullPath, "nutki.gcode")

		self.assertTrue(result["isRePrintable"])
		self.assertEqual("toppery/nutki.gcode", result["storagePath"])
		self.assertFalse(os.path.isabs(result["storagePath"]))
		self.assertEqual(fullPath, result["fullFileLocation"])

	def test_leadingSlashIsStripped(self):
		self._createFile("toppery/nutki.gcode")

		result = PrintJobUtils.isPrintJobReprintable(self._fileManager, LOCAL, "/toppery/nutki.gcode", "nutki.gcode")

		self.assertEqual("toppery/nutki.gcode", result["storagePath"])
		self.assertTrue(result["isRePrintable"])

	def test_fileNameIsUsedWhenPathIsEmpty(self):
		self._createFile("nutki.gcode")

		result = PrintJobUtils.isPrintJobReprintable(self._fileManager, LOCAL, "", "nutki.gcode")

		self.assertTrue(result["isRePrintable"])
		self.assertEqual("nutki.gcode", result["storagePath"])

	def test_originNoneIsTreatedAsLocal(self):
		# csv-imported jobs carry no origin
		self._createFile("nutki.gcode")

		result = PrintJobUtils.isPrintJobReprintable(self._fileManager, None, "nutki.gcode", "nutki.gcode")

		self.assertTrue(result["isRePrintable"])
		self.assertEqual([(LOCAL, "nutki.gcode")], self._fileManager.pathOnDiskCalls)

	######################################################################################## PRINTER-HOSTED
	def test_printerHostedJobKeepsThePlainPath(self):
		result = PrintJobUtils.isPrintJobReprintable(self._fileManager, PRINTER, "cover.gcode", "cover.gcode")

		self.assertTrue(result["isRePrintable"])
		# the origin belongs in the text for people, never in the path for the file manager
		self.assertEqual("cover.gcode", result["storagePath"])
		self.assertEqual("printer:/cover.gcode", result["fullFileLocation"])
		self.assertEqual(None, result["notReprintableReason"])
		# the file lives on the printer, asking the disk about it would only raise
		self.assertEqual([], self._fileManager.pathOnDiskCalls)

	def test_printerHostedJobWithFolder(self):
		result = PrintJobUtils.isPrintJobReprintable(self._fileManager, PRINTER, "cache/rocket.gcode.3mf", "rocket.gcode.3mf")

		self.assertTrue(result["isRePrintable"])
		self.assertEqual("cache/rocket.gcode.3mf", result["storagePath"])

	######################################################################################## PLACEHOLDER
	def test_placeholderPathIsNotReprintable(self):
		# the printer had a job but would not say which file
		result = PrintJobUtils.isPrintJobReprintable(self._fileManager, PRINTER, "???", "???")

		self.assertFalse(result["isRePrintable"])
		self.assertEqual(None, result["storagePath"])
		self.assertEqual("placeholder", result["notReprintableReason"])
		self.assertEqual([], self._fileManager.pathOnDiskCalls)
		self.assertEqual([], self._fileManager.pathInStorageCalls)

	def test_emptyPathAndNameIsNotReprintable(self):
		result = PrintJobUtils.isPrintJobReprintable(self._fileManager, LOCAL, "", "")

		self.assertFalse(result["isRePrintable"])
		self.assertEqual("placeholder", result["notReprintableReason"])

	def test_isPlaceholderFilePath(self):
		self.assertTrue(PrintJobUtils.isPlaceholderFilePath(""))
		self.assertTrue(PrintJobUtils.isPlaceholderFilePath(None))
		self.assertTrue(PrintJobUtils.isPlaceholderFilePath("???"))
		self.assertFalse(PrintJobUtils.isPlaceholderFilePath("nutki.gcode"))
		self.assertFalse(PrintJobUtils.isPlaceholderFilePath("???.gcode"))

	######################################################################################## STORAGE ERRORS
	def test_pathOnDiskRaisingIsNotAnError(self):
		fileManager = FakeFileManager(self._basefolder, pathOnDiskError=FakeStorageError("unsupported"))

		result = PrintJobUtils.isPrintJobReprintable(fileManager, LOCAL, "toppery/nutki.gcode", "nutki.gcode")

		self.assertFalse(result["isRePrintable"])
		self.assertEqual("unresolvable", result["notReprintableReason"])
		self.assertEqual("local:/toppery/nutki.gcode", result["fullFileLocation"])

	def test_pathInStorageRaisingFallsBackToTheStoredPath(self):
		fileManager = FakeFileManager(self._basefolder, pathInStorageError=FakeStorageError("nope"))
		self._createFile("toppery/nutki.gcode")

		result = PrintJobUtils.isPrintJobReprintable(fileManager, LOCAL, "toppery/nutki.gcode", "nutki.gcode")

		self.assertTrue(result["isRePrintable"])
		self.assertEqual("toppery/nutki.gcode", result["storagePath"])

	######################################################################################## EXTENSION POINT
	def test_localPathCandidatesHoldsOnlyTheRecordedPath(self):
		# Pins the hook an archive-folder lookup would extend: a second candidate added here
		# must not change anything else about the resolution.
		self.assertEqual(["toppery/nutki.gcode"], PrintJobUtils._localPathCandidates("toppery/nutki.gcode"))


class SdFlagTest(unittest.TestCase):
	"""
	The destination the selection goes to. 'sd' is only a way of saying "not the local
	storage"; since OctoPrint 2.0 FileDestinations.SDCARD and .PRINTER are the same value,
	so every printer-hosted job - sd-card, Bambu, Klipper - takes the same branch.
	"""

	def _sdFlagOf(self, fileOrigin):
		return PrintJobUtils.isPrinterHosted(fileOrigin)

	def test_localJobsDoNotGoToThePrinterStorage(self):
		self.assertFalse(self._sdFlagOf("local"))

	def test_jobsWithoutOriginAreTreatedAsLocal(self):
		# csv import again: PrintJobUtils resolves these as local, so the flag has to agree
		self.assertFalse(self._sdFlagOf(None))

	def test_printerHostedJobsGoToThePrinterStorage(self):
		self.assertTrue(self._sdFlagOf("printer"))


if __name__ == "__main__":
	unittest.main()
