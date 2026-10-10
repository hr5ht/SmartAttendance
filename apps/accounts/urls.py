from django.contrib.auth import views as auth_views
from django.urls import path

from apps.accounts import views
from apps.accounts.forms import LoginForm

app_name = "accounts"

urlpatterns = [
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="accounts/login.html",
            authentication_form=LoginForm,
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("post-login/", views.post_login, name="post_login"),
    path("password/change/", views.ForcePasswordChangeView.as_view(), name="force_password_change"),
    path("dashboard/admin/", views.admin_dashboard, name="admin_dashboard"),
    path("dashboard/student/", views.student_dashboard, name="student_dashboard"),
]
