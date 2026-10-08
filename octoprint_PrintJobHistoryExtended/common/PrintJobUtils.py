# coding=utf-8
from __future__ import absolute_import

import logging

from octoprint.filemanager import FileDestinations
from octoprint_PrintJobHistoryExtended.common import StringUtils

# The path a printer reports when it has a job but will not say which file it is. The Bambu
# connector sends it for a job that was already running when OctoPrint connected.
PLACEHOLDER_FILE_PATH = "???"


# True when the stored path says nothing about which file was printed.
def isPlaceholderFilePath(filePath):
	return StringUtils.isEmpty(filePath) or filePath == PLACEHOLDER_FILE_PATH


# The storage a job belongs to. A job without an origin is a csv import, and those are
# assumed local - so whoever asks has to go through here instead of comparing the raw value,
# or a missing origin would end up pointing at the printer.
def fileOriginOrLocal(fileOrigin):
	return FileDestinations.LOCAL if fileOrigin == None else fileOrigin


# True when the file is kept by the printer (sd-card, Bambu, Klipper) rather than on disk.
# This is the "sd" flag OctoPrint's job handling expects; since OctoPrint 2.0 SDCARD and
# PRINTER are one and the same destination.
def isPrinterHosted(fileOrigin):
	return fileOriginOrLocal(fileOrigin) != FileDestinations.LOCAL


# The places a local file of this print job could be, best guess first. Today that is only
# where the job said it was; this is the hook for looking somewhere else as well, e.g. the
# archive folder DeleteAfterPrint moves finished files into.
def _localPathCandidates(filePath):
	return [filePath]


def _storagePathOf(fileManager, fileOrigin, filePath, logger):
	"""
	The path as the storage knows it: relative to the storage root, no leading slash.

	This is the only form that may be handed to the file manager. A path that is already
	relative passes through unchanged, and one that is absolute - as this plugin stored it
	before, and as `path_on_disk` returns it - is turned back into its relative form.
	"""
	try:
		return fileManager.path_in_storage(fileOrigin, filePath)
	except Exception as e:
		# Storage could not make sense of the path. Carry on with what the job recorded:
		# it is what the old code used, and the readability check decides in the end.
		logger.warning("Could not resolve '" + str(filePath) + "' inside the '" + str(fileOrigin) + "' storage: " + str(e))
		return filePath


def _resolveLocal(fileManager, filePath, logger):
	resultOfLastCandidate = None
	for candidate in _localPathCandidates(filePath):
		storagePath = _storagePathOf(fileManager, FileDestinations.LOCAL, candidate, logger)
		try:
			fullFileLocation = fileManager.path_on_disk(FileDestinations.LOCAL, storagePath)
		except Exception as e:
			# Not a missing file - the storage could not even say where it would be.
			logger.warning("Could not locate '" + str(storagePath) + "' on disk: " + str(e))
			resultOfLastCandidate = _buildResult(False, storagePath,
												 FileDestinations.LOCAL + ":/" + str(storagePath), "unresolvable")
			continue

		if (_isFileReadable(fullFileLocation, logger) == True):
			return _buildResult(True, storagePath, fullFileLocation, None)
		resultOfLastCandidate = _buildResult(False, storagePath, fullFileLocation, "notFound")

	return resultOfLastCandidate


# A job the printer stores itself (sd-card, and connectors like Bambu or Moonraker). The file
# is not on this machine, so there is nothing to check - `path_on_disk` would only raise. The
# job is offered for selection and a printer that will not take it says so when asked.
def _resolvePrinter(fileManager, fileOrigin, filePath, logger):
	storagePath = _storagePathOf(fileManager, fileOrigin, filePath, logger)
	return _buildResult(True, storagePath, str(fileOrigin) + ":/" + str(storagePath), None)


def _buildResult(isRePrintable, storagePath, fullFileLocation, notReprintableReason):
	return {
		"isRePrintable": isRePrintable,
		# relative to the storage, the only form the file manager accepts
		"storagePath": storagePath,
		# for people to read: an error message, a tooltip. Never pass this to the file manager.
		"fullFileLocation": fullFileLocation,
		# why it is not reprintable: None, "placeholder", "notFound" or "unresolvable"
		"notReprintableReason": notReprintableReason
	}


def isPrintJobReprintable(fileManager, fileOrigin, filePathName, fileName, logger=None):
	if (logger == None):
		logger = logging.getLogger(__name__)

	filePath = filePathName if StringUtils.isNotEmpty(filePathName) else fileName

	if (isPlaceholderFilePath(filePath) == True):
		# Nothing was recorded about the file, so there is nothing to select.
		return _buildResult(False, None, PLACEHOLDER_FILE_PATH, "placeholder")

	# could be during csv import, assumption it is local
	fileOrigin = fileOriginOrLocal(fileOrigin)

	if (isPrinterHosted(fileOrigin) == False):
		return _resolveLocal(fileManager, filePath, logger)
	return _resolvePrinter(fileManager, fileOrigin, filePath, logger)


def _isFileReadable(fullFileLocation, logger):
	result = False
	try:
		with open(fullFileLocation):
			result = True
	except OSError as err:
		logger.info("Print job file is not readable '" + str(fullFileLocation) + "': " + str(err))
	return result
