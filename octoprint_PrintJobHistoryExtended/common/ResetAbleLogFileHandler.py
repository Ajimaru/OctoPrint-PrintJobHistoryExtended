# coding=utf-8
from __future__ import absolute_import

import logging
import os


class ResetAbleLogFileHandler(logging.FileHandler):

	def __init__(self, filename, loggerNameToCapture):
		logging.FileHandler.__init__(self, filename)

		self.loggingStarted = False
		self.loggerNameToCapture = loggerNameToCapture

	# def assignLoggerNameToCapture(self, loggerNameToCapture):
	# 	self.loggerNameToCapture = loggerNameToCapture

	def emit(self, record):
		if (self.loggingStarted):
			name = record.name  # octoprint.plugins.SpoolManager
			if (name.startswith(self.loggerNameToCapture)):
				# super(logging.FileHandler, self).emit(record)
				logging.FileHandler.emit(self, record)

	def startLogging(self):
		self.loggingStarted = True

	def stopLogging(self):
		self.loggingStarted = False

	# Starts the technical log of a single print over. Called as the first thing a print does,
	# so it must not raise: whatever is or is not on disk, a missing log file is no reason to
	# abandon a print job. The file is absent more often than one would think - a fresh
	# install before the first line was written, a user who cleaned out the log folder, or a
	# second PrintStarted for the same print (the Bambu connector sends two), which would
	# otherwise try to delete the file this method just removed.
	# The stream is closed first because the file is deleted underneath it; FileHandler
	# reopens it on the next emit, as it is opened in append mode.
	def resetLog(self):
		self.close()
		try:
			os.remove(self.baseFilename)
		except OSError:
			# no log file to reset (never written, already removed, or not ours to delete)
			pass

	# The technical log of the print that just ended, stored with the job. Returns an empty
	# string when there is no log file, for the same reasons resetLog() tolerates its absence:
	# losing the technical log must not cost the captured print job.
	def readLogContent(self):
		self.close()
		# self.baseFilename = os.path.abspath(filename)
		try:
			with open(self.baseFilename) as f:
				contents = f.read()
				return contents
		except OSError:
			return ""
