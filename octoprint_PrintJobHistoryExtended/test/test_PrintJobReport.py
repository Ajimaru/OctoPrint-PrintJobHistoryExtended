# coding=utf-8
"""
Tests for the default print job report templates.

2026-10-06: the multi-job report returned HTTP 500 on every instance, because it always loads
all jobs and K9 job 146 had been stored without any filament row ("'dict object' has no
attribute 'total'"). The single report failed the same way for that job, and for every job
that did not print on tool0 (U1 jobs on T2/T3 have no tool0 row).
"""
import datetime
import os
import unittest

import jinja2


TEMPLATE_FOLDER = os.path.join(os.path.dirname(__file__), "..", "templates")


def renderReport(reportType, printJobModelAsJson):
	templateLocation = os.path.join(TEMPLATE_FOLDER, "PrintJobHistoryExtended_default" + reportType + "PrintJobReport.jinja2")
	with open(templateLocation) as templateFile:
		# like OctoPrint's Flask app: autoescape on, the default Undefined
		template = jinja2.Environment(autoescape=True).from_string(templateFile.read())
	return template.render(reportCreationTime=datetime.datetime(2026, 10, 6, 18, 30),
						   printJobModelAsJson=printJobModelAsJson,
						   url_for=lambda endpoint, **values: "/snapshot/" + str(values.get("snapshotFilename")))


def createToolFilament(toolId, spoolName, material):
	return {"toolId": toolId, "spoolName": spoolName, "material": material, "vendor": "Kingroon",
			"diameter": 1.75, "density": 1.24}


def createTotalFilament():
	return {"toolId": "total", "calculatedLengthFormatted": "0.43", "usedLengthFormatted": "0.43",
			"usedWeight": "1.27", "usedCost": "0.01"}


def createJob(fileName, filamentModels):
	return {"fileName": fileName, "userName": "robby", "printStatusResult": "success",
			"snapshotFilename": "20261006-174428", "filamentModels": filamentModels}


class SinglePrintJobReportTestCase(unittest.TestCase):

	def test_jobOnAnotherHeadShowsItsSpool(self):
		job = createJob("rocket_PETG_10m27s.gcode", {"tool2": createToolFilament("tool2", "White", "PETG"),
													 "total": createTotalFilament()})

		report = renderReport("Single", job)

		self.assertIn("Spoolname: White", report)
		self.assertIn("Material: PETG", report)
		self.assertIn("Used length: 0.43", report)

	def test_everyToolOfAMultiToolJobIsNamed(self):
		job = createJob("two_colours.gcode", {"tool3": createToolFilament("tool3", "White", "PLA"),
											  "tool2": createToolFilament("tool2", "White", "PETG"),
											  "total": createTotalFilament()})

		report = renderReport("Single", job)

		self.assertIn("Material (tool2): PETG", report)
		self.assertIn("Material (tool3): PLA", report)
		self.assertLess(report.index("(tool2)"), report.index("(tool3)"))

	def test_jobWithoutFilamentRows(self):
		report = renderReport("Single", createJob("Rocket.gcode", {}))

		self.assertIn("Filename: Rocket.gcode", report)
		self.assertNotIn("Spoolname", report)
		self.assertIn("Used length: </div>", report)


class MultiPrintJobReportTestCase(unittest.TestCase):

	def test_jobWithoutFilamentRowsDoesNotBreakTheReport(self):
		allJobs = [createJob("rocket_PLA_18m0s.gcode", {"tool0": createToolFilament("tool0", "Orange", "PLA"),
														"total": createTotalFilament()}),
				   createJob("Rocket.gcode", {}),
				   {"fileName": "without_filament_key.gcode"}]

		report = renderReport("Multi", allJobs)

		self.assertIn("Filename: rocket_PLA_18m0s.gcode", report)
		self.assertIn("Used length: 0.43", report)
		self.assertIn("Filename: Rocket.gcode", report)
		self.assertIn("Filename: without_filament_key.gcode", report)
		self.assertIn("Page 3 of 3", report)


if __name__ == '__main__':
	unittest.main()
