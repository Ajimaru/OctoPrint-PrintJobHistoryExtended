/*
 * View model for PrintJobHistory
 *
 * Author: vojtakaniok
 * License: AGPLv3
 */
$(function() {

    ////////////////////////////////////////////////////////////
//    function formatFilamentLength(length){
//        var pattern = "%.02fm"
//        var result = _.sprintf(pattern, length)
//        return result
//    }

    var global = this;
    global.componentFactory = new PrintJobComponentFactory();

    dayjs.extend(window.dayjs_plugin_customParseFormat);

    // Helper
    var toFloatOrZero = function(floatNumber){
        var result = 0.0
        try{
            result = parseFloat(floatNumber);
            if (Number.isNaN(result)){
                return 0.0;
            }
        } catch (e) {
            result = 0.0;
        }
        return result;
    }

    ////////////////////////////////////////////////////////////
    var PrintJobItem = function(data) {

        self = this;
        // Init Item
		this.databaseId = ko.observable();
		this.userName = ko.observable();
		this.fileName = ko.observable();
		this.filePathName = ko.observable();
		this.fileSize = ko.observable();
		this.fileSizeFormatted = ko.observable();

        var printStartDateTimeViewModel = global.componentFactory.createDateTimePicker("printStartDateTimeExtended-picker");
		this.printStartDateTimeFormatted = printStartDateTimeViewModel.currentDateTime;
		this.printStartDateTime = ko.observable();
//		this.printStartDateTimeFormatted = ko.observable();

        var printEndDateTimeViewModel = global.componentFactory.createDateTimePicker("printEndDateTimeExtended-picker");
		this.printEndDateTimeFormatted = printEndDateTimeViewModel.currentDateTime;
		this.printEndDateTime = ko.observable();
//		this.printEndDateTimeFormatted = ko.observable();

		this.printStatusResult = ko.observable();
		this.duration = ko.observable();
		this.durationFormatted = ko.observable();
		this.noteText = ko.observable();
		this.noteDeltaFormat = ko.observable();
		this.noteHtml = ko.observable();
		this.printedLayers = ko.observable();
		this.printedHeight = ko.observable();
		this.temperatureBed = ko.observable();
		this.temperatureNozzle = ko.observable();

//        this.usedSpoolManagerPlugin = ko.observable();
//        this.spoolItem = ko.observable();
		this.diameter = ko.observable();
		this.density = ko.observable();
		this.material = ko.observable();
        this.vendor = ko.observable();
		this.spoolName = ko.observable();
		this.weight = ko.observable();
		this.usedLengthFormatted = ko.observable();
		this.calculatedLengthFormatted = ko.observable();
		this.usedWeight = ko.observable(""); // empty sting, because length-check is needed in PringHistory_tab.jinja2
        this.usedCost = ko.observable();

        // Tab costs
		self.totalCosts = ko.observable();
		self.filamentCost = ko.observable();
		self.electricityCost = ko.observable();
		self.printerCost = ko.observable();
		self.otherCostLabel = ko.observable();
		self.otherCost = ko.observable();
		this.currencySymbol = ko.observable();
		this.withDefaultSpoolValues = ko.observable();

		recalculateTotalCosts = function(newValue){
		    var filamentCost = toFloatOrZero(self.filamentCost());
		    var electricityCost = toFloatOrZero(self.electricityCost());
		    var printerCost = toFloatOrZero(self.printerCost());
		    var otherCost = toFloatOrZero(self.otherCost());
		    var newTotal = (filamentCost + electricityCost + printerCost + otherCost).toFixed(2);
		    self.totalCosts(newTotal);
        }

        this.filamentCost.subscribe(recalculateTotalCosts);
        this.electricityCost.subscribe(recalculateTotalCosts);
        this.printerCost.subscribe(recalculateTotalCosts);
        this.otherCost.subscribe(recalculateTotalCosts);

		this.snapshotFilename = ko.observable();
		this.slicerSettingsAsText = ko.observable();
		this.technicalLog = ko.observable();

		this.isMultiToolPrint = ko.observable(false);
		this.isCostsAvailable = ko.observable(false);
		this.isRePrintable = ko.observable(false);
		this.fullFileLocation = ko.observable();
/*
        this.successful = ko.computed(function() {
            return this.success() == 1;
        }, this);
        this.filamentUsage = ko.computed(self.formatFilament, this);
        this.formattedDate = ko.computed(function () {
            return formatDate(this.timestamp());
        }, this);

*/
        // Fill Item with data
        this.update(data);
    }

    PrintJobItem.prototype.update = function (data) {
        var updateData = data || {}

        this.databaseId(updateData.databaseId);
        this.userName(updateData.userName);
        this.fileName(updateData.fileName);
        this.filePathName(updateData.filePathName);
        this.fileSize(updateData.fileSize)
        this.fileSizeFormatted(updateData.fileSizeFormatted)
        this.printStartDateTime(updateData.printStartDateTime);
        this.printEndDateTime(updateData.printEndDateTime);
        this.printStartDateTimeFormatted(updateData.printStartDateTimeFormatted);
        this.printEndDateTimeFormatted(updateData.printEndDateTimeFormatted);
        this.printStatusResult(updateData.printStatusResult);
        this.duration(updateData.duration);
        this.durationFormatted(updateData.durationFormatted);
        this.noteText(updateData.noteText);
        this.noteDeltaFormat(updateData.noteDeltaFormat);
        if (updateData.noteHtml != null){
            this.noteHtml(updateData.noteHtml);
        } else {
            // Fallback text
            this.noteHtml(updateData.noteText);
        }

        this.printedLayers(updateData.printedLayers);
        this.printedHeight(updateData.printedHeight);

        // Flatten all releations
        // Temperature
        tempDataArray = updateData["temperatureModels"];
        // Just grab the first value
        if (tempDataArray != null && tempDataArray.length >=2) {
            this.temperatureBed(tempDataArray[0].sensorValue);
            this.temperatureNozzle(tempDataArray[1].sensorValue);
        } else {
            this.temperatureBed(updateData.temperatureBed);
            this.temperatureNozzle(updateData.temperatureNozzle);
        }

//        this.usedSpoolManagerPlugin(updateData.usedSpoolManagerPlugin);
//        if (updateData.spoolItemData != null){
//            this.spoolItem = self.spoolDialog.createSpoolItemForTable(updateData.spoolItemData);
//        } else {
//            this.spoolItem = null;
//        }

        //Filament
        if (updateData.filamentModels == null){
            if (updateData.allFilamentModels != null) {
                updateData.filamentModels = updateData.allFilamentModels;
            }
        }
        if (updateData.filamentModels != null && Object.keys(updateData.filamentModels).length != 0){
            // result from backend
            this.allFilamentModels = updateData.filamentModels;
            this.allToolKeys = Object.keys(this.allFilamentModels);
            this.allToolKeys = this.allToolKeys.sort(); // total must be on the bottom
            this.isMultiToolPrint(Object.keys(this.allFilamentModels).length > 1)
            totalFilamentModel = updateData.filamentModels["total"];    // should always be present
            // should never happen, but in the past we have some jobs where "total" is missing
            if (totalFilamentModel != null){
                this.diameter(totalFilamentModel.diameter);
                this.density(totalFilamentModel.density);
                this.material(totalFilamentModel.material);
                this.vendor(totalFilamentModel.vendor);
                this.spoolName(totalFilamentModel.spoolName);
                this.weight(totalFilamentModel.weight);
    //            this.usedLength( formatFilamentLength(updateData.filamentEntity.usedLength) );
    //            this.calculatedLength( formatFilamentLength(updateData.filamentEntity.calculatedLength) );
                this.usedLengthFormatted(totalFilamentModel.usedLengthFormatted );
                this.calculatedLengthFormatted(totalFilamentModel.calculatedLengthFormatted );
                this.usedWeight(totalFilamentModel.usedWeight );
                this.usedCost(totalFilamentModel.usedCost );
            }
        }

        this.isCostsAvailable(updateData.isCostsAvailable);
        if  (this.isCostsAvailable() == true && updateData.hasOwnProperty("costs")){
            // data from backend
            this.totalCosts(updateData.costs.totalCosts?.toFixed(2));
            this.filamentCost(updateData.costs.filamentCost?.toFixed(2));
            this.electricityCost(updateData.costs.electricityCost?.toFixed(2));
            this.printerCost(updateData.costs.printerCost?.toFixed(2));
            this.otherCostLabel(updateData.costs.otherCostLabel);
            this.otherCost(updateData.costs.otherCost?.toFixed(2));
            this.currencySymbol(updateData.costs.currencySymbol);
            this.withDefaultSpoolValues(updateData.costs.withDefaultSpoolValues);
        } else {
            this.totalCosts(updateData.totalCosts);
            this.filamentCost(updateData.filamentCost);
            this.electricityCost(updateData.electricityCost);
            this.printerCost(updateData.printerCost);
            this.otherCostLabel(updateData.otherCostLabel);
            this.otherCost(updateData.otherCost);
            this.currencySymbol(updateData.currencySymbol);
            this.withDefaultSpoolValues(updateData.withDefaultSpoolValues);
        }

		this.snapshotFilename(updateData.snapshotFilename);
		this.slicerSettingsAsText(updateData.slicerSettingsAsText)
		this.technicalLog(updateData.technicalLog)
        this.isRePrintable(updateData.isRePrintable);
        this.fullFileLocation(updateData.fullFileLocation);
    };


    var TableAttributeVisibility = function (){
        this.status = ko.observable(true);
        this.user = ko.observable(true);
        this.date = ko.observable(true);
        this.startDateTime = ko.observable(true);
        this.endDateTime = ko.observable(true);
        this.duration = ko.observable(true);
        this.file = ko.observable(true);
        this.fileName = ko.observable(true);
        this.fileSize = ko.observable(true);
        this.tempBed = ko.observable(true);
        this.tempTool = ko.observable(true);
        this.height = ko.observable(true);
        this.layer = ko.observable(true);
        this.usage = ko.observable(true);
        this.material = ko.observable(true);
        this.vendor = ko.observable(true);
        this.spool = ko.observable(true);
        this.usedLength = ko.observable(true);
        this.usedWeight = ko.observable(true);
        this.note = ko.observable(true);
        this.costs = ko.observable(true);
        this.image = ko.observable(true);
    }


    ////////////////////////////////////////////////////////////////////////////// VIEW MODEL
    function PrintjobhistoryExtendedViewModel(parameters) {

        var PLUGIN_ID = "PrintJobHistoryExtended"; // from setup.py plugin_identifier

        var self = this;

/// START MOCK MODEL
        var emptyPrintJobItemAsJson = {
                "databaseId" : null,
                "printStartDateTimeFormatted" : "",
                "printEndDateTimeFormatted" : "",
                "printStatusResult" : "success",
                "duration" : "",
                "durationFormatted" : "",
                "fileName" : "",
                "fileSize" : "",
                "fileSizeFormatted" : "",
                "temperatureBed" : "0.0",
                "temperatureNozzle" : "0.0",
                "printedHeight" : "0.0 / 0.0",
                "printedLayers" : "0 / 0",
                "spoolName" : "",
                "spoolCost" : "",
                "material" : "",
                "usedLengthFormatted" : "",
                "calculatedLengthFormatted" : "",
                "usedWeight" : "",
                "usedCost" : "",
                "noteText" : "",
                "noteDeltaFormat" : null,
                "noteHtml" : "",
                "snapshotFilename" : "empty",
                "slicerSettingsAsText" : "",
                "technicalLog" : ""
        }
        var printHistoryJobItems = [];

//         var printHistoryJobItems = [
//              {
//                 "databaseId" : ko.observable(1),
//                 "success" : ko.observable("1"),
//                 "printStartDateTimeFormatted" : "19.09.2019 16:25",
//                 "printEndDateTimeFormatted" : "19.09.2019 17:25",
//                 "printStatusResult" : ko.observable("success"),
//                 "duration" : "123",
//                 "durationFormatted" : "1h12min",
//                 "fileName" : "Legolas.gcode",
//                 "fileSize" : "134",
//                 "fileSizeFormatted" : "134KB",
//                 "temperatureBed" : "50C",
//                 "temperatureNozzle" : "200C",
//                 "printedHeight" : "23 / 23mm",
//                 "printedLayers" : "149 / 149",
//                 "spoolName" : "myspool",
//                 "spoolCost" : "2,23",
//                 "material" : "PLA",
//                 "usedLengthFormatted" : "1m22mm",
//                 "calculatedLengthFormatted" : "12,5g",
//                 "usedWeight" : "12,5g",
//                 "usedCost" : "0,003",
//                  "isCostsAvailable": ko.observable(false),
//                 "noteText" : "Good output of Legolas",
//                 "noteDeltaFormat" : "Good output of Legolas",
//                 "noteHtml" : "<h1>Good output of Legolas</h1>",
//                 "snapshotFilename" : ko.observable("20191003-123322"),
//                 "slicerSettingsAsText" : ko.observable()
// //                "usedSpoolManagerPlugin": ko.observable()
//             },{
//                 "databaseId" : ko.observable(2),
//                 "success" : ko.observable("0"),
//                 "printStartDateTimeFormatted" : "20.09.2019 15:25",
//                 "printEndDateTimeFormatted" : "20.09.2019 17:25",
//                 "printStatusResult" : ko.observable("fail"),
//                 "duration" : "321",
//                 "durationFormatted" : "2h34min",
//                 "fileName" : "Benchy3D.gcode",
//                 "fileSize" : "324",
//                 "fileSizeFormatted" : "324KB",
//                 "temperatureBed" : "60C",
//                 "temperatureNozzle" : "220C",
//
//                 "printedHeight" : "13 / 143mm",
//                 "printedLayers" : "3 / 68",
//                 "spoolName" : "Master",
//                 "spoolCost" : "2,23",
//                 "material" : "ABS",
//                 "usedLengthFormatted" : "2m22mm",
//                 "calculatedLengthFormatted" : "312,6g",
//                 "usedWeight" : "312,6g",
//                 "usedCost" : "1,34",
//
//                 "noteText" : "Bad quality",
//                 "noteDeltaFormat" : "Bad quality",
//                 "noteHtml" : "<h2>Bad quality,/h2>",
//                 "snapshotFilename" : ko.observable("20191003-153312"),
//                 "slicerSettingsAsText" : ko.observable()
// //                "usedSpoolManagerPlugin": ko.observable()
//             }
//         ];
//        self.printJobHistoryExtendedListHelper.updateItems(printHistoryJobItems);

/// END MOCK MODEL

        // assign the injected parameters, e.g.:
        self.loginStateViewModel = parameters[0];
        self.loginState = parameters[0];
        self.settingsViewModel = parameters[1];
        self.accessViewModel = parameters[2];

        self.pluginSettings = null;

        self.apiClient = new PrintJobHistoryExtendedAPIClient(PLUGIN_ID, BASEURL);
        self.componentFactory = new PrintJobComponentFactory(PLUGIN_ID);
        self.printJobEditDialog = new PrintJobHistoryExtendedEditDialog(null, null);
        self.pluginCheckDialog = new PrintJobHistoryExtendedPluginCheckDialog();
        self.statisticDialog = new StatisticDialog();
        self.compareSlicerSettingsDialog = new CompareSlicerSettingsDialog();
        self.messageConfirmDialog = new PrintJobHistoryExtendedPluginMessageConfirmDialog();
        self.legacyMigration = new PrintJobHistoryExtendedLegacyMigration();

        self.printJobForEditing = ko.observable();
        self.printJobForEditing(new PrintJobItem(printHistoryJobItems[0]));

        self.printJobToShowAfterStartup = null;
        self.missingPluginDialogMessage = null;
        self.confirmMessageExtendedDialogData = null;

        //self.tableAttributeVisibility = ko.observable();
        self.tableAttributeVisibility = new TableAttributeVisibility();

        self.csvFileUploadName = ko.observable();
        self.csvImportInProgress = ko.observable(false);

        self.isPrintHistoryPluginAvailable = ko.observable(false);
        self.isSpoolManagerInstalled = ko.observable(false);
        self.isSpoolmanInstalled = ko.observable(false);
        self.isFilamentManagerInstalled = ko.observable(false);

        self.isPreHeatPluginAvailableText = ko.observable("unknown");

        self.databaseFileLocation = ko.observable();
        self.snapshotFileLocation = ko.observable();

        // database backend / external database
        self.databaseConnectionTestResult = ko.observable("");
        self.databaseConnectionTestSuccess = ko.observable(true);
        self.databaseBusy = ko.observable(false);
        self.databaseSchemeVersionFromPlugin = ko.observable("-");
        self.databaseLocalSchemeVersion = ko.observable("-");
        self.databaseLocalJobCount = ko.observable("-");
        self.databaseExternalSchemeVersion = ko.observable("-");
        self.databaseExternalJobCount = ko.observable("-");
        self.databaseSchemeUpgradeNeeded = ko.observable(false);
        self.knownInstanceNames = ko.observableArray([]);

        self.isCostEstimationPluginAvailableText = ko.observable("unknown");
        self.currencySymbol = ko.observable();
        self.currencyFormat = ko.observable();

        self.csvImportDialog = new PrintJobHistoryExtendedImportDialog();

        ////////////////////////////////////////////////////// Knockout model-binding/observer

        ///////////////////////////////////////////////////// START: HELPER
        loadSettingsFromBrowserStore = function(){
            initTableVisibilities();

            // TODO maybe in a separate js-file
            // load all settings from browser storage
            if (!Modernizr.localstorage) {
                // damn!!!
                return false;
            }
            var storageKey = "pjh.table.selectedPageSize";
            if (localStorage[storageKey] == null){
                localStorage[storageKey] = "25"; // default page size
            } else {
                self.printJobHistoryExtendedTableHelper.selectedPageSize(localStorage[storageKey]);
            }
            self.printJobHistoryExtendedTableHelper.selectedPageSize.subscribe(function(newValue){
                localStorage[storageKey] = newValue;
            });
        }

        self.showPopUp = function(popupType, popupTitle, message, hide){
            if (hide){
                hide = hide;
            } else {
                hide = false
            }

            var title = popupType.toUpperCase() + ": " + popupTitle;
            var popupId = (title+message).replace(/([^a-z0-9]+)/gi, '-');
            if($("."+popupId).length <1) {
                new PNotify({
                    title: "PJH:" + title,
                    text: message,
                    type: popupType,
                    hide: hide,
                    addclass: popupId
                });
            }
        };

        ///////////////////////////////////////////////////// END: HELPER

        ///////////////////////////////////////////////////// START: SETTINGS
        self.busyIndicatorActive = ko.observable(false);

        self.downloadDatabaseUrl = ko.observable();
        self.cameraSnapShotURLAvailable = ko.observable(false);

        isSnapshotUrlPresent = function(snapshotUrl){
            return snapshotUrl != null && snapshotUrl.trim().length != 0;
        }
        self.initCameraSettingsBehaviour = function(){

            if (self.settingsViewModel.settings.webcam.snapshotUrl != null){
                // assign inital values
                self.cameraSnapShotURLAvailable(isSnapshotUrlPresent(self.settingsViewModel.settings.webcam.snapshotUrl()));

                self.settingsViewModel.settings.webcam.snapshotUrl.subscribe(function(newSnapShotUrl){
                    self.cameraSnapShotURLAvailable(isSnapshotUrlPresent(newSnapShotUrl));
                });
            }
            // Checkbox - stuff
            self.pluginSettings.takeSnapshotAfterPrint.subscribe(function(newValue){
                  if (newValue == true){
                        self.pluginSettings.takeSnapshotOnM118Commnd(false);
                        self.pluginSettings.takeSnapshotOnGCodeCommnd(false);
                    }
            });
            self.pluginSettings.takeSnapshotOnGCodeCommnd.subscribe(function(newValue){
                    if (newValue == true){
                        self.pluginSettings.takeSnapshotOnM118Commnd(false);
                        self.pluginSettings.takeSnapshotAfterPrint(false);
                    }
            });
            self.pluginSettings.takeSnapshotOnM118Commnd.subscribe(function(newValue){
                    if (newValue == true){
                        self.pluginSettings.takeSnapshotOnGCodeCommnd(false);
                        self.pluginSettings.takeSnapshotAfterPrint(false);
                    }
            });

        }

        self.deleteDatabaseAction = function() {
            var confirmMessage = "Do you really want to delete all PrintJobHistory data?";
            if (self.pluginSettings.useExternal() == true){
                // A shared external database holds the history of every connected instance.
                confirmMessage = "WARNING: an external database is in use.\n\n"
                    + "This deletes the print jobs of ALL OctoPrint instances connected to '"
                    + self.pluginSettings.databaseName() + "', not just this one.\n\n"
                    + "Do you really want to continue?";
            }
            var result = confirm(confirmMessage);
            if (result == true){
                self.apiClient.callDeleteDatabase(function(responseData) {
                    self.printJobHistoryExtendedTableHelper.reloadItems();
                    self.loadDatabaseMetaData();
                });
            }
        };

        // Collect the database settings from the dialog, so they can be tested before saving.
        self.buildDatabaseSettings = function() {
            return {
                "useExternal": self.pluginSettings.useExternal(),
                "databaseType": self.pluginSettings.databaseType(),
                "databaseHost": self.pluginSettings.databaseHost(),
                "databasePort": self.pluginSettings.databasePort(),
                "databaseName": self.pluginSettings.databaseName(),
                "databaseUser": self.pluginSettings.databaseUser(),
                "databasePassword": self.pluginSettings.databasePassword()
            };
        };

        self.handleDatabaseMetaDataResponse = function(metaData) {
            self.databaseBusy(false);
            if (metaData == null){
                return;
            }
            self.databaseSchemeVersionFromPlugin(metaData.schemeVersionFromPlugin);
            self.databaseLocalSchemeVersion(metaData.localSchemeVersion == null ? "-" : metaData.localSchemeVersion);
            self.databaseLocalJobCount(metaData.localJobCount == null ? "-" : metaData.localJobCount);
            self.databaseExternalSchemeVersion(metaData.externalSchemeVersion == null ? "-" : metaData.externalSchemeVersion);
            self.databaseExternalJobCount(metaData.externalJobCount == null ? "-" : metaData.externalJobCount);
            self.databaseSchemeUpgradeNeeded(metaData.schemeUpgradeNeeded == true);

            self.databaseConnectionTestSuccess(metaData.success == true);
            if (metaData.success == true){
                self.databaseConnectionTestResult("Connection successful.");
            } else {
                self.databaseConnectionTestResult("Connection failed: " + metaData.errorMessage);
            }
        };

        self.loadDatabaseMetaData = function() {
            self.apiClient.callLoadDatabaseMetaData(function(responseData) {
                self.handleDatabaseMetaDataResponse(responseData.metadata);
            });
        };

        self.testDatabaseConnection = function() {
            self.databaseBusy(true);
            self.databaseConnectionTestResult("Testing connection...");
            self.apiClient.callTestDatabaseConnection(
                self.buildDatabaseSettings(),
                function(responseData) {
                    self.handleDatabaseMetaDataResponse(responseData.metadata);
                },
                function(jqXHR) {
                    self.databaseBusy(false);
                    self.databaseConnectionTestSuccess(false);
                    self.databaseConnectionTestResult("Connection test failed. See OctoPrint.log for details.");
                }
            );
        };

        self.copyDatabaseAction = function() {
            $("#dialog_printJobHistoryExtended_copyDatabase").modal("show");
        };

        self.cancelCopyDatabaseAction = function() {
            $("#dialog_printJobHistoryExtended_copyDatabase").modal("hide");
        };

        self.confirmCopyDatabaseAction = function() {
            self.databaseBusy(true);
            self.databaseConnectionTestResult("Copying data...");
            self.apiClient.callCopyDatabase(
                self.buildDatabaseSettings(),
                function(responseData) {
                    self.databaseBusy(false);
                    $("#dialog_printJobHistoryExtended_copyDatabase").modal("hide");
                    var metaData = responseData.metadata;
                    if (metaData.success == true){
                        self.databaseConnectionTestSuccess(true);
                        var message = "Copied " + metaData.copiedJobCount + " print job(s).";
                        if (metaData.totalJobCountAfterCopy != null){
                            message = message + " The external database now holds "
                                + metaData.totalJobCountAfterCopy + " print job(s) in total.";
                        }
                        self.databaseConnectionTestResult(message);
                        self.loadDatabaseMetaData();
                        self.loadKnownInstances();
                    } else {
                        self.databaseConnectionTestSuccess(false);
                        self.databaseConnectionTestResult("Copy failed: " + metaData.errorMessage);
                    }
                },
                function(jqXHR) {
                    self.databaseBusy(false);
                    $("#dialog_printJobHistoryExtended_copyDatabase").modal("hide");
                    self.databaseConnectionTestSuccess(false);
                    self.databaseConnectionTestResult("Copy failed. See OctoPrint.log for details.");
                }
            );
        };

        self.upgradeDatabaseSchemeAction = function() {
            self.databaseBusy(true);
            self.apiClient.callUpgradeDatabaseScheme(
                function(responseData) {
                    self.databaseBusy(false);
                    var metaData = responseData.metadata;
                    if (metaData.success == true){
                        self.databaseConnectionTestSuccess(true);
                        self.databaseConnectionTestResult("Database scheme upgraded to version " + metaData.schemeVersion + ".");
                        self.databaseSchemeUpgradeNeeded(false);
                        self.loadDatabaseMetaData();
                    } else {
                        self.databaseConnectionTestSuccess(false);
                        self.databaseConnectionTestResult("Upgrade failed: " + metaData.errorMessage);
                    }
                },
                function(jqXHR) {
                    self.databaseBusy(false);
                    self.databaseConnectionTestSuccess(false);
                    self.databaseConnectionTestResult("Upgrade failed. See OctoPrint.log for details.");
                }
            );
        };

        self.loadKnownInstances = function() {
            self.apiClient.callLoadKnownInstances(function(responseData) {
                self.knownInstanceNames(responseData.instanceNames);
            });
        };

        self.csvImportUploadButton = $("#settings-pjhe-importcsv-upload");
        self.csvImportUploadData = undefined;
        self.csvImportUploadButton.fileupload({
            dataType: "json",
            maxNumberOfFiles: 1,
            autoUpload: false,
            headers: OctoPrint.getRequestHeaders(),
            add: function(e, data) {
                if (data.files.length === 0) {
                    // no files? ignore
                    return false;
                }
                self.csvFileUploadName(data.files[0].name);
                self.csvImportUploadData = data;
            },
            done: function(e, data) {
                self.csvImportInProgress(false);
                self.csvFileUploadName(undefined);
                self.csvImportUploadData = undefined;
            },
            error: function(response, data, errorMessage){
                self.csvImportInProgress(false);
                statusCode = response.status;       // e.g. 400
                statusText = response.statusText;   // e.g. BAD REQUEST
                responseText = response.responseText; // e.g. Invalid request
            }
        });

        self.performCSVImportFromUpload = function() {
            if (self.csvImportUploadData === undefined) return;

            self.csvImportInProgress(true);
            self.csvImportDialog.showDialog(function(shouldTableReload){
                    //
                    if (shouldTableReload == true){
                        self.printJobHistoryExtendedTableHelper.reloadItems();
                    }
                }
            );
            self.csvImportUploadData.submit();
        };

        // download sample csv-file
        self.sampleCSVUrl = function(printJobItem){
            return self.apiClient.getSampleCSVUrl();
        }

        /////// Report - Template handling
        self.resetPrintJobReportTemplate = function(reportType){
            var result = confirm("Do you really want reset to the default template report ?");
            if (result == true) {
                self.apiClient.callResetPrintJobReportTemplate(reportType, function(responseData){
                    // handle response
                    if (responseData.result == true){
                        if (reportType == "multi"){
                            self.pluginSettings.multiPrintJobTemplateName(responseData.printJobTemplateName);
                        }else {
                            self.pluginSettings.singlePrintJobTemplateName(responseData.printJobTemplateName);
                        }
                    }
                });
            }
        }

        self.downloadPrintJobReportTemplateUrl = function(reportType){
            return self.apiClient.getPrintJobReportTemplateUrl(reportType);
        }

        self.singlePrintJobReportTemplateUploadName = ko.observable();
        self.multiPrintJobReportTemplateUploadName = ko.observable();
        self.singlePrintJobReportTemplateInProgress = ko.observable(false);
        self.multiPrintJobReportTemplateInProgress = ko.observable(false);

        self.singlePrintJobReportTemplateUploadButton = $("#settings-pjhe-uploadSingleReportTemplate-upload");
        self.multiPrintJobReportTemplateUploadButton = $("#settings-pjhe-uploadMultiReportTemplate-upload");
        self.singlePrintJobReportTemplateUploadData = undefined;
        self.multiPrintJobReportTemplateUploadData = undefined;
        self.singlePrintJobReportTemplateUploadButton.fileupload({
            dataType: "json",
            maxNumberOfFiles: 1,
            autoUpload: false,
            headers: OctoPrint.getRequestHeaders(),
            add: function(e, data) {
                if (data.files.length === 0) {
                    // no files? ignore
                    return false;
                }
                self.singlePrintJobReportTemplateUploadName(data.files[0].name);
                self.singlePrintJobReportTemplateUploadData = data;
            },
            done: function(e, data) {
                self.singlePrintJobReportTemplateInProgress(false);
                self.singlePrintJobReportTemplateUploadName(undefined);
                self.singlePrintJobReportTemplateUploadNameUploadData = undefined;

                if (data.result.result == true){
                    self.pluginSettings.singlePrintJobTemplateName(data.result.printJobTemplateName);
                }
            },
            error: function(response, data, errorMessage){
                self.singlePrintJobReportTemplateInProgress(false);
                statusCode = response.status;       // e.g. 400
                statusText = response.statusText;   // e.g. BAD REQUEST
                responseText = response.responseText; // e.g. Invalid request
            }
        });
        self.multiPrintJobReportTemplateUploadButton.fileupload({
            dataType: "json",
            maxNumberOfFiles: 1,
            autoUpload: false,
            headers: OctoPrint.getRequestHeaders(),
            add: function(e, data) {
                if (data.files.length === 0) {
                    // no files? ignore
                    return false;
                }
                self.multiPrintJobReportTemplateUploadName(data.files[0].name);
                self.multiPrintJobReportTemplateUploadData = data;
            },
            done: function(e, data) {
                self.multiPrintJobReportTemplateInProgress(false);
                self.multiPrintJobReportTemplateUploadName(undefined);
                self.multiPrintJobReportTemplateUploadNameUploadData = undefined;

                if (data.result.result == true){
                    self.pluginSettings.multiPrintJobTemplateName(data.result.printJobTemplateName);
                }
            },
            error: function(response, data, errorMessage){
                self.multiPrintJobReportTemplateInProgress(false);
                statusCode = response.status;       // e.g. 400
                statusText = response.statusText;   // e.g. BAD REQUEST
                responseText = response.responseText; // e.g. Invalid request
            }
        });

        self.performSinglePrintJobReportTemplateUploadNameFromUpload = function() {
            if (self.singlePrintJobReportTemplateUploadData === undefined) return;

            self.singlePrintJobReportTemplateInProgress(true);

            self.singlePrintJobReportTemplateUploadData.submit();
        };
        self.performMultiPrintJobReportTemplateUploadNameFromUpload = function() {
            if (self.multiPrintJobReportTemplateUploadData === undefined) return;

            self.multiPrintJobReportTemplateInProgress(true);

            self.multiPrintJobReportTemplateUploadData.submit();
        };

        // create sample report
        self.reportSamplePrintJobItem = function(reportType) {
            if (reportType == "multi"){
                var tableQuery = self.printJobHistoryExtendedTableHelper.getTableQuery();
                window.open(self.apiClient.callCreateMultiReportUrl("sample"), '_blank').focus();

            } else {
                window.open(self.apiClient.callCreateSingleReportUrl("sample"), '_blank').focus();
            }
        }
        self.reportMultiPrintJobItem = function(reportAll) {
            var tableQuery = null;
            if ("all" == reportAll) {
                tableQuery = self.printJobHistoryExtendedTableHelper.getTableQuery();
            } else {
                var selectedTableItems = self.printJobHistoryExtendedTableHelper.selectedTableItems();
                if (selectedTableItems.length > 0){
                    tableQuery = "databaseIds="+ self.selectedDatabaseIdsAsCSV
                } else {
                    tableQuery = self.printJobHistoryExtendedTableHelper.getTableQuery();
                }
            }
            return self.apiClient.callCreateMultiReportUrl(tableQuery);
        }

        ///////////////////////////////////////////////////// END: SETTINGS

        ///////////////////////////////////////////////////// START: OctoPrint Hooks

        self.onBeforeBinding = function() {
            // assign current pluginSettings
            self.pluginSettings = self.settingsViewModel.settings.plugins[PLUGIN_ID];
            self.printJobEditDialog.init(self.apiClient, self.settingsViewModel.settings.webcam);
            self.pluginCheckDialog.init(self.apiClient, self.pluginSettings);
            self.messageConfirmDialog.init(self.apiClient, self.pluginSettings);
            self.csvImportDialog.init(self.apiClient);
            self.statisticDialog.init(self.apiClient);
            self.compareSlicerSettingsDialog.init(self.apiClient, self.busyIndicatorActive);
            self.legacyMigration.init(PLUGIN_ID);
            self.legacyMigration.loadStatus();

            // load browser stored settings
            loadSettingsFromBrowserStore();

            // resetSettings-Stuff
            new ResetSettingsUtilV3(self.pluginSettings).assignResetSettingsFeature(PLUGIN_ID, function(data){
                // no additional reset function
             });

            self.initCameraSettingsBehaviour();
        }

        self.onAfterBinding = function() {
            //
            // debugger
            // var mylog = anylogger('my-module');
            // mylog('Logging is easy!');
            // mylog.level = log.INFO;
            // mylog.info('This info message will NOT be logged.');
            // mylog.warn('This warning message WILL be logged.');
            //
            // debugger
            // all inits were done
            self.downloadDatabaseUrl(self.apiClient.getDownloadDatabaseUrl());

            self.loadDatabaseMetaData();
            self.loadKnownInstances();

            // to bring up dialogs the binding must be already done
            if (self.printJobToShowAfterStartup != null){
                self.showPrintJobDetailsDialogAction(self.printJobToShowAfterStartup, true);
            }
            if (self.missingPluginDialogMessage != null){
                self.pluginCheckDialog.showMissingPluginsDialog(self.missingPluginDialogMessage);
                self.missingPluginDialogMessage = null;
            }
            if (self.confirmMessageExtendedDialogData != null){
                self.confirmMessageExtendedDialog.showDialog(self.confirmMessageExtendedDialogData);
                self.confirmMessageExtendedDialogData = null;
            }


        }

        self.onUserLoggedIn = function(currentUser) {
            self.printJobEditDialog.setCurrentUser(currentUser);
        }

        self.onUserLoggedOut = function() {
            if (self.printJobEditDialog != null) {
                self.printJobEditDialog.setCurrentUser(null);
            }
        }

        // receive data from server
        self.onDataUpdaterPluginMessage = function (plugin, data) {

            if (plugin != PLUGIN_ID) {
                return;
            }

            if ("initalData" == data.action){
                self.databaseFileLocation(data.databaseFileLocation);
                self.snapshotFileLocation(data.snapshotFileLocation);
                self.isPrintHistoryPluginAvailable(data.isPrintHistoryPluginAvailable);
                self.isSpoolManagerInstalled(data.isSpoolManagerInstalled);
                self.isSpoolmanInstalled(data.isSpoolmanInstalled);
                self.isFilamentManagerInstalled(data.isFilamentManagerInstalled);

                if (data.isCostEstimationPluginAvailable == true){
                    self.isCostEstimationPluginAvailableText("<span style='color:green'>available</span>");
                } else {
                    self.isCostEstimationPluginAvailableText("<span style='color:red'>not available</span>");
                }
                if (data.isPreHeatPluginAvailable == true){
                    self.isPreHeatPluginAvailableText("<span style='color:green'>available</span>");
                } else {
                    self.isPreHeatPluginAvailableText("<span style='color:red'>not available</span>");
                }
                self.currencySymbol(data.currencySymbol);
                self.currencyFormat(data.currencyFormat);

                return;
            }

            if ("missingPlugin" == data.action){
                // NOT POSSIBLE, because init not done
                if (self.pluginCheckDialog != null && self.pluginCheckDialog.isInitialized()){
                    self.pluginCheckDialog.showMissingPluginsDialog(data.message);
                } else {
                    // save message for later use, because binding is also not finished, but we received a message from backend
                    self.missingPluginDialogMessage = data.message;
                }
                return;
            }

            if ("showMessageConfirmDialog" == data.action){
                // NOT POSSIBLE, because init not done
                if (self.confirmMessageExtendedDialog != null && self.confirmMessageExtendedDialog.isInitialized()){
                    self.confirmMessageExtendedDialog.showDialog(data.confirmMessageExtendedData);
                } else {
                    // save message for later use, because binding is also not finished, but we received a message from backend
                    self.confirmMessageExtendedDialogData = data.confirmMessageExtendedData;
                }
                return;
            }

            if ("printFinished" == data.action){
                self.printJobHistoryExtendedTableHelper.reloadItems();
                if (data.printJobItem != null){
                    self.printJobToShowAfterStartup = data.printJobItem;
                    self.showPrintJobDetailsDialogAction(data.printJobItem, true);
                }
                return;
            }

            if ("reloadTableItems" == data.action){
                self.printJobHistoryExtendedTableHelper.reloadItems();
                return;
            }

            if ("closeEditDialog" == data.action){
                self.printJobEditDialog.closeDialog();
                return;
            }

            if ("csvImportStatus" == data.action){
                self.csvImportDialog.updateText(data);
                return;
            }

            if ("showPrintJobDialogAfterClientConnection" == data.action){
                if (data.printJobItem != null){
                    self.printJobToShowAfterStartup = data.printJobItem;
                    self.showPrintJobDetailsDialogAction(data.printJobItem, true);
                }
                return;
            }

            if ("messagePopUp" == data.action){
                self.showPopUp("" + data.popupType,
                               "" + data.title,
                                "" + data.message,
                                 data.hide);
                return;
            }

            if ("errorPopUp" == data.action){
                self.showPopUp("error",
                                data.title,
                                "" + data.message);
                return;
            }
        }

        self.onTabChange = function(next, current){
            //alert("Next:"+next +" Current:"+current);
            if ("#tab_plugin_PrintJobHistoryExtended" == next){
                //self.reloadTableData();
            }
        }

        ///////////////////////////////////////////////////// END: OctoPrint Hooks

        ///////////////////////////////////////////////////// START: DIALOG Stuff

        self.showStatisticDialog = function(){
            self.statisticDialog.showDialog(self.printJobHistoryExtendedTableHelper.getTableQuery(), function(shouldTableReload){
            });
        }

        printJobDialogCloseHandler = function(shouldTableReload){
            // refresh snapshotImage
            printJob = self.printJobForEditing();
            var snapshotImageId = "#"+self.snapshotImageId(printJob);
            var snapshotImage = $(snapshotImageId);
            var snapshotUrl = snapshotImage.attr("src");
            snapshotImage.attr("src", snapshotUrl+"?" + new Date().getTime()); // cache - break

            if (shouldTableReload == true){
                self.printJobHistoryExtendedTableHelper.reloadItems();
            }

            if (self.printJobToShowAfterStartup != null){
                // PrintJob was presented to user and user confirmed
                self.printJobToShowAfterStartup = null;
                payload = {
                    "showPrintJobDialogAfterPrint_jobId": null
                };
                OctoPrint.settings.savePluginSettings(PLUGIN_ID, payload);
            }
        }

        self.showPrintJobDetailsDialogAction = function(selectedPrintJobItem, forceCloseDialog) {

            if (forceCloseDialog == null){
                forceCloseDialog = false;
            }
            self.printJobForEditing(new PrintJobItem(ko.mapping.toJS(selectedPrintJobItem)));
//            self.printJobEditDialog.showDialog(self.printJobForEditing(), printJobDialogCloseHandler);
            self.printJobEditDialog.showDialog(self.printJobForEditing(), function(shouldTableReload){
                // delegate to default close handler
                printJobDialogCloseHandler(shouldTableReload);
                if (forceCloseDialog == true){

                    self.apiClient.callForceCloseEditDialog(function(responseData){
                        // do nothing
                    });
                }
            });
        };

        ///////////////////////////////////////////////////// END: DIALOG Stuff

        ///////////////////////////////////////////////////// START: TABLE BEHAVIOR

        initTableVisibilities = function(){
            // load all settings from browser storage
            if (!Modernizr.localstorage) {
                // damn!!!
                return false;
            }

            assignVisibility = function(attributeName){
                var storageKey = "pjh.table.visible." + attributeName;
                if (localStorage[storageKey] == null){
                    localStorage[storageKey] = true
                } else {
                    self.tableAttributeVisibility[attributeName]( "true" == localStorage[storageKey]);
                }
                self.tableAttributeVisibility[attributeName].subscribe(function(newValue){
                    localStorage[storageKey] = newValue;
                });
            }

            assignVisibility("status");
            assignVisibility("user");
            assignVisibility("date");
            assignVisibility("startDateTime");
            assignVisibility("endDateTime");
            assignVisibility("duration");
            assignVisibility("file");
            assignVisibility("fileName");
            assignVisibility("fileSize");
            assignVisibility("tempBed");
            assignVisibility("tempTool");
            assignVisibility("height");
            assignVisibility("layer");
            assignVisibility("usage");
            assignVisibility("material");
            assignVisibility("vendor");
            assignVisibility("spool");
            assignVisibility("usedLength");
            assignVisibility("usedWeight");
            assignVisibility("note");
            assignVisibility("costs");
            assignVisibility("image");
        }

        self.addNewPrintJobItem = function(){
//            debugger
            var emptyItem = new PrintJobItem(emptyPrintJobItemAsJson);
            self.printJobForEditing(emptyItem);

            self.printJobEditDialog.showDialog(self.printJobForEditing(), printJobDialogCloseHandler, true);
        }

        self.cloneNewPrintJobItem = function(){
            debugger

            var emptyItem = new PrintJobItem(emptyPrintJobItemAsJson);
            self.printJobForEditing(emptyItem);

            self.printJobEditDialog.showDialog(self.printJobForEditing(), printJobDialogCloseHandler, true);
        }


        loadJobFunction = function(tableQuery, observableTableModel, observableTotalItemCount, observableCurrentItemCount){
            // api-call
            self.apiClient.callLoadPrintJobsByQuery(tableQuery, function(responseData){
                // handle response
                totalItemCount = responseData["totalItemCount"];
                allPrintJobs = responseData["allPrintJobs"];
                var dataRows = ko.utils.arrayMap(allPrintJobs, function (data) {
                    return new PrintJobItem(data);
                });
                observableTotalItemCount(totalItemCount);
                observableCurrentItemCount(dataRows.length);
                observableTableModel(dataRows);

            });
        }

        self.printJobHistoryExtendedTableHelper = new PrintJobTableItemHelper(loadJobFunction, 25, "printStartDateTime", "all");

        // - timeframe query
        self.allTimeFrames = ko.observableArray([
            { key: "all", label: "all" },
            { key: "custom", label: "custom" },
            { key: "lastWeek", label: "last 7 days" },
            { key: "lastMonth", label: "last 30 days" },
            { key: "lastThreeMonth", label: "last 90 days" },
            { key: "lastYear", label: "last 365 days" },
        ]);

        self.selectedTimeFrame = ko.observable("all");
        self.isCustomTimeFrame = ko.observable(false);

        self.selectedTimeFrame.subscribe(function(newValue){
                    self.isCustomTimeFrame("custom" == newValue);


                    if ("custom" == newValue){
                        self.isQueryStartEnabled(true);
                        self.isQueryEndEnabled(true);
                        return
                    }
                    self.isQueryStartEnabled(false);
                    self.isQueryEndEnabled(false);
                    // calculate new start/end date
                    var startDate = new Date(Date.now());
                    var endDate = new Date(Date.now());

                    if ("lastWeek" == newValue){
                        startDate.setDate(startDate.getDate() - 7);
                    } else
                    if ("lastMonth" == newValue){
                        startDate.setDate(startDate.getDate() - 30);
                    } else
                    if ("lastThreeMonth" == newValue){
                        startDate.setDate(startDate.getDate() - 90);
                    } else
                    if ("lastYear" == newValue){
                        startDate.setDate(startDate.getDate() - 365);
                    } else
                    if ("all" == newValue ){
                        startDate = null;
                        endDate = null;
                        self.printJobHistoryExtendedTableHelper.queryStartDate("");
                        self.printJobHistoryExtendedTableHelper.queryEndDate("");
                    }

                    if (startDate != null && endDate != null){
                        var yyyy = startDate.getFullYear().toString();
                        var mm = (startDate.getMonth() + 101).toString().slice(-2);
                        var dd = (startDate.getDate() + 100).toString().slice(-2);
                        self.printJobHistoryExtendedTableHelper.queryStartDate(dd + "." + mm + "." + yyyy);

                        yyyy = endDate.getFullYear().toString();
                        mm = (endDate.getMonth() + 101).toString().slice(-2);
                        dd = (endDate.getDate() + 100).toString().slice(-2);
                        self.printJobHistoryExtendedTableHelper.queryEndDate(dd + "." + mm + "." + yyyy);
                    }
                    self.printJobHistoryExtendedTableHelper.reloadItems();
            });
        var queryStartViewModel = self.componentFactory.createDateTimePicker("queryStartExtended-date-picker", false);
        self.printJobHistoryExtendedTableHelper.queryStartDate = queryStartViewModel.currentDateTime;
        self.isQueryStartEnabled = queryStartViewModel.isEnabled;
        self.isQueryStartEnabled(false);

        var queryEndViewModel = self.componentFactory.createDateTimePicker("queryEndExtended-date-picker", false);
        self.printJobHistoryExtendedTableHelper.queryEndDate = queryEndViewModel.currentDateTime;
        self.isQueryEndEnabled = queryEndViewModel.isEnabled;
        self.isQueryEndEnabled(false);

        // - export csv data
        self.exportUrl = function(exportType) {
            if (self.printJobHistoryExtendedTableHelper.items().length > 0) {
                var defaultURL =  self.apiClient.getExportUrl(exportType)
                return defaultURL;
            } else {
                return false;
            }
        };
        self.exportedSelectedURL = ko.observable();

        // Will be filled by table-item checkbox-subscriber
        self.selectedDatabaseIdsAsCSV = "";

        self.deleteSelectedPrintJobs = function(){
            if (self.selectedDatabaseIdsAsCSV.trim().length >1) {
                var result = confirm("Do you really want to delete all selected(" + self.printJobHistoryExtendedTableHelper.selectedTableItems().length + ") printjobs?");
                if (result == true) {
                    self.apiClient.callRemovePrintJob("0?databaseIds=" + self.selectedDatabaseIdsAsCSV, function (responseData) {

                        self.printJobHistoryExtendedTableHelper.selectedTableItems.removeAll();
                        self.printJobHistoryExtendedTableHelper.reloadItems();

                    });
                }
            }
            return false;
        }

        self.compareSelectedPrintJobs = function(){
            if (self.selectedDatabaseIdsAsCSV.split(",").length >=2){
                self.compareSlicerSettingsDialog.showDialog(self.selectedDatabaseIdsAsCSV, function(shouldTableReload){
                    // nothing special to do here
                    });
            }
            return false;
        }

        self.printJobHistoryExtendedTableHelper.selectedTableItems.subscribe(function(newValue) {

            var newCSVUrl = self.apiClient.getExportUrl("CSV");
            if (newValue != null && newValue.length > 0){
                var databaseIds = "";
                for (var itemIndex = 0; itemIndex<newValue.length; itemIndex++){
                    databaseIds = databaseIds + newValue[itemIndex].databaseId();
                    if (itemIndex+1 < newValue.length){
                        databaseIds = databaseIds + ",";
                    }
                }
                newCSVUrl = newCSVUrl + "?databaseIds="+ databaseIds;
                self.selectedDatabaseIdsAsCSV = databaseIds;
            }

            self.exportedSelectedURL(newCSVUrl);
        });

        self.sortOrderLabel = function(orderType) {
            var order = "";

            if (orderType == "fileName") {
                order = (self.printJobHistoryExtendedListHelper.currentSorting() == 'fileNameAsc') ? '(' + _('ascending') + ')' : (self.printJobHistoryExtendedListHelper.currentSorting() == 'fileNameDesc') ? '(' + _('descending') + ')' : '';
            } else if (orderType == "startDateTime") {
                order = (self.printJobHistoryExtendedListHelper.currentSorting() == 'startDateTimeAsc') ? '(' + _('ascending') + ')' : (self.printJobHistoryExtendedListHelper.currentSorting() == 'startDateTimeDesc') ? '(' + _('descending') + ')' : '';
            }
            return order;
        };

        self.changeSortOrder = function(columnToSort) {
            if ("fileName" == columnToSort){
                if (self.printJobHistoryExtendedListHelper.currentSorting() == "fileNameAsc") {
                    self.printJobHistoryExtendedListHelper.changeSorting("fileNameDesc");
                } else {
                    self.printJobHistoryExtendedListHelper.changeSorting("fileNameAsc");
                }
            }
            if ("startDateTime" == columnToSort){
                if (self.printJobHistoryExtendedListHelper.currentSorting() == "startDateTimeAsc") {
                    self.printJobHistoryExtendedListHelper.changeSorting("startDateTimeDesc");
                } else {
                    self.printJobHistoryExtendedListHelper.changeSorting("startDateTimeAsc");
                }
            }
        };

        self.snapshotUrl = function(printJobItem){
            return self.apiClient.getSnapshotUrl(printJobItem.snapshotFilename()) + "?" + new Date().getTime();
        }

        self.snapshotImageId = function(printJobItem){
            return "pjh-imageid-" + printJobItem.databaseId();
        }

        // - tableSearch
        self.clearTableSearchQuery = function () {
            self.printJobHistoryExtendedTableHelper.searchQuery("");
            self.printJobHistoryExtendedTableHelper.reloadItems();
        };

        self.printJobHistoryExtendedTableHelper.searchQuery.subscribe(function(newValue){
                self.printJobHistoryExtendedTableHelper.reloadItems();
                    // if (newValue != null && newValue.length > 2){
                    //     self.printJobHistoryExtendedTableHelper.reloadItems();
                    // }
        });

        ///////////////////////////////////////////////////// END: TABLE BEHAVIOR

    }

    /* view model class, parameters for constructor, container to bind to
     * Please see http://docs.octoprint.org/en/master/plugins/viewmodels.html#registering-custom-viewmodels for more details
     * and a full list of the available options.
     */
    OCTOPRINT_VIEWMODELS.push({
        construct: PrintjobhistoryExtendedViewModel,
        // ViewModels your plugin depends on, e.g. loginStateViewModel, settingsViewModel, ...
        dependencies: [
            "loginStateViewModel",
            "settingsViewModel",
            "accessViewModel"
        ],
        // Elements to bind to, e.g. #settings_plugin_PrintJobHistory, #tab_plugin_PrintJobHistoryExtended, ...
        elements: [
            document.getElementById("tab_printJobHistoryExtended"),
            document.getElementById("settings_printJobHistoryExtended"),
            document.getElementById("modal-dialogs-printJobHistoryExtended")
        ]
    });
});
