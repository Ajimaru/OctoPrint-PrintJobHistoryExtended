# coding=utf-8
from __future__ import absolute_import

from octoprint_PrintJobHistoryExtended.common.SettingsKeys import SettingsKeys

# The camera tab used to offer three snapshot triggers, a thumbnail switch and a preference
# between camera and thumbnail. All of it collapses into SETTINGS_KEY_IMAGE_SOURCE.
#
# OctoPrint only stores values that differ from the default, so a key missing from a
# config means the user never touched it. It then counts with the default of the release
# that still had it - not as "off", which would turn every untouched install into "none".
LEGACY_CAMERA_DEFAULTS = {
	SettingsKeys.LEGACY_SETTINGS_KEY_TAKE_SNAPSHOT_AFTER_PRINT: True,
	SettingsKeys.LEGACY_SETTINGS_KEY_TAKE_PLUGIN_THUMBNAIL_AFTER_PRINT: True,
	SettingsKeys.SETTINGS_KEY_TAKE_SNAPSHOT_ON_M118_COMMAND: False,
	SettingsKeys.LEGACY_SETTINGS_KEY_TAKE_SNAPSHOT_ON_GCODE_COMMAND: False,
	SettingsKeys.LEGACY_SETTINGS_KEY_TAKE_SNAPSHOT_GCODE_COMMAND_PATTERN: "M117 Snap",
	SettingsKeys.LEGACY_SETTINGS_KEY_PREFERED_IMAGE_SOURCE: SettingsKeys.KEY_IMAGE_SOURCE_THUMBNAIL,
}

# Keys without any meaning once translated. The M118 switch is not among them: it lives on
# as an option of the camera source, so its stored value is kept as it is.
OBSOLETE_CAMERA_KEYS = tuple(key for key in LEGACY_CAMERA_DEFAULTS
							 if key != SettingsKeys.SETTINGS_KEY_TAKE_SNAPSHOT_ON_M118_COMMAND)


def translateCameraSettings(values):
	"""
	Returns the settings with the obsolete camera keys replaced by SETTINGS_KEY_IMAGE_SOURCE.

	Settings without any obsolete key come back as the very same object, so callers can
	tell "nothing to do" from a translation. An image source that is already present wins
	over the one derived from the old keys.

	The result mirrors what the old keys effectively did: with both a camera trigger and
	the thumbnail switched on, the preference decided.
	"""
	if values is None:
		return None
	if not any(key in values for key in OBSOLETE_CAMERA_KEYS):
		return values

	def valueOf(key):
		value = values.get(key)
		return LEGACY_CAMERA_DEFAULTS[key] if value is None else value

	takeSnapshotAfterPrint = _isTrue(valueOf(SettingsKeys.LEGACY_SETTINGS_KEY_TAKE_SNAPSHOT_AFTER_PRINT))
	takeSnapshotOnGCode = _isTrue(valueOf(SettingsKeys.LEGACY_SETTINGS_KEY_TAKE_SNAPSHOT_ON_GCODE_COMMAND))
	takeSnapshotOnM118 = _isTrue(valueOf(SettingsKeys.SETTINGS_KEY_TAKE_SNAPSHOT_ON_M118_COMMAND))
	takeThumbnail = _isTrue(valueOf(SettingsKeys.LEGACY_SETTINGS_KEY_TAKE_PLUGIN_THUMBNAIL_AFTER_PRINT))
	preferedImageSource = valueOf(SettingsKeys.LEGACY_SETTINGS_KEY_PREFERED_IMAGE_SOURCE)

	# The deprecated gcode trigger has no successor; its users still wanted camera images.
	wantsCamera = takeSnapshotAfterPrint or takeSnapshotOnM118 or takeSnapshotOnGCode
	if (wantsCamera and takeThumbnail):
		if (preferedImageSource == SettingsKeys.KEY_IMAGE_SOURCE_CAMERA):
			imageSource = SettingsKeys.KEY_IMAGE_SOURCE_CAMERA
		else:
			imageSource = SettingsKeys.KEY_IMAGE_SOURCE_THUMBNAIL
	elif (wantsCamera):
		imageSource = SettingsKeys.KEY_IMAGE_SOURCE_CAMERA
	elif (takeThumbnail):
		imageSource = SettingsKeys.KEY_IMAGE_SOURCE_THUMBNAIL
	else:
		imageSource = SettingsKeys.KEY_IMAGE_SOURCE_NONE

	translated = dict((key, value) for key, value in values.items() if key not in OBSOLETE_CAMERA_KEYS)
	if (translated.get(SettingsKeys.SETTINGS_KEY_IMAGE_SOURCE) is None):
		translated[SettingsKeys.SETTINGS_KEY_IMAGE_SOURCE] = imageSource
	return translated


# config.yaml holds real booleans, but a hand-edited file can hold "false" - which is truthy.
def _isTrue(value):
	if isinstance(value, str):
		return value.strip().lower() in ("true", "yes", "on", "1")
	return bool(value)
