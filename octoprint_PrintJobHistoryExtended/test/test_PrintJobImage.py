# coding=utf-8
"""
Tests for the image of a finished print job: which source is asked, the fallback between
webcam and file preview, the M118 trigger, and how CameraManager stores the images.

The plugin class cannot be instantiated without a running OctoPrint, so the methods are
exercised on an instance created without __init__ that provides only what they touch. The
background thread's target is called directly.
"""
import datetime
import io
import logging
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from PIL import Image

import octoprint_PrintJobHistoryExtended as pluginModule
from octoprint_PrintJobHistoryExtended import PrintJobHistoryExtendedPlugin
from octoprint_PrintJobHistoryExtended.CameraManager import CameraManager
from octoprint_PrintJobHistoryExtended.models.PrintJobModel import PrintJobModel


PRINT_START = datetime.datetime(2026, 9, 29, 17, 5, 0)
SNAPSHOT_FILENAME = CameraManager.buildSnapshotFilename(PRINT_START)


class FakeSettings:
	def __init__(self, values=None):
		self._values = dict(values or {})

	def get(self, path):
		return self._values.get(path[0])

	def get_boolean(self, path):
		return self._values.get(path[0]) == True


class FakeHandle:
	def __init__(self, data):
		self._stream = io.BytesIO(data)
		self.closed = False

	def read(self):
		return self._stream.read()

	def close(self):
		self.closed = True


class FakeFileManager:
	def __init__(self, thumbnails=True, hasThumbnail=True, thumbnail=None, metadata=None):
		self._thumbnails = thumbnails
		self._hasThumbnail = hasThumbnail
		self._thumbnail = thumbnail
		self._metadata = metadata
		self.readThumbnailCalls = 0

	def capabilities(self, origin):
		return mock.Mock(thumbnails=self._thumbnails)

	def has_thumbnail(self, origin, path):
		return self._hasThumbnail

	def read_thumbnail(self, origin, path):
		self.readThumbnailCalls += 1
		if isinstance(self._thumbnail, Exception):
			raise self._thumbnail
		return self._thumbnail

	def get_metadata(self, origin, path):
		return self._metadata


def createPlugin(settings=None, fileManager=None):
	plugin = PrintJobHistoryExtendedPlugin.__new__(PrintJobHistoryExtendedPlugin)
	plugin._logger = logging.getLogger("test")
	plugin._settings = FakeSettings(settings)
	plugin._file_manager = fileManager if fileManager != None else FakeFileManager(thumbnails=False)
	plugin._cameraManager = mock.Mock()
	plugin._sendDataToClient = mock.Mock()
	plugin._currentPrintJobModel = PrintJobModel()
	plugin._currentPrintJobModel.printStartDateTime = PRINT_START
	plugin._m118SnapshotTaken = False
	return plugin


