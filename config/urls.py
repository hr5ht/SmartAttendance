from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.accounts import views as account_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", account_views.home, name="home"),
    path("accounts/", include("apps.accounts.urls")),
    path("manage/", include("apps.students.urls")),
    path("student/", include("apps.face.urls")),
    path("attendance/", include("apps.attendance.urls")),
    path("reports/", include("apps.reports.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

admin.site.site_header = f"{settings.INSTITUTION_NAME} administration"
admin.site.site_title = settings.INSTITUTION_NAME
admin.site.index_title = "Data management"
