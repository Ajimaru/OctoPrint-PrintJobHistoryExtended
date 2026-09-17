# coding=utf-8
from __future__ import absolute_import

from octoprint_PrintJobHistoryExtended.models.BaseModel import BaseModel
from octoprint_PrintJobHistoryExtended.models.PrintJobModel import PrintJobModel
from peewee import CharField, Model, DecimalField, FloatField, DateField, DateTimeField, TextField, ForeignKeyField, \
	IntegerField


class PrintJobSpoolMapModel(BaseModel):

	printJob = ForeignKeyField(PrintJobModel, backref='spoolMap', on_delete='CASCADE')

	spoolManagerId = IntegerField()
