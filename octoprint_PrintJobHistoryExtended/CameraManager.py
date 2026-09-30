# coding=utf-8
from __future__ import absolute_import
import urllib.parse

import io
import shutil
import threading
import datetime
from PIL import Image
from PIL import ImageFile
from octoprint.webcams import get_snapshot_webcam

import logging
import os.path
import os
import zipfile

SNAPSHOT_BACKUP_FILENAME = "snapshots-backup-{timestamp}.zip"

class CameraManager(object):

    def __init__(self, parentLogger):
        self._logger = logging.getLogger(parentLogger.name + "." + self.__class__.__name__)

        self._snapshotStoragePath = None

    @staticmethod
    def buildSnapshotFilename(startDateTime):
        dateTimeThumb = startDateTime.strftime("%Y%m%d-%H%M%S") + ".jpg"
        return dateTimeThumb


    def initCamera(self, pluginDataBaseFolder, pluginBaseFolder):
        self._logger.info("Init CameraManager")

        snapshotStoragePath = pluginDataBaseFolder + "/snapshots"
        if not os.path.exists(snapshotStoragePath):
            os.makedirs(snapshotStoragePath)
        self._logger.info("Snapshot-Folder:"+snapshotStoragePath)

        self._snapshotStoragePath = snapshotStoragePath
        self._pluginDataBaseFolder = pluginDataBaseFolder
        self._pluginBaseFolder = pluginBaseFolder

        self._logger.info("Done CameraMenager")


    def getSnapshotFileLocation(self):
        return self._snapshotStoragePath


    def isSnapshotPresent(self, snapshotFilename):
        imageLocation = self.buildSnapshotFilenameLocation(snapshotFilename, False)
        result = os.path.exists(imageLocation)
        return result

    def buildSnapshotFilenameLocation(self, snapshotFilename, returnDefaultImage = True):
        if str(snapshotFilename).endswith(".png"):
            imageLocation = self._snapshotStoragePath + "/" + snapshotFilename
        else:
            imageLocation = self._snapshotStoragePath + "/" + snapshotFilename + ".png"

        if os.path.isfile(imageLocation):
            return imageLocation
        else:
            # try jpg for old files
            imageLocation = imageLocation.replace('.png','')

            if not str(imageLocation).endswith(".jpg"):
                imageLocation = imageLocation + ".jpg"

            if os.path.isfile(imageLocation):
                return imageLocation



        if returnDefaultImage:
            # defaultImageSnapshotName = self._pluginBaseFolder + "/static/images/no-photo-icon.jpg"
            defaultImageSnapshotName = self._pluginBaseFolder + "/static/images/no-image-icon-big.png"
            return defaultImageSnapshotName
        return imageLocation


    def renameSnapshotFilename(self, oldStartDateTime, newStartDateTime):
        oldFilename = CameraManager.buildSnapshotFilename(oldStartDateTime)
        oldFilenameLocation = self.buildSnapshotFilenameLocation(oldFilename, False)
        newFilename = CameraManager.buildSnapshotFilename(newStartDateTime)
        newFilenameLocation = self.buildSnapshotFilenameLocation(newFilename, False)

        # only rename if source file exists
        if os.path.isfile(oldFilenameLocation):
            # os.rename(oldFilenameLocation, newFilenameLocation)
            shutil.move(oldFilenameLocation, newFilenameLocation)

    def deleteSnapshot(self, snapshotFilename):
        imageLocation= self.buildSnapshotFilenameLocation(snapshotFilename, False)

        if os.path.isfile(imageLocation):
            os.remove(imageLocation)
        self._logger.info("Snapshot '" + imageLocation + "' deleted")


    def backupAllSnapshots(self, targetBackupFolder):

        now = datetime.datetime.now()
        currentDate = now.strftime("%Y%m%d-%H%M")
        backupZipFileName = SNAPSHOT_BACKUP_FILENAME.format(timestamp=currentDate)
        backupZipFilePath = os.path.join(targetBackupFolder, backupZipFileName)

        self._createZipFile(backupZipFilePath, self._snapshotStoragePath)

        return backupZipFilePath

    def reCreateSnapshotFolder(self):
        # delete current folder and recreate the folder

        shutil.rmtree(self._snapshotStoragePath)
        os.makedirs(self._snapshotStoragePath)


    def _createZipFile(self, zipname, path):
        # function to create a zip file
        # Parameters: zipname - name of the zip file; path - name of folder/file to be put in zip file

        zipf = zipfile.ZipFile(zipname, 'w', zipfile.ZIP_DEFLATED)
        zipf.setpassword(b"password")  # if you want to set password to zipfile

        # checks if the path is file or directory
        if os.path.isdir(path):
            for files in os.listdir(path):
                zipf.write(os.path.join(path, files), files)

        elif os.path.isfile(path):
            zipf.write(os.path.join(path), path)
        zipf.close()


    #######################################################################################   WEBCAM

    def getSnapshotWebcam(self):
        """
        The webcam OctoPrint takes snapshots with ("Webcam & Timelapse" settings), or None
        when none of the configured webcams can take one.

        Deliberately OctoPrint's webcam system and not the global "webcam.snapshot" URL: that
        one is only a deprecated compatibility view of the default webcam since OctoPrint 1.9.
        It lacks the orientation, ignores the chosen snapshot webcam and is going away.
        """
        try:
            webcam = get_snapshot_webcam()
        except Exception as error:
            self._logger.exception("Could not look up the snapshot webcam: " + str(error))
            return None
        if (webcam is None or webcam.config is None or webcam.config.canSnapshot != True):
            return None
        return webcam

    def takeSnapshot(self, snapshotFilename, sendErrorMessageToClientFunction=None, callbackFunction=None):
        """
        Stores a snapshot of the snapshot webcam as the image of a print job. Returns whether
        an image was stored, and hands the same answer to callbackFunction.
        """
        snapshotLocation = self._buildImageLocation(snapshotFilename)
        success = False

        webcam = self.getSnapshotWebcam()
        if (webcam is None):
            self._logger.info("No webcam that can take snapshots is configured, no snapshot taken")
            if (sendErrorMessageToClientFunction != None):
                sendErrorMessageToClientFunction("Take Snapshot", "No webcam that can take snapshots is configured in OctoPrint.")
        else:
            webcamConfig = webcam.config
            self._logger.info("Try taking snapshot '" + snapshotLocation + "' from webcam '" + str(webcamConfig.name) + "' provided by '" + str(webcam.providerIdentifier) + "'")
            try:
                # authentication, timeout and certificate checks are up to the provider
                snapshot = webcam.providerPlugin.take_webcam_snapshot(webcamConfig.name)
                imageData = b"".join(chunk for chunk in snapshot if chunk)
                imageData = self._applyWebcamOrientation(imageData, webcamConfig)
                self._writeImageFile(snapshotLocation, imageData)
                self._logger.info("Snapshot stored to '" + snapshotLocation + "'")
                success = True
            except Exception as error:
                self._logger.exception("Could not take a snapshot from webcam '" + str(webcamConfig.name) + "': " + str(error))
                if (sendErrorMessageToClientFunction != None):
                    sendErrorMessageToClientFunction("Take Snapshot", "Unable to get a snapshot from webcam '" + str(webcamConfig.displayName) + "': " + str(error))

        if (callbackFunction != None):
            callbackFunction(success)
        return success

    def takeSnapshotAsync(self, snapshotFilename, sendErrorMessageToClientFunction=None, callbackFunction=None):
        thread = threading.Thread(name='TakeSnapshot', target=self.takeSnapshot, args=(snapshotFilename, sendErrorMessageToClientFunction, callbackFunction,))
        thread.daemon = True
        thread.start()

    # Webcam images arrive the way the camera sees them. OctoPrint only turns them in the
    # browser and when rendering a timelapse, so a stored snapshot has to be turned here, in
    # the timelapse's order: flip horizontally, flip vertically, rotate 90 degrees
    # counter-clockwise.
    def _applyWebcamOrientation(self, imageData, webcamConfig):
        if (len(imageData) == 0):
            raise ValueError("The webcam delivered an empty image")

        # without this, the truncated frames some webcams deliver fail to load
        ImageFile.LOAD_TRUNCATED_IMAGES = True
        # parses the header, so an error page delivered instead of an image fails right here
        image = Image.open(io.BytesIO(imageData))

        flipH = webcamConfig.flipH == True
        flipV = webcamConfig.flipV == True
        rotate90 = webcamConfig.rotate90 == True
        if (flipH == False and flipV == False and rotate90 == False):
            return imageData

        if (flipH):
            image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if (flipV):
            image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        if (rotate90):
            image = image.transpose(Image.Transpose.ROTATE_90)
        # stays a JPEG: as PNG a camera picture grows about tenfold
        output = io.BytesIO()
        image.convert("RGB").save(output, format="JPEG", quality=90)
        return output.getvalue()

    #######################################################################################   PREVIEW IMAGE

    def storeThumbnail(self, snapshotFilename, imageData):
        """Stores a slicer preview image (bytes of any format PIL reads) as the image of a print job."""
        snapshotLocation = self._buildImageLocation(snapshotFilename)
        with Image.open(io.BytesIO(imageData)) as image:
            self._writeImageFile(snapshotLocation, self._toThumbnailPng(image))
        self._logger.info("Preview image stored to '" + snapshotLocation + "'")
        return True

    # Preview images stored by a thumbnail plugin, referenced from the file's metadata as
    # "plugin/{plugin_folder}/thumbnail/{image_path}"
    def takePluginThumbnail(self, snapshotFilename, thumbnailLocation):
        # clear timestamp in path
        thumbnailLocation = thumbnailLocation.split("?", 1)[0]

        splitPath = thumbnailLocation.split("/", 3)
        if (len(splitPath) != 4):
            self._logger.error("Can not split thumbnail path '" + thumbnailLocation + "'")
            return False

        pluginFolder = splitPath[1]
        # account for encoded filenames from the metadata
        thumbnailName = urllib.parse.unquote(splitPath[3])

        thumbnailLocation = self._pluginDataBaseFolder + "/../" + pluginFolder + "/" + thumbnailName
        if (os.path.isfile(thumbnailLocation) == False):
            self._logger.error("Thumbnail doesn't exists in: '" + thumbnailLocation + "'")
            return False

        snapshotLocation = self._buildImageLocation(snapshotFilename)
        self._logger.info("Try converting thumbnail '" + thumbnailLocation + "' to '" + snapshotLocation + "'")
        with Image.open(thumbnailLocation) as image:
            self._writeImageFile(snapshotLocation, self._toThumbnailPng(image))
        self._logger.info("Converting successfull!")
        return True

    # Slicer previews are transparent around the model. A barely visible white background
    # keeps them readable on light and dark themes alike,
    # see https://github.com/OllisGit/OctoPrint-PrintJobHistory/issues/160
    def _toThumbnailPng(self, image):
        image = image.convert("RGBA")
        background = Image.new("RGBA", image.size, (255, 255, 255, 9))
        background.paste(image, image)
        output = io.BytesIO()
        background.save(output, format="PNG")
        return output.getvalue()

    #######################################################################################   IMAGE FILES

    # The name comes from buildSnapshotFilename and ends in ".jpg", the stored file has always
    # carried ".png" on top. Kept like that, otherwise the existing images would not be found.
    def _buildImageLocation(self, snapshotFilename):
        if str(snapshotFilename).endswith(".png"):
            return self._snapshotStoragePath + "/" + snapshotFilename
        return self._snapshotStoragePath + "/" + snapshotFilename + ".png"

    # Written next to the target and then moved over it: the image may be requested by a
    # browser at any moment, and half a file would be served as a broken image.
    def _writeImageFile(self, imageLocation, imageData):
        temporaryLocation = imageLocation + ".part"
        with open(temporaryLocation, "wb") as imageFile:
            imageFile.write(imageData)
        os.replace(temporaryLocation, imageLocation)
