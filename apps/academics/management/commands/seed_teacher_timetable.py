"""Add a repeatable demo timetable without replacing existing classes."""
from datetime import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.academics.models import Teacher, TimetableSlot


class Command(BaseCommand):
    help = "Fill a teacher's Monday–Saturday demo timetable from their assignments."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="t.mehra")

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            teacher = Teacher.objects.get(user__username=options["username"])
        except Teacher.DoesNotExist as exc:
            raise CommandError("No teacher profile found for this username.") from exc
        assignments = list(teacher.assignments.filter(section__is_active=True).order_by(
            "section__year", "section__section_letter", "subject__code"
        ))
        if not assignments:
            raise CommandError("Assign this teacher to a section and subject first.")
        created = 0
        for weekday in range(6):
            for period in range(1, min(settings.PERIODS_PER_DAY, 8) + 1):
                if teacher.slots.filter(weekday=weekday).count() >= 3:
                    break
                if teacher.slots.filter(weekday=weekday, period=period).exists():
                    continue
                offset = (weekday + period - 1) % len(assignments)
                for assignment in assignments[offset:] + assignments[:offset]:
                    if TimetableSlot.objects.filter(
                        section=assignment.section, weekday=weekday, period=period
                    ).exists():
                        continue
                    # Keep each subject/class pair unique within the day.
                    if teacher.slots.filter(
                        section=assignment.section, subject=assignment.subject, weekday=weekday
                    ).exists():
                        continue
                    TimetableSlot.objects.create(
                        teacher=teacher, section=assignment.section, subject=assignment.subject,
                        weekday=weekday, period=period,
                        start_time=time(8 + period, 0), end_time=time(8 + period, 50),
                    )
                    created += 1
                    break
        self.stdout.write(self.style.SUCCESS(
            f"Added {created} timetable entries for {teacher.user.username}; existing entries preserved."
        ))
