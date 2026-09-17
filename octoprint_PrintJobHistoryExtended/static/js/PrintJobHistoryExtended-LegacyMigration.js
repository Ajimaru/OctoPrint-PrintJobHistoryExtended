/**
 * Adopting the data of a previous "PrintJobHistory" install.
 *
 * All state lives here rather than in the main view model: the dialog is self-contained
 * and only needs the plugin id to build its urls.
 */
function PrintJobHistoryExtendedLegacyMigration() {

    var self = this;

    var pluginId = null;

    self.status = ko.observable({
        available: false,
        done: ko.observable(false),
        hasSettings: false,
        dataFolder: null,
        databaseUndoAvailable: ko.observable(false),
        settingsUndoAvailable: ko.observable(false),
        restartRequired: ko.observable(false)
    });

    self.preview = ko.observable({
        readable: false,
        jobCount: 0,
        schemeVersion: null,
        firstJobDate: null,
        lastJobDate: null,
        jobs: [],
        moreJobs: 0
    });

    self.files = ko.observableArray([]);
    self.includeSettings = ko.observable(true);
    self.overwriteExisting = ko.observable(false);
    self.inProgress = ko.observable(false);
    self.restartInProgress = ko.observable(false);
    self.conflict = ko.observable(false);
    self.errorMessage = ko.observable(null);
    self.resultMessage = ko.observable(null);

    self.init = function(currentPluginId) {
        pluginId = currentPluginId;
    };

    var url = function(path) {
        return "./plugin/" + pluginId + "/" + path;
    };

    self.formatSize = function(bytes) {
        if (bytes === undefined || bytes === null) return "";
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
        return (bytes / 1024 / 1024).toFixed(1) + " MB";
    };

    /** Status is also read on startup, so the banner can appear without opening the dialog. */
    self.loadStatus = function(onDone) {
        $.ajax({url: url("legacyMigrationStatus"), type: "GET", dataType: "json"})
            .done(function(data) {
                self.status({
                    available: data.available,
                    done: ko.observable(data.done),
                    hasSettings: data.hasSettings,
                    dataFolder: data.dataFolder,
                    databaseUndoAvailable: ko.observable(data.databaseUndoAvailable),
                    settingsUndoAvailable: ko.observable(data.settingsUndoAvailable),
                    restartRequired: ko.observable(data.restartRequired)
                });
                self.files($.map(data.files || [], function(entry) {
                    return {
                        name: entry.name,
                        kind: entry.kind,
                        size: entry.size,
                        isDirectory: entry.isDirectory,
                        selected: ko.observable(entry.preselected)
                    };
                }));
                if (onDone) onDone();
            })
            .fail(function() {
                self.errorMessage("Could not read the migration status.");
                if (onDone) onDone();
            });
    };

    self.showDialog = function() {
        self.conflict(false);
        self.errorMessage(null);
        self.resultMessage(null);
        self.overwriteExisting(false);

        self.loadStatus(function() {
            if (self.status().available) {
                $.ajax({url: url("legacyDatabasePreview"), type: "GET", dataType: "json"})
                    .done(function(data) { self.preview(data); })
                    .fail(function() { self.preview($.extend({}, self.preview(), {readable: false})); });
            }
            $("#dialog_printJobHistoryExtended_legacyMigration").modal("show");
        });
    };

    self.migrate = function() {
        self.inProgress(true);
        self.errorMessage(null);
        self.resultMessage(null);

        var selectedNames = $.map(self.files(), function(entry) {
            return entry.selected() ? entry.name : null;
        });

        $.ajax({
            url: url("migrateFromLegacy"),
            type: "PUT",
            dataType: "json",
            contentType: "application/json; charset=UTF-8",
            data: JSON.stringify({
                overwriteExisting: self.overwriteExisting(),
                includeSettings: self.includeSettings(),
                fileNames: selectedNames
            })
        })
        .done(function(data) {
            self.conflict(false);
            self.resultMessage(
                "Migrated " + data.copiedFiles + " entr" + (data.copiedFiles === 1 ? "y" : "ies") +
                (data.settingsMigrated ? " and the settings" : "") + "."
            );
            self.loadStatus();
        })
        .fail(function(xhr) {
            var data = xhr.responseJSON || {};
            // 409 means the target database already holds jobs - a decision, not a failure
            self.conflict(xhr.status === 409);
            self.errorMessage(data.errorMessage || "The migration failed. See octoprint.log for details.");
        })
        .always(function() {
            self.inProgress(false);
        });
    };

    self.undo = function(undoKind) {
        self.inProgress(true);
        self.errorMessage(null);
        self.resultMessage(null);

        $.ajax({url: url("undoLegacyMigration/" + undoKind), type: "PUT", dataType: "json"})
            .done(function(data) {
                self.resultMessage(
                    "Undone: " + data.restoredFiles + " file(s) and " +
                    data.restoredSettings + " setting(s) restored."
                );
                self.loadStatus();
            })
            .fail(function(xhr) {
                var data = xhr.responseJSON || {};
                self.errorMessage(data.errorMessage || "The undo failed.");
            })
            .always(function() {
                self.inProgress(false);
            });
    };

    /**
     * Restarts the OctoPrint server through its own system command, so the plugin reopens
     * the migrated database. Only this makes the copied jobs visible - the plugin holds
     * its database open from startup, so a swapped file stays unseen until then.
     */
    self.restartServer = function() {
        if (!confirm("Restart the OctoPrint server now? Do not do this while a print is running.")) {
            return;
        }
        self.restartInProgress(true);
        self.errorMessage(null);

        OctoPrint.system.executeCommand("core", "restart")
            .done(function() {
                self.resultMessage("The server is restarting. This page will reconnect on its own.");
            })
            .fail(function() {
                self.restartInProgress(false);
                self.errorMessage(
                    "Could not restart the server. Restart it yourself, or from " +
                    "OctoPrint's own restart button, to see the migrated print jobs."
                );
            });
    };
}
