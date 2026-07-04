from django.core.management.base import BaseCommand
from scheduler.models import (
    Department, Course, Semester, Teacher, Subject, Classroom, TimeSlot
)
import random
from datetime import time


class Command(BaseCommand):
    help = 'Create sample data for timetable system'

    def handle(self, *args, **options):
        self.stdout.write('Deleting old data...')
        Department.objects.all().delete()
        Classroom.objects.all().delete()
        TimeSlot.objects.all().delete()
        Teacher.objects.all().delete()
        Course.objects.all().delete()
        Semester.objects.all().delete()
        Subject.objects.all().delete()

        self.stdout.write('Creating departments...')
        depts = []
        for name in ['Computer Science', 'Mechanical', 'Electrical']:
            depts.append(Department.objects.create(name=name, description=f'{name} department'))

        self.stdout.write('Creating classrooms...')
        rooms = []
        for i in range(1, 11):
            rooms.append(Classroom.objects.create(
                room_number=f'R{i}', capacity=30, is_lab=(i % 3 == 0)
            ))

        self.stdout.write('Creating time slots...')
        slots = []
        start = 9
        for i in range(6):
            st = time(start + i, 0)
            et = time(start + i + 1, 0)
            slots.append(TimeSlot.objects.create(start_time=st, end_time=et))

        self.stdout.write('Creating teachers...')
        teachers = []
        for i in range(1, 11):
            dept = random.choice(depts)
            teachers.append(Teacher.objects.create(
                name=f'Teacher {i}',
                email=f'teacher{i}@college.edu',
                department=dept
            ))

        self.stdout.write('Creating courses and semesters...')
        courses = []
        for i in range(1, 6):
            dept = random.choice(depts)
            c = Course.objects.create(name=f'Course {i}', department=dept, duration_years=3)
            courses.append(c)
            # create two semesters per course
            for num in [1, 2]:
                Semester.objects.create(number=num, course=c)

        self.stdout.write('Creating subjects...')
        for sem in Semester.objects.all():
            for j in range(1, 5):
                Subject.objects.create(
                    name=f'Subject {sem.id}-{j}',
                    semester=sem,
                    teacher=random.choice(teachers),
                    is_lab=(j % 4 == 0)
                )

        self.stdout.write(self.style.SUCCESS('Sample data created successfully.'))
