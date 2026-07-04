import re

import json

from django import forms
from django.db.models import Q
from .models import (
    Course, Teacher, Subject, Division, Department, Semester,
    DivisionSubject, Classroom, LabBatchAssignment,
)




class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ['name', 'department', 'duration_years']


class TeacherForm(forms.ModelForm):
    subjects_known = forms.CharField(
        required=False,
        label='Teaching Subjects',
        help_text='Enter subject names separated by commas (for example: Math, Physics).',
        widget=forms.TextInput(
            attrs={
                'placeholder': 'Type subject names, separated by commas',
            }
        ),
    )

    class Meta:
        model = Teacher
        fields = ['name', 'email', 'department', 'additional_departments']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].widget.attrs.update({'class': 'form-control'})
        self.fields['email'].widget.attrs.update({'class': 'form-control'})
        self.fields['subjects_known'].widget.attrs.update({'class': 'form-control'})

        department_qs = Department.objects.order_by('name')
        self.fields['department'].required = False
        self.fields['department'].queryset = department_qs
        self.fields['department'].widget.attrs.update({'class': 'form-select'})
        self.fields['department'].empty_label = 'Multiple Departments'
        self.fields['department'].help_text = (
            'Choose one primary department, or choose "Multiple Departments" and use the list below.'
        )
        self.fields['additional_departments'].required = False
        self.fields['additional_departments'].queryset = department_qs
        self.fields['additional_departments'].widget = forms.CheckboxSelectMultiple()
        # Re-bind choices after replacing the widget, otherwise the rendered
        # choices may appear empty even when the queryset has records.
        self.fields['additional_departments'].widget.choices = self.fields['additional_departments'].choices
        self.fields['additional_departments'].help_text = (
            'Select one or more departments from the list below.'
        )
        if not department_qs.exists():
            self.fields['additional_departments'].help_text += ' No departments are available yet.'
        if self.instance and self.instance.pk:
            # Show unique subject names from both reference text and linked subjects.
            names = self.instance.get_known_subject_name_list()
            names.extend(self.instance.subjects_known.values_list('name', flat=True))
            unique_names = []
            seen = set()
            for name in names:
                key = name.casefold()
                if key in seen:
                    continue
                seen.add(key)
                unique_names.append(name)
            self.fields['subjects_known'].initial = ', '.join(unique_names)

    def clean_subjects_known(self):
        raw = (self.cleaned_data.get('subjects_known') or '').strip()
        if not raw:
            self._subjects_known_names = []
            self._subjects_known_selection = []
            return raw

        entered_names = [n.strip() for n in re.split(r'[,;\n]+', raw) if n.strip()]
        unique_names = []
        seen_names = set()
        for name in entered_names:
            key = name.casefold()
            if key in seen_names:
                continue
            seen_names.add(key)
            unique_names.append(name)

        selected_subjects = []
        selected_ids = set()
        for name in unique_names:
            matches = Subject.objects.filter(name__iexact=name).order_by('id')
            for subject in matches:
                if subject.id not in selected_ids:
                    selected_ids.add(subject.id)
                    selected_subjects.append(subject)

        self._subjects_known_names = unique_names
        self._subjects_known_selection = selected_subjects
        return raw

    def clean(self):
        cleaned = super().clean()
        primary = cleaned.get('department')
        additional = cleaned.get('additional_departments')
        if primary:
            cleaned['additional_departments'] = Department.objects.none()
        elif not additional:
            self.add_error(
                'department',
                'Select one department, or choose "Multiple Departments" and select one or more additional departments.',
            )
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        subject_names = getattr(self, '_subjects_known_names', [])
        instance.known_subject_names = ', '.join(subject_names)
        subjects = getattr(self, '_subjects_known_selection', [])
        additional_departments = self.cleaned_data.get('additional_departments')

        if commit:
            instance.save()
            if additional_departments is not None:
                instance.additional_departments.set(additional_departments)
            instance.subjects_known.set(subjects)
        else:
            def save_m2m():
                if additional_departments is not None:
                    instance.additional_departments.set(additional_departments)
                instance.subjects_known.set(subjects)

            self.save_m2m = save_m2m

        return instance


