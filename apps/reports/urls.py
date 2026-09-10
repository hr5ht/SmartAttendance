from django.urls import path

from apps.reports import views

app_name = "reports"

urlpatterns = [
    path("", views.index, name="index"),
    path("export.csv", views.export_csv, name="export_csv"),
    path("me/", views.my_attendance, name="my_attendance"),
]