def imageBytes(format, size=(40, 20)):
	# left half red, right half blue - so flips and rotations can be told apart
	image = Image.new("RGB", size, (0, 0, 255))
	image.paste((255, 0, 0), (0, 0, size[0] // 2, size[1]))
	output = io.BytesIO()
	image.save(output, format=format)
	return output.getvalue()


def isRed(pixel):
	return pixel[0] > 200 and pixel[2] < 60


def isBlue(pixel):
	return pixel[2] > 200 and pixel[0] < 60


class ImageSourceTest(unittest.TestCase):

	def test_webcamFirstWhenItIsTheSource(self):
		plugin = createPlugin()
		plugin._cameraManager.takeSnapshot.return_value = True
		plugin._takePreviewImage = mock.Mock(return_value=True)

		plugin._grabImageInBackground("camera", SNAPSHOT_FILENAME, "local", "a.gcode")

		plugin._cameraManager.takeSnapshot.assert_called_once_with(SNAPSHOT_FILENAME)
		plugin._takePreviewImage.assert_not_called()
		plugin._sendDataToClient.assert_called_once_with({"action": "printJobImageUpdated", "snapshotFilename": SNAPSHOT_FILENAME})

	def test_previewStandsInWhenTheWebcamHasNoImage(self):
		plugin = createPlugin()
		plugin._cameraManager.takeSnapshot.return_value = False
		plugin._takePreviewImage = mock.Mock(return_value=True)

		plugin._grabImageInBackground("camera", SNAPSHOT_FILENAME, "printer", "a.gcode")

		plugin._takePreviewImage.assert_called_once_with(SNAPSHOT_FILENAME, "printer", "a.gcode")
		plugin._sendDataToClient.assert_called_once()

	def test_previewFirstWhenItIsTheSource(self):
		plugin = createPlugin()
		plugin._takePreviewImage = mock.Mock(return_value=True)

		plugin._grabImageInBackground("thumbnail", SNAPSHOT_FILENAME, "local", "a.gcode")

		plugin._cameraManager.takeSnapshot.assert_not_called()
		plugin._sendDataToClient.assert_called_once()

	def test_webcamStandsInWhenTheFileHasNoPreview(self):
		plugin = createPlugin()
		plugin._takePreviewImage = mock.Mock(return_value=False)
		plugin._cameraManager.takeSnapshot.return_value = True

		plugin._grabImageInBackground("thumbnail", SNAPSHOT_FILENAME, "local", "a.gcode")

		plugin._cameraManager.takeSnapshot.assert_called_once_with(SNAPSHOT_FILENAME)
		plugin._sendDataToClient.assert_called_once()

	def test_noImageAtAllIsNoError(self):
		plugin = createPlugin()
		plugin._takePreviewImage = mock.Mock(return_value=False)
		plugin._cameraManager.takeSnapshot.return_value = False

		plugin._grabImageInBackground("thumbnail", SNAPSHOT_FILENAME, "local", "a.gcode")

		plugin._sendDataToClient.assert_not_called()

	def test_failingSourceDoesNotEscapeTheThread(self):
		plugin = createPlugin()
		plugin._takePreviewImage = mock.Mock(side_effect=RuntimeError("boom"))

		plugin._grabImageInBackground("thumbnail", SNAPSHOT_FILENAME, "local", "a.gcode")

		plugin._sendDataToClient.assert_not_called()


class GrabImageTest(unittest.TestCase):

	def grab(self, plugin):
		with mock.patch.object(pluginModule.threading, "Thread") as threadClass:
			plugin._grabImage({"origin": "printer", "path": "a.3mf"})
		return threadClass

	def test_noImageWanted(self):
		threadClass = self.grab(createPlugin({"imageSourceAfterPrint": "none"}))
		threadClass.assert_not_called()

	def test_runsInItsOwnThread(self):
		plugin = createPlugin({"imageSourceAfterPrint": "thumbnail"})

		threadClass = self.grab(plugin)

		threadClass.assert_called_once()
		kwargs = threadClass.call_args.kwargs
		self.assertEqual(plugin._grabImageInBackground, kwargs["target"])
		self.assertEqual(("thumbnail", SNAPSHOT_FILENAME, "printer", "a.3mf"), kwargs["args"])
		threadClass.return_value.start.assert_called_once()

	def test_snapshotTheGcodeAskedForIsKept(self):
		plugin = createPlugin({"imageSourceAfterPrint": "camera"})
		plugin._m118SnapshotTaken = True

		self.grab(plugin).assert_not_called()

	def test_m118SnapshotDoesNotReplaceThePreview(self):
		plugin = createPlugin({"imageSourceAfterPrint": "thumbnail"})
		plugin._m118SnapshotTaken = True

		self.grab(plugin).assert_called_once()


class PreviewImageTest(unittest.TestCase):

	def test_storageWithoutThumbnailsIsNotAsked(self):
		fileManager = FakeFileManager(thumbnails=False)
		plugin = createPlugin(fileManager=fileManager)

		self.assertFalse(plugin._takeFilePreviewImage(SNAPSHOT_FILENAME, "printer", "a.gcode"))
		self.assertEqual(0, fileManager.readThumbnailCalls)

	def test_connectorAnswersHasThumbnailWithAList(self):
		# the Moonraker connector returns the thumbnail list instead of True
		handle = FakeHandle(b"png-bytes")
		fileManager = FakeFileManager(hasThumbnail=["a-300x300.png"],
									  thumbnail=(types.SimpleNamespace(name="a-300x300.png", sizehint="300x300"), handle))
		plugin = createPlugin(fileManager=fileManager)
		plugin._cameraManager.storeThumbnail.return_value = True

		self.assertTrue(plugin._takeFilePreviewImage(SNAPSHOT_FILENAME, "printer", "a.gcode"))
		plugin._cameraManager.storeThumbnail.assert_called_once_with(SNAPSHOT_FILENAME, b"png-bytes")
		self.assertTrue(handle.closed)

	def test_fileWithoutPreview(self):
		fileManager = FakeFileManager(hasThumbnail=False)
		plugin = createPlugin(fileManager=fileManager)

		self.assertFalse(plugin._takeFilePreviewImage(SNAPSHOT_FILENAME, "local", "a.gcode"))
		self.assertEqual(0, fileManager.readThumbnailCalls)

	def test_storageDeliversNothing(self):
		plugin = createPlugin(fileManager=FakeFileManager(thumbnail=None))

		self.assertFalse(plugin._takeFilePreviewImage(SNAPSHOT_FILENAME, "printer", "a.3mf"))

	def test_failingDownloadIsNoError(self):
		plugin = createPlugin(fileManager=FakeFileManager(thumbnail=IOError("printer offline")))

		self.assertFalse(plugin._takeFilePreviewImage(SNAPSHOT_FILENAME, "printer", "a.3mf"))

	def test_thumbnailPluginAsSecondChoice(self):
		fileManager = FakeFileManager(hasThumbnail=False,
									  metadata={"thumbnail": "plugin/prusaslicerthumbnails/thumbnail/a.png?20260929"})
		plugin = createPlugin(fileManager=fileManager)
		plugin._cameraManager.takePluginThumbnail.return_value = True

		self.assertTrue(plugin._takePreviewImage(SNAPSHOT_FILENAME, "local", "a.gcode"))
		plugin._cameraManager.takePluginThumbnail.assert_called_once_with(
			SNAPSHOT_FILENAME, "plugin/prusaslicerthumbnails/thumbnail/a.png?20260929")

	def test_noPreviewAnywhere(self):
		plugin = createPlugin(fileManager=FakeFileManager(hasThumbnail=False, metadata={}))

		self.assertFalse(plugin._takePreviewImage(SNAPSHOT_FILENAME, "local", "a.gcode"))
		plugin._cameraManager.takePluginThumbnail.assert_not_called()


class M118ActionTest(unittest.TestCase):

	def test_ignoredWhenTheFilePreviewIsTheSource(self):
		plugin = createPlugin({"imageSourceAfterPrint": "thumbnail", "takeSnapshotOnM118Commnd": True})

		plugin.on_receivedActionHook(None, "//action:pjhTakeSnapshot", "pjhTakeSnapshot")

		plugin._cameraManager.takeSnapshotAsync.assert_not_called()

	def test_ignoredWhenSwitchedOff(self):
		plugin = createPlugin({"imageSourceAfterPrint": "camera", "takeSnapshotOnM118Commnd": False})

		plugin.on_receivedActionHook(None, "//action:pjhTakeSnapshot", "pjhTakeSnapshot")

		plugin._cameraManager.takeSnapshotAsync.assert_not_called()

	def test_otherActionsAreNotOurs(self):
		plugin = createPlugin({"imageSourceAfterPrint": "camera", "takeSnapshotOnM118Commnd": True})

		plugin.on_receivedActionHook(None, "//action:pause", "pause")

		plugin._cameraManager.takeSnapshotAsync.assert_not_called()

	def test_successfulSnapshotIsRemembered(self):
		plugin = createPlugin({"imageSourceAfterPrint": "camera", "takeSnapshotOnM118Commnd": True})

		plugin.on_receivedActionHook(None, "//action:pjhTakeSnapshot", "pjhTakeSnapshot")

		snapshotFilename, sendError, callback = plugin._cameraManager.takeSnapshotAsync.call_args.args
		self.assertEqual(SNAPSHOT_FILENAME, snapshotFilename)
		self.assertFalse(plugin._m118SnapshotTaken)
		callback(True)
		self.assertTrue(plugin._m118SnapshotTaken)

	def test_failedSnapshotLeavesItToThePrintEnd(self):
		plugin = createPlugin({"imageSourceAfterPrint": "camera", "takeSnapshotOnM118Commnd": True})

		plugin.on_receivedActionHook(None, "//action:pjhTakeSnapshot", "pjhTakeSnapshot")
		plugin._cameraManager.takeSnapshotAsync.call_args.args[2](False)

		self.assertFalse(plugin._m118SnapshotTaken)

	def test_lateSnapshotDoesNotCountForTheNextPrint(self):
		plugin = createPlugin({"imageSourceAfterPrint": "camera", "takeSnapshotOnM118Commnd": True})
		plugin.on_receivedActionHook(None, "//action:pjhTakeSnapshot", "pjhTakeSnapshot")
		callback = plugin._cameraManager.takeSnapshotAsync.call_args.args[2]

		nextPrint = PrintJobModel()
		nextPrint.printStartDateTime = PRINT_START + datetime.timedelta(hours=1)
		plugin._currentPrintJobModel = nextPrint
		callback(True)

		self.assertFalse(plugin._m118SnapshotTaken)


class CameraManagerTest(unittest.TestCase):

	def setUp(self):
		self.baseFolder = tempfile.mkdtemp()
		self.dataFolder = os.path.join(self.baseFolder, "data", "PrintJobHistoryExtended")
		os.makedirs(self.dataFolder)
		self.cameraManager = CameraManager(logging.getLogger("test"))
		self.cameraManager.initCamera(self.dataFolder, self.baseFolder)
		self.storedImage = os.path.join(self.dataFolder, "snapshots", SNAPSHOT_FILENAME + ".png")

	def tearDown(self):
		shutil.rmtree(self.baseFolder, ignore_errors=True)

	def webcam(self, snapshot, canSnapshot=True, flipH=False, flipV=False, rotate90=False):
		webcam = mock.Mock()
		webcam.providerIdentifier = "classicwebcam"
		webcam.config.name = "classic"
		webcam.config.displayName = "Classic Webcam"
		webcam.config.canSnapshot = canSnapshot
		webcam.config.flipH = flipH
		webcam.config.flipV = flipV
		webcam.config.rotate90 = rotate90
		if isinstance(snapshot, Exception):
			webcam.providerPlugin.take_webcam_snapshot.side_effect = snapshot
		else:
			webcam.providerPlugin.take_webcam_snapshot.return_value = iter([snapshot[:10], b"", snapshot[10:]])
		return webcam

	def takeSnapshot(self, webcam, **kwargs):
		with mock.patch("octoprint_PrintJobHistoryExtended.CameraManager.get_snapshot_webcam", return_value=webcam):
			return self.cameraManager.takeSnapshot(SNAPSHOT_FILENAME, **kwargs)

	def test_snapshotIsStoredAsDelivered(self):
		jpeg = imageBytes("JPEG")
		webcam = self.webcam(jpeg)

		self.assertTrue(self.takeSnapshot(webcam))

		webcam.providerPlugin.take_webcam_snapshot.assert_called_once_with("classic")
		with open(self.storedImage, "rb") as storedFile:
			self.assertEqual(jpeg, storedFile.read())

	def test_snapshotIsTurnedLikeTheWebcamIsConfigured(self):
		self.assertTrue(self.takeSnapshot(self.webcam(imageBytes("JPEG"), flipH=True, rotate90=True)))

		with Image.open(self.storedImage) as image:
			# 40x20 turned by 90 degrees
			self.assertEqual((20, 40), image.size)
			self.assertEqual("JPEG", image.format)
			rgb = image.convert("RGB")
			# flipped first: red moves to the right, then counter-clockwise: to the top
			self.assertTrue(isRed(rgb.getpixel((10, 5))))
			self.assertTrue(isBlue(rgb.getpixel((10, 35))))

	def test_withoutWebcamNothingIsStored(self):
		callback = mock.Mock()
		sendError = mock.Mock()

		self.assertFalse(self.takeSnapshot(None, sendErrorMessageToClientFunction=sendError, callbackFunction=callback))

		callback.assert_called_once_with(False)
		sendError.assert_called_once()
		self.assertFalse(os.path.exists(self.storedImage))

	def test_webcamThatCannotTakeSnapshots(self):
		webcam = self.webcam(imageBytes("JPEG"), canSnapshot=False)

		self.assertFalse(self.takeSnapshot(webcam))

		webcam.providerPlugin.take_webcam_snapshot.assert_not_called()

	def test_failingWebcamIsNoError(self):
		self.assertFalse(self.takeSnapshot(self.webcam(IOError("401 Unauthorized"))))
		self.assertFalse(os.path.exists(self.storedImage))

	def test_errorPageInsteadOfAnImageIsNotStored(self):
		self.assertFalse(self.takeSnapshot(self.webcam(b"<html><body>Not found</body></html>")))
		self.assertFalse(os.path.exists(self.storedImage))
		self.assertFalse(os.path.exists(self.storedImage + ".part"))

	def test_previewIsStoredAsPng(self):
		self.assertTrue(self.cameraManager.storeThumbnail(SNAPSHOT_FILENAME, imageBytes("PNG", (30, 30))))

		with Image.open(self.storedImage) as image:
			self.assertEqual("PNG", image.format)
			self.assertEqual((30, 30), image.size)

	def test_pluginThumbnailFromItsDataFolder(self):
		pluginFolder = os.path.join(self.baseFolder, "data", "prusaslicerthumbnails")
		os.makedirs(pluginFolder)
		with open(os.path.join(pluginFolder, "my part.png"), "wb") as thumbnailFile:
			thumbnailFile.write(imageBytes("PNG", (16, 16)))

		stored = self.cameraManager.takePluginThumbnail(SNAPSHOT_FILENAME, "plugin/prusaslicerthumbnails/thumbnail/my%20part.png?20260929")

		self.assertTrue(stored)
		with Image.open(self.storedImage) as image:
			self.assertEqual((16, 16), image.size)

	def test_missingPluginThumbnail(self):
		self.assertFalse(self.cameraManager.takePluginThumbnail(SNAPSHOT_FILENAME, "plugin/prusaslicerthumbnails/thumbnail/gone.png"))


if __name__ == "__main__":
	unittest.main()
