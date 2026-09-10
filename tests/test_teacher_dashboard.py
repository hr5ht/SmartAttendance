"""Teacher dashboard: class details, photo upload, the day's uploads, timetable."""
import datetime as dt

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from apps.academics.models import Teacher, TeacherAssignment, TimetableSlot
from apps.accounts.models import Role, User
from apps.attendance.models import AttendanceSession, SessionImage, SessionStatus
from tests.conftest import make_photo

DASHBOARD = reverse("attendance:dashboard")


@pytest.fixture
def monday():
    return dt.date(2026, 9, 7)  # a Monday


@pytest.fixture
def slot(teacher, section, subject, monday):
    return TimetableSlot.objects.create(
        section=section,
        subject=subject,
        teacher=teacher,
        weekday=monday.weekday(),
        period=3,
        start_time=dt.time(11, 0),
        end_time=dt.time(11, 50),
    )


def details(section, subject, day, period=3):
    return {
        "department": section.department_id,
        "year": section.year,
        "section": section.id,
        "subject": subject.id,
        "period": period,
        "date": day.isoformat(),
    }


# ---------------------------------------------------------------- rendering


def test_dashboard_shows_details_form_and_timetable(client, teacher, slot, monday):
    client.force_login(teacher.user)
    response = client.get(DASHBOARD, {"date": monday.isoformat()})
    assert response.status_code == 200

    body = response.content.decode()
    for field in ("id_department", "id_section", "id_subject", "id_period", "id_date"):
        assert field in body
    assert "CSE-3B" in body
    assert "11:00" in body  # the timetable slot's start time
    assert list(response.context["slots"]) == [slot]


def test_timetable_only_shows_that_weekday(client, teacher, slot, monday):
    client.force_login(teacher.user)
    tuesday = monday + dt.timedelta(days=1)
    response = client.get(DASHBOARD, {"date": tuesday.isoformat()})
    assert list(response.context["slots"]) == []
    assert response.context["day"] == tuesday


def test_dropdowns_offer_every_active_class(
    client, teacher, section, other_section, subject, other_subject
):
    """Default policy: any teacher may take any class."""
    client.force_login(teacher.user)
    form = client.get(DASHBOARD).context["form"]
    assert other_section in form.fields["section"].queryset
    assert other_subject in form.fields["subject"].queryset


def test_dropdowns_only_offer_assigned_classes_when_restricted(
    client, teacher, section, other_section, subject, other_subject, settings
):
    settings.RESTRICT_TEACHER_TO_ASSIGNMENTS = True
    client.force_login(teacher.user)
    form = client.get(DASHBOARD).context["form"]
    assert list(form.fields["section"].queryset) == [section]
    assert list(form.fields["subject"].queryset) == [subject]
    assert other_section not in form.fields["section"].queryset
    assert other_subject not in form.fields["subject"].queryset


# -------------------------------------------------------------------- upload


def test_upload_creates_draft_with_images(client, teacher, section, subject, monday):
    client.force_login(teacher.user)
    response = client.post(
        DASHBOARD,
        {**details(section, subject, monday), "images": [make_photo("a.jpg"), make_photo("b.jpg")]},
    )
    assert response.status_code == 302

    session = AttendanceSession.objects.get()
    assert session.status == SessionStatus.DRAFT
    assert session.section == section and session.subject == subject
    assert session.period == 3 and session.date == monday
    assert session.images.count() == 2
    assert [image.order for image in session.images.all()] == [0, 1]


def test_uploads_are_reencoded_with_uuid_names_and_no_exif(
    client, teacher, section, subject, monday
):
    client.force_login(teacher.user)
    client.post(
        DASHBOARD,
        {**details(section, subject, monday), "images": [make_photo("holiday.png"), make_photo("b.jpg")]},
    )
    image = SessionImage.objects.first()

    assert "holiday" not in image.image.name          # original name discarded
    assert image.image.name.endswith(".jpg")          # re-encoded as JPEG
    with image.image.open("rb") as handle:
        stored = Image.open(handle)
        assert stored.format == "JPEG"
        assert not stored.getexif()                   # EXIF stripped
    assert image.width and image.height


