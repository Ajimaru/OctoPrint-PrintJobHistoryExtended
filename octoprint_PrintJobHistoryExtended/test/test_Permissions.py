# coding=utf-8
"""
Tests for the plugin's access permissions.

The plugin used to declare only EDIT_JOB and DELETE_JOB, so an admin had no way to grant
read-only access to the history (OllisGit #220) while, the other way round, every read route
was reachable by any logged-in account regardless of group. A VIEW permission plus an
explicit check on every route closes both halves, and these tests pin the parts of that which
are easy to undo by accident.
"""
import ast
import io
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from octoprint_PrintJobHistoryExtended import PrintJobHistoryExtendedPlugin


API_MODULE = os.path.join(
	os.path.dirname(__file__), "..", "api", "PrintJobHistoryExtendedAPI.py"
)
TEMPLATE_FOLDER = os.path.join(os.path.dirname(__file__), "..", "templates")

VIEW = "PLUGIN_PRINTJOBHISTORYEXTENDED_VIEW"
EDIT = "PLUGIN_PRINTJOBHISTORYEXTENDED_EDIT_JOB"
DELETE = "PLUGIN_PRINTJOBHISTORYEXTENDED_DELETE_JOB"
SETTINGS = "SETTINGS"

# Every blueprint route of the plugin and the permission its view has to check. A route
# missing from this table, or checking something else, fails test_every_route_is_guarded -
# which is the point: a newly added route cannot stay unguarded unnoticed.
EXPECTED_ROUTE_PERMISSIONS = {
	"/confirmMessageDialog": VIEW,
	"/deactivatePluginCheck": SETTINGS,
	"/loadStatisticByQuery": VIEW,
	"/compareSlicerSettings/": VIEW,
	"/loadPrintJobHistoryByQuery": VIEW,
	"/printJobReprintable/<int:databaseId>": EDIT,
	"/selectPrintJobForPrint/<int:databaseId>": EDIT,
	"/removePrintJob/<int:databaseId>": DELETE,
	"/storePrintJob/<databaseId>": EDIT,
	"/forceCloseEditDialog": EDIT,
	"/printJobSnapshot/<string:snapshotFilename>": VIEW,
	"/takeSnapshot/<string:snapshotFilename>": EDIT,
	"/upload/snapshot/<string:snapshotFilename>": EDIT,
	"/deleteSnapshotImage/<string:snapshotFilename>": DELETE,
	"/downloadDatabase": SETTINGS,
	"/deleteDatabase": SETTINGS,
	"/testDatabaseConnection": SETTINGS,
	"/loadDatabaseMetaData": SETTINGS,
	"/copyDatabase": SETTINGS,
	"/upgradeDatabaseScheme": SETTINGS,
	"/createDatabaseBackup": SETTINGS,
	"/downloadDatabaseBackup/<string:backupFileName>": SETTINGS,
	"/exportDatabaseDump": SETTINGS,
	"/loadKnownInstances": VIEW,
	"/exportPrintJobHistory/<string:exportType>": VIEW,
	"/sampleCSV": SETTINGS,
	"/importCSV": SETTINGS,
	"/singlePrintJobReport/<databaseId>": VIEW,
	"/multiPrintJobReport": VIEW,
	"/uploadPrintJobReport/<reportType>": SETTINGS,
	"/downloadPrintJobReportTemplate/<reportType>": SETTINGS,
	"/resetPrintJobReportTemplate/<reportType>": SETTINGS,
	"/legacyMigrationStatus": SETTINGS,
	"/legacyDatabasePreview": SETTINGS,
	"/legacySettingsComparison": SETTINGS,
	"/migrateFromLegacy": SETTINGS,
	"/applyLegacySettings": SETTINGS,
	"/undoLegacyMigration/<undoKind>": SETTINGS,
}


# Read routes call a helper instead of naming a permission, because a reader may hold
# VIEW or a write permission - OctoPrint has no "implies" between permissions. Resolve the
# helper to that set so the table below still reads one route to one decision.
VIEW_HELPER = "_canViewHistory"
VIEW_HELPER_PERMISSIONS = frozenset({VIEW, EDIT, DELETE})


