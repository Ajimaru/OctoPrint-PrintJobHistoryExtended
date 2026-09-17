

function PrintJobHistoryExtendedPluginCheckDialog(){

    var self = this;

    self.apiClient = null;
    myPluginSettings = null;

    self.missingPluginsDialog = null;
    self.missingPluginMessageExtended = null;
    self.confirmButton = null;
    self.deactivatePluginCheck = ko.observable(false);

    self.init = function(apiClient, pluginSettings){
        self.apiClient = apiClient;
        myPluginSettings = pluginSettings
        self.missingPluginsDialog = $("#dialog_printJobHistoryExtended_missingPlugins");
        self.missingPluginMessageExtended = $("#missingPluginMessageExtended");
        self.confirmButton = $("button.btn-confirm", self.missingPluginsDialog);
    }

    this.isInitialized = function() {
        return self.apiClient != null;
    }

    this.hideDialog = function(){
        self.missingPluginsDialog.modal('hide');
    }

    this.showMissingPluginsDialog = function(dialogMessage){
        if (self.missingPluginsDialog != null && self.missingPluginsDialog.is(":visible")){
            return;
        }

        self.missingPluginMessageExtended.html(dialogMessage);

        self.confirmButton.unbind("click");
        self.confirmButton.bind("click", function() {
            disableCheck = self.deactivatePluginCheck();
            if (disableCheck == true) {
                myPluginSettings.pluginCheckActivated(false)
                self.apiClient.callDeactivatePluginCheck();
            }
            self.hideDialog();
        });

        self.missingPluginsDialog.modal({
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



}
