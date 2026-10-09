
function PrintJobHistoryExtendedEditDialog(){
    "use strict";


    var self = this;

    this.apiClient = null;
    this.currentUser = null;

    this.editPrintJobItemDialog = null;
    this.printJobItemForEdit = null;
    this.closeDialogHandler = null;

    this.lastSnapshotImageSource = null;
    this.snapshotSuccessMessageSpan = null;
    this.snapshotErrorMessageSpan = null;

    this.snapshotImage = null;
    this.captureButtonText = null;
    this.cameraShutterUrl = null;

    this.noteEditor = null;

    this.shouldPrintJobTableReload = false;

    // Snapshot of the editable state, taken when the dialog opens. Closing compares
    // against it so the user is warned before unsaved edits are thrown away.
    this.unchangedFieldsSnapshot = null;
    this.unchangedNoteSnapshot = null;

    var SHUTTER_DURATION = 4;   // in seconds
    var IMAGEDISPLAYMODE_SNAPSHOTIMAGE = "snapshotImage";
    var IMAGEDISPLAYMODE_VIDEOSTREAM = "videoStream";
    var IMAGEDISPLAYMODE_VIDEOSTREAM_WITH_SHUTTER = "videoStreamWithShutter";
    var IMAGEDISPLAYMODE_VIDEOSTREAM_LOADING = "videoStreamLoading";
    var IMAGEDISPLAYMODE_VIDEOSTREAM_ERROR = "videoStreamError";

    this.imageDisplayMode = ko.observable(IMAGEDISPLAYMODE_SNAPSHOTIMAGE);

    this.snapshotUploadName = ko.observable();
    this.snapshotUploadInProgress = ko.observable(false);

    self.webCamSettings = null;

    // The settings view model, so the webcam values can be read when they are needed.
    // `settings.webcam` is undefined until the first settings response arrives, and
    // onBeforeBinding can run before that: grabbing the object once stored null and left
    // every webcam read throwing. OctoPrint's own timelapse view model reads it the same
    // lazy way.
    self.settingsViewModel = null;

    // OctoPrint's /api/util/test is admin-only AND requires the password to have been
    // entered within accessControl.defaultReauthenticationTimeout (5 minutes by default).
    // Without asking for it first the call comes back 403 "Please reauthenticate with your
    // credentials", which the capture only reported as "Something went wrong".
    self.loginState = null;
    // Supplied by the owning view model, which is the side that knows the permission
    // names. Defaults to denying, so a caller that forgets it cannot leak a request.
    self.canEdit = function(){ return false; };

    // The current webcam settings, or null while the settings have not been loaded yet.
    // Read fresh every time instead of caching: the settings view model replaces its
    // settings object on the first response, so anything kept from before is stale.
    function _webCamSettings(){
        if (self.settingsViewModel == null || self.settingsViewModel.settings == null){
            return self.webCamSettings;
        }
        var webCamSettings = self.settingsViewModel.settings.webcam;
        if (webCamSettings == null || typeof webCamSettings.snapshotUrl !== "function"){
            return null;
        }
        self.webCamSettings = webCamSettings;
        return webCamSettings;
    }
    self._webCamSettings = _webCamSettings;

    // The templates must not reach into webCamSettings directly: OctoPrint withholds the
    // webcam settings from a user without SETTINGS, so the object stays null for them and
    // every such binding throws. Knockout then abandons the rest of that subtree without
    // an error on the console - which took out the whole modal footer, leaving a read-only
    // user with a visible Save button and a Close button that did nothing.
    self.webCamStreamUrl = function(){
        var settings = _webCamSettings();
        return settings == null ? "" : settings.streamUrl();
    };
    self.webCamRotate90 = function(){
        var settings = _webCamSettings();
        return settings == null ? false : settings.rotate90() === true;
    };
    self.webCamFlipH = function(){
        var settings = _webCamSettings();
        return settings == null ? false : settings.flipH() === true;
    };
    self.webCamFlipV = function(){
        var settings = _webCamSettings();
        return settings == null ? false : settings.flipV() === true;
    };

    // Run the callback, asking the user for their password first if the login session is
    // too old for the endpoints that insist on a recent one. Falls back to calling straight
    // through on an OctoPrint without that helper.
    function _reauthenticateIfNecessary(callback){
        if (self.loginState != null && typeof self.loginState.reauthenticateIfNecessary === "function"){
            self.loginState.reauthenticateIfNecessary(callback);
        } else {
            callback();
        }
    }
    self._reauthenticateIfNecessary = _reauthenticateIfNecessary;

    // "Computed" field-binding
    self.webCamEnabled = ko.pureComputed(function(){
        var webCamSettings = _webCamSettings();
        if (webCamSettings == null){
            return false;
        }
        if (webCamSettings.webcamEnabled != null){
            return webCamSettings.webcamEnabled();
        } else {
            return webCamSettings.snapshotUrl() != null && webCamSettings.streamUrl();
        }
    });
    // "Computed" field-binding
    self.webcamRatioClass = ko.pureComputed(function() {
        var webCamSettings = _webCamSettings();
        if (webCamSettings != null && webCamSettings.streamRatio() == "4:3") {
            return "ratio43";
        } else {
            return "ratio169";
        }
    });


    // Image functions
    function _setSnapshotImageSource(snapshotUrl){
        self.lastSnapshotImageSource = self.snapshotImage.attr("src")
        if (self.lastSnapshotImageSource="#"){
            self.lastSnapshotImageSource = snapshotUrl;
        }
        self.snapshotImage.attr("src", snapshotUrl+"?" + new Date().getTime()); // new Date == cache breaker
    }

    function _restoreSnapshotImageSource(){
        _setSnapshotImageSource(self.lastSnapshotImageSource);
    }

    self.isSlicerSettingsPresent = ko.observable(false);
    self.isTechnicalLogPresent = ko.observable(false);

    self.textAreaTitleDialog = ko.observable();
    self.textAreaContentDialog = ko.observable();

    /////////////////////////////////////////////////////////////////////////////////////////////////// INIT

    this.init = function(apiClient, settingsViewModel, loginState, canEdit){
        self.apiClient = apiClient;

        // keep the view model, not settings.webcam: that object does not exist yet when
        // the settings have not been received, and is only filled in later
        self.settingsViewModel = settingsViewModel;
        self.loginState = loginState;
        if (typeof canEdit === "function"){
            self.canEdit = canEdit;
        }
        self.webCamSettings = null;

        self.editPrintJobItemDialog = $("#dialog_printJobHistoryExtended_editPrintJobItem");
        self.snapshotSuccessMessageSpan = $("#printJobHistoryExtended-editdialog-success-message");
        self.snapshotErrorMessageSpan = $("#printJobHistoryExtended-editdialog-error-message");
        self.snapshotImage = $("#printJobHistoryExtended-snapshotImage");
        self.captureButtonText = $("#printJobHistoryExtended-captureButtonText");
        self.cameraShutterUrl = "plugin/PrintJobHistoryExtended/static/images/camera-shutter.png";   // TODO replace with PLUGIN_ID

        // INIT Note Editor
        self.noteEditor = new Quill('#note-quill-editor-extended', {
            modules: {
                toolbar: [
                    ['bold', 'italic', 'underline'],
                    [{ 'color': [] }, { 'background': [] }],
                    [{ 'list': 'ordered' }, { 'list': 'bullet' }],
                    ['link']
                ]
            },
            theme: 'snow'
        });

        // INIT FileUpload
        self.snapshotUploadData = undefined;    // data with submit function
        self.snapshotUploadButton = $("#printJobHistoryExtended-snapshotUploadButton");
        self.snapshotUploadButton.fileupload({
            dataType: "json",
            maxNumberOfFiles: 1,
            autoUpload: false,
            headers: OctoPrint.getRequestHeaders(),
            add: function(e, data) {
                self.snapshotSuccessMessageSpan.hide();
                self.snapshotErrorMessageSpan.hide();
                if (data.files.length === 0) {
                    // no files? ignore
                    return false;
                }
                data.url = self.apiClient.uploadSnapshotUrl(self.printJobItemForEdit.snapshotFilename());
                self.snapshotUploadName(data.files[0].name);
                self.snapshotUploadData = data;
            },
            done: function(e, data) {
                self.snapshotSuccessMessageSpan.show();
                self.snapshotSuccessMessageSpan.text("Snapshot uploaded!");
                self.snapshotUploadName(undefined);
                self.snapshotUploadData = undefined;
                _setSnapshotImageSource(self.apiClient.getSnapshotUrl(data.result.snapshotFilename));

                self.snapshotUploadInProgress(false);
            },
            fail: function(e, data) {
                new PNotify({
                    title: gettext("Something went wrong"),
                    text: gettext("Maybe the filesize was to big (limit 5MB). Please consult octoprint.log for details"),
                    type: "error",
                    hide: false
                });

//                self.uploadButton.unbind("click");
                self.snapshotUploadName(undefined);
                self.snapshotUploadInProgress(false);
            }
        });

        self.textAreaDialog = $("#dialog_printJobHistoryExtended_textAreaDialog");
        self.changePrintStatusDialog = $("#dialog_printJobHistoryExtended_changePrintStatus");
    }

    this.showPrintStatusDialog = function(){
        self.changePrintStatusDialog.modal({
            //minHeight: function() { return Math.max($.fn.modal.defaults.maxHeight() - 80, 250); }
            keyboard: false,
            clickClose: false,
            showClose: false,
            backdrop: "static"
        }).css({
            width: 'auto',
            'margin-left': function() { return -($(this).width() /2); }
        });
    }

    this.closePrintStatusDialog = function(){
        self.changePrintStatusDialog.modal('hide');
    }
    this.selectedPrintStatus = function(selectedColor){
        if ("green" == selectedColor){
            self.printJobItemForEdit.printStatusResult("success");
        } else {
            self.printJobItemForEdit.printStatusResult("failed");
        }
        self.closePrintStatusDialog();
    }

    self.showSlicerSettingsDialog = function(){
        self._showTextAreaDialog("Slicer-Settings for print job",  self.printJobItemForEdit.slicerSettingsAsText());
    }

    self.showTechnicalLogDialog = function(){
        self._showTextAreaDialog("Technical log for print job", self.printJobItemForEdit.technicalLog());
    }

    self._showTextAreaDialog = function(title, content){
        self.textAreaTitleDialog(title);
        self.textAreaContentDialog(content);

        self.textAreaDialog.modal({
            //minHeight: function() { return Math.max($.fn.modal.defaults.maxHeight() - 80, 250); }
            keyboard: false,
            clickClose: false,
            showClose: false,
            backdrop: "static"
        }).css({
            width: 'auto',
            'margin-left': function() { return -($(this).width() /2); }
        });
    }

    this.closeTextAreaDialog = function(){
        self.textAreaDialog.modal('hide');
    }

    this.isInitialized = function() {
        return self.apiClient != null;
    }


    /////////////////////////////////////////////////////////////////////////////////////////////////// SETTER
    this.setCurrentUser = function(currentUser){
        this.currentUser = currentUser;
    }

    /////////////////////////////////////////////////////////////////////////////////////////////////// SHOW DIALOG
    this.showDialog = function(printJobItemForEdit, closeDialogHandler, fullEditMode){

        if (fullEditMode != null){
            self.fullEditMode(fullEditMode);
        } else {
            self.fullEditMode(false);
        }
        self.printJobItemForEdit = printJobItemForEdit;
        self.closeDialogHandler = closeDialogHandler;

        self.shouldPrintJobTableReload = false;
//        TODO Wieso this statt self????
        _setSnapshotImageSource(self.apiClient.getSnapshotUrl(printJobItemForEdit.snapshotFilename()));
        self.captureButtonText.text(reCaptureText);

//        reset message
        self.snapshotSuccessMessageSpan.hide();
        self.snapshotErrorMessageSpan.hide();
        self.imageDisplayMode(IMAGEDISPLAYMODE_SNAPSHOTIMAGE);

        // assign content to the Note-Section
        var noteContent = null;
        if (printJobItemForEdit.noteDeltaFormat() == null){
            // Fallback is text (if present), not Html
            if (printJobItemForEdit.noteText() != null){
                self.noteEditor.setText(printJobItemForEdit.noteText(), 'api');
            } else {
                self.noteEditor.setContents(null, 'api');
            }
        } else {
            var deltaFormat = JSON.parse(printJobItemForEdit.noteDeltaFormat());
            self.noteEditor.setContents(deltaFormat, 'api');
        }

        var slicerSettingsPresent = self.printJobItemForEdit.slicerSettingsAsText();
        if (slicerSettingsPresent != null && self.printJobItemForEdit.slicerSettingsAsText().length != 0){
            self.isSlicerSettingsPresent(true);
        } else {
            self.isSlicerSettingsPresent(false);
        }

        var technicalLogPresent = self.printJobItemForEdit.technicalLog();
        if (technicalLogPresent != null && self.printJobItemForEdit.technicalLog().length != 0){
            self.isTechnicalLogPresent(true);
        } else {
            self.isTechnicalLogPresent(false);
        }
        // some magic, if in edit mode


        var calcDuration = function(){
            // update duration only in edit-mode
            if (self.fullEditMode() == false){
                return;
            }
            var noDatetime = isEmpty(self.printJobItemForEdit.printStartDateTimeFormatted()) || isEmpty(self.printJobItemForEdit.printEndDateTimeFormatted());
            if (noDatetime){
                self.printJobItemForEdit.duration(0);
            } else {
                const startDateTime = dayjs(self.printJobItemForEdit.printStartDateTimeFormatted(), 'DD.MM.YYYY HH:mm').toDate();
                const endDateTime = dayjs(self.printJobItemForEdit.printEndDateTimeFormatted(), 'DD.MM.YYYY HH:mm').toDate();
                const duration = (endDateTime - startDateTime) / 1000;
                if (duration > 0){
                    self.printJobItemForEdit.duration(duration);
                } else {
                    self.printJobItemForEdit.duration(0);
                }
            }
        }
        self.printJobItemForEdit.printStartDateTimeFormatted.subscribe(function(newValue){
            calcDuration();
        });
        self.printJobItemForEdit.printEndDateTimeFormatted.subscribe(function(newValue){
            calcDuration();
        });

        self.printJobItemForEdit.isRePrintable.subscribe(function(newValue){
            self.tooltipForSelection(self._buildTooltipForSelection());
        });
        self._refreshReprintableState();

        // Select first Tab
        $('a[href="#tab-pjhe-editjob-total"]').tab("show");

        // Baseline for the unsaved-changes check. Taken last, so everything the dialog
        // filled in above counts as "unchanged" and only the user's own edits show up.
        self._takeChangeSnapshot();

        self.editPrintJobItemDialog.modal({
            //minHeight: function() { return Math.max($.fn.modal.defaults.maxHeight() - 80, 250); }
            keyboard: false,
            clickClose: false,
            showClose: false,
            backdrop: "static"
        }).css({
            width: 'auto',
            'margin-left': function() { return -($(this).width() /2); }
        });


    }

    /////////////////////////////////////////////////////////////////////////////////////////////////// UNSAVED CHANGES
    // The bound fields all live on the PrintJobItem as plain observables, so serializing
    // it captures every one of them without listing them here - a new field in the dialog
    // is covered automatically. The note lives in Quill, outside Knockout, so it needs its
    // own snapshot.
    this._takeChangeSnapshot = function(){
        self.unchangedFieldsSnapshot = self._serializeEditableFields();
        self.unchangedNoteSnapshot = self._serializeNote();
    }

    // Fields the server fills in by itself. They are not the user's edits, so a change to
    // them must not make the dialog claim there is something to save. Whether the file can
    // still be selected is answered after the dialog is already open, so it would otherwise
    // always arrive after the snapshot was taken.
    this.NOT_USER_EDITABLE_FIELDS = ["isRePrintable", "fullFileLocation"];

    this._serializeEditableFields = function(){
        if (self.printJobItemForEdit == null){
            return null;
        }
        try {
            return ko.toJSON(self.printJobItemForEdit, function(key, value){
                return self.NOT_USER_EDITABLE_FIELDS.indexOf(key) != -1 ? undefined : value;
            });
        } catch (error){
            // Never let a serialization problem block closing the dialog.
            console.warn("PrintJobHistoryExtended: could not snapshot dialog fields", error);
            return null;
        }
    }

    this._serializeNote = function(){
        if (self.noteEditor == null){
            return null;
        }
        try {
            return JSON.stringify(self.noteEditor.getContents());
        } catch (error){
            console.warn("PrintJobHistoryExtended: could not snapshot note", error);
            return null;
        }
    }

    this.hasUnsavedChanges = function(){
        // A snapshot that could not be taken means "unknown", and warning on every close
        // would train the user to click it away. Stay silent instead.
        if (self.unchangedFieldsSnapshot == null && self.unchangedNoteSnapshot == null){
            return false;
        }
        if (self.unchangedFieldsSnapshot != null &&
            self.unchangedFieldsSnapshot != self._serializeEditableFields()){
            return true;
        }
        if (self.unchangedNoteSnapshot != null &&
            self.unchangedNoteSnapshot != self._serializeNote()){
            return true;
        }
        return false;
    }

    /////////////////////////////////////////////////////////////////////////////////////////////////// CLOSE DIALOG
    // Forced close, triggered by the server rather than the user. No prompt here: the
    // dialog is being taken away regardless, and a confirm nobody asked for would just
    // hang on screen.
    this.closeDialog = function(){
        var visible = self.editPrintJobItemDialog.hasClass('in');
        if (visible == true){
            self._discardChangeSnapshot();
            self.editPrintJobItemDialog.modal('hide');
        }
    }

    /////////////////////////////////////////////////////////////////////////////////////////////////// ABORT PRINT JOB ITEM
    this.abortPrintJobItem  = function(){
        if (self.hasUnsavedChanges() == true){
            var discard = confirm("There are unsaved changes.\n\nDiscard them and close the dialog?");
            if (discard != true){
                return;
            }
        }
        self._discardChangeSnapshot();
        self.editPrintJobItemDialog.modal('hide');
        self.closeDialogHandler(self.shouldPrintJobTableReload);
    }

    // Drop the baseline once the dialog is on its way out, so a later close path cannot
    // compare against a stale job.
    this._discardChangeSnapshot = function(){
        self.unchangedFieldsSnapshot = null;
        self.unchangedNoteSnapshot = null;
    }

    /////////////////////////////////////////////////////////////////////////////////////////////////// SAVE PRINT JOB ITEM
    var isEmpty = function(value){
        if (value == null){
            return true;
        }
        value = "" + value;
        if (value.trim().length == 0 || value.replace(/\s/g,"") == ""){
            return true;
        }
        return false;
    }

    this.savePrintJobItem  = function(){

        // check for mandatory fields
        if (
            isEmpty(self.printJobItemForEdit.fileName()) ||
            isEmpty(self.printJobItemForEdit.printStartDateTimeFormatted()) ||
            isEmpty(self.printJobItemForEdit.printEndDateTimeFormatted()) ||
            isEmpty(self.printJobItemForEdit.duration())

        ){
            alert("fields are required: filename, start/end datetime, duration")
            return;
        }

        var noteText = self.noteEditor.getText();
        var noteDeltaFormat = self.noteEditor.getContents();
        // Quill 2 keeps every list as <ol> internally and only tells bullets apart through
        // data-list, so the raw editor innerHTML would show bullet lists numbered wherever
        // the Quill stylesheet does not apply (the note column in the job table).
        var noteHtml = self.noteEditor.getSemanticHTML();
        self.printJobItemForEdit.noteText(noteText);
        self.printJobItemForEdit.noteDeltaFormat(noteDeltaFormat);
        self.printJobItemForEdit.noteHtml(noteHtml);

        if (self.printJobItemForEdit.userName == null ||
            self.printJobItemForEdit.userName() == null ||
            self.printJobItemForEdit.userName().trim().length === 0){
            if (self.currentUser != null){
                self.printJobItemForEdit.userName(self.currentUser.name);
            }
        }

        self.apiClient.callStorePrintJob(self.printJobItemForEdit.databaseId(), self.printJobItemForEdit, function(allPrintJobsResponse){
            // Saved, so there is nothing left to warn about.
            self._discardChangeSnapshot();
            self.editPrintJobItemDialog.modal('hide');
            self.closeDialogHandler(true);
        });

    }

    /////////////////////////////////////////////////////////////////////////////////////////////////// DELETE PRINT JOB
    this.deletePrintJobItem  = function(){
        var result = confirm("Do you really want to delete the print job?");
        if (result == true){
            self.apiClient.callRemovePrintJob(self.printJobItemForEdit.databaseId(), function(responseData) {
                // The job is gone - warning about unsaved edits to it would be absurd.
                self._discardChangeSnapshot();
                self.editPrintJobItemDialog.modal('hide');
                self.closeDialogHandler(true);
            });
        }
    }

    /////////////////////////////////////////////////////////////////////////////////////////////////// REPORT PRINT JOB
    this.reportPrintJobItem = function() {
        window.open(self.apiClient.callCreateSingleReportUrl(self.printJobItemForEdit.databaseId()), '_blank').focus();
    }

    /////////////////////////////////////////////////////////////////////////////////////////////////// SELECT PRINT JOB
    self.tooltipForSelection = ko.observable("");
    // "pending" while the server is being asked, then "done", or "failed" if it could not answer
    self.selectionCheckState = ko.observable("pending");
    self.notReprintableReason = ko.observable(null);

    // Asks whether this job's file can still be selected. Done here instead of for every row
    // of the table, because answering it touches the disk.
    // Anything that swaps printJobItemForEdit for another item has to call this again,
    // otherwise the button keeps the answer that belonged to the previous job.
    this._refreshReprintableState = function(){
        if (self.printJobItemForEdit == null){
            return;
        }
        var databaseId = self.printJobItemForEdit.databaseId();
        // the button is hidden without a databaseId (a new or cloned job), nothing to ask about
        if (databaseId == null || databaseId == ""){
            self.selectionCheckState("done");
            self.notReprintableReason(null);
            self.printJobItemForEdit.isRePrintable(false);
            self.printJobItemForEdit.isRePrintable.valueHasMutated();
            return;
        }

        // Selecting a job for printing needs EDIT_JOB, and so does the route that answers
        // this. Without that permission the button is hidden anyway, so asking would only
        // hit the disk server-side and return a 403.
        if (self.canEdit() == false){
            self.selectionCheckState("done");
            self.notReprintableReason(null);
            self.printJobItemForEdit.isRePrintable(false);
            self.printJobItemForEdit.isRePrintable.valueHasMutated();
            return;
        }

        // no answer yet means no selection: the button stays disabled until the server replies
        self.selectionCheckState("pending");
        self.notReprintableReason(null);
        self.printJobItemForEdit.isRePrintable(false);
        self.tooltipForSelection(self._buildTooltipForSelection());

        var itemWhenAsked = self.printJobItemForEdit;
        self.apiClient.callLoadPrintJobReprintable(databaseId, function(responseData){
            // the dialog may show a different job by now
            if (self.printJobItemForEdit != itemWhenAsked){
                return;
            }
            self.printJobItemForEdit.fullFileLocation(responseData.fullFileLocation);
            self.notReprintableReason(responseData.notReprintableReason);
            self.selectionCheckState("done");
            self.printJobItemForEdit.isRePrintable(responseData.isRePrintable == true);
            // knockout stays quiet when the value did not change, but the tooltip still has to
            // be rebuilt - the reason behind it may well have changed
            self.printJobItemForEdit.isRePrintable.valueHasMutated();
        }, function(){
            if (self.printJobItemForEdit != itemWhenAsked){
                return;
            }
            self.selectionCheckState("failed");
            self.printJobItemForEdit.isRePrintable(false);
            self.printJobItemForEdit.isRePrintable.valueHasMutated();
        });
    };

    this.selectForPrinting = function(){
        // This closes the dialog too, so unsaved edits would be lost just as silently.
        if (self.hasUnsavedChanges() == true){
            var discard = confirm("There are unsaved changes.\n\nDiscard them and select this file for printing?");
            if (discard != true){
                return;
            }
        }
        self.apiClient.callSelectPrintJobForPrinting(self.printJobItemForEdit.databaseId(), function(responseData) {
            self._discardChangeSnapshot();
            self.editPrintJobItemDialog.modal('hide');
            self.closeDialogHandler(true);
        }, function(errorData) {
            // Deliberately quiet: the server already sent the error popup. Showing one here
            // as well would tell the user the same thing twice.
            // The dialog stays open on purpose, so unsaved edits survive a failed selection.
            self._refreshReprintableState();
        });
    }

    this._buildTooltipForSelection = function(){
        var toolTip = "";
        if (self.printJobItemForEdit != null ){
            var fullPath = self.printJobItemForEdit.fullFileLocation();
            if (self.selectionCheckState() == "pending"){
                toolTip = "Checking whether the file is still there...";
            } else if (self.selectionCheckState() == "failed"){
                toolTip = "Could not check whether the file is still there. Reopen the dialog to try again.";
            } else if (self.printJobItemForEdit.isRePrintable() == true){
                toolTip = "Print file: " + fullPath;
            } else if (self.notReprintableReason() == "placeholder"){
                toolTip = "Selecting not possible! The printer never reported which file it was printing.";
            } else if (self.notReprintableReason() == "missing"){
                toolTip = "Selecting not possible! This print job is no longer in the database.";
            } else if (self.notReprintableReason() == "unresolvable"){
                toolTip = "Selecting not possible! The storage could not work out where this file is.";
            } else {
                toolTip = "Selecting not possible! File not found in " + fullPath;
            }
        }
        return toolTip;
    };

    /////////////////////////////////////////////////////////////////////////////////////////////////// DELETE IMAGE
    this.deleteImage  = function(){


        var result = confirm("Do you really want to delete the image?");
        if (result == true){
            self.apiClient.callDeleteSnapshotImage(self.printJobItemForEdit.snapshotFilename(), function(responseData){
                // Update Image URL is the same, backend send the "no photo"-image
                _setSnapshotImageSource(self.apiClient.getSnapshotUrl(responseData.snapshotFilename));
                self.shouldPrintJobTableReload = true;
            });
        }
    }


    /////////////////////////////////////////////////////////////////////////////////////////////////// CAPTURE IMAGE
    var reCaptureText = "Capture";
    var takeSnapshotText = "Take snapshot";

    this.captureImage = function(){
        self.snapshotSuccessMessageSpan.hide();
        self.snapshotErrorMessageSpan.hide();

        var newImageUrl = null;
        // check is in capture mode
        if (self.captureButtonText.text() == reCaptureText){
            // SHOW VIDEOSTREAM
            self.imageDisplayMode(IMAGEDISPLAYMODE_VIDEOSTREAM_LOADING);

            var webCamSettings = self._webCamSettings();
            if (webCamSettings == null){
                // settings not loaded yet - without them there is no url to test
                self.imageDisplayMode(IMAGEDISPLAYMODE_VIDEOSTREAM_ERROR);
                self.snapshotErrorMessageSpan.show();
                self.snapshotErrorMessageSpan.text("Camera settings are not loaded yet. Try again in a moment.");
                return;
            }

            var snapshotUrl = webCamSettings.snapshotUrl();
            var streamUrl = webCamSettings.streamUrl();

            if (snapshotUrl == null || streamUrl == null || snapshotUrl.length == 0 || streamUrl.length == 0) {
                // stop here: testing an empty url only produced a generic "went wrong"
                self.imageDisplayMode(IMAGEDISPLAYMODE_VIDEOSTREAM_ERROR);
                alert("Camera-Error: Please make sure that both stream- and snapshot-url is configured in your camera-settings")
                return;
            }

            // ask for the password first if the session is older than the reauthentication
            // timeout, otherwise /api/util/test answers 403. Same wrapper OctoPrint's own
            // classic webcam settings use around this call.
            _reauthenticateIfNecessary(function(){
                OctoPrint.util.testUrl(snapshotUrl, {
                    method: "GET",
                    response: "bytes",
                    timeout: webCamSettings.streamTimeout(),
//                    validSsl: self.webcam_snapshotSslValidation(),
                    content_type_whitelist: ["image/*"]
                })
                .done(function(response){
                    // Check if videoStream is available
                    if (response.status == 200 && response.result == true){
                        //show stream in image
                        self.imageDisplayMode(IMAGEDISPLAYMODE_VIDEOSTREAM);

                        $("#printJobHistoryExtended-videoStream").attr("src", streamUrl);
                        self.captureButtonText.text(takeSnapshotText);
                    } else {
                        // the url could be reached but did not return an image
                        self.imageDisplayMode(IMAGEDISPLAYMODE_VIDEOSTREAM_ERROR);
                        self.snapshotErrorMessageSpan.show();
                        self.snapshotErrorMessageSpan.text("The snapshot URL did not return an image. Check your camera settings.");
                    }
                })
                .fail(function(jqXHR){
                    self.imageDisplayMode(IMAGEDISPLAYMODE_VIDEOSTREAM_ERROR);

                    // say which error it was: a plain "went wrong" sent us looking in the
                    // wrong place for a session that had simply gone stale
                    var errorMessage = "Something went wrong. Try again!";
                    if (jqXHR != null && jqXHR.status == 403){
                        errorMessage = "Not allowed to test the camera URL. Log in again as an administrator and retry.";
                    } else if (jqXHR != null && jqXHR.status != null && jqXHR.status != 0){
                        errorMessage = "Could not test the camera URL (HTTP " + jqXHR.status + "). Try again!";
                    }
                    self.snapshotErrorMessageSpan.show();
                    self.snapshotErrorMessageSpan.text(errorMessage);
                });
            });
        } else {
            // TAKE SNAPSHOT
            var startShutter = new Date().getTime();
            self.imageDisplayMode(IMAGEDISPLAYMODE_VIDEOSTREAM_WITH_SHUTTER);
            // freeze video stream -> show current tken snapshot
            var mySnapshotUrl = self.apiClient.getProxiedSnapshotUrl();
            $("#printJobHistoryExtended-videoStream").attr("src", mySnapshotUrl);

            self.apiClient.callTakeSnapshot(self.printJobItemForEdit.snapshotFilename(), function(responseData){
                if (responseData["snapshotFilename"] != undefined){
                    self.snapshotSuccessMessageSpan.show();
                    self.snapshotSuccessMessageSpan.text("Snapshot captured!");

                } else {
                    self.snapshotErrorMessageSpan.show();
                    self.snapshotErrorMessageSpan.text("Something went wrong. Try again!");
                }

                self.snapshotImage.attr("src", self.apiClient.getSnapshotUrl(responseData.snapshotFilename));
                self.captureButtonText.text(reCaptureText);

                // SOME UI-SUGAR, if a minimum of time is not passed, just wait and after that remove the "nice" shutter
                var now = new Date().getTime();
                var captureDuration = now-startShutter;
                if (captureDuration < (SHUTTER_DURATION*1000)){
                    var waitingDelta = (SHUTTER_DURATION*1000) - captureDuration
                    setTimeout(function() {
                        self.imageDisplayMode(IMAGEDISPLAYMODE_SNAPSHOTIMAGE);
                    }, waitingDelta);
                } else {
                    // server call takes already a long time
                    self.imageDisplayMode(IMAGEDISPLAYMODE_SNAPSHOTIMAGE);
                }
                self.shouldPrintJobTableReload = true;
            });
        }
    }

    this.cancelCaptureImage = function(){
        self.imageDisplayMode(IMAGEDISPLAYMODE_SNAPSHOTIMAGE);
        self.captureButtonText.text(reCaptureText);
        $("#printJobHistoryExtended-cancelCaptureButton").hide();
    }

    // The image of a finished print is taken in the background and may arrive while the
    // dialog for that print is already open.
    this.refreshSnapshotImage = function(snapshotFilename){
        if (self.printJobItemForEdit == null || self.snapshotImage == null){
            return;
        }
        if (self.printJobItemForEdit.snapshotFilename() != snapshotFilename){
            return;
        }
        self.snapshotImage.attr("src", self.apiClient.getSnapshotUrl(snapshotFilename) + "?" + new Date().getTime()); // cache breaker
    }

    /////////////////////////////////////////////////////////////////////////////////////////////////// UPLOAD IMAGE
    this.performSnapshotUpload = function() {
        if (self.snapshotUploadData === undefined) return;

        var perform = function() {
            self.snapshotUploadInProgress(true);
//                self.loglines.removeAll();
//                self.loglines.push({line: "Uploading backup, this can take a while. Please wait...", stream: "message"});
//                self.loglines.push({line: " ", stream: "message"});
//                self.restoreDialog.modal({keyboard: false, backdrop: "static", show: true});

            self.snapshotUploadData.submit();
            self.shouldPrintJobTableReload = true;
        };
        perform();
//            showConfirmationDialog(_.sprintf(gettext("You are about to upload and restore the backup file \"%(name)s\". This cannot be undone."), {name: self.backupUploadName()}),
//                perform);
    };

//        self.selectedSnapshotlFilenameUrl = ko.observable();
//        self.selectedUploaadSnapshotlUrl =  ko.observable();


    /////////////////////////////////////////////////////////////////////////////////////////////////// TOGGLE FULL EDIT MODE
    this.fullEditMode = ko.observable(false);

    self.toggleFullEditMode = function() {
        self.fullEditMode(!this.fullEditMode());
    };


}
