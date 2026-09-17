# coding=utf-8
from __future__ import absolute_import

import shutil
import sqlite3
import tempfile
import threading

import octoprint.plugin
from flask import jsonify, request, make_response, Response, send_file
import flask

import json

import os

from datetime import datetime
from datetime import timedelta

from werkzeug.datastructures import Headers

from octoprint.filemanager import FileDestinations

from octoprint_PrintJobHistoryExtended import PrintJobModel, TemperatureModel, FilamentModel, CostModel
from octoprint_PrintJobHistoryExtended.api import TransformPrintJob2JSON, TransformSlicerSettings2JSON

from octoprint_PrintJobHistoryExtended.common import StringUtils
from octoprint_PrintJobHistoryExtended.common import PrintJobUtils

from octoprint_PrintJobHistoryExtended.common.SettingsKeys import SettingsKeys

from octoprint_PrintJobHistoryExtended.CameraManager import CameraManager
from octoprint_PrintJobHistoryExtended.common import CSVExportImporter
from octoprint_PrintJobHistoryExtended.services.SlicerSettingsService import SlicerSettingsService

#############################################################
# Internal API for all Frontend communications
#############################################################



class PrintJobHistoryExtendedAPI(octoprint.plugin.BlueprintPlugin):

    def is_blueprint_csrf_protected(self):
        return True

    def _updatePrintJobFromJson(self, printJobModel,  jsonData):
        # transfer header values
        printJobModel.userName = self._getValueFromJSONOrNone("userName", jsonData)
        printJobModel.fileName = self._getValueFromJSONOrNone("fileName", jsonData)
        # printJobModel.filePathName = self._getValueFromJSONOrNone("fileName", jsonData) # pech
        printJobModel.printStartDateTime = StringUtils.transformToDateTimeOrNone(self._getValueFromJSONOrNone("printStartDateTimeFormatted", jsonData))
        printJobModel.printEndDateTime = StringUtils.transformToDateTimeOrNone(self._getValueFromJSONOrNone("printEndDateTimeFormatted", jsonData))
        printJobModel.duration = self._getValueFromJSONOrNone("duration", jsonData)
        printJobModel.printedHeight = self._getValueFromJSONOrNone("printedHeight", jsonData)
        printJobModel.printedLayers = self._getValueFromJSONOrNone("printedLayers", jsonData)

        # changable...
        printJobModel.printStatusResult = self._getValueFromJSONOrNone("printStatusResult", jsonData)
        printJobModel.noteText = self._getValueFromJSONOrNone("noteText", jsonData)
        printJobModel.noteDeltaFormat = json.dumps(self._getValueFromJSONOrNone("noteDeltaFormat", jsonData))
        printJobModel.noteHtml = self._getValueFromJSONOrNone("noteHtml", jsonData)
        printJobModel.printedLayers = self._getValueFromJSONOrNone("printedLayers", jsonData)
        printJobModel.printedHeight = self._getValueFromJSONOrNone("printedHeight", jsonData)

        filamentModelTotal = printJobModel.getFilamentModelByToolId("total")

        filamentModelTotal.vendor = self._getValueFromJSONOrNone("vendor", jsonData)
        filamentModelTotal.spoolName = self._getValueFromJSONOrNone("spoolName", jsonData)
        filamentModelTotal.material = self._getValueFromJSONOrNone("material", jsonData)
        filamentModelTotal.usedLength = self._convertM2MM(self._getValueFromJSONOrNone("usedLengthFormatted", jsonData))
        filamentModelTotal.calculatedLength = self._convertM2MM(self._getValueFromJSONOrNone("calculatedLengthFormatted", jsonData))
        filamentModelTotal.usedWeight = self._getValueFromJSONOrNone("usedWeight", jsonData)
        filamentModelTotal.usedCost = self._getValueFromJSONOrNone("usedCost", jsonData)

        # temperatureModel = TemperatureModel
        if (printJobModel.databaseId != None):
            allTemperaturesModels = printJobModel.getTemperatureModels()
        else:
            allTemperaturesModels = printJobModel.allTemperatures
        for tempModel in allTemperaturesModels:
            sensorName = StringUtils.to_native_str(tempModel.sensorName)
            if (sensorName == "bed"):
                newBedTemp = self._getValueFromJSONOrNone("temperatureBed", jsonData)
                tempModel.sensorValue = newBedTemp
                continue
            if (sensorName.startswith("tool")):
                newToolTemp = self._getValueFromJSONOrNone("temperatureNozzle", jsonData)
                tempModel.sensorValue = newToolTemp

        # Costs (if present)
        totalCosts = self._toFloatFromJSONOrNone("totalCosts", jsonData)
        filamentCost = self._toFloatFromJSONOrNone("filamentCost", jsonData)
        electricityCost = self._toFloatFromJSONOrNone("electricityCost", jsonData)
        printerCost = self._toFloatFromJSONOrNone("printerCost", jsonData)
        otherCostLabel = self._getValueFromJSONOrNone("otherCostLabel", jsonData)
        otherCost = self._toFloatFromJSONOrNone("otherCost", jsonData)
        withDefaultSpoolValues = self._getValueFromJSONOrNone("withDefaultSpoolValues", jsonData)

        if (totalCosts != None or
            filamentCost != None or
            electricityCost != None or
            printerCost != None or
            otherCost != None):
            # okay, we need to save the data, update or insert
            if (printJobModel.getCosts() == None):
                costs = CostModel()
                printJobModel.setCosts(costs)
            else:
                costs = printJobModel.getCosts()

            costs.totalCosts = totalCosts
            costs.filamentCost = filamentCost
            costs.electricityCost = electricityCost
            costs.printerCost = printerCost
            costs.otherCostLabel = otherCostLabel
            costs.otherCost = otherCost
            costs.withDefaultSpoolValues = withDefaultSpoolValues

        return printJobModel

    def _getValueFromJSONOrNone(self, key, values):
        if key in values:
            return values[key]
        return None

    def _toIntFromJSONOrNone(self, key, json):
        value = self._getValueFromJSONOrNone(key, json)
        if (value != None):
            if (StringUtils.isNotEmpty(value)):
                try:
                    value = int(value)
                except Exception as e:
                    errorMessage = str(e)
                    self._logger.error("could not transform value '"+str(value)+"' for key '"+key+"' to int:" + errorMessage)
                    value = None
            else:
                value = None
        return value

    def _toFloatFromJSONOrNone(self, key, json):
        value = self._getValueFromJSONOrNone(key, json)
        if (value != None):
            if (StringUtils.isNotEmpty(value)):
                try:
                    value = float(value)
                except Exception as e:
                    errorMessage = str(e)
                    self._logger.error("could not transform value '"+str(value)+"' for key '"+key+"' to float:" + errorMessage)
                    value = None
            else:
                value = None
        return value

    #  convert m to mm
    def _convertM2MM(self, value):
        if (value == None or value == ""):
            return 0.0
        floatValue = float(value)
        return floatValue * 1000.0


    def _sendCSVUploadStatusToClient(self, importStatus, currenLineNumber, backupFilePath, backupSnapshotFilePath, successMessages, errorCollection):
        self._sendDataToClient(dict(action="csvImportStatus",
                                    importStatus=importStatus,
                                    currenLineNumber = currenLineNumber,
                                    backupFilePath = backupFilePath,
                                    backupSnapshotFilePath = backupSnapshotFilePath,
                                    successMessages=successMessages,
                                    errorCollection = errorCollection
                                    )
                               )

    def _createSamplePrintModels(self):
        return [
            self._createSamplePrintModel(),
            self._createSamplePrintModel("MyBenchy.gcode"),
            self._createSamplePrintModel("MyThirdBenchy.gcode"),
        ]

    def _createSamplePrintModel(self, fileName="OllisBenchy.gcode"):
        p1 = PrintJobModel()
        p1.userName = "Olli"
        p1.printStatusResult = "success"
        p1.printStartDateTime = datetime.now()
        p1.printEndDateTime = datetime.now() + timedelta(minutes=123)
        p1.duration = (p1.printEndDateTime - p1.printStartDateTime).total_seconds()
        p1.fileName = fileName
        p1.filePathName = "/_archive/OllisBenchy.gcode"
        p1.fileSize = 123456
        p1.printedLayers = "45 / 45"
        p1.printedHeight = "0.4 / 23.8"
        p1.noteText = "Geiles Teil!!"

        t1 = TemperatureModel()
        t1.sensorName = "bed"
        t1.sensorValue = "53"
        p1.addTemperatureModel(t1)
        t2 = TemperatureModel()
        t2.sensorName = "tool0"
        t2.sensorValue = "210"
        p1.addTemperatureModel(t2)

        f1 = FilamentModel()
        f1.toolId = "tool0"
        f1.vendor = "Ollis-Factory"
        f1.spoolName = "My best spool"
        f1.material = "PLA"
        f1.diameter = 1.75
        f1.density = 1.24
        f1.usedLength = 1345.0
        f1.calculatedLength = 1456.0
        f1.usedWeight = 321.0
        f1.usedCost = 1.34
        p1.addFilamentModel(f1)

        f2 = FilamentModel()
        f2.toolId = "total"
        f2.vendor = "Ollis-Factory"
        f2.spoolName = "My best spool"
        f2.material = "PLA"
        f2.diameter = 1.75
        f2.density = 1.24
        f2.usedLength = 1345.0
        f2.calculatedLength = 1456.0
        f2.usedWeight = 321.0
        f2.usedCost = 1.34
        p1.addFilamentModel(f2)

        c1 = CostModel()
        c1.filamentCost = 3.123
        c1.electricityCost = 0.89
        c1.printerCost = 1.234
        c1.otherCostLabel = "Delivery"
        c1.otherCost = 22.67
        c1.withDefaultSpoolValues = True
        p1.setCosts(c1)

        return p1

