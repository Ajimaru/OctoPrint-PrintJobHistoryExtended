

function PrintJobHistoryExtendedPluginMessageConfirmDialog(){

    var self = this;

    self.apiClient = null;
    myPluginSettings = null;

    self.messageConfirmDialog = null;
    self.confirmMessageExtended = null;
    self.confirmButton = null;

    self.init = function(apiClient, pluginSettings){
        self.apiClient = apiClient;
        myPluginSettings = pluginSettings
        self.messageConfirmDialog = $("#dialog_printJobHistoryExtended_messageConfirm");
        self.confirmMessageExtended = $("#confirmMessageExtended", self.messageConfirmDialog);
        self.confirmButton = $("#pjhe_confirmmessage_button", self.messageConfirmDialog);
        self.cancelButton = $("#pjhe_cancel_button", self.messageConfirmDialog);
    }

    this.isInitialized = function() {
        return self.apiClient != null;
    }

    this.hideDialog = function(){
        self.messageConfirmDialog.modal('hide');
    }

    this.showDialog = function(messageConfirmData){
        if (self.messageConfirmDialog != null && self.messageConfirmDialog.is(":visible")){
            return;
        }

        //
        var dialogMessage = "<h1>" + messageConfirmData.title + "</h1>" +
                            "<span>" + messageConfirmData.data + "</span>";

        self.confirmMessageExtended.html(dialogMessage);

        self.confirmButton.unbind("click");
        self.confirmButton.bind("click", function() {
            self.apiClient.callConfirmMessageDialog();
            self.hideDialog();
        });

        self.cancelButton.unbind("click");
        self.cancelButton.bind("click", function() {
            self.hideDialog();
        });

        self.messageConfirmDialog.modal({
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