class SubjectForm(forms.ModelForm):
    lab_batches_json = forms.CharField(required=False, widget=forms.HiddenInput())
    weekly_hours = forms.IntegerField(
        required=False,
        disabled=True,
        label='Weekly hours',
        help_text='Calculated from total semester hours and semester length',
    )

    class Meta:
        model = Subject
        # teacher is added dynamically when a division is passed into the form
        fields = ['name', 'semester', 'is_lab', 'total_hours', 'lab_batches_json']
        help_texts = {
            'is_lab': 'If checked the subject will be scheduled as a lab and requires two consecutive time slots.',
        }

    @staticmethod
    def _resolve_course_id_from_context(value):
        if not value:
            return None
        if hasattr(value, 'course_id'):
            return value.course_id
        if hasattr(value, 'id'):
            return value.id
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_positive_int(raw, label):
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            raise forms.ValidationError(f'{label} must be a valid number.')
        if value <= 0:
            raise forms.ValidationError(f'{label} must be greater than 0.')
        return value

    def __init__(self, *args, **kwargs):
        # pop division argument if supplied by the view
        self.division = kwargs.pop('division', None)
        self.semester_context = kwargs.pop('semester_context', None)
        self.course_context = kwargs.pop('course_context', None)
        if self.semester_context and not hasattr(self.semester_context, 'course_id'):
            try:
                sem_id = int(self.semester_context)
            except (TypeError, ValueError):
                sem_id = None
            if sem_id:
                self.semester_context = Semester.objects.select_related('course').filter(pk=sem_id).first()

        super().__init__(*args, **kwargs)
        self.fields['name'].widget.attrs.update({'class': 'form-control'})
        self.fields['semester'].widget.attrs.update({'class': 'form-select'})
        self.fields['is_lab'].widget.attrs.update({'class': 'form-check-input'})
        self.fields['total_hours'].widget.attrs.update({'class': 'form-control', 'min': '1'})
        self.fields['weekly_hours'].widget.attrs.update({'class': 'form-control'})
        self.fields['lab_batches_json'].widget.attrs.update({'id': 'id_lab_batches_json'})

        self.teacher_rows = []
        self.selected_teacher_id = None
        self.lab_room_rows = []
        self.lab_batch_rows = []
        self.lab_batch_rows_json = '[]'
        self.cleaned_lab_batches = []

        context_course_id = None
        if self.division and self.division.semester_id and self.division.semester:
            context_course_id = self.division.semester.course_id
        if not context_course_id:
            context_course_id = self._resolve_course_id_from_context(self.semester_context)
        if not context_course_id:
            context_course_id = self._resolve_course_id_from_context(self.course_context)
        if not context_course_id and self.instance and self.instance.pk and self.instance.semester_id:
            context_course_id = self.instance.semester.course_id
        if context_course_id:
            self.fields['semester'].queryset = Semester.objects.filter(course_id=context_course_id).order_by('number', 'id')
            if self.semester_context and not self.is_bound and hasattr(self.semester_context, 'id'):
                self.fields['semester'].initial = self.semester_context.id

        # populate weekly_hours if instance exists
        if self.instance and self.instance.pk:
            self.fields['weekly_hours'].initial = self.instance.weekly_hours

        # if editing under a division context, add a teacher field
        if self.division:
            qs = (
                Teacher.objects
                .select_related('department')
                .prefetch_related('additional_departments', 'subjects_known')
            )
            department_id = (
                self.division.semester.course.department_id
                if self.division.semester and self.division.semester.course
                else None
            )
            if department_id:
                qs = qs.filter(
                    Q(department_id=department_id) | Q(additional_departments__id=department_id)
                ).distinct()
            self.teacher_queryset = qs.order_by('name')
            self.fields['teacher'] = forms.ModelChoiceField(
                queryset=self.teacher_queryset,
                required=False,
                label='Teacher',
                help_text='Assign this teacher for the selected division',
            )
            if self.instance and self.instance.pk:
                try:
                    assign = DivisionSubject.objects.get(
                        division=self.division,
                        subject=self.instance,
                    )
                    self.fields['teacher'].initial = assign.teacher_id
                except DivisionSubject.DoesNotExist:
                    pass
            selected_raw = (
                self.data.get(self.add_prefix('teacher'))
                if self.is_bound
                else self.fields['teacher'].initial
            )
            try:
                self.selected_teacher_id = int(selected_raw)
            except (TypeError, ValueError):
                self.selected_teacher_id = None

            teacher_records = list(self.fields['teacher'].queryset)
            subject_name_for_hint = (
                (self.data.get(self.add_prefix('name')) or '').strip()
                if self.is_bound
                else ((self.instance.name if self.instance and self.instance.pk else '') or '')
            )
            for teacher in teacher_records:
                dept_names = []
                if teacher.department_id:
                    dept_names.append(teacher.department.name)
                additional_names = sorted(
                    dept.name
                    for dept in teacher.additional_departments.all()
                    if dept.id != teacher.department_id
                )
                for dept_name in additional_names:
                    if dept_name not in dept_names:
                        dept_names.append(dept_name)
                if len(dept_names) > 1:
                    dept_text = f"Multiple Department ({', '.join(dept_names)})"
                elif dept_names:
                    dept_text = dept_names[0]
                else:
                    dept_text = 'No Department'

                subject_names = teacher.get_known_subject_name_list()
                for subject in teacher.subjects_known.all():
                    if subject.name not in subject_names:
                        subject_names.append(subject.name)
                subject_text = ', '.join(subject_names) if subject_names else 'Not specified'

                knows_subject = False
                if self.instance and self.instance.pk:
                    knows_subject = (
                        any(subject.pk == self.instance.pk for subject in teacher.subjects_known.all())
                        or teacher.knows_subject_name(self.instance.name)
                    )
                elif subject_name_for_hint:
                    knows_subject = teacher.knows_subject_name(subject_name_for_hint)

                self.teacher_rows.append({
                    'id': teacher.id,
                    'name': teacher.name,
                    'subjects': subject_text,
                    'departments': dept_text,
                    'knows_subject': knows_subject,
                })

            lab_qs = Classroom.objects.filter(is_lab=True).filter(
                Q(division=self.division) | Q(division__isnull=True)
            ).order_by('room_number')
            self.lab_room_rows = [
                {
                    'id': lab.id,
                    'room_number': lab.room_number,
                    'capacity': lab.capacity,
                    'division_id': lab.division_id,
                }
                for lab in lab_qs
            ]

            existing_rows = []
            if self.instance and self.instance.pk and self.instance.is_lab:
                existing_rows = [
                    {
                        'batch_number': row.batch_number,
                        'from_roll_no': row.from_roll_no,
                        'to_roll_no': row.to_roll_no,
                        'teacher_id': row.teacher_id,
                        'laboratory_id': row.laboratory_id,
                    }
                    for row in LabBatchAssignment.objects.filter(
                        division=self.division,
                        subject=self.instance,
                    ).select_related('teacher', 'laboratory').order_by('batch_number')
                ]

            posted_text = self.data.get(self.add_prefix('lab_batches_json')) if self.is_bound else ''
            if posted_text:
                try:
                    parsed_rows = json.loads(posted_text)
                    if isinstance(parsed_rows, list):
                        existing_rows = parsed_rows
                except json.JSONDecodeError:
                    pass
            self.lab_batch_rows = existing_rows
            self.lab_batch_rows_json = json.dumps(self.lab_batch_rows)
            if not self.is_bound and self.lab_batch_rows:
                self.initial['lab_batches_json'] = self.lab_batch_rows_json

    def _parse_lab_batches(self, payload):
        if not payload:
            raise forms.ValidationError('Add at least one lab batch for a lab subject.')
        try:
            rows = json.loads(payload)
        except json.JSONDecodeError:
            raise forms.ValidationError('Lab batch data is invalid. Please re-enter lab batches.')

        if not isinstance(rows, list):
            raise forms.ValidationError('Lab batch data format is invalid.')
        if not rows:
            raise forms.ValidationError('Add at least one lab batch for a lab subject.')

        teacher_map = {row['id']: row for row in self.teacher_rows}
        lab_map = {row['id']: row for row in self.lab_room_rows}
        if not lab_map:
            raise forms.ValidationError(
                'No laboratories are available for this division. Add laboratory rooms first.'
            )

        cleaned_rows = []
        seen_batch = set()
        seen_teacher = set()
        roll_ranges = []
        lab_student_totals = {}
        lab_batch_counts = {}
        pending_rows = []

        for idx, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                raise forms.ValidationError(f'Lab batch row {idx} is invalid.')

            batch_number = str(row.get('batch_number', '')).strip()
            if not batch_number:
                raise forms.ValidationError(f'Batch number is required for row {idx}.')
            normalized_batch = batch_number.casefold()
            if normalized_batch in seen_batch:
                raise forms.ValidationError(f'Duplicate batch number "{batch_number}" is not allowed.')
            seen_batch.add(normalized_batch)

            from_roll = self._to_positive_int(row.get('from_roll_no'), f'From roll no. (row {idx})')
            to_roll = self._to_positive_int(row.get('to_roll_no'), f'To roll no. (row {idx})')
            if from_roll > to_roll:
                raise forms.ValidationError(f'Row {idx}: To roll no. must be greater than or equal to from roll no.')

            teacher_id = self._to_positive_int(row.get('teacher_id'), f'Teacher (row {idx})')
            if teacher_id not in teacher_map:
                raise forms.ValidationError(f'Row {idx}: selected teacher is not valid for this division.')
            if teacher_id in seen_teacher:
                raise forms.ValidationError(
                    f'Row {idx}: each batch must have a different teacher because batches run in parallel.'
                )
            seen_teacher.add(teacher_id)

            batch_size = to_roll - from_roll + 1

            raw_lab_id = row.get('laboratory_id')
            if str(raw_lab_id or '').strip():
                lab_id = self._to_positive_int(raw_lab_id, f'Laboratory (row {idx})')
                if lab_id not in lab_map:
                    raise forms.ValidationError(f'Row {idx}: selected laboratory is not available for this division.')
            else:
                lab_id = None

            roll_ranges.append((from_roll, to_roll, batch_number))
            pending_rows.append({
                'row_index': idx,
                'batch_number': batch_number,
                'from_roll_no': from_roll,
                'to_roll_no': to_roll,
                'teacher_id': teacher_id,
                'laboratory_id': lab_id,
                'batch_size': batch_size,
            })

        roll_ranges.sort(key=lambda item: (item[0], item[1]))
        for i in range(1, len(roll_ranges)):
            prev_start, prev_end, prev_batch = roll_ranges[i - 1]
            curr_start, curr_end, curr_batch = roll_ranges[i]
            if curr_start <= prev_end:
                raise forms.ValidationError(
                    f'Roll ranges overlap between batch {prev_batch} and batch {curr_batch}.'
                )

        def can_fit(lab_id, additional_students):
            lab_meta = lab_map[lab_id]
            projected_students = lab_student_totals.get(lab_id, 0) + additional_students
            required_capacity = projected_students
            return required_capacity <= lab_meta['capacity'], required_capacity

        def pick_auto_lab(batch_size):
            candidates = []
            for lab_id, meta in lab_map.items():
                fits, required_capacity = can_fit(lab_id, batch_size)
                if not fits:
                    continue
                projected_count = lab_batch_counts.get(lab_id, 0) + 1
                projected_students = lab_student_totals.get(lab_id, 0) + batch_size
                candidates.append((
                    projected_count > 1,                      # prefer unused lab first
                    meta['capacity'] - required_capacity,     # tighter fit
                    projected_students,                        # balance packed labs
                    meta['room_number'],
                    lab_id,
                ))
            if not candidates:
                return None
            candidates.sort()
            return candidates[0][4]

        # Assign laboratory automatically when not provided.
        pending_rows.sort(key=lambda row: (-row['batch_size'], row['row_index']))
        for row in pending_rows:
            lab_id = row['laboratory_id']
            if lab_id is None:
                lab_id = pick_auto_lab(row['batch_size'])
                if lab_id is None:
                    raise forms.ValidationError(
                        f'Row {row["row_index"]}: no laboratory has enough remaining capacity for this batch.'
                    )
                row['laboratory_id'] = lab_id
            fits, required_capacity = can_fit(lab_id, row['batch_size'])
            if not fits:
                lab_meta = lab_map[lab_id]
                raise forms.ValidationError(
                    f'Row {row["row_index"]}: required students ({required_capacity}) exceed '
                    f'lab {lab_meta["room_number"]} capacity ({lab_meta["capacity"]}).'
                )
            lab_student_totals[lab_id] = lab_student_totals.get(lab_id, 0) + row['batch_size']
            lab_batch_counts[lab_id] = lab_batch_counts.get(lab_id, 0) + 1

        pending_rows.sort(key=lambda row: row['row_index'])
        for row in pending_rows:
            cleaned_rows.append({
                'batch_number': row['batch_number'],
                'from_roll_no': row['from_roll_no'],
                'to_roll_no': row['to_roll_no'],
                'teacher_id': row['teacher_id'],
                'laboratory_id': row['laboratory_id'],
            })

        for lab_id, total_students in lab_student_totals.items():
            lab_meta = lab_map[lab_id]
            required_capacity = total_students
            if required_capacity > lab_meta['capacity']:
                raise forms.ValidationError(
                    f'Lab {lab_meta["room_number"]} capacity ({lab_meta["capacity"]}) is less than '
                    f'required students ({required_capacity}) for assigned batches.'
                )
        return cleaned_rows

    def clean(self):
        cleaned = super().clean()
        # ensure total_hours positive
        th = cleaned.get('total_hours')
        if th and th <= 0:
            raise forms.ValidationError('Total hours must be greater than zero.')
        # if total_hours given but semester lacks dates, the weekly calculation will
        # be impossible; warn the user rather than silently falling back to a
        # single slot.
        semester = cleaned.get('semester')
        if th and semester and (not semester.start_date or not semester.end_date):
            raise forms.ValidationError(
                'Semester start/end dates must be set before assigning total hours. '
                'Edit the semester to add dates.'
            )

        is_lab = cleaned.get('is_lab')
        self.cleaned_lab_batches = []
        if self.division and is_lab:
            lab_payload = cleaned.get('lab_batches_json') or ''
            self.cleaned_lab_batches = self._parse_lab_batches(lab_payload)
        return cleaned


