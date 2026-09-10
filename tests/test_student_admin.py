"""Admin student enrollment, credential issuing and the permission boundary."""
import csv
import io

import pytest
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.students.forms import username_for
from apps.students.models import Student
from apps.students.views import ISSUED_KEY

LIST = reverse("students:list")
CREATE = reverse("students:create")
CREDENTIALS = reverse("students:credentials")


def new_student_payload(section, roll_no="CSE3B007", name="Priya Menon"):
    return {"roll_no": roll_no, "name": name, "section": section.pk, "email": "p@example.edu"}


# ------------------------------------------------------------------- creation


def test_admin_creates_a_student_with_generated_credentials(client, admin_user, section):
    client.force_login(admin_user)
    response = client.post(CREATE, new_student_payload(section))
    assert response.status_code == 302

    student = Student.objects.get()
    assert student.roll_no == "CSE3B007"
    assert student.section == section
    assert student.user.role == Role.STUDENT
    assert student.user.username == "cse3b007"          # derived from the roll number
    assert student.user.must_change_password is True     # forced change on first login
    assert student.face_enrolled is False                # admin never uploads faces

    issued = client.session[ISSUED_KEY]
    assert len(issued) == 1
    assert issued[0]["username"] == "cse3b007"
    assert len(issued[0]["password"]) >= 8
    # The generated password must actually work.
    assert student.user.check_password(issued[0]["password"])


def test_generated_passwords_avoid_ambiguous_characters(client, admin_user, section):
    client.force_login(admin_user)
    for i in range(12):
        client.post(CREATE, new_student_payload(section, roll_no=f"CSE3B1{i:02d}"))
    for row in client.session[ISSUED_KEY]:
        assert not set(row["password"]) & set("O0oIl1")


def test_usernames_are_uniquified_on_collision(db, section):
    User.objects.create_user("cse3b007", password="x-pass-1234")
    assert username_for("CSE3B007") == "cse3b0072"


def test_duplicate_roll_number_is_rejected_with_the_typed_values_kept(
    client, admin_user, section
):
    client.force_login(admin_user)
    client.post(CREATE, new_student_payload(section))

    response = client.post(CREATE, new_student_payload(section, name="Someone Else"))
    assert response.status_code == 200
    assert "already registered" in " ".join(response.context["form"].errors["roll_no"])
    assert response.context["form"].data["name"] == "Someone Else"
    assert Student.objects.count() == 1
    assert User.objects.filter(role=Role.STUDENT).count() == 1  # no orphan account


def test_admin_never_uploads_a_face_on_the_create_form(client, admin_user, section):
    client.force_login(admin_user)
    form = client.get(CREATE).context["form"]
    assert set(form.fields) == {"roll_no", "name", "section", "email"}


# ---------------------------------------------------------------- credentials


def test_credentials_page_shows_the_issued_password_once(client, admin_user, section):
    client.force_login(admin_user)
    client.post(CREATE, new_student_payload(section))
    password = client.session[ISSUED_KEY][0]["password"]

    response = client.get(CREDENTIALS)
    assert response.status_code == 200
    assert password in response.content.decode()

    client.post(CREDENTIALS, {"action": "clear"})
    assert client.session[ISSUED_KEY] == []
    assert password not in client.get(CREDENTIALS).content.decode()


def test_credentials_export_as_csv(client, admin_user, section):
    client.force_login(admin_user)
    client.post(CREATE, new_student_payload(section))
    password = client.session[ISSUED_KEY][0]["password"]

    response = client.get(reverse("students:credentials_csv"))
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    assert "attachment" in response["Content-Disposition"]

    rows = list(csv.reader(io.StringIO(response.content.decode())))
    assert rows[0] == ["Roll number", "Name", "Section", "Username", "Initial password"]
    assert rows[1][0] == "CSE3B007" and rows[1][4] == password


def test_password_reset_issues_a_new_working_password(client, admin_user, section):
    client.force_login(admin_user)
    client.post(CREATE, new_student_payload(section))
    student = Student.objects.get()
    original = client.session[ISSUED_KEY][0]["password"]

    # Student sets their own password, clearing the forced-change flag.
    student.user.set_password("chosen-pass-9182")
    student.user.mark_password_changed()
    student.user.save()

    client.post(reverse("students:reset_password", args=[student.pk]))
    student.user.refresh_from_db()
    fresh = client.session[ISSUED_KEY][-1]["password"]

    assert fresh != original
    assert student.user.check_password(fresh)
    assert not student.user.check_password("chosen-pass-9182")
    assert student.user.must_change_password is True     # forced change again


# --------------------------------------------------------------------- roster


def test_roster_search_and_filters(client, admin_user, section, other_section):
    client.force_login(admin_user)
    client.post(CREATE, new_student_payload(section, "CSE3B007", "Priya Menon"))
    client.post(CREATE, {**new_student_payload(other_section, "CSE3A001", "Rahul Bose"),
                         "section": other_section.pk})
    Student.objects.filter(roll_no="CSE3B007").update(face_enrolled=True)

    assert len(client.get(LIST, {"q": "Priya"}).context["students"]) == 1
    assert len(client.get(LIST, {"section": section.pk}).context["students"]) == 1
    assert len(client.get(LIST, {"enrolled": "yes"}).context["students"]) == 1
    assert len(client.get(LIST, {"enrolled": "no"}).context["students"]) == 1
    assert len(client.get(LIST).context["students"]) == 2


def test_student_detail_and_edit_render(client, admin_user, section):
    client.force_login(admin_user)
    client.post(CREATE, new_student_payload(section))
    student = Student.objects.get()

    assert client.get(reverse("students:detail", args=[student.pk])).status_code == 200
    response = client.post(
        reverse("students:edit", args=[student.pk]),
        {**new_student_payload(section), "name": "Priya R Menon"},
    )
    assert response.status_code == 302
    student.refresh_from_db()
    assert student.name == "Priya R Menon"
    assert student.user.first_name == "Priya"


# ---------------------------------------------------------------- permissions


@pytest.mark.parametrize(
    "url_name,args",
    [
        ("students:list", ()), ("students:create", ()),
        ("students:credentials", ()), ("students:credentials_csv", ()),
        ("students:resets", ()),
    ],
)
def test_only_admins_reach_student_management(client, teacher, url_name, args):
    url = reverse(url_name, args=args)
    assert client.get(url).status_code == 302        # anonymous -> login

    client.force_login(teacher.user)
    assert client.get(url).status_code == 403


def test_a_teacher_cannot_reset_a_students_password(client, admin_user, teacher, section):
    client.force_login(admin_user)
    client.post(CREATE, new_student_payload(section))
    student = Student.objects.get()
    client.logout()

    client.force_login(teacher.user)
    response = client.post(reverse("students:reset_password", args=[student.pk]))
    assert response.status_code == 403


def test_a_student_cannot_see_another_students_record(client, admin_user, section):
    client.force_login(admin_user)
    client.post(CREATE, new_student_payload(section))
    student = Student.objects.get()
    client.logout()

    other = User.objects.create_user("cse3b999", password="pw-o-123", role=Role.STUDENT)
    client.force_login(other)
    assert client.get(reverse("students:detail", args=[student.pk])).status_code == 403
    assert client.get(LIST).status_code == 403
