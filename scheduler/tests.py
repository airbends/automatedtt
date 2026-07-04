import json
from django.test import TestCase
from datetime import date, time, datetime
from django.core import signing

from .models import (
    Department, Course, Semester, Teacher, Classroom, Subject, Division,
    DivisionSubject, LabBatchAssignment,
)
from .timetable_generator import (
    TimetableGenerationError,
    generate_timetable_for_semester,
    get_timeslots_for_semester,
)


class SubjectModelTests(TestCase):
    def test_weekly_hours_calculation(self):
        dept = Department.objects.create(name='Dept')
        course = Course.objects.create(name='Course', department=dept)
        # semester spanning 30 days (~5 weeks)
        sem = Semester.objects.create(
            number=1,
            course=course,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 30),
        )
        subj = Subject.objects.create(
            name='Subj',
            semester=sem,
            total_hours=20,
        )
        # weeks = ceil(30/7)=5 -> weekly = ceil(20/5)=4
        self.assertEqual(subj.weekly_hours, 4)

    def test_weekly_hours_none_when_dates_missing(self):
        dept = Department.objects.create(name='D2')
        course = Course.objects.create(name='C2', department=dept)
        sem = Semester.objects.create(number=1, course=course)
        subj = Subject.objects.create(name='Subj2', semester=sem, total_hours=10)
        self.assertIsNone(subj.weekly_hours)
        # also ensure SubjectForm validation catches missing dates when total_hours provided
        from .forms import SubjectForm
        # form no longer exposes the ``teacher`` field; omit it entirely
        form = SubjectForm(data={'name': 'X', 'semester': sem.id, 'is_lab': False, 'total_hours': 5})
        self.assertFalse(form.is_valid())
        self.assertIn('Semester start/end dates must be set', str(form.errors))


class TeacherDepartmentFilteringTests(TestCase):
    def setUp(self):
        self.dept_a = Department.objects.create(name='Dept A')
        self.dept_b = Department.objects.create(name='Dept B')
        self.dept_c = Department.objects.create(name='Dept C')
        self.course_a = Course.objects.create(name='Course A', department=self.dept_a)
        self.sem_a = Semester.objects.create(
            number=1,
            course=self.course_a,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 30),
        )
        self.div_a = Division.objects.create(name='A', semester=self.sem_a, course=self.course_a)
        self.subject_a = Subject.objects.create(
            name='Dept Subject',
            semester=self.sem_a,
            total_hours=6,
        )

    def test_subject_form_teacher_dropdown_filters_by_department(self):
        from .forms import SubjectForm

        teacher_in_dept = Teacher.objects.create(
            name='Dept Teacher',
            email='dept_teacher@example.com',
            department=self.dept_a,
        )
        teacher_other = Teacher.objects.create(
            name='Other Teacher',
            email='other_teacher@example.com',
            department=self.dept_b,
        )
        teacher_multi = Teacher.objects.create(
            name='Multi Teacher',
            email='multi_teacher@example.com',
            department=self.dept_b,
            known_subject_names='Data Structures, Algorithms',
        )
        teacher_multi.additional_departments.add(self.dept_a)

        form = SubjectForm(instance=self.subject_a, division=self.div_a)
        teacher_ids = set(form.fields['teacher'].queryset.values_list('id', flat=True))

        self.assertIn(teacher_in_dept.id, teacher_ids)
        self.assertIn(teacher_multi.id, teacher_ids)
        self.assertNotIn(teacher_other.id, teacher_ids)
        row_by_id = {row['id']: row for row in form.teacher_rows}
        self.assertIn(teacher_multi.id, row_by_id)
        self.assertIn('Multiple Department', row_by_id[teacher_multi.id]['departments'])
        self.assertIn('Dept A', row_by_id[teacher_multi.id]['departments'])
        self.assertIn('Dept B', row_by_id[teacher_multi.id]['departments'])
        self.assertIn('Data Structures', row_by_id[teacher_multi.id]['subjects'])

    def test_teacher_form_saves_multiple_departments(self):
        from .forms import TeacherForm

        form = TeacherForm(data={
            'name': 'Cross Department Teacher',
            'email': 'cross_dept@example.com',
            'department': '',
            'additional_departments': [self.dept_a.id, self.dept_b.id, self.dept_c.id],
            'subjects_known': 'Math, Physics',
        })
        self.assertTrue(form.is_valid(), form.errors)
        teacher = form.save()

        self.assertIsNone(teacher.department)
        additional_ids = set(teacher.additional_departments.values_list('id', flat=True))
        self.assertSetEqual(additional_ids, {self.dept_a.id, self.dept_b.id, self.dept_c.id})

    def test_teacher_form_allows_multiple_option_without_primary_department(self):
        from .forms import TeacherForm

        form = TeacherForm(data={
            'name': 'Only Multiple Teacher',
            'email': 'only_multiple@example.com',
            'department': '',
            'additional_departments': [self.dept_a.id, self.dept_b.id],
            'subjects_known': 'Math',
        })
        self.assertTrue(form.is_valid(), form.errors)
        teacher = form.save()

        self.assertIsNone(teacher.department)
        additional_ids = set(teacher.additional_departments.values_list('id', flat=True))
        self.assertSetEqual(additional_ids, {self.dept_a.id, self.dept_b.id})

    def test_teacher_form_department_field_has_multiple_departments_option(self):
        from .forms import TeacherForm
        from django import forms as django_forms

        form = TeacherForm()
        self.assertFalse(form.fields['department'].required)
        self.assertEqual(form.fields['department'].empty_label, 'Multiple Departments')
        self.assertIsInstance(
            form.fields['additional_departments'].widget,
            django_forms.CheckboxSelectMultiple,
        )
        self.assertTrue(form.fields['additional_departments'].widget.allow_multiple_selected)

    def test_teacher_form_ignores_additional_departments_when_primary_selected(self):
        from .forms import TeacherForm

        form = TeacherForm(data={
            'name': 'Single Department Teacher',
            'email': 'single_dept@example.com',
            'department': self.dept_a.id,
            'additional_departments': [self.dept_b.id, self.dept_c.id],
            'subjects_known': '',
        })
        self.assertTrue(form.is_valid(), form.errors)
        teacher = form.save()

        self.assertEqual(teacher.department_id, self.dept_a.id)
        self.assertFalse(teacher.additional_departments.exists())

    def test_teacher_form_renders_additional_department_options(self):
        from .forms import TeacherForm

        form = TeacherForm()
        html = str(form['additional_departments'])
        self.assertIn('Dept A', html)
        self.assertIn('Dept B', html)

    def test_department_detail_includes_multi_department_teachers(self):
        from django.urls import reverse

        Teacher.objects.create(
            name='Primary Teacher',
            email='primary_teacher@example.com',
            department=self.dept_a,
        )
        multi = Teacher.objects.create(
            name='Shared Teacher',
            email='shared_teacher@example.com',
            department=self.dept_b,
        )
        multi.additional_departments.add(self.dept_a)

        response = self.client.get(reverse('scheduler:department_detail', args=[self.dept_a.id]))

        self.assertContains(response, 'Primary Teacher')
        self.assertContains(response, 'Shared Teacher')


