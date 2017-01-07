from django.contrib import admin
from . import models

admin.site.register(models.Respondent, admin.ModelAdmin)
admin.site.register(models.DeviceType, admin.ModelAdmin)
admin.site.register(models.LocationType, admin.ModelAdmin)
admin.site.register(models.Occupation, admin.ModelAdmin)