################################################### APIs


    #######################################################################################   CONFIRM MESSAGE
    @octoprint.plugin.BlueprintPlugin.route("/confirmMessageDialog", methods=["PUT"])
    def put_confirmMessageDialog(self):
        self._settings.set([SettingsKeys.SETTINGS_KEY_MESSAGE_CONFIRM_DATA], None)
        self._settings.save()

        return flask.jsonify([])

    #######################################################################################   DEACTIVATE PLUGIN CHECK
    @octoprint.plugin.BlueprintPlugin.route("/deactivatePluginCheck", methods=["PUT"])
    def put_pluginDependencyCheck(self):
        self._settings.setBoolean([SettingsKeys.SETTINGS_KEY_PLUGIN_DEPENDENCY_CHECK], False)
        self._settings.save()

        return flask.jsonify([])

    #######################################################################################   LOAD STATISTIC BY QUERY
    @octoprint.plugin.BlueprintPlugin.route("/loadStatisticByQuery", methods=["GET"])
    def get_statisticByQuery(self):

        tableQuery = flask.request.values
        statistic = self._databaseManager.calculatePrintJobsStatisticByQuery(tableQuery)

        return flask.jsonify(statistic)

    #######################################################################################   COMPARE Slicer Settings
    @octoprint.plugin.BlueprintPlugin.route("/compareSlicerSettings/", methods=["GET"])
    def get_compareSlicerSettings(self):

        if "databaseIds" in flask.request.values:
            selectedDatabaseIds = flask.request.values["databaseIds"]

            # selectedDatabaseIds = "21, 17"
            allJobsModels = self._databaseManager.loadSelectedPrintJobs(selectedDatabaseIds)

            slicerSettingssJobToCompareList = []
            for job in allJobsModels:
                settingsForCompare = SlicerSettingsService.SlicerSettingsJob()
                settingsForCompare.databaseId = job.databaseId
                settingsForCompare.fileName = job.fileName
                settingsForCompare.slicerSettingsAsText = job.slicerSettingsAsText

                slicerSettingssJobToCompareList.append(settingsForCompare)

            slicerService = SlicerSettingsService(self._logger)
            slicerSettingsExpressions = self._settings.get([SettingsKeys.SETTINGS_KEY_SLICERSETTINGS_KEYVALUE_EXPRESSION])
            if (slicerSettingsExpressions != None and len(slicerSettingsExpressions) != 0):
                compareResult = slicerService.compareSlicerSettings(slicerSettingssJobToCompareList, slicerSettingsExpressions)
                compoareResultAsJson = TransformSlicerSettings2JSON.transformSlicerSettingsCompareResult(compareResult)

                return flask.jsonify(compoareResultAsJson)

        return flask.jsonify()

    #######################################################################################   LOAD ALL JOBS BY QUERY
    @octoprint.plugin.BlueprintPlugin.route("/loadPrintJobHistoryByQuery", methods=["GET"])
    def get_printjobhistoryextendedByQuery(self):

        tableQuery = flask.request.values
        allJobsModels = self._databaseManager.loadPrintJobsByQuery(tableQuery)
        # allJobsAsDict = self._convertPrintJobHistoryModelsToDict(allJobsModels)
        # selectedFile = self._file_manager.path_on_disk(fileLocation, selectedFilename)
        allJobsAsDict = TransformPrintJob2JSON.transformAllPrintJobModels(allJobsModels, self._file_manager)

        totalItemCount = self._databaseManager.countPrintJobsByQuery(tableQuery)
        return flask.jsonify({
                                "totalItemCount": totalItemCount,
                                "allPrintJobs": allJobsAsDict
                            })

    #######################################################################################   SELECT JOB FOR PRINTING
    @octoprint.plugin.BlueprintPlugin.route("/selectPrintJobForPrint/<int:databaseId>", methods=["PUT"])
    def put_select_printjob(self, databaseId):

        printJobModel = self._databaseManager.loadPrintJob(databaseId);
        if (printJobModel == None):
            # PrintJob was deleted
            message = "PrintJob not in database anymore! Selection not possible."
            self._logger.error(message)
            self._sendDataToClient(dict(action="errorPopUp",
                                        title="Print selection not possible",
                                        message=message))
            return flask.jsonify()

        printJobPrintable = PrintJobUtils.isPrintJobReprintable(self._file_manager,
                                                                printJobModel.fileOrigin,
                                                                printJobModel.filePathName,
                                                                printJobModel.fileName)
        fullFileLocation = printJobPrintable["fullFileLocation"]
        if (printJobPrintable["isRePrintable"] == False):
            message = "PrintJob not found in: " +fullFileLocation
            self._logger.error(message)
            self._sendDataToClient(dict(action="errorPopUp",
                                        title="Print selection not possible",
                                        message=message))
            return flask.jsonify()
        sd = False if (printJobModel.fileOrigin != None and printJobModel.fileOrigin == "local") else True
        self._printer.select_file(fullFileLocation, sd)

        return flask.jsonify()

    #######################################################################################   DELETE JOB
    @octoprint.plugin.BlueprintPlugin.route("/removePrintJob/<int:databaseId>", methods=["DELETE"])
    def delete_printjob(self, databaseId):

        allJobsModels = []
        if "databaseIds" in flask.request.values:
            selectedDatabaseIds = flask.request.values["databaseIds"]
            allJobsModels = self._databaseManager.loadSelectedPrintJobs(selectedDatabaseIds)
        else:
            allJobsModels.append(self._databaseManager.loadPrintJob(databaseId))

        for printJob in allJobsModels:
            if (printJob == None):
                continue
            self._databaseManager.deletePrintJob(printJob.databaseId)
            snapshotFilename = CameraManager.buildSnapshotFilename(printJob.printStartDateTime)
            self._cameraManager.deleteSnapshot(snapshotFilename)

        return flask.jsonify()

    #######################################################################################   UPDATE JOB
    @octoprint.plugin.BlueprintPlugin.route("/storePrintJob/<databaseId>", methods=["PUT"])
    def put_printjob(self, databaseId):
        jsonData = request.json

        printJobModel = None
        oldStartDateTimeIfChanged = None
        newStartDateTime = None
        if (databaseId == 'null'):
            printJobModel = PrintJobModel()

            filemanentModel = FilamentModel()
            filemanentModel.toolId = "total"
            printJobModel.addFilamentModel(filemanentModel)

            tempModel = TemperatureModel()
            tempModel.sensorName = "bed"
            printJobModel.addTemperatureModel(tempModel)

            tempModel = TemperatureModel()
            tempModel.sensorName = self._settings.get([SettingsKeys.SETTINGS_KEY_DEFAULT_TOOL_ID])
            printJobModel.addTemperatureModel(tempModel)

        else:
            printJobModel = self._databaseManager.loadPrintJob(databaseId)
            # check if the startDate is changed, if true -> change snapshot image as well
            currentStartDateTime = printJobModel.printStartDateTime
            newStartDateTime = StringUtils.transformToDateTimeOrNone(self._getValueFromJSONOrNone("printStartDateTimeFormatted", jsonData))
            changed = currentStartDateTime != newStartDateTime
            if (changed):
                oldStartDateTimeIfChanged = currentStartDateTime
                try:
                    self._cameraManager.renameSnapshotFilename(oldStartDateTimeIfChanged, newStartDateTime)
                except (Exception) as error:
                    self._logger.error(error)
                    message = errorMessage = str(error)
                    self._sendDataToClient(dict(action="errorPopUp",
                                                title= "could not rename snapshot image to new startdatetime",
                                                message=message))


        def imageRollbackHandler():
            if (oldStartDateTimeIfChanged != None):
                # do rollback on snapshotimage filename
                try:
                    self._cameraManager.renameSnapshotFilename(newStartDateTime, oldStartDateTimeIfChanged)
                except (Exception) as error:
                    self._logger.error(error)
                    message = errorMessage = str(error)
                    self._sendDataToClient(dict(action="errorPopUp",
                                                title= "could not rename snapshot image to new startdatetime",
                                                message=message))
                pass
            pass


        # transfer values from ui to the model
        self._updatePrintJobFromJson(printJobModel, jsonData)

        if (databaseId == 'null'):
            self._databaseManager.insertPrintJob(printJobModel)
        else:

            self._databaseManager.updatePrintJob(printJobModel, imageRollbackHandler)

        return flask.jsonify()

    #######################################################################################   FORCE CLOSE EDIT DIALOG
    @octoprint.plugin.BlueprintPlugin.route("/forceCloseEditDialog", methods=["PUT"])
    def put_forceCloseEditDialog(self):

        # Inform all Browser to close the EditDialog
        self._sendDataToClient(dict(action="closeEditDialog"))

        return flask.jsonify()

    #######################################################################################   GET SNAPSHOT
    @octoprint.plugin.BlueprintPlugin.route("/printJobSnapshot/<string:snapshotFilename>", methods=["GET"])
    def get_snapshot(self, snapshotFilename):
        absoluteFilename = self._cameraManager.buildSnapshotFilenameLocation(snapshotFilename)
        return send_file(absoluteFilename, mimetype='image/png', max_age=86400)

    #######################################################################################   TAKE SNAPSHOT
    @octoprint.plugin.BlueprintPlugin.route("/takeSnapshot/<string:snapshotFilename>", methods=["PUT"])
    def put_snapshot(self, snapshotFilename):
        self._cameraManager.takeSnapshot(snapshotFilename, self._sendErrorMessageToClient)
        return flask.jsonify({
            "snapshotFilename": snapshotFilename
        })

    #######################################################################################   UPLOAD SNAPSHOT
    @octoprint.plugin.BlueprintPlugin.route("/upload/snapshot/<string:snapshotFilename>", methods=["POST"])
    def post_snapshot(self, snapshotFilename):

        input_name = "file"
        input_upload_path = input_name + "." + self._settings.global_get(["server", "uploads", "pathSuffix"])

        if input_upload_path in flask.request.values:
            # file was uploaded
            sourceLocation = flask.request.values[input_upload_path]
            targetLocation = self._cameraManager.buildSnapshotFilenameLocation(snapshotFilename, False)
            # os.rename(sourceLocation, targetLocation)
            shutil.move(sourceLocation, targetLocation)
            pass

        return flask.jsonify({
            "snapshotFilename": snapshotFilename
        })  \

    #######################################################################################   DELETE SNAPSHOT
    @octoprint.plugin.BlueprintPlugin.route("/deleteSnapshotImage/<string:snapshotFilename>", methods=["DELETE"])
    def delete_snapshot(self, snapshotFilename):

        self._cameraManager.deleteSnapshot(snapshotFilename)

        return flask.jsonify({
            "snapshotFilename": snapshotFilename
        })


    #######################################################################################   DOWNLOAD DATABASE-FILE
    @octoprint.plugin.BlueprintPlugin.route("/downloadDatabase", methods=["GET"])
    def get_download_database(self):
        # There is no local database file to hand out when an external database is in use.
        if (self._databaseManager.isExternalDatabase() == True):
            return flask.make_response(flask.jsonify({
                "error": "An external database is in use. Back it up with the database "
                         "server's own tools (e.g. mysqldump)."
            }), 409)

        return send_file(self._databaseManager.getDatabaseFileLocation(),
                         mimetype='application/octet-stream',
                         download_name='printJobHistoryExtended.db',
                         as_attachment=True)


    #######################################################################################   DELETE DATABASE
    @octoprint.plugin.BlueprintPlugin.route("/deleteDatabase", methods=["DELETE"])
    def delete_database(self):

        self._databaseManager.reCreateDatabase()

        return flask.jsonify({
            "result": "success"
        })


    #######################################################################################   TEST DATABASE CONNECTION
    @octoprint.plugin.BlueprintPlugin.route("/testDatabaseConnection", methods=["PUT"])
    def put_test_database_connection(self):
        jsonData = request.json
        databaseSettings = self._buildDatabaseSettingsFromJson(jsonData)

        errorMessage = self._databaseManager.testDatabaseConnection(databaseSettings)
        if (errorMessage != None):
            return flask.jsonify({
                "metadata": {
                    "success": False,
                    "errorMessage": errorMessage
                }
            })

        # The connection works, so report the scheme version and job count in the same round trip.
        metaData = self._databaseManager.loadDatabaseMetaInformations(databaseSettings)
        return flask.jsonify({
            "metadata": metaData
        })


    #######################################################################################   LOAD DATABASE METADATA
    @octoprint.plugin.BlueprintPlugin.route("/loadDatabaseMetaData", methods=["GET"])
    def get_database_metadata(self):
        return flask.jsonify({
            "metadata": self._databaseManager.loadDatabaseMetaInformations(None)
        })


    #######################################################################################   COPY DATABASE
    @octoprint.plugin.BlueprintPlugin.route("/copyDatabase", methods=["POST"])
    def post_copy_database(self):
        jsonData = request.json
        databaseSettings = self._buildDatabaseSettingsFromJson(jsonData)

        result = self._databaseManager.copyPrintJobDataToExternalDatabase(databaseSettings)
        return flask.jsonify({
            "metadata": result
        })


    #######################################################################################   UPGRADE DATABASE SCHEME
    @octoprint.plugin.BlueprintPlugin.route("/upgradeDatabaseScheme", methods=["PUT"])
    def put_upgrade_database_scheme(self):
        return flask.jsonify({
            "metadata": self._databaseManager.upgradeDatabaseScheme()
        })


    #######################################################################################   KNOWN INSTANCES
    @octoprint.plugin.BlueprintPlugin.route("/loadKnownInstances", methods=["GET"])
    def get_known_instances(self):
        return flask.jsonify({
            "instanceNames": self._databaseManager.loadKnownInstanceNames(),
            "currentInstanceName": self._databaseManager.getInstanceName()
        })


    def _buildDatabaseSettingsFromJson(self, jsonData):
        """Map the JSON sent by the settings dialog onto a DatabaseSettings carrier."""
        from octoprint_PrintJobHistoryExtended.DatabaseManager import DatabaseManager

        databaseSettings = DatabaseManager.DatabaseSettings()
        databaseSettings.useExternal = self._getValueFromJSONOrNone(
            SettingsKeys.SETTINGS_KEY_DATABASE_USE_EXTERNAL, jsonData) == True
        databaseSettings.type = self._getValueFromJSONOrNone(
            SettingsKeys.SETTINGS_KEY_DATABASE_TYPE, jsonData)
        databaseSettings.host = self._getValueFromJSONOrNone(
            SettingsKeys.SETTINGS_KEY_DATABASE_HOST, jsonData)
        databaseSettings.name = self._getValueFromJSONOrNone(
            SettingsKeys.SETTINGS_KEY_DATABASE_NAME, jsonData)
        databaseSettings.user = self._getValueFromJSONOrNone(
            SettingsKeys.SETTINGS_KEY_DATABASE_USER, jsonData)
        databaseSettings.password = self._getValueFromJSONOrNone(
            SettingsKeys.SETTINGS_KEY_DATABASE_PASSWORD, jsonData)

        port = self._getValueFromJSONOrNone(SettingsKeys.SETTINGS_KEY_DATABASE_PORT, jsonData)
        portAsInt = StringUtils.transformToIntOrNone(port)
        databaseSettings.port = portAsInt if portAsInt != None else 3306

        databaseSettings.fileLocation = self._databaseManager.getDatabaseFileLocation()
        return databaseSettings

    #######################################################################################   EXPORT DATABASE as CSV
    @octoprint.plugin.BlueprintPlugin.route("/exportPrintJobHistory/<string:exportType>", methods=["GET"])
    def get_exportPrintJobHistoryData(self, exportType):

        if exportType == "CSV":
            if "databaseIds" in flask.request.values:
                selectedDatabaseIds = flask.request.values["databaseIds"]
                allJobsModels = self._databaseManager.loadSelectedPrintJobs(selectedDatabaseIds)
            elif "instanceName" in flask.request.values:
                # Export what the table currently shows, so the instance filter is respected
                # instead of silently exporting every instance's jobs.
                tableQuery = {
                    "sortColumn": flask.request.values.get("sortColumn", "printStartDateTime"),
                    "sortOrder": flask.request.values.get("sortOrder", "desc"),
                    "filterName": flask.request.values.get("filterName", "all"),
                    "instanceName": flask.request.values.get("instanceName", "all"),
                    "from": 0,
                    "to": 999999
                }
                allJobsModels = self._databaseManager.loadPrintJobsByQuery(tableQuery)
            else:
                allJobsModels = self._databaseManager.loadAllPrintJobs()

            return Response(CSVExportImporter.transform2CSV(allJobsModels),
                            mimetype='text/csv',
                            headers={'Content-Disposition': 'attachment; filename=OctoprintPrintJobHistory.csv'}) # TODO add timestamp

        else:
            if (exportType == "legacyPrintHistory"):
                return self.exportPrintHistoryData()

            print("BOOOMM not supported type")
        pass


    @octoprint.plugin.BlueprintPlugin.route("/sampleCSV", methods=["GET"])
    def get_sampleCSV(self):

        allJobsModels = list()

        printModel = self._createSamplePrintModel()
        allJobsModels.append(printModel)

        # allJobsModels = self._databaseManager.loadAllPrintJobs()
        # allJobsDict = TransformPrintJob2JSON.transformAllPrintJobModels(allJobsModels)
        # csvContent = Transform2CSV.transform2CSV(allJobsDict)
        # csvContent = CSVExportImporter.transform2CSV(allJobsModels)



        return Response(CSVExportImporter.transform2CSV(allJobsModels),
                        mimetype='text/csv',
                        headers={'Content-Disposition': 'attachment; filename=PrintJobHistory-SAMPLE.csv'})

    ######################################################################################   UPLOAD CSV FILE (in Thread)

    def _processCSVUploadAsync(self, path, importCSVMode, databaseManager, cameraManager, backupFolder, sendCSVUploadStatusToClient, logger):
        errorCollection = list()

        # - parsing
        # - backup
        # - append or replace

        def updateParsingStatus(lineNumber):
            # importStatus, currenLineNumber, backupFilePath, backupSnapshotFilePath, successMessages, errorCollection
            sendCSVUploadStatusToClient("running", lineNumber, "", "", "", errorCollection)


        resultOfPrintJobs = CSVExportImporter.parseCSV(path, updateParsingStatus, errorCollection, logger)

        if (len(errorCollection) != 0):
            successMessage = "Some error(s) occurs during parsing! No jobs imported!"
            sendCSVUploadStatusToClient("finished", "", "", "", successMessage, errorCollection)
            return

        importModeText = "append"
        backupDatabaseFilePath = None
        backupSnapshotFilePath = None
        if (len(resultOfPrintJobs) > 0):
            # we could import some jobs

            # - backup
            backupDatabaseFilePath = databaseManager.backupDatabaseFile(backupFolder)
            backupSnapshotFilePath = cameraManager.backupAllSnapshots(backupFolder)

            # - import mode append/replace
            if (SettingsKeys.KEY_IMPORTCSV_MODE_REPLACE == importCSVMode):
                # delete old database and init a clean database
                databaseManager.reCreateDatabase()
                cameraManager.reCreateSnapshotFolder()

                importModeText = "fully replaced"

            # - insert all printjobs in database.
            #   One transaction for the whole import: a transaction per row means a network
            #   round trip per row, which is unusable against an external database.
            currentPrintJobNumber = 0
            with databaseManager.getDatabase().atomic():
                for printJob in resultOfPrintJobs:
                    currentPrintJobNumber = currentPrintJobNumber + 1
                    updateParsingStatus(currentPrintJobNumber)
                    databaseManager.insertPrintJob(printJob)
                    # print(printJob)
            pass
        else:
            errorCollection.append("Nothing to import!")

        successMessage = ""
        if (len(errorCollection) == 0):
            successMessage = "All data is successful " + importModeText + " with '" + str(len(resultOfPrintJobs)) + "' print jobs."
        else:
            successMessage = "Some error(s) occurs! Maybe you need to manually rollback the database!"

        if (backupDatabaseFilePath == None):
            # No file backup exists when an external database is in use.
            backupDatabaseFilePath = ""

        sendCSVUploadStatusToClient("finished","", backupDatabaseFilePath, backupSnapshotFilePath, successMessage, errorCollection)
        pass


    @octoprint.plugin.BlueprintPlugin.route("/importCSV", methods=["POST"])
    def post_csvUpload(self):

        input_name = "file"
        input_upload_path = input_name + "." + self._settings.global_get(["server", "uploads", "pathSuffix"])

        if input_upload_path in flask.request.values:

            importMode = flask.request.form["importCSVMode"]
            # file was uploaded
            sourceLocation = flask.request.values[input_upload_path]

            # because we process in seperate thread we need to create our own temp file, the uploaded temp file will be deleted after this request-call
            archive = tempfile.NamedTemporaryFile(delete=False)
            archive.close()
            shutil.copy(sourceLocation, archive.name)
            sourceLocation = archive.name


            thread = threading.Thread(target=self._processCSVUploadAsync,
                                      args=(sourceLocation,
                                            importMode,
                                            self._databaseManager,
                                            self._cameraManager,
                                            self.get_plugin_data_folder(),
                                            self._sendCSVUploadStatusToClient,
                                            self._logger))
            thread.daemon = True
            thread.start()

            # targetLocation = self._cameraManager.buildSnapshotFilenameLocation(snapshotFilename, False)
            # os.rename(sourceLocation, targetLocation)
            pass
        else:
            return flask.make_response("Invalid request, neither a file nor a path of a file to restore provided", 400)


        return flask.jsonify(started=True)

    ###################################################################################### EXPORT of PrintHistory.db - Plugin
    # @octoprint.plugin.BlueprintPlugin.route("/exportPrintHistory", methods=["GET"])
    def exportPrintHistoryData(self):

        allJobsModels = list()

        history_db_path = self.get_plugin_data_folder()+"/../printhistory/history.db"

        conn = sqlite3.connect(history_db_path)
        cur = conn.cursor()
        cur.execute("SELECT * FROM print_history ORDER BY timestamp")

        for row in cur.fetchall():
            printJob = PrintJobModel()
            filamentModel = FilamentModel()
            isFilamentValuesPresent = False
            for i, value in enumerate(row):
                # id, fileName, note, spool, filamentVolume (float), filamentLength (float), printTime (float), success (int), timestamp (float), user (unicode), parameters (json-string)
                fieldName = cur.description[i][0]
                if (fieldName == "fileName"):
                    printJob.fileName = value
                    continue
                if (fieldName == "note"):
                    printJob.noteText = value
                    continue
                if (fieldName == "spool"):
                    isFilamentValuesPresent = True
                    filamentModel.spoolName = value
                    continue
                if (fieldName == "filamentVolume"):
                    # isFilamentValuesPresent = True
                    # filamentModel.spoolName = value
                    continue # just ignore
                if (fieldName == "filamentLength"):
                    isFilamentValuesPresent = True
                    filamentModel.usedLength = value
                    filamentModel.calculatedLength = value
                    continue
                if (fieldName == "printTime"):
                    printJob.duration = int(value)
                    continue
                if (fieldName == "success"):
                    if (value == 1):
                        printJob.printStatusResult = "success"
                    else:
                        printJob.printStatusResult = "failed"
                    continue
                if (fieldName == "timestamp"):
                    startPrintDataTime = datetime.fromtimestamp(value)
                    printJob.printStartDateTime = startPrintDataTime
                    continue
                if (fieldName == "user"):
                    printJob.userName = value
                    continue

                if (fieldName == "parameters"):
                    continue # just ignore
                pass

            if (isFilamentValuesPresent == True):
                filamentModel.toolId = "total"
                printJob.addFilamentModel(filamentModel)

            # Calculate endtime
            endDateTime = printJob.printStartDateTime + timedelta(seconds=printJob.duration)
            printJob.printEndDateTime = endDateTime

            allJobsModels.append(printJob)
        # history_dict = [dict(
        #                       (cur.description[i][0], value) \
        #                    for i, value in enumerate(row)
        #                   ) for row in cur.fetchall()]

        conn.close()

        return Response(CSVExportImporter.transform2CSV(allJobsModels),
                        mimetype='text/csv',
                        headers={'Content-Disposition': 'attachment; filename=PrintHistory.csv'})

    ###################################################################################### PRINTJOB - REPORT
    @octoprint.plugin.BlueprintPlugin.route("/singlePrintJobReport/<databaseId>", methods=["GET"])
    def get_createSinglePrintJobReport(self, databaseId):

        if (databaseId == "sample"):
            printJobModel = self._createSamplePrintModel()
        else:
            printJobModel = self._databaseManager.loadPrintJob(databaseId)

        if (printJobModel == None):
            # PrintJob was deleted
            message = "PrintJob not in database anymore! PrintReport not possible."
            self._logger.error(message)
            self._sendDataToClient(dict(action="errorPopUp",
                                        title="Print selection not possible",
                                        message=message))
            return flask.jsonify()

        reportHtmlTemplate = self._loadPrintJobReportTemplateContent("single")

        printJobModelAsJson=TransformPrintJob2JSON.transformPrintJobModel(printJobModel, self._file_manager, False)
        # printJobModelAsJson = {
        #   "Hallo": "du"
        # }
        printJobModelAsJsonString = json.dumps(printJobModelAsJson, indent=1, sort_keys=True, default=str)
        printJobModelAsJsonDict = json.loads(printJobModelAsJsonString)
        # print(printJobModelAsJsonString)
        # send rendered report to browser
        return Response(
                        # flask.render_template("singlePrintJobReport.jinja2"),
                        flask.render_template_string(reportHtmlTemplate,
                                                     reportCreationTime=datetime.now(),
                                                     printJobModel=printJobModel,
                                                     hallo="welt",
                                                     printJobModelAsJson=printJobModelAsJsonDict
                                                     ),
                        mimetype='text/html'
                        # headers={'Content-Disposition': 'attachment; filename=PrintJobHistory-SAMPLE.csv'}
                        )

    @octoprint.plugin.BlueprintPlugin.route("/multiPrintJobReport", methods=["GET"])
    def get_createMultiPrintJobReport(self):

        tableQuery = flask.request.values.to_dict()
        allPrintJobModels = []
        if ("sample" in tableQuery):
            allPrintJobModels = self._createSamplePrintModels()
        else:
            # tableQuery or muliple databaseIds
            if ("databaseIds" in tableQuery):
                selectedDatabaseIds = tableQuery["databaseIds"]
                # selectedDatabaseIds = "21, 17"
                allPrintJobModels = self._databaseManager.loadSelectedPrintJobs(selectedDatabaseIds)
            else:
                # always load all print jobs
                tableQuery["from"] = 0
                tableQuery["to"] = 99999
                allPrintJobModels = self._databaseManager.loadPrintJobsByQuery(tableQuery)

        if (len(allPrintJobModels) == 0):
            # PrintJob was deleted
            message = "PrintJobs not in database anymore! PrintReport not possible."
            self._logger.error(message)
            self._sendDataToClient(dict(action="errorPopUp",
                                        title="Print selection not possible",
                                        message=message))
            return flask.jsonify()

        # build mulit-page report
        reportHtmlTemplate = self._loadPrintJobReportTemplateContent("multi")

        allJobsAsDict = TransformPrintJob2JSON.transformAllPrintJobModels(allPrintJobModels, self._file_manager, False)

        # allPrintJobModelAsJson = TransformPrintJob2JSON.transformPrintJobModel(printJobModel, self._file_manager, False)
        # printJobModelAsJson = {
        #   "Hallo": "du"
        # }
        printJobModelAsJsonString = json.dumps(allJobsAsDict, indent=1, sort_keys=True, default=str)
        printJobModelAsJsonDict = json.loads(printJobModelAsJsonString)
        # print(printJobModelAsJsonString)
        # send rendered report to browser
        return Response(
                        # flask.render_template("singlePrintJobReport.jinja2"),
                        flask.render_template_string(reportHtmlTemplate,
                                                     reportCreationTime = datetime.now(),
                                                     allPrintJobModels = allPrintJobModels,
                                                     hallo = "welt",
                                                     printJobModelAsJson = printJobModelAsJsonDict
                                                     ),
                        mimetype='text/html'
                        # headers={'Content-Disposition': 'attachment; filename=PrintJobHistory-SAMPLE.csv'}
                        )

    ################################################################################ PRINTJOB - UPLOAD REPORT Template
    @octoprint.plugin.BlueprintPlugin.route("/uploadPrintJobReport/<reportType>", methods=["POST"])
    def post_uploadPrintJobReportTemplate(self, reportType):

        result = True
        targetFilename = "unknown"
        input_name = "file"
        input_upload_path = input_name + "." + self._settings.global_get(["server", "uploads", "pathSuffix"])


        if input_upload_path in flask.request.values:

            # file was uploaded
            try:
                # source
                sourceLocation = flask.request.values[input_upload_path]
                # setup target
                now = datetime.now()
                currentDate = now.strftime("%Y%m%d-%H%M")
                # "singleReportTemplate-20210604.jinja2"
                if (reportType == "multi"):
                    targetFilename = "multiReportTemplate-"+currentDate + ".jinja2"
                else:
                    targetFilename = "singleReportTemplate-" + currentDate + ".jinja2"
                targetLocation = self._getPrintJobReportTemplateLocation(targetFilename)
                shutil.move(sourceLocation, targetLocation)

                if (reportType == "multi"):
                    self._settings.set([SettingsKeys.SETTINGS_KEY_MULTI_PRINTJOB_REPORT_TEMPLATENAME], targetFilename)
                else:
                    self._settings.set([SettingsKeys.SETTINGS_KEY_SINGLE_PRINTJOB_REPORT_TEMPLATENAME], targetFilename)
                self._settings.save()
            except Exception as e:
                errorMessage = "Error during upload report template !!!! See log file."
                self._logger.error(errorMessage)
                self._logger.exception(e)

                self._sendDataToClient(dict(action="errorPopUp",
                                            title="Could not upload Report Template",
                                            message=errorMessage))
                result = False

        return flask.jsonify(
            printJobTemplateName=targetFilename,
            result=result
        )


    ################################################################################ PRINTJOB - DWONLOAD REPORT Template
    @octoprint.plugin.BlueprintPlugin.route("/downloadPrintJobReportTemplate/<reportType>", methods=["GET"])
    def get_download_printJobReportTemplate(self, reportType):
        '''
        :param reportType: single or multi
        :return:
        '''
        reportHtmlTemplate = self._loadPrintJobReportTemplateContent(reportType)
        # send rendered report to browser
        return Response(
                        reportHtmlTemplate,
                        mimetype='text/html',
                        headers={'Content-Disposition': 'attachment; filename='+reportType+'PrintJobReport-Template.jinja2'}
                        )

    def _loadPrintJobReportTemplateContent(self, reportType):
        reportHtmlTemplate = "<h1>Something was wrong!!! Please take a look into the octoprint.log</h1>"
        try:
            pluginBaseFolder = self._basefolder
            currentReportTemplate = ""
            if (reportType == "single"):
                currentReportTemplate = self._settings.get([SettingsKeys.SETTINGS_KEY_SINGLE_PRINTJOB_REPORT_TEMPLATENAME])
                if (SettingsKeys.SETTINGS_DEFAULT_VALUE_SINGLE_PRINTJOB_REPORT_TEMPLATENAME == currentReportTemplate):
                    # load defaultReportTemplate
                    reportTemplateLocation = pluginBaseFolder + "/templates/PrintJobHistoryExtended_"+SettingsKeys.SETTINGS_DEFAULT_VALUE_SINGLE_PRINTJOB_REPORT_TEMPLATENAME+".jinja2"
                else:
                    # load custom template
                    reportTemplateLocation = self._getPrintJobReportTemplateLocation(currentReportTemplate)
            else:
                currentReportTemplate = self._settings.get([SettingsKeys.SETTINGS_KEY_MULTI_PRINTJOB_REPORT_TEMPLATENAME])
                if (SettingsKeys.SETTINGS_DEFAULT_VALUE_MULTI_PRINTJOB_REPORT_TEMPLATENAME == currentReportTemplate):
                    # load defaultReportTemplate
                    reportTemplateLocation = pluginBaseFolder + "/templates/PrintJobHistoryExtended_"+SettingsKeys.SETTINGS_DEFAULT_VALUE_MULTI_PRINTJOB_REPORT_TEMPLATENAME+".jinja2"
                else:
                    # load custom template
                    reportTemplateLocation = self._getPrintJobReportTemplateLocation(currentReportTemplate)

            # read template
            file = open(reportTemplateLocation)
            reportHtmlTemplate  = file.read()
            file.close()
        except Exception as e:
            self._logger.error("Error during loading the report template !!!!")
            self._logger.exception(e)

        return reportHtmlTemplate

    def _savePrintJobReportTemplateContent(self, templateFilename, templateContent):
        reportTemplateLocation = self._getPrintJobReportTemplateLocation(templateFilename)
        file = open(reportTemplateLocation)
        file.write(templateContent)
        file.close()

    def _getPrintJobReportTemplateLocation(self, reportTemplateFilename):
        pluginDataBaseFolder = self.get_plugin_data_folder()
        reportFolderName = "/reportTemplates/"
        reportFolderLocation = pluginDataBaseFolder + reportFolderName
        if not os.path.exists(reportFolderLocation):
            os.makedirs(reportFolderLocation)
        reportTemplateLocation = reportFolderLocation + reportTemplateFilename
        return reportTemplateLocation

    ################################################################################ PRINTJOB - RESET REPORT Template
    @octoprint.plugin.BlueprintPlugin.route("/resetPrintJobReportTemplate/<reportType>", methods=["PUT"])
    def put_confirmMessageDialog(self, reportType):
        defaultReportTemplateName = ""

        if (reportType == "multi"):
            self._settings.set([SettingsKeys.SETTINGS_KEY_MULTI_PRINTJOB_REPORT_TEMPLATENAME],
                                SettingsKeys.SETTINGS_DEFAULT_VALUE_MULTI_PRINTJOB_REPORT_TEMPLATENAME)
            defaultReportTemplateName = SettingsKeys.SETTINGS_DEFAULT_VALUE_MULTI_PRINTJOB_REPORT_TEMPLATENAME
        else:
            self._settings.set([SettingsKeys.SETTINGS_KEY_SINGLE_PRINTJOB_REPORT_TEMPLATENAME],
                                SettingsKeys.SETTINGS_DEFAULT_VALUE_SINGLE_PRINTJOB_REPORT_TEMPLATENAME)
            defaultReportTemplateName = SettingsKeys.SETTINGS_DEFAULT_VALUE_SINGLE_PRINTJOB_REPORT_TEMPLATENAME

        self._settings.save()


        return flask.jsonify(
            result=True,
            printJobTemplateName=defaultReportTemplateName
        )


    #######################################################################################   LEGACY MIGRATION
    # Adopting the data of a previous "PrintJobHistory" install. Implemented in the plugin
    # class; these routes only expose it.

    @octoprint.plugin.BlueprintPlugin.route("/legacyMigrationStatus", methods=["GET"])
    def get_legacyMigrationStatus(self):
        legacyDataFolder = self._getLegacyDataFolder()
        return flask.jsonify(
            available=self._isLegacyMigrationAvailable(),
            done=self._isLegacyMigrationDone(),
            hasSettings=self._hasLegacySettings(),
            dataFolder=legacyDataFolder,
            files=self._getLegacyFileEntries(),
            databaseUndoAvailable=self._isLegacyMigrationUndoAvailable("database"),
            settingsUndoAvailable=self._isLegacyMigrationUndoAvailable("settings"),
            restartRequired=self._isRestartRequired()
        )


    @octoprint.plugin.BlueprintPlugin.route("/legacyDatabasePreview", methods=["GET"])
    def get_legacyDatabasePreview(self):
        legacyDataFolder = self._getLegacyDataFolder()
        if legacyDataFolder is None:
            return flask.jsonify(readable=False, jobCount=0, jobs=[], moreJobs=0)
        databaseFile = os.path.join(legacyDataFolder, "printJobHistory.db")
        return flask.jsonify(self._readLegacyDatabasePreview(databaseFile))


    @octoprint.plugin.BlueprintPlugin.route("/legacySettingsComparison", methods=["GET"])
    def get_legacySettingsComparison(self):
        return flask.jsonify(settings=self._getLegacySettingsComparison())


    @octoprint.plugin.BlueprintPlugin.route("/migrateFromLegacy", methods=["PUT"])
    def put_migrateFromLegacy(self):
        jsonData = request.json if request.is_json else {}
        result = self._performLegacyMigration(
            overwriteExisting=jsonData.get("overwriteExisting", False),
            includeSettings=jsonData.get("includeSettings", True),
            fileNames=jsonData.get("fileNames", None)
        )
        # a conflict is a decision for the user to make, not a server error
        return flask.jsonify(result), (200 if result["success"] else 409 if result.get("conflict") else 500)


    @octoprint.plugin.BlueprintPlugin.route("/applyLegacySettings", methods=["PUT"])
    def put_applyLegacySettings(self):
        jsonData = request.json if request.is_json else {}
        keys = jsonData.get("keys", [])
        result = self._applyLegacySettings(keys)
        return flask.jsonify(result), (200 if result["success"] else 400)


    @octoprint.plugin.BlueprintPlugin.route("/undoLegacyMigration/<undoKind>", methods=["PUT"])
    def put_undoLegacyMigration(self, undoKind):
        if undoKind not in ("database", "settings"):
            return flask.jsonify(success=False, errorMessage="Unknown undo kind."), 400
        result = self._undoLegacyMigration(undoKind)
        return flask.jsonify(result), (200 if result["success"] else 400)