def test_oversized_photo_is_rejected_with_a_readable_message(
    client, teacher, section, subject, monday, settings
):
    settings.MAX_UPLOAD_SIZE_BYTES = 1024
    client.force_login(teacher.user)
    response = client.post(
        DASHBOARD,
        {**details(section, subject, monday), "images": [make_photo("big.jpg", size=(1600, 1200)), make_photo("b.jpg")]},
    )
    assert response.status_code == 200
    assert "MB" in " ".join(response.context["form"].errors["images"])
    assert not AttendanceSession.objects.exists()


def test_non_image_upload_is_rejected(client, teacher, section, subject, monday):
    client.force_login(teacher.user)
    response = client.post(
        DASHBOARD,
        {
            **details(section, subject, monday),
            "images": [
                SimpleUploadedFile("notes.pdf", b"%PDF-1.4 not an image", content_type="application/pdf"),
                make_photo("b.jpg"),
            ],
        },
    )
    assert response.status_code == 200
    assert "not a supported image" in " ".join(response.context["form"].errors["images"])
    assert not AttendanceSession.objects.exists()


def test_too_few_photos_is_rejected(client, teacher, section, subject, monday, settings):
    settings.MIN_SESSION_IMAGES = 2
    client.force_login(teacher.user)
    response = client.post(
        DASHBOARD, {**details(section, subject, monday), "images": [make_photo()]}
    )
    assert response.status_code == 200
    assert response.context["form"].errors["images"]
    assert not AttendanceSession.objects.exists()


def test_too_many_photos_is_rejected(client, teacher, section, subject, monday, settings):
    settings.MAX_SESSION_IMAGES = 3
    client.force_login(teacher.user)
    photos = [make_photo(f"{i}.jpg") for i in range(4)]
    response = client.post(
        DASHBOARD, {**details(section, subject, monday), "images": photos}
    )
    assert response.status_code == 200
    assert not AttendanceSession.objects.exists()


def test_form_preserves_input_when_a_field_is_wrong(client, teacher, section, subject, monday):
    client.force_login(teacher.user)
    response = client.post(
        DASHBOARD,
        {**details(section, subject, monday), "period": "", "images": [make_photo(), make_photo("b.jpg")]},
    )
    assert response.status_code == 200
    form = response.context["form"]
    assert form.errors["period"]
    assert form.data["section"] == str(section.id)
    assert form.data["subject"] == str(subject.id)


# -------------------------------------------------------- the day's uploads


def test_day_list_shows_only_that_day(client, teacher, section, subject, monday):
    client.force_login(teacher.user)
    client.post(DASHBOARD, {**details(section, subject, monday), "images": [make_photo(), make_photo("b.jpg")]})

    response = client.get(DASHBOARD, {"date": monday.isoformat()})
    assert len(response.context["sessions"]) == 1

    response = client.get(DASHBOARD, {"date": (monday + dt.timedelta(days=1)).isoformat()})
    assert len(response.context["sessions"]) == 0


def test_taken_slot_is_linked_back_to_its_timetable_entry(
    client, teacher, section, subject, monday, slot
):
    client.force_login(teacher.user)
    client.post(DASHBOARD, {**details(section, subject, monday), "images": [make_photo(), make_photo("b.jpg")]})
    response = client.get(DASHBOARD, {"date": monday.isoformat()})
    assert response.context["slots"][0].session is not None


def test_duplicate_session_for_the_same_period_is_refused(
    client, teacher, section, subject, monday
):
    client.force_login(teacher.user)
    payload = {**details(section, subject, monday), "images": [make_photo(), make_photo("b.jpg")]}
    client.post(DASHBOARD, payload)

    response = client.post(
        DASHBOARD,
        {**details(section, subject, monday), "images": [make_photo("c.jpg"), make_photo("d.jpg")]},
    )
    assert response.status_code == 200
    assert "already exists" in " ".join(response.context["form"].errors["period"])
    assert AttendanceSession.objects.count() == 1