class TimetableGeneratorTests(TestCase):
    def setUp(self):
        dept = Department.objects.create(name='DeptX')
        course = Course.objects.create(name='CourseX', department=dept)
        self.sem = Semester.objects.create(
            number=1,
            course=course,
            start_time=time(9, 0),
            end_time=time(15, 0),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 28),
            working_days='MON-FRI',
            max_lectures_per_day=6,
        )
        self.teacher = Teacher.objects.create(name='T', email='t@x.com', department=dept)
        self.room = Classroom.objects.create(room_number='R', capacity=20, is_lab=False)

    def test_get_timeslots_for_semester_generates_slots(self):
        # no slots present initially
        slots = get_timeslots_for_semester(self.sem)
        self.assertTrue(slots)
        # each slot should be one hour long by default
        for slot in slots:
            self.assertEqual(
                (datetime.combine(datetime.min, slot.end_time) - datetime.combine(datetime.min, slot.start_time)).seconds,
                3600,
            )

    def test_semester_form_includes_max_lectures(self):
        from .forms import SemesterForm
        form = SemesterForm()
        self.assertIn('max_lectures_per_day', form.fields)

    def test_nonlab_subjects_daily_limit(self):
        # create multiple non-lab subjects with enough weekly hours that one day
        # will need a second slot; verify only one subject doubles and it's
        # contiguous
        subjs = []
        for i in range(3):
            s = Subject.objects.create(name=f'N{i}', semester=self.sem, teacher=self.teacher, total_hours=20)
            subjs.append(s)
        schedule = generate_timetable_for_semester(self.sem)
        # group by day, subject
        perday = {}
        doubles = []
        for e in schedule:
            key = (e.day, e.subject.name)
            perday[key] = perday.get(key, 0) + 1
            if perday[key] > 1:
                doubles.append((e.day, e.subject.name))
        # at most one subject should ever double, but it may happen on multiple days
        subjects_with_doubles = set(subj for _, subj in doubles)
        self.assertLessEqual(len(subjects_with_doubles), 1)
        if doubles:
            # verify each double is contiguous and diagnose triples
            for day, subj in doubles:
                entries = [e for e in schedule if e.day == day and e.subject.name == subj]
                if len(entries) != 2:
                    print("DEBUG schedule entries for triple:")
                    for e in entries:
                        print(day, subj, e.time_slot.start_time, e.time_slot.end_time)
                self.assertEqual(len(entries), 2)
                entries.sort(key=lambda x: x.time_slot.start_time)
                self.assertEqual(entries[0].time_slot.end_time, entries[1].time_slot.start_time)

    def test_lab_single_session_per_day(self):
        # a lab subject which would normally generate multiple sessions should
        # still appear at most once per day (i.e. one contiguous double-slot).
        # ensure at least one lab classroom is available
        Classroom.objects.create(room_number='L1', capacity=20, is_lab=True)
        lab = Subject.objects.create(name='L', semester=self.sem, teacher=self.teacher, total_hours=20, is_lab=True)
        schedule = generate_timetable_for_semester(self.sem)
        perday = {}
        for e in schedule:
            key = (e.day, e.subject.name)
            perday[key] = perday.get(key, 0) + 1
        # since lab sessions take two slots, count should never exceed 2 per day
        for key,count in perday.items():
            if key[1] == 'L':
                self.assertLessEqual(count, 2)
                if count == 2:
                    # ensure contiguous
                    entries = [e for e in schedule if e.day == key[0] and e.subject.name=='L']
                    entries.sort(key=lambda x: x.time_slot.start_time)
                    self.assertEqual(entries[0].time_slot.end_time, entries[1].time_slot.start_time)

    def test_weekly_hours_exact(self):
        subj = Subject.objects.create(name='W', semester=self.sem, teacher=self.teacher, total_hours=20)
        schedule = generate_timetable_for_semester(self.sem)
        count = sum(1 for e in schedule if e.subject == subj)
        expected = subj.weekly_hours or 1
        self.assertEqual(count, expected)

    def test_mon_sat_mode(self):
        # create fresh semester with Saturday included and make sure constraints
        # still hold there
        sem2 = Semester.objects.create(
            number=2,
            course=self.sem.course,
            start_time=self.sem.start_time,
            end_time=self.sem.end_time,
            start_date=self.sem.start_date,
            end_date=self.sem.end_date,
            working_days='MON-SAT',
            max_lectures_per_day=6,
        )
        # add a few subjects
        for i in range(4):
            Subject.objects.create(name=f'M{i}', semester=sem2, teacher=self.teacher, total_hours=15)
        # also add a lab
        Classroom.objects.create(room_number='L3', capacity=20, is_lab=True)
        Subject.objects.create(name='LabM', semester=sem2, teacher=self.teacher, total_hours=12, is_lab=True)
        schedule2 = generate_timetable_for_semester(sem2)
        # duplicate check
        perday = {}
        for e in schedule2:
            key = (e.day, e.subject.id, e.subject.is_lab)
            perday[key] = perday.get(key, 0) + 1
        for (day, sid, is_lab), cnt in perday.items():
            limit = 2 if is_lab else 1
            self.assertLessEqual(cnt, limit)

    def test_timetable_replication_by_weekly_hours(self):
        # make subjects with known totals
        # semester ~ 4 weeks -> weekly hours approx
        s1 = Subject.objects.create(name='A', semester=self.sem, teacher=self.teacher, total_hours=20)
        s2 = Subject.objects.create(name='B', semester=self.sem, teacher=self.teacher, total_hours=10)
        s3 = Subject.objects.create(name='C', semester=self.sem, teacher=self.teacher, total_hours=0)
        # compute weekly hours from model property
        wh1 = s1.weekly_hours or 1
        wh2 = s2.weekly_hours or 1
        wh3 = s3.weekly_hours or 1
        schedule = generate_timetable_for_semester(self.sem)
        counts = {}
        for e in schedule:
            counts[e.subject.name] = counts.get(e.subject.name, 0) + 1
        self.assertEqual(counts.get('A', 0), wh1)
        self.assertEqual(counts.get('B', 0), wh2)
        # subject with zero total should still appear once
        self.assertEqual(counts.get('C', 0), 1)

    def test_no_conflicts(self):
        # ensure generator doesn't place two subjects in same slot/day
        Subject.objects.create(name='X', semester=self.sem, teacher=self.teacher, total_hours=5)
        Subject.objects.create(name='Y', semester=self.sem, teacher=self.teacher, total_hours=5)
        schedule = generate_timetable_for_semester(self.sem)
        seen = set()
        for e in schedule:
            key = (e.day, e.time_slot)
            self.assertNotIn(key, seen)
            seen.add(key)

    def test_generator_auto_picks_available_room_when_others_blocked(self):
        subject = Subject.objects.create(name='AvailPick', semester=self.sem, teacher=self.teacher, total_hours=5)
        backup_room = Classroom.objects.create(room_number='R-BACKUP', capacity=80, is_lab=False)
        all_slots = get_timeslots_for_semester(self.sem)
        days = ['MON', 'TUE', 'WED', 'THU', 'FRI']
        blocked = {
            (self.room.id, day, slot.id)
            for day in days
            for slot in all_slots
        }

        schedule = generate_timetable_for_semester(self.sem, blocked_room_slots=blocked)
        subject_entries = [entry for entry in schedule if entry.subject_id == subject.id]
        self.assertTrue(subject_entries)
        self.assertTrue(all(entry.classroom_id == backup_room.id for entry in subject_entries))

    def test_generator_prefers_capacity_fit_for_classroom_and_laboratory(self):
        # classroom fit
        division = Division.objects.create(name='FIT-A', semester=self.sem, course=self.sem.course, strength=28)
        fitting_room = Classroom.objects.create(room_number='FIT-ROOM', capacity=30, is_lab=False)
        roomy_room = Classroom.objects.create(room_number='ROOMY-ROOM', capacity=120, is_lab=False)
        theory_subject = Subject.objects.create(
            name='FitTheory',
            semester=self.sem,
            teacher=self.teacher,
            total_hours=5,
            is_lab=False,
        )

        theory_schedule = generate_timetable_for_semester(self.sem, division=division)
        theory_entries = [entry for entry in theory_schedule if entry.subject_id == theory_subject.id]
        self.assertTrue(theory_entries)
        self.assertTrue(all(entry.classroom_id == fitting_room.id for entry in theory_entries))
        self.assertFalse(any(entry.classroom_id == roomy_room.id for entry in theory_entries))

        # laboratory fit (non-division scope uses automatic lab selection)
        lab_teacher = Teacher.objects.create(
            name='LabFitTeacher',
            email='lab_fit_teacher@x.com',
            department=self.sem.course.department,
        )
        small_lab = Classroom.objects.create(room_number='FIT-LAB', capacity=30, is_lab=True)
        big_lab = Classroom.objects.create(room_number='BIG-LAB', capacity=120, is_lab=True)
        lab_subject = Subject.objects.create(
            name='FitLab',
            semester=self.sem,
            teacher=lab_teacher,
            total_hours=10,
            is_lab=True,
        )
        lab_schedule = generate_timetable_for_semester(self.sem)
        lab_entries = [entry for entry in lab_schedule if entry.subject_id == lab_subject.id]
        self.assertTrue(lab_entries)
        self.assertTrue(all(entry.classroom_id == small_lab.id for entry in lab_entries))
        self.assertFalse(any(entry.classroom_id == big_lab.id for entry in lab_entries))

    def test_respects_max_lectures_per_day(self):
        # set a very low cap and add many subjects
        self.sem.max_lectures_per_day = 2
        self.sem.save()
        for i in range(5):
            Subject.objects.create(name=f'S{i}', semester=self.sem, teacher=self.teacher, total_hours=5)
        schedule = generate_timetable_for_semester(self.sem)
        # count per day
        perday = {}
        for e in schedule:
            perday[e.day] = perday.get(e.day, 0) + 1
        for day,count in perday.items():
            self.assertLessEqual(count, 2)

    def test_overloaded_division_override_still_generates(self):
        # mirror a realistic overloaded setup: subject teachers unset, but
        # division overrides are provided for all subjects.
        self.sem.start_time = time(8, 0)
        self.sem.end_time = time(12, 30)
        self.sem.breaks = '12:00-12:30'
        self.sem.max_lectures_per_day = 4
        self.sem.save()

        div = Division.objects.create(name='A1', semester=self.sem, course=self.sem.course)
        Classroom.objects.create(room_number='LABX', capacity=25, is_lab=True)

        subject_specs = [
            ('S1', False, 48),
            ('S2', False, 60),
            ('S3', False, 48),
            ('S4', False, 48),
            ('S5', False, 36),
            ('LabS', True, 30),
        ]
        overrides = {}
        for idx, (name, is_lab, hours) in enumerate(subject_specs, start=1):
            subj = Subject.objects.create(
                name=name,
                semester=self.sem,
                teacher=None,
                is_lab=is_lab,
                total_hours=hours,
            )
            t = Teacher.objects.create(
                name=f'OV{idx}',
                email=f'ov{idx}@x.com',
                department=self.sem.course.department,
            )
            DivisionSubject.objects.create(division=div, subject=subj, teacher=t)
            overrides[subj.id] = t

        schedule = generate_timetable_for_semester(self.sem, teacher_overrides=overrides)
        self.assertTrue(schedule)

        slots_per_day = len(get_timeslots_for_semester(self.sem))
        weekly_capacity = min(self.sem.max_lectures_per_day, slots_per_day) * 5
        self.assertLessEqual(len(schedule), weekly_capacity)
        self.assertTrue(all(e.teacher_id for e in schedule))

    def test_division_generation_falls_back_to_shared_lab(self):
        # Division has only a regular assigned room, but a shared unassigned
        # lab exists. Generator should still schedule lab subjects.
        div = Division.objects.create(name='B1', semester=self.sem, course=self.sem.course)
        Classroom.objects.create(room_number='B1-R1', capacity=40, is_lab=False, division=div)
        shared_lab = Classroom.objects.create(room_number='LAB-SHARED', capacity=25, is_lab=True)

        Subject.objects.create(
            name='TheoryX',
            semester=self.sem,
            teacher=self.teacher,
            total_hours=20,
            is_lab=False,
        )
        lab_teacher = Teacher.objects.create(
            name='LabTeacherX',
            email='labteacherx@x.com',
            department=self.sem.course.department,
        )
        lab_subject = Subject.objects.create(
            name='LabX',
            semester=self.sem,
            teacher=lab_teacher,
            total_hours=20,
            is_lab=True,
        )
        LabBatchAssignment.objects.create(
            division=div,
            subject=lab_subject,
            batch_number='A',
            from_roll_no=1,
            to_roll_no=20,
            teacher=lab_teacher,
            laboratory=shared_lab,
        )

        schedule = generate_timetable_for_semester(self.sem, division=div)
        self.assertTrue(schedule)

        lab_entries = [e for e in schedule if e.subject_id == lab_subject.id]
        self.assertTrue(lab_entries)
        self.assertTrue(all(e.classroom.is_lab for e in lab_entries))
        self.assertTrue(any(e.classroom_id == shared_lab.id for e in lab_entries))

    def test_parallel_lab_batches_run_in_same_slots(self):
        div = Division.objects.create(name='PB1', semester=self.sem, course=self.sem.course)
        lab_a = Classroom.objects.create(room_number='LAB-A', capacity=30, is_lab=True, division=div)
        lab_b = Classroom.objects.create(room_number='LAB-B', capacity=30, is_lab=True)

        Subject.objects.create(
            name='TheoryForParallel',
            semester=self.sem,
            teacher=self.teacher,
            total_hours=10,
            is_lab=False,
        )
        lab_subject = Subject.objects.create(
            name='ParallelLab',
            semester=self.sem,
            teacher=None,
            total_hours=20,
            is_lab=True,
        )
        t1 = Teacher.objects.create(name='BatchTeacher1', email='batch_t1@x.com', department=self.sem.course.department)
        t2 = Teacher.objects.create(name='BatchTeacher2', email='batch_t2@x.com', department=self.sem.course.department)
        LabBatchAssignment.objects.create(
            division=div,
            subject=lab_subject,
            batch_number='A',
            from_roll_no=1,
            to_roll_no=20,
            teacher=t1,
            laboratory=lab_a,
        )
        LabBatchAssignment.objects.create(
            division=div,
            subject=lab_subject,
            batch_number='B',
            from_roll_no=21,
            to_roll_no=40,
            teacher=t2,
            laboratory=lab_b,
        )

        schedule = generate_timetable_for_semester(self.sem, division=div)
        lab_entries = [e for e in schedule if e.subject_id == lab_subject.id]
        self.assertTrue(lab_entries)

        by_day_slot = {}
        for entry in lab_entries:
            key = (entry.day, entry.time_slot_id)
            by_day_slot.setdefault(key, []).append(entry)
        # strict parallel means each occupied lab slot must contain all batches.
        self.assertTrue(all(len(rows) == 2 for rows in by_day_slot.values()))

    def test_parallel_lab_batches_can_share_one_large_lab(self):
        div = Division.objects.create(name='PB3', semester=self.sem, course=self.sem.course, strength=80)
        shared_lab = Classroom.objects.create(room_number='LAB-100', capacity=100, is_lab=True, division=div)

        Subject.objects.create(
            name='TheoryForSharedLab',
            semester=self.sem,
            teacher=self.teacher,
            total_hours=10,
            is_lab=False,
        )
        lab_subject = Subject.objects.create(
            name='SharedLabSub',
            semester=self.sem,
            teacher=None,
            total_hours=20,
            is_lab=True,
        )
        t1 = Teacher.objects.create(name='SharedT1', email='shared_t1@x.com', department=self.sem.course.department)
        t2 = Teacher.objects.create(name='SharedT2', email='shared_t2@x.com', department=self.sem.course.department)
        t3 = Teacher.objects.create(name='SharedT3', email='shared_t3@x.com', department=self.sem.course.department)
        t4 = Teacher.objects.create(name='SharedT4', email='shared_t4@x.com', department=self.sem.course.department)

        LabBatchAssignment.objects.create(division=div, subject=lab_subject, batch_number='1', from_roll_no=1, to_roll_no=20, teacher=t1, laboratory=shared_lab)
        LabBatchAssignment.objects.create(division=div, subject=lab_subject, batch_number='2', from_roll_no=21, to_roll_no=40, teacher=t2, laboratory=shared_lab)
        LabBatchAssignment.objects.create(division=div, subject=lab_subject, batch_number='3', from_roll_no=41, to_roll_no=60, teacher=t3, laboratory=shared_lab)
        LabBatchAssignment.objects.create(division=div, subject=lab_subject, batch_number='4', from_roll_no=61, to_roll_no=80, teacher=t4, laboratory=shared_lab)

        schedule = generate_timetable_for_semester(self.sem, division=div)
        self.assertTrue(schedule)
        lab_entries = [e for e in schedule if e.subject_id == lab_subject.id]
        self.assertTrue(lab_entries)
        # One physical lab room entry per slot (not duplicated by batches).
        by_day_slot = {}
        for entry in lab_entries:
            key = (entry.day, entry.time_slot_id)
            by_day_slot.setdefault(key, []).append(entry)
        self.assertTrue(all(len(rows) == 1 for rows in by_day_slot.values()))

    def test_parallel_lab_batches_can_share_same_40_capacity_lab_for_two_batches(self):
        div = Division.objects.create(name='PB40', semester=self.sem, course=self.sem.course, strength=80)
        shared_lab = Classroom.objects.create(room_number='LAB-40', capacity=40, is_lab=True, division=div)
        # Division-level scheduling now enforces room capacity >= division strength.
        # Provide one suitable non-lab room for theory sessions.
        Classroom.objects.create(room_number='PB40-R1', capacity=80, is_lab=False, division=div)

        Subject.objects.create(
            name='TheoryFor40Share',
            semester=self.sem,
            teacher=self.teacher,
            total_hours=10,
            is_lab=False,
        )
        lab_subject = Subject.objects.create(
            name='Share40Sub',
            semester=self.sem,
            teacher=None,
            total_hours=20,
            is_lab=True,
        )
        t1 = Teacher.objects.create(name='Share40T1', email='share40_t1@x.com', department=self.sem.course.department)
        t2 = Teacher.objects.create(name='Share40T2', email='share40_t2@x.com', department=self.sem.course.department)
        LabBatchAssignment.objects.create(
            division=div,
            subject=lab_subject,
            batch_number='1',
            from_roll_no=1,
            to_roll_no=20,
            teacher=t1,
            laboratory=shared_lab,
        )
        LabBatchAssignment.objects.create(
            division=div,
            subject=lab_subject,
            batch_number='2',
            from_roll_no=21,
            to_roll_no=40,
            teacher=t2,
            laboratory=shared_lab,
        )

        schedule = generate_timetable_for_semester(self.sem, division=div)
        self.assertTrue(schedule)
        lab_entries = [e for e in schedule if e.subject_id == lab_subject.id]
        self.assertTrue(lab_entries)
        # Both batches share one physical lab room per slot.
        self.assertTrue(all(entry.classroom_id == shared_lab.id for entry in lab_entries))

    def test_parallel_lab_batches_raise_specific_capacity_error(self):
        div = Division.objects.create(name='PB2', semester=self.sem, course=self.sem.course)
        lab = Classroom.objects.create(room_number='LAB-SMALL', capacity=10, is_lab=True, division=div)

        Subject.objects.create(
            name='TheoryForCapacity',
            semester=self.sem,
            teacher=self.teacher,
            total_hours=10,
            is_lab=False,
        )
        lab_subject = Subject.objects.create(
            name='CapacityLab',
            semester=self.sem,
            teacher=None,
            total_hours=20,
            is_lab=True,
        )
        batch_teacher = Teacher.objects.create(
            name='CapacityTeacher',
            email='capacity_t@x.com',
            department=self.sem.course.department,
        )
        LabBatchAssignment.objects.create(
            division=div,
            subject=lab_subject,
            batch_number='C1',
            from_roll_no=1,
            to_roll_no=20,
            teacher=batch_teacher,
            laboratory=lab,
        )

        with self.assertRaises(TimetableGenerationError) as cm:
            generate_timetable_for_semester(self.sem, division=div)
        self.assertIn('batch C1 size', str(cm.exception))

    def test_delete_nonexistent_subject_redirects(self):
        # pressing delete link for an id that doesn't exist should redirect with warning
        from django.urls import reverse
        url = reverse('scheduler:subject_delete', args=[999]) + f'?semester={self.sem.id}'
        resp = self.client.get(url)
        self.assertRedirects(resp, reverse('scheduler:semester_detail', args=[self.sem.id]))
        # message should be in cookie storage
        messages = list(resp.wsgi_request._messages)
        self.assertTrue(any('not found' in str(m) for m in messages))

    def test_division_delete_nonexistent_redirects(self):
        from django.urls import reverse
        url = reverse('scheduler:division_delete', args=[999]) + f'?semester={self.sem.id}&course={self.sem.course.id}'
        resp = self.client.get(url)
        self.assertRedirects(resp, reverse('scheduler:semester_detail', args=[self.sem.id]))
        msgs = list(resp.wsgi_request._messages)
        self.assertTrue(any('Division not found' in str(m) for m in msgs))

    def test_division_strength_field(self):
        # form validation and model persistence
        from .forms import DivisionForm
        data = {'name': 'Div1', 'semester': self.sem.id, 'strength': 30}
        form = DivisionForm(data)
        self.assertTrue(form.is_valid())
        div = form.save()
        self.assertEqual(div.strength, 30)
        # negative strength should be invalid
        form2 = DivisionForm({'name':'D2','semester':self.sem.id,'strength':-5})
        self.assertFalse(form2.is_valid())
        # field-level validator triggers
        self.assertIn('Ensure this value is greater than or equal to 0.', form2.errors['strength'])

    def test_break_slots_in_timeslots(self):
        # create a semester with breaks and verify display slots contain BreakSlot
        from .timetable_generator import get_timeslots_for_semester, BreakSlot
        self.sem.start_time = time(9,0)
        self.sem.end_time = time(12,0)
        self.sem.breaks = '10:00-11:00'
        self.sem.save()
        slots = get_timeslots_for_semester(self.sem, include_breaks=True)
        # should include a break slot in middle
        self.assertTrue(any(isinstance(s, BreakSlot) for s in slots))
        # scheduling with breaks excluded should not include BreakSlot
        sched_slots = get_timeslots_for_semester(self.sem)
        self.assertFalse(any(hasattr(s, 'is_break') for s in sched_slots))

    def test_generator_skips_break_slots(self):
        # ensure timetable generation does not assign a subject during break
        self.sem.start_time = time(9,0)
        self.sem.end_time = time(12,0)
        self.sem.breaks = '10:00-11:00'
        self.sem.save()
        t = Teacher.objects.create(name='T2', email='t2@x', department=self.teacher.department)
        Subject.objects.create(name='S1', semester=self.sem, teacher=t, total_hours=5)
        schedule = generate_timetable_for_semester(self.sem)
        # none of the entries should have a time_slot that falls in break
        for e in schedule:
            self.assertFalse(e.time_slot.start_time >= time(10,0) and e.time_slot.end_time <= time(11,0))

    def test_lab_subjects_get_two_contiguous_slots(self):
        # ensure there's at least one lab classroom available
        Classroom.objects.create(room_number='L1', capacity=20, is_lab=True)
        # lab subject with enough hours to require multiple sessions.  The
        # underlying generator calculates the number of 2‑hour lab units using
        # ``floor(weekly_hours/2)`` (at least one unit), so we mirror that here.
        lab = Subject.objects.create(
            name='Lab1', semester=self.sem, teacher=self.teacher,
            is_lab=True, total_hours=20  # ~5 weeks -> weekly_hours likely 5
        )
        schedule = generate_timetable_for_semester(self.sem)
        # gather entries for lab subject sorted by day/start time
        lab_entries = [e for e in schedule if e.subject == lab]
        self.assertTrue(lab_entries, "expected some lab entries")
        lab_entries.sort(key=lambda e: (e.day, e.time_slot.start_time))
        # ensure entries come in pairs of contiguous slots
        for i in range(0, len(lab_entries), 2):
            first = lab_entries[i]
            # there should be a matching second entry
            self.assertTrue(i+1 < len(lab_entries), "lab slots not in pairs")
            second = lab_entries[i+1]
            self.assertEqual(first.day, second.day)
            self.assertEqual(first.time_slot.end_time, second.time_slot.start_time)
        # total count of timetable rows for lab should equal two times
        # the number of lab sessions (each session uses two slots).
        # follow the same floor-based logic used by the generator
        wh = lab.weekly_hours or 0
        expected_sessions = max(wh // 2, 1)
        self.assertEqual(len(lab_entries), expected_sessions * 2)

    def test_parse_time_formats(self):
        from .timetable_generator import parse_time
        self.assertEqual(parse_time('15:30').hour, 15)
        self.assertEqual(parse_time('3:30 PM').hour, 15)
        self.assertEqual(parse_time('03:30pm').hour, 15)
        self.assertEqual(parse_time('10:10mins').minute, 10)
        self.assertEqual(parse_time('10:10 min').minute, 10)
        self.assertEqual(parse_time('12:00 am').hour, 0)
        self.assertEqual(parse_time('12:00 pm').hour, 12)

    def test_timeslots_split_around_short_break(self):
        from .timetable_generator import get_timeslots_for_semester, BreakSlot
        self.sem.start_time = time(9,0)
        self.sem.end_time = time(11,0)
        # 10‑10:10 break splits a one‑hour slot into 9‑10, 10‑10:10(break), 10:10-11
        self.sem.breaks = '10:00-10:10'
        self.sem.save()
        slots = get_timeslots_for_semester(self.sem, include_breaks=True)
        # verify break slot present and slots before/after exist
        self.assertTrue(any(isinstance(s, BreakSlot) for s in slots))
        times = [(s.start_time, s.end_time, getattr(s, 'is_break', False)) for s in slots]
        # expect something like [(9:00,10:00,False),(10:00,10:10,True),(10:10,11:00,False)]
        self.assertTrue(any(t[0].hour==9 for t in times))
        self.assertTrue(any(t[2] for t in times))
        self.assertTrue(any(t[0].hour==10 and not t[2] and t[1].minute==0 for t in times))

    def test_timeslots_split_around_short_break_with_minute_suffix(self):
        from .timetable_generator import get_timeslots_for_semester, BreakSlot
        self.sem.start_time = time(9, 0)
        self.sem.end_time = time(11, 0)
        self.sem.breaks = '10:00-10:10 mins'
        self.sem.save()

        slots = get_timeslots_for_semester(self.sem, include_breaks=True)
        self.assertTrue(any(isinstance(s, BreakSlot) for s in slots))
        self.assertTrue(
            any(
                getattr(s, 'is_break', False)
                and s.start_time == time(10, 0)
                and s.end_time == time(10, 10)
                for s in slots
            )
        )

    def test_timeslots_normalize_afternoon_break_without_ampm(self):
        from .timetable_generator import get_timeslots_for_semester, BreakSlot
        self.sem.start_time = time(12, 30)
        self.sem.end_time = time(17, 40)
        # User-entered 12-hour style without AM/PM should map into semester window.
        self.sem.breaks = '03:30-03:40'
        self.sem.save()

        slots = get_timeslots_for_semester(self.sem, include_breaks=True)
        self.assertTrue(any(isinstance(s, BreakSlot) for s in slots))
        self.assertTrue(
            any(
                getattr(s, 'is_break', False)
                and s.start_time == time(15, 30)
                and s.end_time == time(15, 40)
                for s in slots
            )
        )

    def test_smart_form_has_division(self):
        from .forms import SmartTimetableForm
        form = SmartTimetableForm()
        self.assertIn('division', form.fields)

    def test_create_subject_with_division_assigns_teacher_via_view(self):
        from django.urls import reverse
        div = Division.objects.create(name='D3', semester=self.sem, course=self.sem.course)
        teacher2 = Teacher.objects.create(name='T2', email='t2@x', department=self.teacher.department)
        url = reverse('scheduler:subject_add') + f'?semester={self.sem.id}&division={div.id}'
        resp = self.client.post(url, {
            'name':'SV', 'semester':self.sem.id, 'is_lab': False,
            'total_hours':10, 'teacher': teacher2.id
        })
        self.assertRedirects(resp, reverse('scheduler:division_detail', args=[div.id]))
        self.assertTrue(DivisionSubject.objects.filter(division=div, subject__name='SV', teacher=teacher2).exists())

    def test_edit_subject_division_teacher(self):
        from django.urls import reverse
        div = Division.objects.create(name='D4', semester=self.sem, course=self.sem.course)
        subj = Subject.objects.create(name='E', semester=self.sem, total_hours=5)
        DivisionSubject.objects.create(division=div, subject=subj, teacher=self.teacher)
        teacher2 = Teacher.objects.create(name='T2', email='t2@x', department=self.teacher.department)
        url = reverse('scheduler:subject_edit', args=[subj.id]) + f'?division={div.id}'
        resp = self.client.post(url, {
            'name':'E', 'semester':self.sem.id, 'is_lab': False,
            'total_hours':5, 'teacher': teacher2.id
        })
        self.assertRedirects(resp, reverse('scheduler:division_detail', args=[div.id]))
        self.assertTrue(DivisionSubject.objects.filter(division=div, subject=subj, teacher=teacher2).exists())

    def test_smart_generator_with_division_override(self):
        # create a division and assign a different teacher for a subject
        div = Division.objects.create(name='D1', semester=self.sem, course=self.sem.course)
        subj = Subject.objects.create(name='SubjA', semester=self.sem, teacher=self.teacher, total_hours=5)
        other_teacher = Teacher.objects.create(name='Other', email='o@x', department=self.teacher.department)
        from .models import DivisionSubject
        DivisionSubject.objects.create(division=div, subject=subj, teacher=other_teacher)
        # generate timetable for division via view logic
        from .timetable_generator import generate_timetable_for_semester
        schedule = generate_timetable_for_semester(self.sem, teacher_overrides={subj.id: other_teacher})
        # check that at least one entry uses other_teacher
        self.assertTrue(any(e.teacher == other_teacher for e in schedule))

    def test_generator_fails_if_subject_has_no_teacher(self):
        # subjects created without a teacher should trigger a helpful error
        Subject.objects.create(name='NoTeacher', semester=self.sem, total_hours=10)
        from .timetable_generator import TimetableGenerationError, generate_timetable_for_semester
        with self.assertRaises(TimetableGenerationError) as cm:
            generate_timetable_for_semester(self.sem)
        # message should mention the missing teacher situation
        self.assertIn('no teacher', str(cm.exception).lower())


class PageSmokeTests(TestCase):
    """Ensure key pages render without error (basic authenticated client)."""

    def setUp(self):
        # create minimal objects to populate pages
        self.dept = Department.objects.create(name='SM')
        self.course = Course.objects.create(name='SC', department=self.dept)
        # give semester minimal timing information so timetables can be produced
        self.sem = Semester.objects.create(
            number=1,
            course=self.course,
            start_time=time(9, 0),
            end_time=time(15, 0),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 28),
            working_days='MON-FRI',
            max_lectures_per_day=6,
        )
        self.teacher = Teacher.objects.create(name='T', email='t@x', department=self.dept)
        # ensure there is at least one classroom so timetables can be generated
        Classroom.objects.create(room_number='R', capacity=20, is_lab=False)
        self.client = self.client  # no login required yet

    def test_pages_return_200(self):
        from django.urls import reverse
        pages = [
            reverse('scheduler:home'),
            reverse('scheduler:department_list'),
            reverse('scheduler:department_detail', args=[self.dept.id]),
            reverse('scheduler:department_add'),
            reverse('scheduler:course_list'),
            reverse('scheduler:course_detail', args=[self.course.id]),
            reverse('scheduler:teacher_list'),
            reverse('scheduler:teacher_add'),
            reverse('scheduler:teacher_detail', args=[self.teacher.id]),
            reverse('scheduler:semester_list'),
            reverse('scheduler:semester_detail', args=[self.sem.id]),
            reverse('scheduler:subject_add'),
            reverse('scheduler:division_add'),
            reverse('scheduler:classroom_list'),
            reverse('scheduler:classroom_add'),
            reverse('scheduler:laboratory_list'),
            reverse('scheduler:laboratory_add'),
            reverse('scheduler:smart_timetable'),
        ]
        for url in pages:
            resp = self.client.get(url)
            self.assertIn(resp.status_code, (200, 302), f"{url} gave {resp.status_code}")
        # also verify division detail itself can be reached once a division exists
        div = Division.objects.create(name='D2', semester=self.sem, course=self.course)
        resp = self.client.get(reverse('scheduler:division_detail', args=[div.id]))
        self.assertEqual(resp.status_code, 200)
        # subject list should be visible
        Subject.objects.create(name='DD', semester=self.sem, total_hours=5)
        resp2 = self.client.get(reverse('scheduler:division_detail', args=[div.id]))
        self.assertContains(resp2, 'DD')
        # if we assign a teacher and refresh, name should appear
        subj2 = Subject.objects.create(name='DD2', semester=self.sem, total_hours=3)
        DivisionSubject.objects.create(division=div, subject=subj2, teacher=self.teacher)
        resp3 = self.client.get(reverse('scheduler:division_detail', args=[div.id]))
        self.assertContains(resp3, self.teacher.name)

    def test_subject_form_no_teacher_without_division(self):
        from django.urls import reverse
        resp = self.client.get(reverse('scheduler:subject_add'))
        self.assertNotContains(resp, 'name="teacher"')

    def test_classroom_and_laboratory_crud(self):
        from django.urls import reverse

        create_classroom = self.client.post(reverse('scheduler:classroom_add'), {
            'room_number': 'CR-101',
            'capacity': 60,
        })
        self.assertRedirects(create_classroom, reverse('scheduler:classroom_list'))
        classroom = Classroom.objects.get(room_number='CR-101')
        self.assertFalse(classroom.is_lab)
        self.assertIsNone(classroom.division_id)

        update_classroom = self.client.post(reverse('scheduler:classroom_edit', args=[classroom.id]), {
            'room_number': 'CR-101A',
            'capacity': 65,
        })
        self.assertRedirects(update_classroom, reverse('scheduler:classroom_list'))
        classroom.refresh_from_db()
        self.assertEqual(classroom.room_number, 'CR-101A')
        self.assertEqual(classroom.capacity, 65)

        create_lab = self.client.post(reverse('scheduler:laboratory_add'), {
            'room_number': 'LAB-1',
            'capacity': 40,
        })
        self.assertRedirects(create_lab, reverse('scheduler:laboratory_list'))
        lab = Classroom.objects.get(room_number='LAB-1')
        self.assertTrue(lab.is_lab)
        self.assertIsNone(lab.division_id)

        delete_lab = self.client.post(reverse('scheduler:laboratory_delete', args=[lab.id]))
        self.assertRedirects(delete_lab, reverse('scheduler:laboratory_list'))
        self.assertFalse(Classroom.objects.filter(pk=lab.id).exists())

    def test_room_form_hides_course_and_division_fields(self):
        from django.urls import reverse
        classroom_add = self.client.get(reverse('scheduler:classroom_add'))
        self.assertNotContains(classroom_add, 'name="course"')
        self.assertNotContains(classroom_add, 'name="division"')

        laboratory_add = self.client.get(reverse('scheduler:laboratory_add'))
        self.assertNotContains(laboratory_add, 'name="course"')
        self.assertNotContains(laboratory_add, 'name="division"')

    def test_subject_form_shows_teacher_with_division(self):
        from django.urls import reverse
        div = Division.objects.create(name='D5', semester=self.sem, course=self.course)
        resp = self.client.get(reverse('scheduler:subject_add') + f'?semester={self.sem.id}&division={div.id}')
        self.assertContains(resp, 'name="teacher"')

    def test_subject_form_saves_lab_batches_for_division(self):
        from django.urls import reverse

        div = Division.objects.create(name='LabDiv', semester=self.sem, course=self.course)
        t1 = Teacher.objects.create(name='LabDivT1', email='lab_div_t1@x.com', department=self.dept)
        t2 = Teacher.objects.create(name='LabDivT2', email='lab_div_t2@x.com', department=self.dept)
        lab1 = Classroom.objects.create(room_number='LD-L1', capacity=30, is_lab=True, division=div)
        lab2 = Classroom.objects.create(room_number='LD-L2', capacity=30, is_lab=True)

        payload = [
            {
                'batch_number': 'A',
                'from_roll_no': 1,
                'to_roll_no': 20,
                'teacher_id': t1.id,
                'laboratory_id': lab1.id,
            },
            {
                'batch_number': 'B',
                'from_roll_no': 21,
                'to_roll_no': 40,
                'teacher_id': t2.id,
                'laboratory_id': lab2.id,
            },
        ]

        url = reverse('scheduler:subject_add') + f'?semester={self.sem.id}&division={div.id}'
        resp = self.client.post(url, {
            'name': 'BatchLabSub',
            'semester': self.sem.id,
            'is_lab': True,
            'total_hours': 20,
            'lab_batches_json': json.dumps(payload),
        })
        self.assertRedirects(resp, reverse('scheduler:division_detail', args=[div.id]))

        subject = Subject.objects.get(name='BatchLabSub')
        rows = list(
            LabBatchAssignment.objects
            .filter(division=div, subject=subject)
            .order_by('batch_number')
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].teacher_id, t1.id)
        self.assertEqual(rows[0].laboratory_id, lab1.id)
        self.assertEqual(rows[1].teacher_id, t2.id)
        self.assertEqual(rows[1].laboratory_id, lab2.id)

    def test_subject_form_auto_assigns_labs_when_not_provided(self):
        from django.urls import reverse

        div = Division.objects.create(name='AutoLabDiv', semester=self.sem, course=self.course, strength=40)
        t1 = Teacher.objects.create(name='AutoLabT1', email='auto_lab_t1@x.com', department=self.dept)
        t2 = Teacher.objects.create(name='AutoLabT2', email='auto_lab_t2@x.com', department=self.dept)
        lab1 = Classroom.objects.create(room_number='AUTO-L1', capacity=45, is_lab=True, division=div)
        lab2 = Classroom.objects.create(room_number='AUTO-L2', capacity=60, is_lab=True)

        payload = [
            {
                'batch_number': 'A',
                'from_roll_no': 1,
                'to_roll_no': 20,
                'teacher_id': t1.id,
            },
            {
                'batch_number': 'B',
                'from_roll_no': 21,
                'to_roll_no': 40,
                'teacher_id': t2.id,
            },
        ]

        url = reverse('scheduler:subject_add') + f'?semester={self.sem.id}&division={div.id}'
        resp = self.client.post(url, {
            'name': 'AutoBatchLabSub',
            'semester': self.sem.id,
            'is_lab': True,
            'total_hours': 20,
            'lab_batches_json': json.dumps(payload),
        })
        self.assertRedirects(resp, reverse('scheduler:division_detail', args=[div.id]))

        subject = Subject.objects.get(name='AutoBatchLabSub')
        rows = list(
            LabBatchAssignment.objects
            .filter(division=div, subject=subject)
            .order_by('batch_number')
        )
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.laboratory_id for row in rows))
        self.assertTrue(all(row.laboratory_id in {lab1.id, lab2.id} for row in rows))

    def test_subject_form_auto_assigns_four_batches_across_two_40_capacity_labs(self):
        from django.urls import reverse

        div = Division.objects.create(name='AutoLab40Div', semester=self.sem, course=self.course, strength=80)
        t1 = Teacher.objects.create(name='Auto40T1', email='auto40_t1@x.com', department=self.dept)
        t2 = Teacher.objects.create(name='Auto40T2', email='auto40_t2@x.com', department=self.dept)
        t3 = Teacher.objects.create(name='Auto40T3', email='auto40_t3@x.com', department=self.dept)
        t4 = Teacher.objects.create(name='Auto40T4', email='auto40_t4@x.com', department=self.dept)
        lab1 = Classroom.objects.create(room_number='AUTO40-L1', capacity=40, is_lab=True, division=div)
        lab2 = Classroom.objects.create(room_number='AUTO40-L2', capacity=40, is_lab=True)

        payload = [
            {'batch_number': '01', 'from_roll_no': 1, 'to_roll_no': 20, 'teacher_id': t1.id},
            {'batch_number': '02', 'from_roll_no': 21, 'to_roll_no': 40, 'teacher_id': t2.id},
            {'batch_number': '03', 'from_roll_no': 41, 'to_roll_no': 60, 'teacher_id': t3.id},
            {'batch_number': '04', 'from_roll_no': 61, 'to_roll_no': 80, 'teacher_id': t4.id},
        ]

        url = reverse('scheduler:subject_add') + f'?semester={self.sem.id}&division={div.id}'
        resp = self.client.post(url, {
            'name': 'Auto40BatchLabSub',
            'semester': self.sem.id,
            'is_lab': True,
            'total_hours': 20,
            'lab_batches_json': json.dumps(payload),
        })
        self.assertRedirects(resp, reverse('scheduler:division_detail', args=[div.id]))

        subject = Subject.objects.get(name='Auto40BatchLabSub')
        rows = list(
            LabBatchAssignment.objects
            .filter(division=div, subject=subject)
            .order_by('batch_number')
        )
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row.laboratory_id in {lab1.id, lab2.id} for row in rows))
        # Each 40-capacity lab can host at most two 20-student batches in parallel.
        students_per_lab = {}
        for row in rows:
            students_per_lab[row.laboratory_id] = students_per_lab.get(row.laboratory_id, 0) + row.batch_size
        self.assertTrue(all(total <= 40 for total in students_per_lab.values()))

    def test_subject_form_hides_manual_laboratory_column_for_lab_batches(self):
        from django.urls import reverse
        div = Division.objects.create(name='LabColumnDiv', semester=self.sem, course=self.course)
        resp = self.client.get(reverse('scheduler:subject_add') + f'?semester={self.sem.id}&division={div.id}')
        self.assertNotContains(resp, '<th>Laboratory</th>', html=True)
        self.assertNotContains(resp, 'lab-batch-lab')

    def test_subject_form_allows_same_lab_when_capacity_fits_division_strength(self):
        from django.urls import reverse

        div = Division.objects.create(name='LabStrengthDiv', semester=self.sem, course=self.course, strength=80)
        t1 = Teacher.objects.create(name='LS-T1', email='ls_t1@x.com', department=self.dept)
        t2 = Teacher.objects.create(name='LS-T2', email='ls_t2@x.com', department=self.dept)
        t3 = Teacher.objects.create(name='LS-T3', email='ls_t3@x.com', department=self.dept)
        t4 = Teacher.objects.create(name='LS-T4', email='ls_t4@x.com', department=self.dept)
        shared_lab = Classroom.objects.create(room_number='LS-LAB-100', capacity=100, is_lab=True, division=div)

        payload = [
            {'batch_number': '1', 'from_roll_no': 1, 'to_roll_no': 20, 'teacher_id': t1.id, 'laboratory_id': shared_lab.id},
            {'batch_number': '2', 'from_roll_no': 21, 'to_roll_no': 40, 'teacher_id': t2.id, 'laboratory_id': shared_lab.id},
            {'batch_number': '3', 'from_roll_no': 41, 'to_roll_no': 60, 'teacher_id': t3.id, 'laboratory_id': shared_lab.id},
            {'batch_number': '4', 'from_roll_no': 61, 'to_roll_no': 80, 'teacher_id': t4.id, 'laboratory_id': shared_lab.id},
        ]

        url = reverse('scheduler:subject_add') + f'?semester={self.sem.id}&division={div.id}'
        resp = self.client.post(url, {
            'name': 'OneLabFitSub',
            'semester': self.sem.id,
            'is_lab': True,
            'total_hours': 20,
            'lab_batches_json': json.dumps(payload),
        })
        self.assertRedirects(resp, reverse('scheduler:division_detail', args=[div.id]))
        subject = Subject.objects.get(name='OneLabFitSub')
        self.assertEqual(
            LabBatchAssignment.objects.filter(division=div, subject=subject, laboratory=shared_lab).count(),
            4,
        )

    def test_subject_add_semester_dropdown_scoped_to_course(self):
        from django.urls import reverse
        Semester.objects.create(
            number=2,
            course=self.course,
            start_time=self.sem.start_time,
            end_time=self.sem.end_time,
            start_date=self.sem.start_date,
            end_date=self.sem.end_date,
            working_days='MON-FRI',
            max_lectures_per_day=6,
        )
        other_dept = Department.objects.create(name='Other Dept')
        other_course = Course.objects.create(name='Other Course', department=other_dept)
        other_sem = Semester.objects.create(
            number=1,
            course=other_course,
            start_time=self.sem.start_time,
            end_time=self.sem.end_time,
            start_date=self.sem.start_date,
            end_date=self.sem.end_date,
            working_days='MON-FRI',
            max_lectures_per_day=6,
        )

        resp = self.client.get(reverse('scheduler:subject_add') + f'?semester={self.sem.id}')
        self.assertContains(resp, str(self.sem))
        self.assertContains(resp, f'{self.course.name} - Semester 2')
        self.assertNotContains(resp, str(other_sem))

    def test_subject_edit_semester_dropdown_scoped_to_subject_course(self):
        from django.urls import reverse
        other_dept = Department.objects.create(name='Alt Dept')
        other_course = Course.objects.create(name='Alt Course', department=other_dept)
        other_sem = Semester.objects.create(
            number=1,
            course=other_course,
            start_time=self.sem.start_time,
            end_time=self.sem.end_time,
            start_date=self.sem.start_date,
            end_date=self.sem.end_date,
            working_days='MON-FRI',
            max_lectures_per_day=6,
        )
        subj = Subject.objects.create(name='Scoped Subject', semester=self.sem, total_hours=2)

        resp = self.client.get(reverse('scheduler:subject_edit', args=[subj.id]))
        self.assertContains(resp, str(self.sem))
        self.assertNotContains(resp, str(other_sem))

    def test_division_add_semester_dropdown_scoped_to_course(self):
        from django.urls import reverse
        Semester.objects.create(
            number=2,
            course=self.course,
            start_time=self.sem.start_time,
            end_time=self.sem.end_time,
            start_date=self.sem.start_date,
            end_date=self.sem.end_date,
            working_days='MON-FRI',
            max_lectures_per_day=6,
        )
        other_dept = Department.objects.create(name='Dept Other')
        other_course = Course.objects.create(name='Course Other', department=other_dept)
        other_sem = Semester.objects.create(
            number=1,
            course=other_course,
            start_time=self.sem.start_time,
            end_time=self.sem.end_time,
            start_date=self.sem.start_date,
            end_date=self.sem.end_date,
            working_days='MON-FRI',
            max_lectures_per_day=6,
        )

        resp = self.client.get(reverse('scheduler:division_add') + f'?course={self.course.id}')
        self.assertContains(resp, str(self.sem))
        self.assertContains(resp, f'{self.course.name} - Semester 2')
        self.assertNotContains(resp, str(other_sem))

    def test_subject_form_teacher_labels_show_qualification(self):
        """Qualified teachers are marked on the subject form when editing."""
        from django.urls import reverse
        subj = Subject.objects.create(name='QSub', semester=self.sem, total_hours=3)
        t = Teacher.objects.create(name='KT', email='kt@x.com', department=self.dept)
        t.subjects_known.add(subj)
        div = Division.objects.create(name='D99', semester=self.sem, course=self.course)
        resp = self.client.get(reverse('scheduler:subject_edit', args=[subj.id]) + f'?semester={self.sem.id}&division={div.id}')
        # option label should contain qualification note
        self.assertContains(resp, 'KT (knows this subject)')

    def test_teacher_form_teaching_subjects_field(self):
        """Teachers can declare which subjects they know; this does not assign
        the subject itself (subject.teacher stays independent)."""
        from django.urls import reverse
        subj1 = Subject.objects.create(name='TS1', semester=self.sem, total_hours=5)
        subj2 = Subject.objects.create(name='TS2', semester=self.sem, total_hours=5)
        # GET form includes the teaching-subject text field
        resp = self.client.get(reverse('scheduler:teacher_add'))
        self.assertContains(resp, 'name="subjects_known"')
        # submit new teacher with both subject names
        data = {
            'name': 'NewT',
            'email': 'new@x.com',
            'department': self.dept.id,
            'subjects_known': f'{subj1.name}, {subj2.name}',
        }
        resp2 = self.client.post(reverse('scheduler:teacher_add'), data)
        if resp2.status_code != 302:
            form = resp2.context.get('form')
            errs = form.errors if form is not None else 'no form in context'
            self.fail(f"Teacher add returned {resp2.status_code}, errors={errs}, html:\n{resp2.content.decode()}")
        teacher = Teacher.objects.get(email='new@x.com')
        self.assertEqual(teacher.known_subject_names, f'{subj1.name}, {subj2.name}')
        self.assertCountEqual(list(teacher.subjects_known.all()), [subj1, subj2])
        # subjects themselves should still have no assigned teacher
        self.assertFalse(Subject.objects.filter(id__in=[subj1.id, subj2.id], teacher__isnull=False).exists())
        # edit and modify known list to only subj2
        resp3 = self.client.get(reverse('scheduler:teacher_edit', args=[teacher.id]))
        self.assertContains(resp3, 'name="subjects_known"')
        data_update = {
            'name': 'NewT',
            'email': 'new@x.com',
            'department': self.dept.id,
            'subjects_known': subj2.name,
        }
        resp4 = self.client.post(reverse('scheduler:teacher_edit', args=[teacher.id]), data_update)
        self.assertEqual(resp4.status_code, 302)
        teacher.refresh_from_db()
        self.assertEqual(teacher.known_subject_names, subj2.name)
        self.assertEqual(list(teacher.subjects_known.all()), [subj2])

    def test_teacher_form_allows_unknown_reference_subject_names(self):
        from django.urls import reverse
        data = {
            'name': 'RefTeacher',
            'email': 'refteacher@x.com',
            'department': self.dept.id,
            'subjects_known': 'Aptitude, Reasoning',
        }
        resp = self.client.post(reverse('scheduler:teacher_add'), data)
        self.assertEqual(resp.status_code, 302)

        teacher = Teacher.objects.get(email='refteacher@x.com')
        self.assertEqual(teacher.known_subject_names, 'Aptitude, Reasoning')
        self.assertEqual(list(teacher.subjects_known.all()), [])

    def test_teacher_cards_click_to_detail_and_detail_has_crud_buttons(self):
        from django.urls import reverse
        list_resp = self.client.get(reverse('scheduler:teacher_list'))
        detail_url = reverse('scheduler:teacher_detail', args=[self.teacher.id])
        self.assertContains(list_resp, f'href="{detail_url}"')

        detail_resp = self.client.get(detail_url)
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, 'Basic Information')
        self.assertContains(detail_resp, reverse('scheduler:teacher_add'))
        self.assertContains(detail_resp, reverse('scheduler:teacher_edit', args=[self.teacher.id]))
        self.assertContains(detail_resp, reverse('scheduler:teacher_delete', args=[self.teacher.id]))

    def test_room_cards_click_to_detail_and_detail_shows_assignment_and_timing(self):
        from django.urls import reverse
        from .models import Timetable, TimeSlot

        div = Division.objects.create(name='RoomDetailDiv', semester=self.sem, course=self.course)
        classroom = Classroom.objects.create(
            room_number='RD-CR-1',
            capacity=55,
            is_lab=False,
            division=div,
        )
        laboratory = Classroom.objects.create(
            room_number='RD-LAB-1',
            capacity=30,
            is_lab=True,
            division=div,
        )

        theory_subject = Subject.objects.create(
            name='RoomDetailTheory',
            semester=self.sem,
            teacher=self.teacher,
            total_hours=4,
        )
        lab_subject = Subject.objects.create(
            name='RoomDetailLab',
            semester=self.sem,
            teacher=self.teacher,
            is_lab=True,
            total_hours=4,
        )

        theory_slot = TimeSlot.objects.create(start_time=time(9, 0), end_time=time(10, 0))
        lab_slot = TimeSlot.objects.create(start_time=time(10, 0), end_time=time(11, 0))

        Timetable.objects.create(
            semester=self.sem,
            division=div,
            subject=theory_subject,
            teacher=self.teacher,
            classroom=classroom,
            day='MON',
            time_slot=theory_slot,
        )
        Timetable.objects.create(
            semester=self.sem,
            division=div,
            subject=lab_subject,
            teacher=self.teacher,
            classroom=laboratory,
            day='TUE',
            time_slot=lab_slot,
        )

        classroom_list_resp = self.client.get(reverse('scheduler:classroom_list'))
        classroom_detail_url = reverse('scheduler:classroom_detail', args=[classroom.id])
        self.assertContains(classroom_list_resp, f'href="{classroom_detail_url}"')

        classroom_detail_resp = self.client.get(classroom_detail_url)
        self.assertEqual(classroom_detail_resp.status_code, 200)
        self.assertContains(classroom_detail_resp, 'Engaged Timings')
        self.assertContains(classroom_detail_resp, self.course.name)
        self.assertContains(classroom_detail_resp, 'Monday')
        self.assertContains(classroom_detail_resp, '9:00-10:00')
        self.assertNotContains(classroom_detail_resp, 'Assigned To')
        self.assertNotContains(classroom_detail_resp, 'Class / Subject')
        self.assertNotContains(classroom_detail_resp, theory_subject.name)

        laboratory_list_resp = self.client.get(reverse('scheduler:laboratory_list'))
        laboratory_detail_url = reverse('scheduler:laboratory_detail', args=[laboratory.id])
        self.assertContains(laboratory_list_resp, f'href="{laboratory_detail_url}"')

        laboratory_detail_resp = self.client.get(laboratory_detail_url)
        self.assertEqual(laboratory_detail_resp.status_code, 200)
        self.assertContains(laboratory_detail_resp, 'Engaged Timings')
        self.assertContains(laboratory_detail_resp, self.course.name)
        self.assertContains(laboratory_detail_resp, 'Tuesday')
        self.assertContains(laboratory_detail_resp, '10:00-11:00')
        self.assertNotContains(laboratory_detail_resp, 'Assigned To')
        self.assertNotContains(laboratory_detail_resp, 'Class / Subject')
        self.assertNotContains(laboratory_detail_resp, lab_subject.name)

    def test_room_form_allows_shared_room_without_division(self):
        from django.urls import reverse

        create_room = self.client.post(reverse('scheduler:classroom_add'), {
            'room_number': 'SHARED-CR-1',
            'capacity': 80,
        })
        self.assertRedirects(create_room, reverse('scheduler:classroom_list'))
        room = Classroom.objects.get(room_number='SHARED-CR-1')
        self.assertIsNone(room.division_id)

    def test_timetable_full_clean_blocks_room_overlap_across_divisions(self):
        from django.core.exceptions import ValidationError
        from .models import Timetable, TimeSlot

        div_a = Division.objects.create(name='CleanA', semester=self.sem, course=self.course)
        div_b = Division.objects.create(name='CleanB', semester=self.sem, course=self.course)
        room = Classroom.objects.create(room_number='CLEAN-ROOM', capacity=40, is_lab=False)
        slot = TimeSlot.objects.create(start_time=time(11, 0), end_time=time(12, 0))
        subj = Subject.objects.create(name='CleanSub', semester=self.sem, teacher=self.teacher, total_hours=2)

        Timetable.objects.create(
            semester=self.sem,
            division=div_a,
            subject=subj,
            teacher=self.teacher,
            classroom=room,
            day='TUE',
            time_slot=slot,
        )

        duplicate = Timetable(
            semester=self.sem,
            division=div_b,
            subject=subj,
            teacher=self.teacher,
            classroom=room,
            day='TUE',
            time_slot=slot,
        )
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_timetable_view_renders_break_cells_and_subject_entries(self):
        from django.urls import reverse
        from .timetable_generator import run_generator_for_semester

        self.sem.breaks = '10:00-10:30'
        self.sem.save()
        subj = Subject.objects.create(name='BreakAwareSub', semester=self.sem, teacher=self.teacher, total_hours=8)
        run_generator_for_semester(self.sem)

        resp = self.client.get(reverse('scheduler:timetable_view', args=[self.sem.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '<em>Break</em>', html=True)
        self.assertContains(resp, subj.name)

    def test_department_crud(self):
        from django.urls import reverse
        # verify GET shows correct form class
        resp = self.client.get(reverse('scheduler:department_add'))
        self.assertEqual(resp.context['form'].__class__.__name__, 'DepartmentForm')
        # create
        resp = self.client.post(reverse('scheduler:department_add'), {'name': 'NewDept', 'description': 'Desc'})
        if resp.status_code != 302:
            self.fail(f"Department add returned {resp.status_code}, html:\n{resp.content.decode()}")
        new = Department.objects.get(name='NewDept')
        # detail contains edit/delete buttons
        resp = self.client.get(reverse('scheduler:department_detail', args=[new.id]))
        self.assertContains(resp, 'Edit')
        self.assertContains(resp, 'Delete')
        # edit
        resp = self.client.post(reverse('scheduler:department_edit', args=[new.id]), {'name': 'Changed', 'description': 'D2'})
        self.assertEqual(resp.status_code, 302)
        new.refresh_from_db()
        self.assertEqual(new.name, 'Changed')
        # delete
        resp = self.client.post(reverse('scheduler:department_delete', args=[new.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Department.objects.filter(pk=new.id).exists())

    def test_smart_timetable_shows_lab_colspan(self):
        # create a semester with fixed timeslots for deterministic output
        from django.urls import reverse
        self.sem.start_time = time(9,0)
        self.sem.end_time = time(11,0)
        self.sem.save()
        # ensure two consecutive timeslots exist
        from .timetable_generator import get_timeslots_for_semester
        get_timeslots_for_semester(self.sem)
        # add a lab classroom and lab subject
        Classroom.objects.create(room_number='L2', capacity=20, is_lab=True)
        lab = Subject.objects.create(
            name='LabX', semester=self.sem, teacher=self.teacher,
            is_lab=True, total_hours=4
        )
        div = Division.objects.create(name='DLAB', semester=self.sem, course=self.course)
        DivisionSubject.objects.create(division=div, subject=lab, teacher=self.teacher)
        lab_room = Classroom.objects.get(room_number='L2')
        LabBatchAssignment.objects.create(
            division=div,
            subject=lab,
            batch_number='A',
            from_roll_no=1,
            to_roll_no=20,
            teacher=self.teacher,
            laboratory=lab_room,
        )
        # generate via view post
        data = {'department': self.dept.id,
                'course': self.course.id,
                'semester': self.sem.id,
                'division': div.id,
                'action': 'generate'}
        resp = self.client.post(reverse('scheduler:smart_timetable'), data)
        self.assertEqual(resp.status_code, 200)
        # inspect context for debugging
        self.assertIn('table', resp.context)
        self.assertIn('semester', resp.context)
        table_ctx = resp.context['table']
        # response should include a colspan attribute indicating merged lab slot
        self.assertIn('colspan="2"', resp.content.decode())
        self.assertContains(resp, '1-20')

    def test_smart_timetable_header_includes_division(self):
        from django.urls import reverse
        # create objects needed
        div = Division.objects.create(name='DivZ', semester=self.sem, course=self.course)
        # subject needs a teacher in order for generation to succeed
        Subject.objects.create(name='SubZ', semester=self.sem, teacher=self.teacher, total_hours=5)
        data = {'department': self.dept.id,
                'course': self.course.id,
                'semester': self.sem.id,
                'division': div.id,
                'action': 'generate'}
        resp = self.client.post(reverse('scheduler:smart_timetable'), data)
        self.assertEqual(resp.status_code, 200)
        text = resp.content.decode()
        self.assertIn(f'Timetable for {self.sem} - {div.name}', text)

        # preview should not be persisted until explicit save
        from .models import Timetable
        self.assertFalse(Timetable.objects.filter(division=div).exists())

        # also make sure behaviour is correct when semester has Saturday included
        self.sem.working_days = 'MON-SAT'
        self.sem.save()
        data['action'] = 'generate'
        resp2 = self.client.post(reverse('scheduler:smart_timetable'), data)
        self.assertEqual(resp2.status_code, 200)
        self.assertContains(resp2, f'Timetable for {self.sem} - {div.name}')

    def test_smart_timetable_shows_failure_breakdown_points(self):
        from django.urls import reverse

        # Force a clear capacity failure for division generation.
        div = Division.objects.create(name='DFail', semester=self.sem, course=self.course, strength=100)
        subj = Subject.objects.create(name='SubFail', semester=self.sem, teacher=self.teacher, total_hours=5)
        DivisionSubject.objects.create(division=div, subject=subj, teacher=self.teacher)

        data = {
            'department': self.dept.id,
            'course': self.course.id,
            'semester': self.sem.id,
            'division': div.id,
            'action': 'generate',
        }
        resp = self.client.post(reverse('scheduler:smart_timetable'), data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Why it failed')
        self.assertIn('failure_details', resp.context)
        self.assertTrue(resp.context['failure_details'])
        self.assertContains(resp, 'capacity greater than or equal to the selected division strength')

    def test_smart_timetable_confirm_save_and_division_view(self):
        from django.urls import reverse
        div = Division.objects.create(name='DSave', semester=self.sem, course=self.course)
        subj = Subject.objects.create(name='SubSave', semester=self.sem, teacher=self.teacher, total_hours=5)
        DivisionSubject.objects.create(division=div, subject=subj, teacher=self.teacher)

        generate_data = {
            'department': self.dept.id,
            'course': self.course.id,
            'semester': self.sem.id,
            'division': div.id,
            'action': 'generate',
        }
        resp_generate = self.client.post(reverse('scheduler:smart_timetable'), generate_data)
        self.assertEqual(resp_generate.status_code, 200)
        self.assertContains(resp_generate, 'Confirm & Save')
        preview_token = resp_generate.context.get('preview_token')
        self.assertTrue(preview_token)

        from .models import Timetable
        self.assertFalse(Timetable.objects.filter(division=div).exists())

        save_data = {
            'department': self.dept.id,
            'course': self.course.id,
            'semester': self.sem.id,
            'division': div.id,
            'preview_token': preview_token,
            'action': 'save_preview',
        }
        resp_save = self.client.post(reverse('scheduler:smart_timetable'), save_data, follow=True)
        self.assertEqual(resp_save.status_code, 200)
        self.assertRedirects(
            resp_save,
            f"{reverse('scheduler:smart_timetable')}?dept={self.dept.id}&course={self.course.id}&sem={self.sem.id}&division={div.id}",
        )
        self.assertNotContains(resp_save, f'Timetable for {self.sem} - {div.name}')
        self.assertNotContains(resp_save, 'Confirm & Save')
        self.assertTrue(Timetable.objects.filter(division=div).exists())

        resp_div_tt = self.client.get(reverse('scheduler:division_timetable_view', args=[div.id]))
        self.assertEqual(resp_div_tt.status_code, 200)
        self.assertContains(resp_div_tt, f'Timetable for {self.sem} - {div.name}')

    def test_smart_timetable_save_preview_blocks_room_overlap_with_other_divisions(self):
        from django.urls import reverse
        from .models import Timetable, TimeSlot

        div_a = Division.objects.create(name='DA', semester=self.sem, course=self.course)
        div_b = Division.objects.create(name='DB', semester=self.sem, course=self.course)
        shared_room = Classroom.objects.create(room_number='SHARED-OVERLAP', capacity=60, is_lab=False)
        slot = TimeSlot.objects.create(start_time=time(9, 0), end_time=time(10, 0))
        subject = Subject.objects.create(name='OverlapSub', semester=self.sem, teacher=self.teacher, total_hours=2)

        Timetable.objects.create(
            semester=self.sem,
            division=div_a,
            subject=subject,
            teacher=self.teacher,
            classroom=shared_room,
            day='MON',
            time_slot=slot,
        )

        preview_payload = {
            'semester_id': self.sem.id,
            'division_id': div_b.id,
            'entries': [{
                'subject_id': subject.id,
                'teacher_id': self.teacher.id,
                'classroom_id': shared_room.id,
                'day': 'MON',
                'time_slot_id': slot.id,
            }],
        }
        preview_token = signing.dumps(
            preview_payload,
            salt='scheduler.smart_timetable.preview',
            compress=True,
        )

        save_data = {
            'department': self.dept.id,
            'course': self.course.id,
            'semester': self.sem.id,
            'division': div_b.id,
            'preview_token': preview_token,
            'action': 'save_preview',
        }
        resp = self.client.post(reverse('scheduler:smart_timetable'), save_data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'already occupied')
        self.assertFalse(Timetable.objects.filter(division=div_b).exists())

    def test_smart_timetable_preview_clears_on_refresh(self):
        from django.urls import reverse
        div = Division.objects.create(name='DRefresh', semester=self.sem, course=self.course)
        subj = Subject.objects.create(name='SubRefresh', semester=self.sem, teacher=self.teacher, total_hours=5)
        DivisionSubject.objects.create(division=div, subject=subj, teacher=self.teacher)

        generate_data = {
            'department': self.dept.id,
            'course': self.course.id,
            'semester': self.sem.id,
            'division': div.id,
            'action': 'generate',
        }
        resp_generate = self.client.post(reverse('scheduler:smart_timetable'), generate_data)
        self.assertEqual(resp_generate.status_code, 200)
        self.assertContains(resp_generate, 'Confirm & Save')
        self.assertContains(resp_generate, f'Timetable for {self.sem} - {div.name}')

        # Refresh / direct GET should not restore preview.
        refresh_url = (
            f"{reverse('scheduler:smart_timetable')}?dept={self.dept.id}"
            f"&course={self.course.id}&sem={self.sem.id}&division={div.id}"
        )
        resp_refresh = self.client.get(refresh_url)
        self.assertEqual(resp_refresh.status_code, 200)
        self.assertNotContains(resp_refresh, 'Confirm & Save')
        self.assertNotContains(resp_refresh, f'Timetable for {self.sem} - {div.name}')

    def test_smart_timetable_cancel_preview_hides_table(self):
        from django.urls import reverse
        div = Division.objects.create(name='DCancel', semester=self.sem, course=self.course)
        subj = Subject.objects.create(name='SubCancel', semester=self.sem, teacher=self.teacher, total_hours=5)
        DivisionSubject.objects.create(division=div, subject=subj, teacher=self.teacher)

        generate_data = {
            'department': self.dept.id,
            'course': self.course.id,
            'semester': self.sem.id,
            'division': div.id,
            'action': 'generate',
        }
        resp_generate = self.client.post(reverse('scheduler:smart_timetable'), generate_data)
        self.assertEqual(resp_generate.status_code, 200)
        self.assertContains(resp_generate, 'Confirm & Save')
        self.assertContains(resp_generate, f'Timetable for {self.sem} - {div.name}')

        cancel_data = {
            'department': self.dept.id,
            'course': self.course.id,
            'semester': self.sem.id,
            'division': div.id,
            'action': 'cancel_preview',
        }
        resp_cancel = self.client.post(reverse('scheduler:smart_timetable'), cancel_data, follow=True)
        self.assertEqual(resp_cancel.status_code, 200)
        self.assertRedirects(
            resp_cancel,
            f"{reverse('scheduler:smart_timetable')}?dept={self.dept.id}&course={self.course.id}&sem={self.sem.id}&division={div.id}",
        )
        self.assertNotContains(resp_cancel, 'Confirm & Save')
        self.assertNotContains(resp_cancel, f'Timetable for {self.sem} - {div.name}')

    def test_smart_timetable_export_image_from_preview(self):
        from django.urls import reverse
        div = Division.objects.create(name='DPDF', semester=self.sem, course=self.course)
        subj = Subject.objects.create(name='SubPDF', semester=self.sem, teacher=self.teacher, total_hours=5)
        DivisionSubject.objects.create(division=div, subject=subj, teacher=self.teacher)

        generate_data = {
            'department': self.dept.id,
            'course': self.course.id,
            'semester': self.sem.id,
            'division': div.id,
            'action': 'generate',
        }
        resp_generate = self.client.post(reverse('scheduler:smart_timetable'), generate_data)
        preview_token = resp_generate.context.get('preview_token')
        self.assertTrue(preview_token)
        export_data = {
            'department': self.dept.id,
            'course': self.course.id,
            'semester': self.sem.id,
            'division': div.id,
            'preview_token': preview_token,
            'action': 'export_preview_image',
        }
        resp_image = self.client.post(reverse('scheduler:smart_timetable'), export_data)
        self.assertEqual(resp_image.status_code, 200)
        self.assertEqual(resp_image['Content-Type'], 'image/png')


