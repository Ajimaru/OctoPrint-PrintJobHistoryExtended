# coding=utf-8
"""
Tests that the frontend only requests snapshot routes the plugin actually serves.

"Take picture" used to point the video stream's img at /plugin/.../mysnapshot to freeze the
picture during the shutter animation, but the UrlProxyHandler serving that route had never
been enabled. Every click produced a 404 and no freeze, while the snapshot itself was taken
correctly by the call right after - so nothing looked broken except the log.
"""
import io
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

STATIC_JS = os.path.join(os.path.dirname(__file__), "..", "static", "js")
PLUGIN_INIT = os.path.join(os.path.dirname(__file__), "..", "__init__.py")


def readJs(fileName):
	with io.open(os.path.join(STATIC_JS, fileName), encoding="utf-8") as jsFile:
		return jsFile.read()


def servedBlueprintRoutes():
	"""The route rules the plugin's API blueprint declares."""
	apiModule = os.path.join(
		os.path.dirname(__file__), "..", "api", "PrintJobHistoryExtendedAPI.py"
	)
	with io.open(apiModule, encoding="utf-8") as apiFile:
		source = apiFile.read()
	return set(re.findall(r'BlueprintPlugin\.route\(\s*"([^"]+)"', source))


class TestSnapshotRoutes(unittest.TestCase):

	def test_no_javascript_requests_the_mysnapshot_route(self):
		for fileName in os.listdir(STATIC_JS):
			if not fileName.endswith(".js") or fileName.endswith(".min.js"):
				continue
			source = readJs(fileName)
			# a comment explaining the removal is fine, a request for it is not
			requests = [
				line
				for line in source.splitlines()
				if "mysnapshot" in line and not line.strip().startswith("//")
			]
			self.assertEqual([], requests, "%s still requests the dead route" % fileName)

	def test_the_snapshot_route_the_dialog_uses_is_actually_served(self):
		source = readJs("PrintJobHistoryExtended-APIClient.js")
		match = re.search(
			r'this\.getSnapshotUrl\s*=\s*function[^}]*?"/([A-Za-z]+)/"', source, re.S
		)
		self.assertIsNotNone(match, "could not read the snapshot url builder")
		served = servedBlueprintRoutes()
		self.assertTrue(
			any(rule.startswith("/" + match.group(1)) for rule in served),
			"getSnapshotUrl builds /%s, which no blueprint route serves" % match.group(1),
		)

	def test_the_dead_proxy_handler_is_not_reintroduced(self):
		with io.open(PLUGIN_INIT, encoding="utf-8") as pluginFile:
			source = pluginFile.read()
		active = [
			line
			for line in source.splitlines()
			if "UrlProxyHandler" in line and not line.strip().startswith("#")
		]
		self.assertEqual(
			[],
			active,
			"reviving the proxy also means porting it off the deprecated webcam.snapshot "
			"setting, and the frontend no longer asks for it",
		)


if __name__ == "__main__":
	unittest.main()