def collectRoutePermissions():
	"""Route rule -> the permissions its view function checks.

	Read off the syntax tree rather than by importing: the API class cannot be instantiated
	without a running OctoPrint. The flip side is that this proves a check is *written* in
	the view, not that it runs before the side effect - read the diff for that.
	"""
	with io.open(API_MODULE, encoding="utf-8") as apiFile:
		source = apiFile.read()
	classNode = next(
		node
		for node in ast.parse(source).body
		if isinstance(node, ast.ClassDef) and node.name == "PrintJobHistoryExtendedAPI"
	)

	routePermissions = {}
	for functionNode in classNode.body:
		if not isinstance(functionNode, (ast.FunctionDef, ast.AsyncFunctionDef)):
			continue

		routeRule = None
		for decorator in functionNode.decorator_list:
			if (
				isinstance(decorator, ast.Call)
				and isinstance(decorator.func, ast.Attribute)
				and decorator.func.attr == "route"
				and decorator.args
				and isinstance(decorator.args[0], ast.Constant)
			):
				routeRule = decorator.args[0].value
		if routeRule is None:
			continue

		permissions = set()
		for node in ast.walk(functionNode):
			if (
				isinstance(node, ast.Attribute)
				and isinstance(node.value, ast.Name)
				and node.value.id == "Permissions"
			):
				permissions.add(node.attr)
			# self._canViewHistory() stands for the read permissions it ors together
			if isinstance(node, ast.Attribute) and node.attr == VIEW_HELPER:
				permissions |= VIEW_HELPER_PERMISSIONS
		routePermissions[routeRule] = permissions

	return routePermissions


def createPlugin():
	# The plugin class needs a running OctoPrint to initialize, and the permission hook
	# touches nothing of the instance.
	return PrintJobHistoryExtendedPlugin.__new__(PrintJobHistoryExtendedPlugin)


def readTemplate(fileName):
	with io.open(os.path.join(TEMPLATE_FOLDER, fileName), encoding="utf-8") as templateFile:
		return templateFile.read()


class TestPermissionDeclarations(unittest.TestCase):

	def setUp(self):
		self.permissions = createPlugin().additional_permissions_hook()
		self.byKey = {entry["key"]: entry for entry in self.permissions}

	def test_declares_view_edit_and_delete(self):
		self.assertEqual({"VIEW", "EDIT_JOB", "DELETE_JOB"}, set(self.byKey.keys()))

	def test_view_is_granted_to_admins_and_users_by_default(self):
		from octoprint.access import ADMIN_GROUP, USER_GROUP

		self.assertEqual(
			[ADMIN_GROUP, USER_GROUP], self.byKey["VIEW"]["default_groups"]
		)

	def test_view_declares_exactly_one_role(self):
		"""Do not add a second role to VIEW.

		OctoPrint satisfies a permission only for an identity carrying *every* need it
		declares, so two roles are ANDed, not ORed - verified against the running instance,
		where a VIEW of [view_job, print_operator] was allowed for neither a view_job holder
		nor a print_operator holder. The same reason rules out expressing "EDIT_JOB implies
		VIEW" through roles or through a "permissions" key, which only widens EDIT_JOB's own
		need set. Read access for a write-permission holder is handled in the API instead,
		by _canViewHistory.
		"""
		self.assertEqual(["view_job"], self.byKey["VIEW"]["roles"])

	def test_write_permissions_do_not_try_to_imply_view(self):
		for writeKey in ("EDIT_JOB", "DELETE_JOB"):
			self.assertNotIn(
				"permissions",
				self.byKey[writeKey],
				"%s must not declare an implied permission: it widens its own need set "
				"instead of granting VIEW" % writeKey,
			)

	def test_view_is_not_dangerous(self):
		"""Do not mark VIEW dangerous.

		OctoPrint filters dangerous permissions out when they are added to the guests group,
		so the flag would make read-only sharing ungrantable - the very thing the permission
		was added for (OllisGit #220).
		"""
		self.assertFalse(self.byKey["VIEW"].get("dangerous", False))

	def test_every_permission_has_a_name_and_description(self):
		for key, entry in self.byKey.items():
			self.assertTrue(entry.get("name"), key)
			self.assertTrue(entry.get("description"), key)