def test_add_and_discard_photos_on_a_draft(client, teacher, section, subject, monday, settings):
    settings.MAX_SESSION_IMAGES = 3
    client.force_login(teacher.user)
    client.post(DASHBOARD, {**details(section, subject, monday), "images": [make_photo(), make_photo("b.jpg")]})
    session = AttendanceSession.objects.get()

    client.post(reverse("attendance:add_images", args=[session.pk]), {"images": [make_photo("c.jpg")]})
    assert session.images.count() == 3

    client.post(reverse("attendance:delete_session", args=[session.pk]))
    assert not AttendanceSession.objects.exists()


# --------------------------------------------------------------- permissions


def test_teacher_may_take_a_class_they_are_not_assigned_to(
    client, teacher, other_section, other_subject, monday
):
    """Default policy: an unassigned section and subject are both accepted."""
    client.force_login(teacher.user)
    response = client.post(
        DASHBOARD,
        {
            **details(other_section, other_subject, monday),
            "images": [make_photo(), make_photo("b.jpg")],
        },
    )
    assert response.status_code == 302

    session = AttendanceSession.objects.get()
    assert session.section == other_section
    assert session.subject == other_subject
    assert session.teacher == teacher


def test_teacher_can_open_and_process_a_class_they_are_not_assigned_to(
    client, teacher, other_section, other_subject, monday
):
    """The session they just created must stay reachable, or creation is useless."""
    client.force_login(teacher.user)
    client.post(
        DASHBOARD,
        {
            **details(other_section, other_subject, monday),
            "images": [make_photo(), make_photo("b.jpg")],
        },
    )
    session = AttendanceSession.objects.get()

    assert client.get(session.get_absolute_url()).status_code == 200
    assert client.get(reverse("attendance:session_status", args=[session.pk])).status_code == 200


def test_teacher_cannot_post_a_section_they_are_not_assigned_to_when_restricted(
    client, teacher, other_section, subject, monday, settings
):
    settings.RESTRICT_TEACHER_TO_ASSIGNMENTS = True
    client.force_login(teacher.user)
    response = client.post(
        DASHBOARD,
        {**details(other_section, subject, monday), "images": [make_photo(), make_photo("b.jpg")]},
    )
    assert response.status_code == 200
    assert response.context["form"].errors["section"]
    assert not AttendanceSession.objects.exists()


def test_teacher_cannot_post_a_subject_they_are_not_assigned_to_when_restricted(
    client, teacher, section, other_subject, monday, settings
):
    settings.RESTRICT_TEACHER_TO_ASSIGNMENTS = True
    client.force_login(teacher.user)
    response = client.post(
        DASHBOARD,
        {**details(section, other_subject, monday), "images": [make_photo(), make_photo("b.jpg")]},
    )
    assert response.status_code == 200
    assert response.context["form"].errors["subject"]
    assert not AttendanceSession.objects.exists()


def test_teacher_cannot_touch_another_teachers_session(
    client, teacher, dept, section, subject, monday
):
    other_user = User.objects.create_user("t.other", password="pw-other-123", role=Role.TEACHER)
    other = Teacher.objects.create(user=other_user, department=dept, employee_id="EMP-2")
    TeacherAssignment.objects.create(teacher=other, section=section, subject=subject)

    client.force_login(teacher.user)
    client.post(DASHBOARD, {**details(section, subject, monday), "images": [make_photo(), make_photo("b.jpg")]})
    session = AttendanceSession.objects.get()
    client.logout()

    client.force_login(other_user)
    assert client.post(reverse("attendance:add_images", args=[session.pk]), {"images": [make_photo("x.jpg")]}).status_code == 404
    assert client.post(reverse("attendance:delete_session", args=[session.pk])).status_code == 404
    assert AttendanceSession.objects.filter(pk=session.pk).exists()


def test_students_cannot_reach_the_dashboard(client, section):
    user = User.objects.create_user("cse3b009", password="pw-student-123", role=Role.STUDENT)
    client.force_login(user)
    assert client.get(DASHBOARD).status_code == 403
