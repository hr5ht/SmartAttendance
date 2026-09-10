from django.urls import path

from apps.students import views

app_name = "students"

urlpatterns = [
    path("students/", views.student_list, name="list"),
    path("students/new/", views.student_create, name="create"),
    path("students/<int:pk>/", views.student_detail, name="detail"),
    path("students/<int:pk>/edit/", views.student_edit, name="edit"),
    path("students/<int:pk>/reset-password/", views.reset_password, name="reset_password"),
    path("students/<int:pk>/clear-flag/", views.clear_flag, name="clear_flag"),
    path("credentials/", views.credentials, name="credentials"),
    path("credentials.csv", views.credentials_csv, name="credentials_csv"),
    path("resets/", views.reset_requests, name="resets"),
    path("resets/<int:pk>/decide/", views.decide_reset, name="decide_reset"),
]