class DivisionForm(forms.ModelForm):
    @staticmethod
    def _resolve_id(value):
        if not value:
            return None
        if hasattr(value, 'id'):
            return value.id
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def __init__(self, *args, **kwargs):
        self.course_context = kwargs.pop('course_context', None)
        self.semester_context = kwargs.pop('semester_context', None)
        if self.semester_context and not hasattr(self.semester_context, 'course_id'):
            sem_id = self._resolve_id(self.semester_context)
            if sem_id:
                self.semester_context = Semester.objects.select_related('course').filter(pk=sem_id).first()
        super().__init__(*args, **kwargs)
        self.fields['name'].widget.attrs.update({'class': 'form-control'})
        self.fields['semester'].widget.attrs.update({'class': 'form-select'})
        self.fields['strength'].widget.attrs.update({'class': 'form-control', 'min': '0'})

        context_course_id = None
        if self.semester_context and getattr(self.semester_context, 'course_id', None):
            context_course_id = self.semester_context.course_id
        if not context_course_id:
            context_course_id = self._resolve_id(self.course_context)
        if not context_course_id and self.instance and self.instance.pk and self.instance.semester_id:
            context_course_id = self.instance.semester.course_id
        if context_course_id:
            self.fields['semester'].queryset = Semester.objects.filter(course_id=context_course_id).order_by('number', 'id')
            if self.semester_context and not self.is_bound and getattr(self.semester_context, 'id', None):
                self.fields['semester'].initial = self.semester_context.id

    class Meta:
        model = Division
        # user chooses a semester when creating divisions; course is derived automatically
        fields = ['name', 'semester', 'strength']


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ['name', 'description']


