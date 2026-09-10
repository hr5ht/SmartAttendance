from django.contrib import admin

from apps.face.models import FaceEnrollment


@admin.register(FaceEnrollment)
class FaceEnrollmentAdmin(admin.ModelAdmin):
    list_display = ("student", "pose", "step_index", "det_score", "quality_score", "face_width", "created_at")
    list_filter = ("pose", "student__section")
    search_fields = ("student__roll_no", "student__name")
    readonly_fields = ("created_at",)
    exclude = ("embedding",)
