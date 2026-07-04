import re

from django.db import models
from django.core.exceptions import ValidationError

# core models for timetable system

class Department(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class Course(models.Model):
    name = models.CharField(max_length=100)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='courses')
    duration_years = models.PositiveSmallIntegerField(default=3)

    def __str__(self):
        return self.name


class Semester(models.Model):
    number = models.PositiveSmallIntegerField()
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='semesters')

    # scheduling constraints
    start_time = models.TimeField(
        null=True, blank=True,
        help_text="Semester daily start time (e.g. 08:00 AM)"
    )
    end_time = models.TimeField(
        null=True, blank=True,
        help_text="Semester daily end time (e.g. 04:00 PM)"
    )
    break_count = models.PositiveSmallIntegerField(
        default=0,
        help_text="Number of breaks during the day"
    )
    breaks = models.TextField(
        blank=True,
        help_text="Comma-separated break ranges (HH:MM-HH:MM)"
    )
    WORKING_DAY_CHOICES = [
        ('MON-FRI', 'Monday–Friday'),
        ('MON-SAT', 'Monday–Saturday'),
    ]
    working_days = models.CharField(
        max_length=7,
        choices=WORKING_DAY_CHOICES,
        default='MON-FRI',
        help_text="Days of week when classes are held"
    )
    max_lectures_per_day = models.PositiveSmallIntegerField(
        default=6,
        help_text="Maximum number of lecture slots allowed per day for this semester"
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    class Meta:
        unique_together = ('number', 'course')
        ordering = ['course', 'number']

    def __str__(self):
        return f"{self.course.name} - Semester {self.number}"


class Teacher(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='teachers',
    )
    additional_departments = models.ManyToManyField(
        Department,
        blank=True,
        related_name='additional_teachers',
        help_text='Other departments this teacher can teach for',
    )
    known_subject_names = models.TextField(
        blank=True,
        default='',
        help_text='Reference-only subject names entered by user (comma separated).',
    )
    # subjects the teacher is qualified to teach (used for filtering when
    # assigning actual subject instances).  This is *not* the same as the
    # Subject.teacher FK, which represents the current assignment for a
    # semester/division.
    subjects_known = models.ManyToManyField(
        'Subject',
        blank=True,
        related_name='qualified_teachers',
        help_text='Subjects this teacher is capable of teaching',
    )

    def __str__(self):
        return self.name

    def get_departments(self):
        departments = []
        if self.department_id:
            departments.append(self.department)
        additional = self.additional_departments.all().order_by('name')
        if self.department_id:
            additional = additional.exclude(pk=self.department_id)
        departments.extend(additional)
        return departments

    def get_known_subject_name_list(self):
        return [
            part.strip()
            for part in re.split(r'[,;\n]+', self.known_subject_names or '')
            if part.strip()
        ]

    def knows_subject_name(self, subject_name):
        target = (subject_name or '').strip().casefold()
        if not target:
            return False
        return any(name.casefold() == target for name in self.get_known_subject_name_list())


class Subject(models.Model):
    name = models.CharField(max_length=100)
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name='subjects')
    teacher = models.ForeignKey(Teacher, on_delete=models.SET_NULL, null=True, related_name='subjects')
    is_lab = models.BooleanField(default=False)
    total_hours = models.PositiveSmallIntegerField(
        default=1,
        help_text='Total lecture hours for this subject during the semester'
    )

    def __str__(self):
        return self.name

    @property
    def weekly_hours(self):
        """Approximate hours per week based on semester duration."""
        sem = self.semester
        if sem.start_date and sem.end_date and self.total_hours:
            days = (sem.end_date - sem.start_date).days + 1
            weeks = max(1, (days + 6) // 7)
            return -(-self.total_hours // weeks)  # ceiling division
        return None


class Division(models.Model):
    """Optional subdivisions within a semester (formerly tied to a course).

    During a transition the old ``course`` field is retained for compatibility but
    the preferred relation is ``semester``.  The ``save`` method keeps the two
    in sync (course copied from semester).
    """

    name = models.CharField(max_length=50)
    semester = models.ForeignKey(
        'Semester',
        on_delete=models.CASCADE,
        related_name='divisions',
        null=True,
        blank=True,
        help_text='Semester this division belongs to',
    )
    # legacy relation used by earlier migrations; kept nullable
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='course_divisions', null=True, blank=True)
    strength = models.PositiveIntegerField(
        default=0,
        help_text='Number of students in this division'
    )

    class Meta:
        unique_together = ('name', 'semester')
        ordering = ['semester__course', 'semester__number', 'name']

    def __str__(self):
        if self.semester:
            return f"{self.semester.course.name} S{self.semester.number} - {self.name}"
        elif self.course:
            return f"{self.course.name} - {self.name}"
        return self.name

    def save(self, *args, **kwargs):
        # keep legacy course field in sync for existing records
        if self.semester and self.semester.course_id:
            self.course_id = self.semester.course_id
        super().save(*args, **kwargs)


class DivisionSubject(models.Model):
    """Link a subject to a division with a specific teacher.

    Used by the smart timetable generator when producing a schedule for a
    particular division.  Subjects themselves belong to semesters; since
    different divisions may have different teachers for the same subject we
    store the override here.
    """
    division = models.ForeignKey(Division, on_delete=models.CASCADE, related_name='assignments')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='division_assignments')
    teacher = models.ForeignKey(Teacher, on_delete=models.SET_NULL, null=True, related_name='division_subjects')

    class Meta:
        unique_together = ('division', 'subject')
        ordering = ['division', 'subject']

    def __str__(self):
        return f"{self.division} - {self.subject.name} ({self.teacher})"