class ClassroomForm(forms.ModelForm):
    class Meta:
        model = Classroom
        fields = ['room_number', 'capacity']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['room_number'].widget.attrs.update({'class': 'form-control'})
        self.fields['capacity'].widget.attrs.update({'class': 'form-control', 'min': '1'})


class SmartTimetableForm(forms.Form):
    department = forms.ModelChoiceField(queryset=Department.objects.all(), required=True)
    course = forms.ModelChoiceField(queryset=Course.objects.none(), required=True)
    semester = forms.ModelChoiceField(queryset=Semester.objects.none(), required=True)
    division = forms.ModelChoiceField(queryset=Division.objects.none(), required=False,
                                      help_text='Select division to generate, save, and view its timetable')

    def __init__(self, *args, **kwargs):
        dept_id = kwargs.pop('dept_id', None)
        course_id = kwargs.pop('course_id', None)
        super().__init__(*args, **kwargs)
        # department -> course cascade
        if dept_id:
            self.fields['course'].queryset = Course.objects.filter(department_id=dept_id)
        else:
            self.fields['course'].queryset = Course.objects.none()
        # course -> semester cascade
        if course_id or self.data.get('course'):
            cid = course_id or self.data.get('course')
            try:
                cid = int(cid)
            except (TypeError, ValueError):
                cid = None
            if cid:
                self.fields['semester'].queryset = Semester.objects.filter(course_id=cid)
            else:
                self.fields['semester'].queryset = Semester.objects.none()
        else:
            self.fields['semester'].queryset = Semester.objects.none()
        # semester -> division cascade
        if self.data.get('semester') or self.initial.get('semester'):
            sid = self.data.get('semester') or self.initial.get('semester')
            try:
                sid = int(sid)
            except (TypeError, ValueError):
                sid = None
            if sid:
                self.fields['division'].queryset = Division.objects.filter(semester_id=sid)
            else:
                self.fields['division'].queryset = Division.objects.none()
        else:
            self.fields['division'].queryset = Division.objects.none()


