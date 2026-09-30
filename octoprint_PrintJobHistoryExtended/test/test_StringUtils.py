# coding=utf-8
"""
Tests for shortening the technical log so it still fits into the database column.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from octoprint_PrintJobHistoryExtended.common.StringUtils import shortenInTheMiddle


def logLines(count, text="Could not read the current temperature"):
	return "".join("2026-09-29 20:52:%02d - WARNING - %s %d\n" % (index % 60, text, index) for index in range(count))


class ShortenInTheMiddleTest(unittest.TestCase):

	def test_textThatFitsIsUnchanged(self):
		text = logLines(10)
		self.assertIs(text, shortenInTheMiddle(text, 60000))
		self.assertIsNone(shortenInTheMiddle(None, 60000))

	def test_longLogFitsTheLimitAndKeepsBothEnds(self):
		text = "PrintJob 'lid.gcode' started!\n" + logLines(1200) + "----- ... End PrintJob captured! -----\n"
		self.assertGreater(len(text.encode("utf-8")), 60000)

		shortened = shortenInTheMiddle(text, 60000, "[... {omitted} bytes left out ...]\n")

		self.assertLessEqual(len(shortened.encode("utf-8")), 60000)
		self.assertTrue(shortened.startswith("PrintJob 'lid.gcode' started!\n"))
		self.assertTrue(shortened.endswith("----- ... End PrintJob captured! -----\n"))
		self.assertEqual(1, shortened.count("bytes left out"))

	def test_onlyWholeLinesAreKept(self):
		shortened = shortenInTheMiddle(logLines(600), 20000)

		for line in shortened.splitlines():
			self.assertTrue(line.startswith("2026-09-29") or line.startswith("[... "), line)

	def test_noteCountsWhatWasLeftOut(self):
		text = logLines(600)

		shortened = shortenInTheMiddle(text, 20000, "[{omitted}]\n")

		note = [line for line in shortened.splitlines() if line.startswith("[")][0]
		omitted = int(note[1:-1])
		kept = len(shortened.encode("utf-8")) - len((note + "\n").encode("utf-8"))
		self.assertEqual(len(text.encode("utf-8")), kept + omitted)

	def test_limitCountsBytesNotCharacters(self):
		# degree signs take two bytes each in UTF-8, which is what the column limit counts
		text = logLines(600, "Bed at 60°C, nozzle at 215°C")

		shortened = shortenInTheMiddle(text, 20000)

		self.assertLessEqual(len(shortened.encode("utf-8")), 20000)
		self.assertIn("°C", shortened)

	def test_oneLongLineWithoutLineBreaks(self):
		text = "ä" * 50000

		shortened = shortenInTheMiddle(text, 20000)

		self.assertLessEqual(len(shortened.encode("utf-8")), 20000)
		self.assertTrue(shortened.startswith("ä"))
		self.assertTrue(shortened.endswith("ä"))


if __name__ == "__main__":
	unittest.main()
