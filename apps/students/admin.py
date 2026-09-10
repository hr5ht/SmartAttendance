from django.contrib import admin

from apps.students.models import EnrollmentResetRequest, Student


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("roll_no", "name", "section", "email", "face_enrolled", "flagged_for_review", "is_active")
    list_filter = ("section", "face_enrolled", "flagged_for_review", "is_active")
    search_fields = ("roll_no", "name", "email", "user__username")
    autocomplete_fields = ("section",)
    readonly_fields = ("enrollment_completed_at", "created_at")


@admin.register(EnrollmentResetRequest)
class EnrollmentResetRequestAdmin(admin.ModelAdmin):
    list_display = ("student", "status", "created_at", "decided_at", "decided_by")
    list_filter = ("status",)
    search_fields = ("student__roll_no", "student__name")
