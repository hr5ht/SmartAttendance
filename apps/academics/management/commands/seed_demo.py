"""Create a small but realistic dataset to develop against.

Idempotent: running it twice leaves the same records. Passwords for the demo
accounts are printed so they can be used immediately.
"""
from __future__ import annotations

from datetime import time

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.academics.models import (
    ClassSection,
    Department,
    SectionLetter,
    Subject,
    Teacher,
    TeacherAssignment,
    TimetableSlot,
    Weekday,
    Year,
)
from apps.accounts.models import Role, User
from apps.students.models import Student

DEMO_PASSWORD = "demo-pass-2026"

BRANCHES = [
    ("CSE", "Computer Science and Engineering"),
    ("IT", "Information Technology"),
    ("ECE", "Electronics and Communication Engineering"),
]

# Every branch runs all four years, each split into sections A, B and C.
YEARS = [Year.FIRST, Year.SECOND, Year.THIRD, Year.FOURTH]
LETTERS = [SectionLetter.A, SectionLetter.B, SectionLetter.C]

# Subjects are per branch and per year, keyed (branch code, year).
SUBJECTS = {
    ("CSE", 1): [("CS101", "Programming Fundamentals"), ("CS102", "Discrete Mathematics")],
    ("CSE", 2): [("CS201", "Data Structures"), ("CS202", "Computer Organisation")],
    ("CSE", 3): [("CS301", "Database Management Systems"), ("CS302", "Operating Systems"),
                 ("CS303", "Computer Networks")],
    ("CSE", 4): [("CS401", "Machine Learning"), ("CS402", "Distributed Systems")],
    ("IT", 1): [("IT101", "Web Technologies"), ("IT102", "Digital Logic")],
    ("IT", 2): [("IT201", "Object Oriented Programming"), ("IT202", "Software Engineering")],
    ("IT", 3): [("IT301", "Information Security"), ("IT302", "Cloud Computing")],
    ("IT", 4): [("IT401", "Data Analytics"), ("IT402", "Mobile Application Development")],
    ("ECE", 1): [("EC101", "Circuit Theory"), ("EC102", "Engineering Physics")],
    ("ECE", 2): [("EC201", "Analog Electronics"), ("EC202", "Signals and Systems")],
    ("ECE", 3): [("EC301", "Digital Signal Processing"), ("EC302", "Microprocessors")],
    ("ECE", 4): [("EC401", "VLSI Design"), ("EC402", "Embedded Systems")],
}

# (username, first, last, employee id, branch, [(year, subject code), ...])
TEACHERS = [
    ("t.mehra", "Anita", "Mehra", "EMP-101", "CSE", [(3, "CS301"), (2, "CS201")]),
    ("r.iyer", "Rohit", "Iyer", "EMP-102", "CSE", [(3, "CS302"), (3, "CS303")]),
    ("s.khan", "Sara", "Khan", "EMP-201", "IT", [(3, "IT301"), (1, "IT101")]),
    ("v.das", "Vivek", "Das", "EMP-301", "ECE", [(3, "EC301"), (4, "EC401")]),
]

STUDENT_NAMES = [
    "Aarav Sharma", "Diya Nair", "Ishaan Verma", "Kavya Reddy", "Manav Gupta",
    "Neha Joshi", "Omkar Patil", "Priya Menon", "Rahul Bose", "Sana Qureshi",
    "Tanvi Desai", "Vikram Rao", "Aditi Rane", "Kabir Malhotra", "Riya Sen",
    "Arjun Pillai", "Meera Kulkarni", "Nikhil Chawla",
]

# Only a few sections get a roster; creating students for all 36 would be noise.
POPULATED = [("CSE", 3, "A"), ("CSE", 3, "B"), ("IT", 3, "A"), ("ECE", 3, "A")]


