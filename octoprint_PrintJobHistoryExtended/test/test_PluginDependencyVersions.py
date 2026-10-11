# coding=utf-8
"""
Tests for the third-party plugin version floors in SettingsKeys.

The SpoolManagerExtended floor is the interesting one: the job-usage API this plugin
relies on only exists from SpoolManagerExtended 2.0.0 onwards, and that line has been
shipping as alpha and dev builds throughout. The floor therefore has to let every
2.0.0 prerelease through while still keeping the 1.x series out - a bare "2.0.0" would
reject them all, because a prerelease sorts BELOW its own final release.

The plugin class cannot be instantiated without a running OctoPrint, so the version
comparison is exercised on an instance created without __init__.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from octoprint_PrintJobHistoryExtended import PrintJobHistoryExtendedPlugin
from octoprint_PrintJobHistoryExtended.common.SettingsKeys import SettingsKeys


class TestPluginDependencyVersions(unittest.TestCase):

	def setUp(self):
		self.plugin = PrintJobHistoryExtendedPlugin.__new__(PrintJobHistoryExtendedPlugin)

	def _accepts(self, installedVersion, floor):
		comparable = self.plugin._get_comparable_version_semantic(installedVersion)
		comparableFloor = self.plugin._get_comparable_version_semantic(floor)
		return comparable >= comparableFloor

	def test_spoolmanager_floor_accepts_every_2_0_0_build(self):
		floor = SettingsKeys.PLUGIN_SPOOL_MANAGER["minVersion"]
		# Every shape SpoolManagerExtended has shipped or could ship on the 2.0.0 line,
		# including the dev builds PJHE was developed against (dev358/365/369).
		for version in ["2.0.0-a0", "2.0.0a0", "2.0.0a1", "2.0.0a1.dev358",
						"2.0.0a1.dev365", "2.0.0a1.dev369", "2.0.0a2",
						"2.0.0a2.dev1", "2.0.0alpha1", "2.0.0b1", "2.0.0dev1",
						"2.0.0rc1", "2.0.0", "2.0.1", "2.1.0"]:
			with self.subTest(version=version):
				self.assertTrue(self._accepts(version, floor),
								version + " must satisfy the floor " + floor)

	def test_spoolmanager_floor_rejects_the_1_x_series(self):
		floor = SettingsKeys.PLUGIN_SPOOL_MANAGER["minVersion"]
		# 1.7.8 is what the sibling's main branch carries, 1.17.5 the highest 1.x tag.
		for version in ["1.7.8", "1.17.3", "1.17.5", "1.99.99"]:
			with self.subTest(version=version):
				self.assertFalse(self._accepts(version, floor),
								 version + " must not satisfy the floor " + floor)

	def test_spoolmanager_is_advertised_as_a_plain_release(self):
		# The floor exists for the comparator only. What the user is told to install
		# must not be a prerelease identifier nobody can download.
		displayVersion = SettingsKeys.PLUGIN_SPOOL_MANAGER["displayVersion"]
		self.assertEqual("2.0.0", displayVersion)
		self.assertNotIn("-", displayVersion)

	def test_every_declared_floor_is_comparable(self):
		# A typo in a floor would otherwise only surface as a logged exception at
		# runtime, where _getPluginInformation swallows it and reports "wrong version".
		pluginInfos = [SettingsKeys.PLUGIN_SPOOL_MANAGER,
					   SettingsKeys.PLUGIN_TASMOTA,
					   SettingsKeys.PLUGIN_DISPLAY_LAYER_PROGRESS,
					   SettingsKeys.PLUGIN_ULTIMAKER_FORMAT_PACKAGE,
					   SettingsKeys.PLUGIN_PRUSA_SLICER_THUMNAIL,
					   SettingsKeys.PLUGIN_PRINT_HISTORY,
					   SettingsKeys.PLUGIN_COST_ESTIMATION]
		for pluginInfo in pluginInfos:
			floor = pluginInfo["minVersion"]
			if (floor == None):
				continue
			with self.subTest(plugin=pluginInfo["key"]):
				self.plugin._get_comparable_version_semantic(floor)


if __name__ == "__main__":
	unittest.main()
