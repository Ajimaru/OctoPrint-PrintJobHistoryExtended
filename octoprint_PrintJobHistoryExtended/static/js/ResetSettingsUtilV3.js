// Ported from OctoPrint-SpoolManagerExtended, whose version adopted the modernisation (const
// over var, async/await, extracted settings-reset helper) and the confirmation before
// resetting from mdziekon/OctoPrint-SpoolManager PR #19 (GH-18).
//
// The reset only fills the defaults into the settings dialog; nothing is stored before the
// user saves. The backend therefore only offers the read-only "getDefaultSettings" action.
// The former "resetSettings" action stored the defaults on a plain GET request.
//
// Deliberately separate from the "resetSettings" button and the global
// "ResetSettingsUtilV3" the original PrintJobHistory and SpoolManager plugins share. With
// several of them installed, the last util loaded replaced the others. Worse, the original
// PrintJobHistory hooks every settings link starting with "#settings_plugin_PrintJobHistory"
// - this plugin's included - and rebinds the shared button once its own request returns, so
// "Reset Settings" on this plugin's page reset the other plugin's settings.
function PrintJobHistoryExtendedResetSettingsUtilV3(pluginSettings) {
    "use strict";

    const pluginSettingsFromPlugin = pluginSettings;

    const RESET_BUTTON_ID = "printJobHistoryExtended-resetSettingsButton";
    const RESET_BUTTON_HTML = `<button id="${RESET_BUTTON_ID}" class="btn btn-warning" style="margin-right:3%; display:none">Reset Settings</button>`;
    const RESET_BUTTON_SELECTOR = `#${RESET_BUTTON_ID}`;
    const EVENT_NAMESPACE = ".printJobHistoryExtendedResetSettings";

    // Only one level of nesting. Not every key of getDefaultSettings is a Knockout observable
    // in the settings view model, and calling a plain value like an observable throws - on
    // the very first key, so the whole reset silently did nothing. Those keys are skipped.
    const resetPluginSettings = (pluginSettingsStorage, newSettings) => {
        Object.entries(newSettings).forEach(([key, value]) => {
            const target = pluginSettingsStorage[key];

            if (!ko.isObservable(target)) {
                return;
            }

            if (typeof value !== "object" || !value || Array.isArray(value)) {
                target(value);

                return;
            }

            Object.entries(value).forEach(([nestedKey, nestedValue]) => {
                if (!ko.isObservable(target[nestedKey])) {
                    return;
                }

                target[nestedKey](nestedValue);
            });
        });
    };

    const resetSettings = async (PLUGIN_ID_string, mapSettingsToViewModel_function) => {
        // A native, blocking confirm() on purpose: the button sits in the settings dialog's
        // own footer, and a second Bootstrap 2 modal on top fires "hide" on #settings_dialog,
        // which hides this very button in the middle of the click.
        const hasConfirmed = confirm(
            "Reset all Print Job History Extended settings to their default values?\n\n" +
                "This includes the database connection on the Storage tab. " +
                "The change is only applied in the dialog and takes effect once you save the settings."
        );

        if (!hasConfirmed) {
            return;
        }

        try {
            const newSettingsData = await $.ajax({
                url: `${API_BASEURL}plugin/${PLUGIN_ID_string}?action=getDefaultSettings`,
                type: "GET"
            });

            // reset all values in the in-memory storage
            resetPluginSettings(pluginSettingsFromPlugin, newSettingsData);

            // delegate to the client. So client is able to reset/init other values
            mapSettingsToViewModel_function(newSettingsData);

            // only reported once the reset actually happened
            new PNotify({
                title: "Default settings restored!",
                text: "The plugin settings have been reset but not yet been saved.<br>Remember to save. If you reset the settings accidentally, you can reload the page to revert.",
                type: "info",
                hide: true
            });
        } catch (error) {
            console.error("ERROR: Plugin settings reset", error);

            new PNotify({
                title: "Plugin settings reset",
                text: "An error occurred while loading the default settings. The settings have not been changed.",
                type: "error",
                hide: true
            });
        }
    };

    this.assignResetSettingsFeature = function (
        PLUGIN_ID_string,
        mapSettingsToViewModel_function
    ) {
        const settingsPaneHref = `#settings_plugin_${PLUGIN_ID_string}`;
        const $settingsDialog = $("#settings_dialog");
        const $settingsTabs = $("#settingsTabs");

        let $resetButton = $(RESET_BUTTON_SELECTOR);
        if ($resetButton.length === 0) {
            $settingsDialog.find(".modal-footer > .aboutlink").after(RESET_BUTTON_HTML);
            $resetButton = $(RESET_BUTTON_SELECTOR);
        }

        $resetButton.off("click" + EVENT_NAMESPACE).on("click" + EVENT_NAMESPACE, () => {
            resetSettings(PLUGIN_ID_string, mapSettingsToViewModel_function);
        });

        // Shown only while this plugin's settings are on screen. The active tab survives
        // closing the dialog, so it is checked again whenever the dialog opens.
        const updateResetButtonVisibility = () => {
            const activeHref = $settingsTabs.find("li.active > a").attr("href");
            $resetButton.toggle(activeHref === settingsPaneHref);
        };

        $settingsTabs
            .off("shown" + EVENT_NAMESPACE)
            .on("shown" + EVENT_NAMESPACE, 'a[data-toggle="tab"]', (event) => {
                $resetButton.toggle($(event.target).attr("href") === settingsPaneHref);
            });

        // "shown" and "hide" also bubble up from tooltips and tabs inside the dialog
        $settingsDialog
            .off("shown" + EVENT_NAMESPACE)
            .on("shown" + EVENT_NAMESPACE, (event) => {
                if (event.target === $settingsDialog.get(0)) {
                    updateResetButtonVisibility();
                }
            });
        $settingsDialog
            .off("hide" + EVENT_NAMESPACE)
            .on("hide" + EVENT_NAMESPACE, (event) => {
                if (event.target === $settingsDialog.get(0)) {
                    $resetButton.hide();
                }
            });
    };
}