class SemesterForm(forms.ModelForm):
    class Meta:
        model = Semester
        fields = ['number', 'course', 'start_time', 'end_time',
                  'break_count', 'breaks', 'working_days',
                  'max_lectures_per_day',
                  'start_date', 'end_date']
        widgets = {
            'start_time': forms.TimeInput(format='%H:%M', attrs={'type': 'time'}),
            'end_time': forms.TimeInput(format='%H:%M', attrs={'type': 'time'}),
            'breaks': forms.TextInput(attrs={
                'size': 24,
                'style': 'max-width: 260px;',
                'placeholder': '10:00-10:10, 12:00-12:30',
            }),
            'start_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'end_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
        }

    def clean(self):
        cleaned = super().clean()
        st = cleaned.get('start_time')
        et = cleaned.get('end_time')
        if st and et and st >= et:
            raise forms.ValidationError('Start time must be before end time.')
        sd = cleaned.get('start_date')
        ed = cleaned.get('end_date')
        if sd and ed and sd > ed:
            raise forms.ValidationError('Start date must be before end date.')
        # lecture limit
        mld = cleaned.get('max_lectures_per_day')
        if mld is not None and mld <= 0:
            raise forms.ValidationError('Max lectures per day must be positive.')
        # break validation: simple syntax check
        br = cleaned.get('breaks')
        if br:
            from .timetable_generator import normalize_break_range_for_semester
            parts = [p.strip() for p in br.split(',') if p.strip()]
            for p in parts:
                normalized = p.replace('–', '-').replace('—', '-')
                normalized = re.sub(r'(?i)\s*to\s*', '-', normalized)
                if '-' not in normalized:
                    raise forms.ValidationError(
                        'Breaks must be given as start-end pairs (for example: 10:00-10:10).'
                    )
                start_end = normalized.split('-', 1)
                if len(start_end) != 2:
                    raise forms.ValidationError(
                        'Break format is invalid. Use start-end pairs separated by commas.'
                    )
                try:
                    bstart, bend = normalize_break_range_for_semester(
                        start_end[0],
                        start_end[1],
                        semester_start=st,
                        semester_end=et,
                    )
                except ValueError:
                    raise forms.ValidationError(
                        'Invalid break time. Example valid inputs: 10:00-10:10, 10:00 AM-10:10 AM.'
                    )
                if bstart >= bend:
                    raise forms.ValidationError('Each break must have an end time after start time.')
                if st and et and not (st <= bstart < bend <= et):
                    raise forms.ValidationError('Breaks must fall within semester start and end time.')
        return cleaned