class TestRouteGuards(unittest.TestCase):

	def setUp(self):
		self.routePermissions = collectRoutePermissions()

	def test_no_route_is_unguarded(self):
		unguarded = sorted(
			rule for rule, permissions in self.routePermissions.items() if not permissions
		)
		self.assertEqual(
			[],
			unguarded,
			"these routes check no permission at all: %s" % ", ".join(unguarded),
		)

	def test_every_route_is_guarded_as_expected(self):
		self.assertEqual(
			set(EXPECTED_ROUTE_PERMISSIONS.keys()),
			set(self.routePermissions.keys()),
			"the route list changed - add the new route to EXPECTED_ROUTE_PERMISSIONS "
			"together with the permission it has to check",
		)
		for rule, expectedPermission in sorted(EXPECTED_ROUTE_PERMISSIONS.items()):
			self.assertIn(
				expectedPermission,
				self.routePermissions[rule],
				"%s must check %s" % (rule, expectedPermission),
			)

	def test_read_routes_accept_view_or_a_write_permission(self):
		"""A holder of EDIT_JOB or DELETE_JOB must keep read access.

		There is no implication between OctoPrint permissions, so an admin who grants only a
		write permission - or takes VIEW off a group that has EDIT_JOB - would otherwise lock
		that user out of the very table they are allowed to edit.
		"""
		helperBody = self._readHelperBody()
		for permission in (VIEW, EDIT, DELETE):
			self.assertIn(permission, helperBody, "%s must satisfy a read route" % permission)
		self.assertIn("or", helperBody, "the read permissions have to be ored, not anded")

	def _readHelperBody(self):
		with io.open(API_MODULE, encoding="utf-8") as apiFile:
			tree = ast.parse(apiFile.read())
		classNode = next(
			node
			for node in tree.body
			if isinstance(node, ast.ClassDef) and node.name == "PrintJobHistoryExtendedAPI"
		)
		helper = next(
			node
			for node in classNode.body
			if isinstance(node, ast.FunctionDef) and node.name == VIEW_HELPER
		)
		return ast.unparse(helper)

	def test_the_read_routes_that_were_open_are_closed(self):
		# The ones that handed out the whole history to any logged-in account.
		for rule in (
			"/loadPrintJobHistoryByQuery",
			"/exportPrintJobHistory/<string:exportType>",
			"/loadStatisticByQuery",
			"/singlePrintJobReport/<databaseId>",
			"/multiPrintJobReport",
		):
			self.assertIn(VIEW, self.routePermissions[rule], rule)


class TestFrontendGating(unittest.TestCase):

	def test_tab_config_carries_no_data_bind(self):
		"""The tab config must not gate the tab.

		OctoPrint merges a tab config's data_bind into the same data-bind attribute as
		allowBindings on the tab's outer div, and that pair governs whether knockout descends
		into the subtree. A "visible" binding there left the dialogs - which sit in that
		subtree - entirely unbound: every permission binding and every click handler inside
		them stopped working, with no error, so a read-only user saw a Save button and could
		not close the dialog. The gating lives in the tab template instead.
		"""
		tabConfigs = [
			config
			for config in createPlugin().get_template_configs()
			if config.get("type") == "tab"
		]
		self.assertEqual(1, len(tabConfigs))
		self.assertNotIn("data_bind", tabConfigs[0])

	def test_tab_div_gates_itself_on_a_read_permission(self):
		template = readTemplate("PrintJobHistoryExtended_tab.jinja2")
		match = re.search(r'<div id="tab_printJobHistoryExtended"([^>]*)>', template)
		self.assertIsNotNone(match)
		self.assertIn(VIEW, match.group(1))

	def test_tab_template_gates_its_read_only_controls(self):
		# Statistic, CSV export, report and compare all read the history.
		self.assertGreaterEqual(
			readTemplate("PrintJobHistoryExtended_tab.jinja2").count(VIEW), 4
		)

	def test_templates_never_reach_into_webcam_settings_directly(self):
		"""Go through the dialog's accessors, not through webCamSettings itself.

		OctoPrint withholds the webcam settings from a user without SETTINGS, so the object
		is null for them and a binding like webCamSettings.streamUrl throws while knockout
		is walking the dialog. Knockout then silently abandons the rest of that subtree: the
		modal footer stopped being bound altogether, so a read-only user saw a Save button
		that should have been hidden and a Close button that did nothing.
		"""
		template = readTemplate("PrintJobHistoryExtended_tab_dialogs.jinja2")
		self.assertNotIn(
			"webCamSettings.",
			template,
			"use printJobEditDialog.webCamStreamUrl/webCamRotate90/webCamFlipH/webCamFlipV, "
			"which tolerate the settings being withheld",
		)

	def test_select_for_printing_is_gated_on_edit(self):
		"""The button 403ed for a user without EDIT_JOB because nothing hid it."""
		template = readTemplate("PrintJobHistoryExtended_tab_dialogs.jinja2")
		match = re.search(
			r'<span data-bind="visible:[^"]*?(\w+)\)">\s*(?:<!--.*?-->\s*)?'
			r"<button[^>]*?selectForPrinting",
			template,
			re.DOTALL,
		)
		self.assertIsNotNone(
			match, "the 'Select for printing' button is not inside a permission binding"
		)
		self.assertEqual(EDIT, match.group(1))


if __name__ == "__main__":
	unittest.main()
