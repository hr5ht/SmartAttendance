from django.contrib import admin

from apps.academics.models import (
    ClassSection,
    Department,
    Subject,
    Teacher,
    TeacherAssignment,
    TimetableSlot,
)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("code", "name")
    search_fields = ("code", "name")


@admin.register(ClassSection)
class ClassSectionAdmin(admin.ModelAdmin):
    list_display = ("name", "department", "year", "section_letter", "is_active")
    list_filter = ("department", "year", "section_letter", "is_active")
    search_fields = ("name",)
    # name is generated from the other three, so it is not an editable field.
    readonly_fields = ("name",)


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "department")
    list_filter = ("department",)
    search_fields = ("code", "name")


class TeacherAssignmentInline(admin.TabularInline):
    model = TeacherAssignment
    extra = 1
    autocomplete_fields = ("section", "subject")


@admin.register(Teacher)
class TeacherAdmin(admin.ModelAdmin):
    list_display = ("employee_id", "user", "department", "is_active")
    list_filter = ("department", "is_active")
    search_fields = ("employee_id", "user__username", "user__first_name", "user__last_name")
    inlines = [TeacherAssignmentInline]


@admin.register(TeacherAssignment)
class TeacherAssignmentAdmin(admin.ModelAdmin):
    list_display = ("teacher", "section", "subject")
    list_filter = ("section", "subject")


@admin.register(TimetableSlot)
class TimetableSlotAdmin(admin.ModelAdmin):
    list_display = ("section", "weekday", "period", "subject", "teacher", "start_time", "end_time")
    list_filter = ("section", "weekday", "subject")
