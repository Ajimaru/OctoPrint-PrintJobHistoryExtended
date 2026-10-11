# coding=utf-8
"""
Tests that a failed "select for printing" does not hand the exception text to the client.

Code-scanning alert #4 (py/stack-trace-exposure) flagged the selection route: it was the
only one of the eleven that is reachable without SETTINGS rights - EDIT_JOB is enough - so
an operator who is not an admin could read whatever the exception carried. In practice that
is a filesystem path or a storage-backend internal, not a formatted traceback, but neither
belongs in a popup. The detail stays in the log, where self._logger.exception already put it.

The handler cannot be called without a running OctoPrint, so the two failure paths are
checked on the source: the client message must not interpolate the exception, and the
logging call that keeps the detail must still be there.
"""
import io
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

API_MODULE = os.path.join(os.path.dirname(__file__), "..", "api", "PrintJobHistoryExtendedAPI.py")


def readApiSource():
	with io.open(API_MODULE, encoding="utf-8") as apiFile:
		return apiFile.read()


def selectionHandlerSource(source):
	"""put_select_printjob plus the helpers it fails through, up to the next route.

	The slice deliberately runs past the handler body to the DELETE JOB route, so that
	_selectionFailed and _buildNotSelectableMessage - which sit between the two and carry
	the actual message building - are covered as well.
	"""
	start = source.index("def put_select_printjob")
	rest = source[start:]
	end = rest.index("#   DELETE JOB")
	return rest[:end]


class TestSelectionErrorDisclosure(unittest.TestCase):

	def setUp(self):
		self.source = readApiSource()
		self.handler = selectionHandlerSource(self.source)

	def test_no_selection_failure_sends_the_exception_to_the_client(self):
		# Every _selectionFailed(...) in this handler must build its message without the
		# exception. str(e) belongs in the _logger.exception call on the line above it.
		# [^()]* rather than .*? so a match cannot run from one call across the code in
		# between and into the next one's reason - that spans the logging lines and would
		# report a leak that is not there.
		calls = re.findall(r"_selectionFailed\(([^()]*(?:\([^()]*\)[^()]*)*?),\s*\"?(\w+)\"?,\s*\d+\)",
						   self.handler, re.DOTALL)
		self.assertGreaterEqual(len(calls), 3, "expected the selection failure paths to be found")
		for message, reason in calls:
			with self.subTest(reason=reason):
				self.assertNotIn("str(e)", message,
								 "reason '" + reason + "' leaks the exception text to the client")

	def test_the_detail_is_still_logged(self):
		# The fix must not have traded the disclosure for a silent failure: both except
		# blocks still have to record the exception, otherwise nothing is diagnosable.
		self.assertEqual(2, self.handler.count("self._logger.exception("),
						 "both failure paths must still log the exception with its detail")
		self.assertEqual(2, self.handler.count("str(e)"),
						 "str(e) should appear only in the two logging calls")

	def test_the_reason_codes_the_frontend_branches_on_are_unchanged(self):
		# The dialog stays open on a failure and reopens the reprintable state from these
		# codes; renaming one would silently change the UI behaviour.
		for reason in ["missing", "unresolvable", "selectFailed"]:
			with self.subTest(reason=reason):
				self.assertIn('"' + reason + '"', self.handler)

	def test_the_client_is_pointed_at_the_log(self):
		# The message loses the detail, so it has to say where the detail went.
		self.assertEqual(2, self.handler.count("See the log for details."))


if __name__ == "__main__":
	unittest.main()
