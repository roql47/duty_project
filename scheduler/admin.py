from django.contrib import admin
from .models import Nurse, Schedule, StaffingRequirement

@admin.register(Nurse)
class NurseAdmin(admin.ModelAdmin):
    list_display = ['name', 'employee_id', 'skill_level', 'is_night_keeper']
    search_fields = ['name', 'employee_id']
    list_filter = ['skill_level', 'is_night_keeper']

@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    list_display = ['nurse', 'date', 'shift']
    list_filter = ['date', 'shift']
    search_fields = ['nurse__name']

@admin.register(StaffingRequirement)
class StaffingRequirementAdmin(admin.ModelAdmin):
    list_display = ['shift', 'required_staff']
