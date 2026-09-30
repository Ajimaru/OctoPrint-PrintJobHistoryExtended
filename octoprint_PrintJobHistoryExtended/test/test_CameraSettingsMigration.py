# coding=utf-8
"""
Tests for collapsing the old camera switches into the one image source setting.

The plugin class cannot be instantiated without a running OctoPrint, so the migration is
exercised on an instance created without __init__ that provides only what it touches.
"""
import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from octoprint_PrintJobHistoryExtended import PrintJobHistoryExtendedPlugin
from octoprint_PrintJobHistoryExtended.common.CameraSettingsMigration import translateCameraSettings


class FakeSettings:
	"""Stored values plus the defaults of the current release, like OctoPrint's."""

	DEFAULTS = {
		"imageSourceAfterPrint": "thumbnail",
		"takeSnapshotOnM118Commnd": False,
	}

	def __init__(self, stored=None):
		self.stored = dict(stored or {})

	def get(self, path):
		if path[0] in self.stored:
			return self.stored[path[0]]
		return self.DEFAULTS.get(path[0])

	def set(self, path, value):
		self.stored[path[0]] = value

	def remove(self, path):
		self.stored.pop(path[0], None)


def createPlugin(stored=None):
	plugin = PrintJobHistoryExtendedPlugin.__new__(PrintJobHistoryExtendedPlugin)
	plugin._logger = logging.getLogger("test")
	plugin._settings = FakeSettings(stored)
	return plugin


class TranslateCameraSettingsTest(unittest.TestCase):

	def test_settingsWithoutOldCameraKeysComeBackUntouched(self):
		values = {"currencySymbol": "GBP", "takeSnapshotOnM118Commnd": True}
		self.assertIs(values, translateCameraSettings(values))
		self.assertIsNone(translateCameraSettings(None))

	def test_untouchedSwitchesCountWithTheirOldDefaults(self):
		# camera and thumbnail were both on by default, so the stored preference decides
		self.assertEqual("camera", translateCameraSettings({"preferedImageSource": "camera"})["imageSourceAfterPrint"])
		self.assertEqual("thumbnail", translateCameraSettings({"preferedImageSource": "thumbnail"})["imageSourceAfterPrint"])

	def test_onlyTheThumbnailSwitchedOn(self):
		translated = translateCameraSettings({"takeSnapshotAfterPrint": False, "preferedImageSource": "camera"})
		self.assertEqual("thumbnail", translated["imageSourceAfterPrint"])

	def test_onlyTheCameraSwitchedOn(self):
		translated = translateCameraSettings({"takePluginThumbnailAfterPrint": False})
		self.assertEqual("camera", translated["imageSourceAfterPrint"])

	def test_everythingSwitchedOff(self):
		translated = translateCameraSettings({"takeSnapshotAfterPrint": False, "takePluginThumbnailAfterPrint": False})
		self.assertEqual("none", translated["imageSourceAfterPrint"])

	def test_hookUsersKeepTheirM118Switch(self):
		translated = translateCameraSettings({
			"takeSnapshotAfterPrint": False,
			"takeSnapshotOnM118Commnd": True,
			"takePluginThumbnailAfterPrint": False,
		})
		self.assertEqual("camera", translated["imageSourceAfterPrint"])
		self.assertTrue(translated["takeSnapshotOnM118Commnd"])

	def test_deprecatedGcodeTriggerBecomesTheCamera(self):
		translated = translateCameraSettings({
			"takeSnapshotAfterPrint": False,
			"takeSnapshotOnGCodeCommnd": True,
			"takeSnapshotGCodeCommndPattern": "M117 Snap",
			"takePluginThumbnailAfterPrint": False,
		})
		self.assertEqual({"imageSourceAfterPrint": "camera"}, translated)

	def test_handEditedStringsAreNotTruthy(self):
		translated = translateCameraSettings({"takeSnapshotAfterPrint": "false", "takePluginThumbnailAfterPrint": "False"})
		self.assertEqual("none", translated["imageSourceAfterPrint"])

	def test_anImageSourceAlreadyPresentWins(self):
		translated = translateCameraSettings({"preferedImageSource": "camera", "imageSourceAfterPrint": "none"})
		self.assertEqual({"imageSourceAfterPrint": "none"}, translated)

	def test_otherSettingsAndTheInputSurvive(self):
		values = {"currencySymbol": "GBP", "preferedImageSource": "camera"}
		translated = translateCameraSettings(values)
		self.assertEqual({"currencySymbol": "GBP", "imageSourceAfterPrint": "camera"}, translated)
		# the caller's dict is not changed underneath it
		self.assertEqual({"currencySymbol": "GBP", "preferedImageSource": "camera"}, values)


class SettingsMigrationTest(unittest.TestCase):

	def test_storedCameraSwitchesAreReplacedByTheImageSource(self):
		# what all three instances on the test host have stored
		plugin = createPlugin({"preferedImageSource": "camera", "currencySymbol": "GBP"})

		plugin.on_settings_migrate(1, None)

		self.assertEqual({"imageSourceAfterPrint": "camera", "currencySymbol": "GBP"}, plugin._settings.stored)

	def test_installWithoutOldCameraSwitchesKeepsTheDefault(self):
		plugin = createPlugin({"currencySymbol": "GBP"})

		plugin.on_settings_migrate(1, None)

		self.assertEqual({"currencySymbol": "GBP"}, plugin._settings.stored)

	def test_storedM118SwitchIsKept(self):
		plugin = createPlugin({"takeSnapshotAfterPrint": False, "takeSnapshotOnM118Commnd": True, "preferedImageSource": "camera"})

		plugin.on_settings_migrate(1, None)

		self.assertEqual({"imageSourceAfterPrint": "camera", "takeSnapshotOnM118Commnd": True}, plugin._settings.stored)

	def test_m118WithThePreferredThumbnailStaysWithTheThumbnail(self):
		# The thumbnail used to replace the M118 snapshot whenever the file had one
		plugin = createPlugin({"takeSnapshotAfterPrint": False, "takeSnapshotOnM118Commnd": True})

		plugin.on_settings_migrate(1, None)

		self.assertEqual({"imageSourceAfterPrint": "thumbnail", "takeSnapshotOnM118Commnd": True}, plugin._settings.stored)

	def test_alreadyMigratedSettingsAreLeftAlone(self):
		plugin = createPlugin({"preferedImageSource": "camera"})

		plugin.on_settings_migrate(1, 1)

		self.assertEqual({"preferedImageSource": "camera"}, plugin._settings.stored)


if __name__ == "__main__":
	unittest.main()
