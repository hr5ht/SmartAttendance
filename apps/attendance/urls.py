from django.urls import path

from apps.attendance import views

app_name = "attendance"

urlpatterns = [
    path("", views.teacher_dashboard, name="dashboard"),
    path("session/<int:pk>/", views.session_detail, name="session_detail"),
    path("session/<int:pk>/process/", views.process_session_view, name="process_session"),
    path("session/<int:pk>/status/", views.session_status, name="session_status"),
    path("session/<int:pk>/images/", views.add_images, name="add_images"),
    path("session/<int:pk>/override/", views.override_record, name="override_record"),
    path("session/<int:pk>/assign/", views.assign_unknown_face, name="assign_face"),
    path("session/<int:pk>/confirm/", views.confirm_session, name="confirm_session"),
    path("session/<int:pk>/delete/", views.delete_session, name="delete_session"),
]
