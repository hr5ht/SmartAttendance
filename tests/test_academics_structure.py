"""Branch x year x section: the shape of the academic structure."""
import pytest
from django.db import IntegrityError
from django.urls import reverse

from apps.academics.models import ClassSection, Department, SectionLetter, Year
from apps.attendance.models import AttendanceSession
from tests.conftest import make_photo


@pytest.fixture
def branches(db):
    return {
        code: Department.objects.create(code=code, name=name)
        for code, name in [
            ("CSE", "Computer Science and Engineering"),
            ("IT", "Information Technology"),
            ("ECE", "Electronics and Communication Engineering"),
        ]
    }


# ------------------------------------------------------------------- the model


def test_name_is_generated_from_branch_year_and_letter(branches):
    section = ClassSection.objects.create(
        department=branches["ECE"], year=Year.SECOND, section_letter=SectionLetter.C
    )
    assert section.name == "ECE-2C"
    assert section.label == "ECE · 2nd year · Section C"


def test_name_follows_the_fields_when_they_change(branches):
    section = ClassSection.objects.create(
        department=branches["IT"], year=Year.FIRST, section_letter=SectionLetter.A
    )
    assert section.name == "IT-1A"

    section.year = Year.FOURTH
    section.section_letter = SectionLetter.B
    section.save()
    assert section.name == "IT-4B"


def test_the_same_branch_year_and_letter_cannot_be_created_twice(branches):
    ClassSection.objects.create(
        department=branches["CSE"], year=Year.THIRD, section_letter=SectionLetter.A
    )
    with pytest.raises(IntegrityError):
        ClassSection.objects.create(
            department=branches["CSE"], year=Year.THIRD, section_letter=SectionLetter.A
        )


def test_the_same_year_and_letter_in_another_branch_is_fine(branches):
    cse = ClassSection.objects.create(
        department=branches["CSE"], year=Year.THIRD, section_letter=SectionLetter.A
    )
    ece = ClassSection.objects.create(
        department=branches["ECE"], year=Year.THIRD, section_letter=SectionLetter.A
    )
    assert cse.name == "CSE-3A" and ece.name == "ECE-3A"


def test_all_four_years_and_three_letters_are_offered(branches):
    assert [label for _, label in Year.choices] == [
        "1st year", "2nd year", "3rd year", "4th year"
    ]
    assert [value for value, _ in SectionLetter.choices] == ["A", "B", "C"]


def test_sections_sort_by_branch_then_year_then_letter(branches):
    for code in ("IT", "CSE"):
        for year in (Year.SECOND, Year.FIRST):
            for letter in (SectionLetter.B, SectionLetter.A):
                ClassSection.objects.create(
                    department=branches[code], year=year, section_letter=letter
                )
    assert list(ClassSection.objects.values_list("name", flat=True)) == [
        "CSE-1A", "CSE-1B", "CSE-2A", "CSE-2B",
        "IT-1A", "IT-1B", "IT-2A", "IT-2B",
    ]


# -------------------------------------------------------------- the seed grid


def test_seed_builds_every_branch_year_and_section(db):
    from django.core.management import call_command
    from io import StringIO

    call_command("seed_demo", stdout=StringIO())

    assert Department.objects.count() == 3
    assert ClassSection.objects.count() == 36          # 3 branches x 4 years x 3 sections
    for code in ("CSE", "IT", "ECE"):
        for year in (1, 2, 3, 4):
            for letter in ("A", "B", "C"):
                assert ClassSection.objects.filter(
                    department__code=code, year=year, section_letter=letter
                ).exists(), f"{code}-{year}{letter} missing"


def test_seed_is_idempotent(db):
    from django.core.management import call_command
    from io import StringIO

    call_command("seed_demo", stdout=StringIO())
    call_command("seed_demo", stdout=StringIO())
    assert ClassSection.objects.count() == 36


# ------------------------------------------------------- the teacher's form


def test_year_dropdown_offers_only_years_the_teacher_teaches(client, teacher, section):
    client.force_login(teacher.user)
    form = client.get(reverse("attendance:dashboard")).context["form"]

    years = [value for value, _ in form.fields["year"].choices if value != ""]
    assert years == [section.year]


def test_section_dropdown_shows_the_letter_once_branch_and_year_are_chosen(
    client, teacher, section
):
    client.force_login(teacher.user)
    form = client.get(reverse("attendance:dashboard")).context["form"]

    labels = [label for _, label in form.fields["section"].choices]
    assert "Section B" in labels


def test_a_year_that_does_not_match_the_section_is_rejected(
    client, teacher, section, subject
):
    """Branch and year filter the browser's list; the server re-checks them.

    The year posted here is one the teacher genuinely teaches, so it clears the
    choice field and reaches the cross-field check — which is the path that a
    hand-built POST would actually take.
    """
    import datetime as dt

    from apps.academics.models import TeacherAssignment

    first_year = ClassSection.objects.create(
        department=section.department, year=Year.FIRST, section_letter=SectionLetter.A
    )
    TeacherAssignment.objects.create(
        teacher=teacher, section=first_year, subject=subject
    )

    client.force_login(teacher.user)
    response = client.post(
        reverse("attendance:dashboard"),
        {
            "department": section.department_id,
            "year": Year.FIRST,             # valid for this teacher, wrong for the section
            "section": section.id,          # a 3rd year section
            "subject": subject.id,
            "period": 3,
            "date": dt.date(2026, 9, 7).isoformat(),
            "images": [make_photo("a.jpg"), make_photo("b.jpg")],
        },
    )
    assert response.status_code == 200
    assert "3rd year" in " ".join(response.context["form"].errors["section"])
    assert not AttendanceSession.objects.exists()


def test_a_branch_that_does_not_match_the_section_is_rejected(
    client, teacher, section, subject
):
    import datetime as dt

    from apps.academics.models import Subject, TeacherAssignment

    ece = Department.objects.create(code="ECE", name="Electronics")
    ece_section = ClassSection.objects.create(
        department=ece, year=section.year, section_letter=SectionLetter.A
    )
    ece_subject = Subject.objects.create(code="EC301", name="DSP", department=ece)
    TeacherAssignment.objects.create(
        teacher=teacher, section=ece_section, subject=ece_subject
    )

    client.force_login(teacher.user)
    response = client.post(
        reverse("attendance:dashboard"),
        {
            "department": ece.id,           # the teacher does teach ECE...
            "year": section.year,
            "section": section.id,          # ...but this section is CSE
            "subject": subject.id,
            "period": 3,
            "date": dt.date(2026, 9, 7).isoformat(),
            "images": [make_photo("a.jpg"), make_photo("b.jpg")],
        },
    )
    assert response.status_code == 200
    assert "isn't a ECE section" in " ".join(response.context["form"].errors["section"])
    assert not AttendanceSession.objects.exists()
