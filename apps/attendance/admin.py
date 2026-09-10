from django.contrib import admin

from apps.attendance.models import (
    AttendanceRecord,
    AttendanceSession,
    DetectedFace,
    SessionImage,
)


class SessionImageInline(admin.TabularInline):
    model = SessionImage
    extra = 0
    readonly_fields = ("status", "faces_detected", "faces_matched", "elapsed_seconds")


class AttendanceRecordInline(admin.TabularInline):
    model = AttendanceRecord
    extra = 0
    autocomplete_fields = ("student",)


@admin.register(AttendanceSession)
class AttendanceSessionAdmin(admin.ModelAdmin):
    list_display = ("date", "section", "subject", "period", "teacher", "status", "email_sent")
    list_filter = ("status", "section", "subject", "email_sent", "date")
    date_hierarchy = "date"
    inlines = [SessionImageInline, AttendanceRecordInline]


@admin.register(DetectedFace)
class DetectedFaceAdmin(admin.ModelAdmin):
    list_display = ("id", "session_image", "det_score", "matched_student", "similarity", "margin")
    list_filter = ("assigned_manually",)
    exclude = ("embedding",)


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ("session", "student", "status", "source", "confidence")
    list_filter = ("status", "source", "session__section")
    search_fields = ("student__roll_no", "student__name")
