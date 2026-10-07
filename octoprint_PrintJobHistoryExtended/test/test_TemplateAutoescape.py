# coding=utf-8
"""
Tests for declaring the plugin's templates as autoescaped.

OctoPrint 2.0 logs a warning on every start for a plugin that does not override
is_template_autoescaped(), and OctoPrint 2.1 enforces autoescaping for all plugins. The UI
templates must render exactly the same with autoescaping on, otherwise the flag would change
what the user sees.
"""
import os
import re
import sys
import unittest

import jinja2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from octoprint_PrintJobHistoryExtended import PrintJobHistoryExtendedPlugin


TEMPLATE_FOLDER = os.path.join(os.path.dirname(__file__), "..", "templates")

UI_TEMPLATES = [
	"PrintJobHistoryExtended_settings.jinja2",
	"PrintJobHistoryExtended_tab.jinja2",
	"PrintJobHistoryExtended_tab_dialogs.jinja2",
	"PrintJobHistoryExtended_navbar.jinja2",
	"PrintJobHistoryExtended_dialog_legacyMigration.jinja2",
]


def renderUiTemplate(templateName, autoescape):
	environment = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATE_FOLDER), autoescape=autoescape)
	# OctoPrint's helpers: _ is the translation (the msgid when there is none), edq escapes double quotes
	environment.globals["_"] = lambda text: text
	environment.filters["edq"] = lambda text: re.sub(r'(?<!\\)"', '\\"', text)
	return environment.get_template(templateName).render()


class TemplateAutoescapeTestCase(unittest.TestCase):

	def test_pluginDeclaresItsTemplatesAutoescaped(self):
		plugin = PrintJobHistoryExtendedPlugin.__new__(PrintJobHistoryExtendedPlugin)

		self.assertIs(plugin.is_template_autoescaped(), True)

	def test_uiTemplatesRenderTheSameWithAutoescape(self):
		# HTML comments are left out: the commented-out hints in the tab dialogs still hold {{ _() }}
		# expressions with quotes and ampersands, which are escaped now but never shown
		for templateName in UI_TEMPLATES:
			with self.subTest(template=templateName):
				withoutAutoescape = re.sub(r"<!--.*?-->", "", renderUiTemplate(templateName, False), flags=re.DOTALL)
				withAutoescape = re.sub(r"<!--.*?-->", "", renderUiTemplate(templateName, True), flags=re.DOTALL)
				self.assertEqual(withoutAutoescape, withAutoescape)

	def test_uiTemplatesHaveNoMarkupBypass(self):
		# a "|safe" or {% autoescape false %} would silently undo the declaration for that spot
		for templateName in UI_TEMPLATES:
			with self.subTest(template=templateName):
				with open(os.path.join(TEMPLATE_FOLDER, templateName), encoding="utf-8") as templateFile:
					source = templateFile.read()
				self.assertNotRegex(source, r"\|\s*safe\b")
				self.assertNotRegex(source, r"\{%-?\s*autoescape\s+false")


if __name__ == '__main__':
	unittest.main()
