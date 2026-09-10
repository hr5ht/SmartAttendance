from django.urls import path

from apps.face import views

app_name = "face"

urlpatterns = [
    path("enroll/", views.enroll, name="enroll"),
    path("enroll/capture/", views.capture, name="capture"),
    path("enroll/reset/", views.request_reset, name="request_reset"),
]