class Command(BaseCommand):
    help = "Seed branches, years, sections, subjects, teachers and students."

    def add_arguments(self, parser):
        parser.add_argument(
            "--students-per-section", type=int, default=6,
            help="How many students to create in each populated section (default 6).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        per_section = options["students_per_section"]

        departments = {}
        for code, name in BRANCHES:
            department, _ = Department.objects.get_or_create(
                code=code, defaults={"name": name}
            )
            departments[code] = department

        # The full grid: 3 branches x 4 years x 3 sections.
        sections = {}
        for code, department in departments.items():
            for year in YEARS:
                for letter in LETTERS:
                    section, _ = ClassSection.objects.get_or_create(
                        department=department, year=year, section_letter=letter
                    )
                    sections[(code, int(year), str(letter))] = section

        subjects = {}
        for (code, year), entries in SUBJECTS.items():
            for subject_code, subject_name in entries:
                subject, _ = Subject.objects.get_or_create(
                    code=subject_code,
                    defaults={"name": subject_name, "department": departments[code]},
                )
                subjects[subject_code] = subject

        admin_user, created = User.objects.get_or_create(
            username="admin",
            defaults={
                "role": Role.ADMIN,
                "email": "admin@example.edu",
                "first_name": "Site",
                "last_name": "Administrator",
                "is_staff": True,
                "is_superuser": True,
            },
        )
        if created:
            admin_user.set_password(DEMO_PASSWORD)
            admin_user.save()

        teachers = {}
        for username, first, last, emp_id, branch, taught in TEACHERS:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "role": Role.TEACHER,
                    "first_name": first,
                    "last_name": last,
                    "email": f"{username}@example.edu",
                    "is_staff": False,
                },
            )
            if created:
                user.set_password(DEMO_PASSWORD)
                user.save()
            teacher, _ = Teacher.objects.get_or_create(
                user=user,
                defaults={"department": departments[branch], "employee_id": emp_id},
            )
            teachers[username] = teacher

            # A teacher takes their subject across all three sections of that year.
            for year, subject_code in taught:
                for letter in LETTERS:
                    TeacherAssignment.objects.get_or_create(
                        teacher=teacher,
                        section=sections[(branch, year, str(letter))],
                        subject=subjects[subject_code],
                    )

        # A Monday timetable so the session form can pre-fill from a slot.
        slot_plan = [
            (("CSE", 3, "A"), 1, "CS301", "t.mehra", time(9, 0), time(9, 50)),
            (("CSE", 3, "A"), 2, "CS302", "r.iyer", time(10, 0), time(10, 50)),
            (("CSE", 3, "B"), 3, "CS301", "t.mehra", time(11, 0), time(11, 50)),
            (("CSE", 3, "B"), 4, "CS303", "r.iyer", time(12, 0), time(12, 50)),
            (("IT", 3, "A"), 2, "IT301", "s.khan", time(10, 0), time(10, 50)),
            (("ECE", 3, "A"), 3, "EC301", "v.das", time(11, 0), time(11, 50)),
        ]
        for key, period, subject_code, teacher_key, start, end in slot_plan:
            TimetableSlot.objects.get_or_create(
                section=sections[key],
                weekday=Weekday.MONDAY,
                period=period,
                defaults={
                    "subject": subjects[subject_code],
                    "teacher": teachers[teacher_key],
                    "start_time": start,
                    "end_time": end,
                },
            )

        created_students = 0
        for section_index, key in enumerate(POPULATED):
            section = sections[key]
            for i in range(per_section):
                name = STUDENT_NAMES[(section_index * per_section + i) % len(STUDENT_NAMES)]
                roll_no = f"{section.name.replace('-', '')}{i + 1:03d}"
                if Student.objects.filter(roll_no=roll_no).exists():
                    continue
                username = roll_no.lower()
                user = User.objects.create(
                    username=username,
                    role=Role.STUDENT,
                    first_name=name.split()[0],
                    last_name=" ".join(name.split()[1:]),
                    email=f"{username}@example.edu",
                )
                user.set_initial_password(DEMO_PASSWORD)
                user.save()
                Student.objects.create(
                    user=user,
                    roll_no=roll_no,
                    name=name,
                    section=section,
                    email=user.email,
                )
                created_students += 1

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {len(departments)} branches, {len(sections)} sections "
            f"({len(YEARS)} years x {len(LETTERS)} sections each), "
            f"{len(subjects)} subjects, {len(teachers)} teachers, "
            f"{created_students} new students."
        ))
        self.stdout.write("")
        self.stdout.write("  admin      admin / " + DEMO_PASSWORD)
        for username, *_ in TEACHERS:
            self.stdout.write(f"  teacher    {username} / {DEMO_PASSWORD}")
        self.stdout.write(f"  student    cse3a001 / {DEMO_PASSWORD}  (must change on first login)")
