"""Phase 1: models, roles, forced password change, and every page renders."""
import pytest
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.students.models import Student


@pytest.fixture
def student(db, section):
    user = User.objects.create_user("cse3b001", password="pw-student-123", role=Role.STUDENT)
    return Student.objects.create(
        user=user, roll_no="CSE3B001", name="Aarav Sharma", section=section
    )


def test_role_flags(admin_user, teacher, student):
    assert admin_user.is_admin_role and not admin_user.is_teacher_role
    assert teacher.user.is_teacher_role
    assert student.user.is_student_role


def test_post_login_routes_each_role(client, admin_user, teacher, student):
    for user, expected in [
        (admin_user, reverse("accounts:admin_dashboard")),
        (teacher.user, reverse("attendance:dashboard")),
        (student.user, reverse("accounts:student_dashboard")),
    ]:
        client.force_login(user)
        response = client.get(reverse("accounts:post_login"))
        assert response.status_code == 302
        assert response.url == expected
        client.logout()


def test_dashboards_render(client, admin_user, teacher, student):
    for user, url in [
        (admin_user, reverse("accounts:admin_dashboard")),
        (teacher.user, reverse("attendance:dashboard")),
        (student.user, reverse("accounts:student_dashboard")),
    ]:
        client.force_login(user)
        response = client.get(url)
        assert response.status_code == 200, url
        client.logout()


def test_login_page_renders_and_accepts_credentials(client, student):
    assert client.get(reverse("accounts:login")).status_code == 200
    response = client.post(
        reverse("accounts:login"),
        {"username": "cse3b001", "password": "pw-student-123"},
    )
    assert response.status_code == 302


def test_login_form_redisplays_errors_without_losing_username(client, student):
    response = client.post(
        reverse("accounts:login"), {"username": "cse3b001", "password": "wrong"}
    )
    assert response.status_code == 200
    assert b"don&#x27;t match" in response.content
    assert response.context["form"].data["username"] == "cse3b001"


def test_must_change_password_redirects_everywhere(client, student):
    student.user.set_initial_password("temp-pass-123")
    student.user.save()
    assert student.user.must_change_password is True

    client.force_login(student.user)
    response = client.get(reverse("accounts:student_dashboard"))
    assert response.status_code == 302
    assert response.url == reverse("accounts:force_password_change")

    # The change page itself is reachable.
    assert client.get(reverse("accounts:force_password_change")).status_code == 200

    response = client.post(
        reverse("accounts:force_password_change"),
        {"new_password1": "fresh-pass-4821", "new_password2": "fresh-pass-4821"},
    )
    assert response.status_code == 302
    student.user.refresh_from_db()
    assert student.user.must_change_password is False
    assert student.user.password_changed_at is not None
    assert client.get(reverse("accounts:student_dashboard")).status_code == 200


def test_cross_role_access_is_denied(client, student, teacher, admin_user):
    client.force_login(student.user)
    assert client.get(reverse("accounts:admin_dashboard")).status_code == 403
    assert client.get(reverse("attendance:dashboard")).status_code == 403
    client.logout()

    client.force_login(teacher.user)
    assert client.get(reverse("accounts:admin_dashboard")).status_code == 403
    assert client.get(reverse("accounts:student_dashboard")).status_code == 403


def test_anonymous_is_redirected_to_login(client):
    response = client.get(reverse("attendance:dashboard"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


def test_student_enrollment_state_transitions(student, settings):
    settings.ENROLLMENT_STEPS = 5
    assert student.refresh_enrollment_state() is False
    assert student.enrollment_progress_percent == 0
