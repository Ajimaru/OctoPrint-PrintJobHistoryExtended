# coding=utf-8
"""
Tests for the per-print technical log handler.

The handler is driven once per print job, in this order (see __init__.py):
resetLog() and startLogging() when the print starts, stopLogging() and
readLogContent() when it ends. resetLog() is the very first statement of
_printJobStarted() and that call is not guarded, so anything it raises aborts
the print-start handling and the job is never captured - which is why both
file-touching methods have to tolerate a missing log file.
"""
import logging
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from octoprint_PrintJobHistoryExtended.common.ResetAbleLogFileHandler import ResetAbleLogFileHandler

LOGGER_NAME_TO_CAPTURE = "octoprint.plugins.PrintJobHistoryExtended"


class ResetAbleLogFileHandlerTest(unittest.TestCase):

	def setUp(self):
		self._logFolder = tempfile.mkdtemp()
		self._logFilename = os.path.join(self._logFolder, "plugin_PrintJobHistoryExtended_technical.log")
		self._handler = ResetAbleLogFileHandler(self._logFilename, LOGGER_NAME_TO_CAPTURE)
		self._handler.setFormatter(logging.Formatter("%(message)s"))
		# a logger of its own per test, so the handlers of other tests do not join in
		self._logger = logging.getLogger(LOGGER_NAME_TO_CAPTURE + ".test" + str(id(self)))
		self._logger.setLevel(logging.DEBUG)
		self._logger.addHandler(self._handler)

	def tearDown(self):
		self._logger.removeHandler(self._handler)
		self._handler.close()
		shutil.rmtree(self._logFolder, ignore_errors=True)

	def _runPrintJob(self, message):
		self._handler.resetLog()
		self._handler.startLogging()
		self._logger.info(message)
		self._handler.stopLogging()
		return self._handler.readLogContent()

	def test_everyPrintJobOfASessionIsLogged(self):
		# the reported symptom was that only the first job of a session had a technical log
		for jobNumber in range(1, 4):
			message = "PrintJob %d started!" % jobNumber
			technicalLog = self._runPrintJob(message)
			self.assertIn(message, technicalLog)
			# and the log holds this job only, not the ones before it
			self.assertNotIn("PrintJob %d started!" % (jobNumber - 1), technicalLog)

	def test_onlyTheCapturedLoggerIsWrittenAndOnlyWhileLoggingIsStarted(self):
		foreignLogger = logging.getLogger("octoprint.plugins.SomeOtherPlugin" + str(id(self)))
		foreignLogger.setLevel(logging.DEBUG)
		foreignLogger.addHandler(self._handler)
		try:
			self._handler.resetLog()
			self._logger.info("before startLogging")
			self._handler.startLogging()
			self._logger.info("captured line")
			foreignLogger.info("line of another plugin")
			self._handler.stopLogging()
			self._logger.info("after stopLogging")

			technicalLog = self._handler.readLogContent()
		finally:
			foreignLogger.removeHandler(self._handler)

		self.assertEqual("captured line\n", technicalLog)

	def test_resetLogSurvivesAMissingLogFile(self):
		# fresh install before the first line was written, or a cleaned-out log folder
		self._handler.close()
		os.remove(self._logFilename)

		self._handler.resetLog()  # must not raise, a print job depends on it

		self._handler.startLogging()
		self._logger.info("first line of a brand new log")
		self._handler.stopLogging()
		self.assertIn("first line of a brand new log", self._handler.readLogContent())

	def test_resetLogSurvivesASecondPrintStartedForTheSamePrint(self):
		# the Bambu connector sends PrintStarted twice, so resetLog() runs twice in a row
		self._handler.resetLog()

		self._handler.resetLog()  # must not raise on the file the first call deleted

		self._handler.startLogging()
		self._logger.info("logging works after the second reset")
		self._handler.stopLogging()
		self.assertIn("logging works after the second reset", self._handler.readLogContent())

	def test_readLogContentReturnsEmptyWhenThereIsNoLogFile(self):
		# losing the technical log must not cost the captured print job
		self._handler.close()
		os.remove(self._logFilename)

		self.assertEqual("", self._handler.readLogContent())

	def test_loggingContinuesAfterTheLogWasRead(self):
		# readLogContent() closes the stream; the next print must still be written
		self._runPrintJob("job of the previous print")

		self._handler.startLogging()
		self._logger.info("line after the log was read")
		self._handler.stopLogging()

		self.assertIn("line after the log was read", self._handler.readLogContent())


if __name__ == "__main__":
	unittest.main()
