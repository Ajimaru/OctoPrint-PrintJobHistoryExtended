# coding=utf-8
from __future__ import absolute_import

from octoprint_PrintJobHistoryExtended.models.PrintJobModel import PrintJobModel
from octoprint_PrintJobHistoryExtended.models.BaseModel import BaseModel
# from octoprint_PrintJobHistoryExtended.models.PrintJobModel import PrintJobModel
from peewee import CharField, Model, DecimalField, FloatField, DateField, DateTimeField, TextField, BooleanField, ForeignKeyField


# Since V7
class CostModel(BaseModel):

	printJob = ForeignKeyField(PrintJobModel, backref='costs', on_delete='CASCADE', null=True, unique=True)

	# maybe later add costs-settings, like Purchase price, Maintenance costs...

	#             var costData = {
	#                 filename: filename,
	#                 filepath: filepath,
	#                 filamentCost: filamentCost,
	#                 electricityCost: electricityCost,
	#                 printerCost: printerCost,
	#                 otherCostLabel: otherCostLabel,
	#                 otherCost: otherCost,
	#             }

	totalCosts = FloatField(null=True)
	filamentCost = FloatField(null=True)
	electricityCost = FloatField(null=True)
	printerCost = FloatField(null=True)
	otherCostLabel = TextField(null=True)
	otherCost = FloatField(null=True)
	withDefaultSpoolValues = BooleanField(null=True)

	# Since V10
	# Energy measured for this job, and where the electricity figure came from ("tasmota"
	# or "none"). Kept alongside the cost so a later change of the kWh price stays traceable.
	electricityKwh = FloatField(null=True)
	costSource = CharField(null=True)