class LabBatchAssignment(models.Model):
    division = models.ForeignKey(Division, on_delete=models.CASCADE, related_name='lab_batch_assignments')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='lab_batch_assignments')
    batch_number = models.CharField(max_length=20)
    from_roll_no = models.PositiveIntegerField()
    to_roll_no = models.PositiveIntegerField()
    teacher = models.ForeignKey(Teacher, on_delete=models.PROTECT, related_name='lab_batch_assignments')
    laboratory = models.ForeignKey('Classroom', on_delete=models.PROTECT, related_name='lab_batch_assignments')

    class Meta:
        unique_together = ('division', 'subject', 'batch_number')
        ordering = ['division', 'subject', 'batch_number']

    def __str__(self):
        return (
            f"{self.division} - {self.subject.name} - Batch {self.batch_number} "
            f"({self.from_roll_no}-{self.to_roll_no})"
        )

    @property
    def batch_size(self):
        return (self.to_roll_no - self.from_roll_no + 1) if self.to_roll_no and self.from_roll_no else 0

    def clean(self):
        errors = {}
        if self.from_roll_no and self.to_roll_no and self.from_roll_no > self.to_roll_no:
            errors['to_roll_no'] = 'To roll number must be greater than or equal to from roll number.'

        if self.subject_id and self.division_id:
            if not self.subject.is_lab:
                errors['subject'] = 'Lab batch assignment is only allowed for lab subjects.'
            if self.division.semester_id and self.subject.semester_id != self.division.semester_id:
                errors['subject'] = 'Subject must belong to the selected division semester.'

        if self.laboratory_id:
            if not self.laboratory.is_lab:
                errors['laboratory'] = 'Only laboratory rooms can be assigned to lab batches.'
            if self.division_id and self.laboratory.division_id and self.laboratory.division_id != self.division_id:
                errors['laboratory'] = 'This laboratory is assigned to a different division.'

            if self.from_roll_no and self.to_roll_no:
                if self.batch_size > self.laboratory.capacity:
                    errors['laboratory'] = (
                        f'Batch size ({self.batch_size}) exceeds laboratory capacity '
                        f'({self.laboratory.capacity}).'
                    )

        if errors:
            raise ValidationError(errors)


class Classroom(models.Model):
    room_number = models.CharField(max_length=50)
    capacity = models.PositiveIntegerField()
    is_lab = models.BooleanField(default=False)
    division = models.ForeignKey(
        Division,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='classrooms',
        help_text='Division this room/lab is assigned to',
    )

    def __str__(self):
        return self.room_number


class TimeSlot(models.Model):
    start_time = models.TimeField()
    end_time = models.TimeField()

    def __str__(self):
        return f"{self.start_time.strftime('%H:%M')} - {self.end_time.strftime('%H:%M')}"


class Timetable(models.Model):
    DAYS = [
        ('MON', 'Monday'),
        ('TUE', 'Tuesday'),
        ('WED', 'Wednesday'),
        ('THU', 'Thursday'),
        ('FRI', 'Friday'),
        ('SAT', 'Saturday'),
    ]

    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name='timetables')
    division = models.ForeignKey(
        Division,
        on_delete=models.CASCADE,
        related_name='timetables',
        null=True,
        blank=True,
        help_text='Division this timetable entry belongs to',
    )
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE)
    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE)
    day = models.CharField(max_length=3, choices=DAYS)
    time_slot = models.ForeignKey(TimeSlot, on_delete=models.CASCADE)

    class Meta:
        unique_together = ('semester', 'division', 'day', 'time_slot', 'classroom')

    def clean(self):
        super().clean()
        if not (self.classroom_id and self.day and self.time_slot_id):
            return

        conflicts = Timetable.objects.filter(
            classroom_id=self.classroom_id,
            day=self.day,
            time_slot_id=self.time_slot_id,
        )
        if self.pk:
            conflicts = conflicts.exclude(pk=self.pk)

        conflict = conflicts.select_related(
            'semester__course',
            'division__semester__course',
            'time_slot',
        ).first()
        if not conflict:
            return

        if conflict.division_id:
            owner = f"{conflict.division.semester.course.name} ({conflict.division.name})"
        else:
            owner = conflict.semester.course.name

        raise ValidationError({
            'classroom': (
                "This classroom/laboratory is already engaged for "
                f"{owner} on {conflict.get_day_display()} "
                f"{conflict.time_slot.start_time.strftime('%H:%M')}-"
                f"{conflict.time_slot.end_time.strftime('%H:%M')}."
            )
        })

    def __str__(self):
        if self.division:
            return f"{self.semester} {self.division.name} - {self.day} {self.time_slot}"
        return f"{self.semester} - {self.day} {self.time_slot}"
