from django.shortcuts import render, get_object_or_404, redirect
from django import forms as django_forms
from django.http import Http404
from django.contrib import messages
from django.http import HttpResponse
from django.db.models import Count, Q, Min, Max, Case, When, Value, IntegerField
from django.core import signing
import json
import io
import re
from urllib.parse import urlencode

from django.urls import reverse, reverse_lazy
from django.views.generic import (
    ListView, CreateView, UpdateView, DeleteView, DetailView
)

from .models import (
    Department, Course, Teacher, Semester, Timetable, Subject, Division,
    DivisionSubject, Classroom, TimeSlot, LabBatchAssignment
)
from .timetable_generator import generate_timetable_for_semester
from .forms import (
    CourseForm, TeacherForm, SubjectForm, DivisionForm, SmartTimetableForm,
    SemesterForm, DepartmentForm, ClassroomForm,
)

# Create your views here.

def home(request):
    # dashboard statistics
    dept_count = Department.objects.count()
    course_count = Course.objects.count()
    teacher_count = Teacher.objects.count()
    classroom_count = Classroom.objects.filter(is_lab=False).count()
    laboratory_count = Classroom.objects.filter(is_lab=True).count()
    timetable_count = Timetable.objects.count()
    # compute subjects per department for chart
    dept_subjects = Department.objects.annotate(
        subject_count=Count('courses__semesters__subjects')
    )
    labels = [d.name for d in dept_subjects]
    values = [d.subject_count for d in dept_subjects]

    return render(request, 'home.html', {
        'stats': {
            'departments': dept_count,
            'courses': course_count,
            'teachers': teacher_count,
            'classrooms': classroom_count,
            'laboratories': laboratory_count,
            'timetables': timetable_count,
            # we don't want to double count subjects, compute separately
            'subjects': sum(values),
        },
        'chart_data': json.dumps({'labels': labels, 'values': values}),
    })


# department CRUD helpers

def department_list(request):
    departments = Department.objects.all()
    return render(request, 'departments.html', {'departments': departments})


def _teachers_for_department(department):
    return (
        Teacher.objects
        .select_related('department')
        .prefetch_related('additional_departments')
        .filter(Q(department=department) | Q(additional_departments=department))
        .distinct()
        .order_by('name')
    )


def department_detail(request, id):
    dept = get_object_or_404(Department, pk=id)
    courses = dept.courses.all()
    teachers = _teachers_for_department(dept)
    return render(request, 'department_detail.html', {
        'department': dept,
        'courses': courses,
        'teachers': teachers,
    })


class DepartmentCreateView(CreateView):
    model = Department
    form_class = DepartmentForm
    template_name = 'department_form.html'
    success_url = reverse_lazy('scheduler:department_list')


class DepartmentUpdateView(UpdateView):
    model = Department
    form_class = DepartmentForm
    template_name = 'department_form.html'
    success_url = reverse_lazy('scheduler:department_list')

    def form_valid(self, form):
        obj = form.save()
        return redirect('scheduler:department_detail', id=obj.id)


class DepartmentDeleteView(DeleteView):
    model = Department
    template_name = 'department_confirm_delete.html'
    success_url = reverse_lazy('scheduler:department_list')

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        response = super().delete(request, *args, **kwargs)
        return redirect('scheduler:department_list')


def department_detail(request, id):
    dept = get_object_or_404(Department, pk=id)
    courses = dept.courses.all()
    teachers = _teachers_for_department(dept)
    return render(request, 'department_detail.html', {
        'department': dept,
        'courses': courses,
        'teachers': teachers,
    })



def semester_list(request):
    semesters = Semester.objects.select_related('course').all()
    return render(request, 'semesters.html', {'semesters': semesters})


# ===== semester CRUD =====
class SemesterCreateView(CreateView):
    model = Semester
    form_class = SemesterForm
    template_name = 'semester_form.html'
    success_url = reverse_lazy('scheduler:course_list')

    def get_initial(self):
        initial = super().get_initial()
        course_id = self.request.GET.get('course')
        if course_id:
            initial['course'] = course_id
        return initial

    def form_valid(self, form):
        obj = form.save()
        return redirect('scheduler:course_detail', pk=obj.course_id)


class SemesterUpdateView(UpdateView):
    model = Semester
    form_class = SemesterForm
    template_name = 'semester_form.html'
    success_url = reverse_lazy('scheduler:course_list')

    def form_valid(self, form):
        obj = form.save()
        return redirect('scheduler:course_detail', pk=obj.course_id)


class SemesterDeleteView(DeleteView):
    model = Semester
    template_name = 'semester_confirm_delete.html'
    success_url = reverse_lazy('scheduler:course_list')

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        course_id = obj.course_id
        response = super().delete(request, *args, **kwargs)
        return redirect('scheduler:course_detail', pk=course_id)


def timetable_view(request, semester_id):
    semester = get_object_or_404(Semester, pk=semester_id)
    entries = Timetable.objects.filter(semester=semester, division__isnull=True).select_related(
        'subject', 'teacher', 'classroom', 'time_slot'
    )
    # group by day and then by slot
    table = {}
    for entry in entries:
        table.setdefault(entry.day, []).append(entry)
    # sort each list by timeslot start
    for day, lst in table.items():
        lst.sort(key=lambda x: x.time_slot.start_time)
    # compute full slot list from semester hours (including break markers)
    from .timetable_generator import get_timeslots_for_semester
    timeslot_list = get_timeslots_for_semester(semester, include_breaks=True)
    # determine ordered working days for this semester
    if semester.working_days == 'MON-SAT':
        day_order = ['MON','TUE','WED','THU','FRI','SAT']
    else:
        day_order = ['MON','TUE','WED','THU','FRI']
    # build cell matrix expected by timetable template (break/empty/entry cells)
    day_cells = _build_day_cells(table, timeslot_list, day_order)
    ordered_table = [(day, day_cells.get(day, [])) for day in day_order]
    return render(request, 'timetable.html', {
        'semester': semester,
        'table': ordered_table,
        'timeslots': timeslot_list,
        'start_time': semester.start_time,
        'end_time': semester.end_time,
        'breaks': semester.breaks,
        'working_days': semester.get_working_days_display(),
    })


# ===== course CRUD =====
class CourseListView(ListView):
    model = Course
    template_name = 'courses.html'
    context_object_name = 'courses'

class CourseCreateView(CreateView):
    model = Course
    form_class = CourseForm
    template_name = 'course_form.html'
    success_url = reverse_lazy('scheduler:course_list')

    def get_initial(self):
        initial = super().get_initial()
        dept_id = self.request.GET.get('dept')
        if dept_id:
            initial['department'] = dept_id
        return initial

    def form_valid(self, form):
        # after save go back to department detail if department set
        self.object = form.save()
        if self.object.department_id:
            return super().form_valid(form) if False else redirect('scheduler:department_detail', id=self.object.department_id)
        return super().form_valid(form)

class CourseUpdateView(UpdateView):
    model = Course
    form_class = CourseForm
    template_name = 'course_form.html'
    success_url = reverse_lazy('scheduler:course_list')

    def form_valid(self, form):
        self.object = form.save()
        if self.object.department_id:
            return redirect('scheduler:department_detail', id=self.object.department_id)
        return super().form_valid(form)

class CourseDeleteView(DeleteView):
    model = Course
    template_name = 'course_confirm_delete.html'
    success_url = reverse_lazy('scheduler:course_list')

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        dept_id = self.object.department_id
        response = super().delete(request, *args, **kwargs)
        if dept_id:
            return redirect('scheduler:department_detail', id=dept_id)
        return response


# course detail with lists of subjects and divisions
class CourseDetailView(DetailView):
    model = Course
    template_name = 'course_detail.html'
    context_object_name = 'course'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # include semesters so we can list them on the detail page
        ctx['semesters'] = self.object.semesters.all()
        return ctx


# new view for semester detail
class SemesterDetailView(DetailView):
    model = Semester
    template_name = 'semester_detail.html'
    context_object_name = 'semester'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sem = self.object
        # subjects belonging only to this semester
        ctx['subjects'] = sem.subjects.all()
        # when subjects are displayed we don't want the template to crash if the
        # teacher field is empty; the template will conditionally render it.
        # divisions are at course level, show those for the semester's course
        # only show divisions explicitly assigned to this semester
        ctx['divisions'] = sem.divisions.all()
        return ctx

# ===== subject CRUD =====
class SubjectCreateView(CreateView):
    model = Subject
    form_class = SubjectForm
    template_name = 'subject_form.html'
    success_url = reverse_lazy('scheduler:course_list')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        div_id = self.request.GET.get('division') or self.request.POST.get('division')
        if div_id:
            ctx['division'] = Division.objects.filter(pk=div_id).first()
        return ctx

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # propagate division id from GET/POST to the form
        div_id = self.request.GET.get('division') or self.request.POST.get('division')
        if div_id:
            kwargs['division'] = Division.objects.filter(pk=div_id).first()
        sem_id = self.request.GET.get('semester') or self.request.POST.get('semester')
        if sem_id:
            kwargs['semester_context'] = Semester.objects.select_related('course').filter(pk=sem_id).first()
        course_id = self.request.GET.get('course') or self.request.POST.get('course')
        if course_id:
            kwargs['course_context'] = Course.objects.filter(pk=course_id).first()
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        # allow preselecting by semester first (used from semester detail page)
        sem_id = self.request.GET.get('semester')
        if sem_id:
            sem = Semester.objects.filter(pk=sem_id).first()
            if sem:
                initial['semester'] = sem
                return initial
        # fallback to course-based preselection
        course_id = self.request.GET.get('course')
        if course_id:
            # choose first semester of course or leave to user
            course = Course.objects.filter(pk=course_id).first()
            if course and course.semesters.exists():
                initial['semester'] = course.semesters.first()
        return initial

    def form_valid(self, form):
        obj = form.save()
        # handle division-specific teacher assignment
        division = getattr(form, 'division', None)
        if division:
            teacher = form.cleaned_data.get('teacher')
            if obj.is_lab:
                # Lab subjects are batch-driven: one teacher/lab per batch.
                DivisionSubject.objects.filter(division=division, subject=obj).delete()
                LabBatchAssignment.objects.filter(division=division, subject=obj).delete()
                cleaned_batches = getattr(form, 'cleaned_lab_batches', [])
                batch_rows = [
                    LabBatchAssignment(
                        division=division,
                        subject=obj,
                        batch_number=row['batch_number'],
                        from_roll_no=row['from_roll_no'],
                        to_roll_no=row['to_roll_no'],
                        teacher_id=row['teacher_id'],
                        laboratory_id=row['laboratory_id'],
                    )
                    for row in cleaned_batches
                ]
                if batch_rows:
                    LabBatchAssignment.objects.bulk_create(batch_rows)
            else:
                LabBatchAssignment.objects.filter(division=division, subject=obj).delete()
                if teacher:
                    DivisionSubject.objects.update_or_create(
                        division=division, subject=obj,
                        defaults={'teacher': teacher}
                    )
                else:
                    DivisionSubject.objects.filter(division=division, subject=obj).delete()
            return redirect('scheduler:division_detail', pk=division.pk)
        return redirect('scheduler:semester_detail', pk=obj.semester_id)

class SubjectUpdateView(UpdateView):
    model = Subject
    form_class = SubjectForm
    template_name = 'subject_form.html'
    success_url = reverse_lazy('scheduler:course_list')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        div_id = self.request.GET.get('division') or self.request.POST.get('division')
        if div_id:
            ctx['division'] = Division.objects.filter(pk=div_id).first()
        return ctx

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        div_id = self.request.GET.get('division') or self.request.POST.get('division')
        if div_id:
            kwargs['division'] = Division.objects.filter(pk=div_id).first()
        sem_id = self.request.GET.get('semester') or self.request.POST.get('semester')
        if sem_id:
            kwargs['semester_context'] = Semester.objects.select_related('course').filter(pk=sem_id).first()
        course_id = self.request.GET.get('course') or self.request.POST.get('course')
        if course_id:
            kwargs['course_context'] = Course.objects.filter(pk=course_id).first()
        return kwargs

    def form_valid(self, form):
        obj = form.save()
        division = getattr(form, 'division', None)
        if division:
            teacher = form.cleaned_data.get('teacher')
            if obj.is_lab:
                DivisionSubject.objects.filter(division=division, subject=obj).delete()
                LabBatchAssignment.objects.filter(division=division, subject=obj).delete()
                cleaned_batches = getattr(form, 'cleaned_lab_batches', [])
                batch_rows = [
                    LabBatchAssignment(
                        division=division,
                        subject=obj,
                        batch_number=row['batch_number'],
                        from_roll_no=row['from_roll_no'],
                        to_roll_no=row['to_roll_no'],
                        teacher_id=row['teacher_id'],
                        laboratory_id=row['laboratory_id'],
                    )
                    for row in cleaned_batches
                ]
                if batch_rows:
                    LabBatchAssignment.objects.bulk_create(batch_rows)
            else:
                LabBatchAssignment.objects.filter(division=division, subject=obj).delete()
                if teacher:
                    DivisionSubject.objects.update_or_create(
                        division=division, subject=obj,
                        defaults={'teacher': teacher}
                    )
                else:
                    DivisionSubject.objects.filter(division=division, subject=obj).delete()
            return redirect('scheduler:division_detail', pk=division.pk)
        return redirect('scheduler:semester_detail', pk=obj.semester_id)

class SubjectDeleteView(DeleteView):
    model = Subject
    template_name = 'subject_confirm_delete.html'
    success_url = reverse_lazy('scheduler:course_list')

    def get(self, request, *args, **kwargs):
        """Override GET so we can gracefully handle a missing object.

        If the subject has already been removed the default DeleteView
        raises Http404; catch that and redirect back to the semester
        detail (if provided) or the course list with a warning message.
        """
        try:
            return super().get(request, *args, **kwargs)
        except Http404:
            sem_id = request.GET.get('semester')
            messages.warning(request, 'Subject not found; it may have already been deleted.')
            if sem_id:
                return redirect('scheduler:semester_detail', pk=sem_id)
            return redirect('scheduler:course_list')

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        sem_id = self.object.semester_id
        response = super().delete(request, *args, **kwargs)
        return redirect('scheduler:semester_detail', pk=sem_id)

# ===== division CRUD =====


class DivisionDetailView(DetailView):
    model = Division
    template_name = 'division_detail.html'
    context_object_name = 'division'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        div = self.object
        subjects = div.semester.subjects.all() if div.semester else []
        # attach division-specific teacher for each subject (may be None)
        assigns = {a.subject_id: a.teacher for a in div.assignments.select_related('teacher')}
        lab_batches_by_subject = {}
        for batch in LabBatchAssignment.objects.filter(division=div).select_related('teacher', 'laboratory', 'subject').order_by(
            'subject__name', 'batch_number'
        ):
            lab_batches_by_subject.setdefault(batch.subject_id, []).append(batch)
        for subj in subjects:
            subj.div_teacher = assigns.get(subj.id)
            subj.lab_batches = lab_batches_by_subject.get(subj.id, [])
        ctx['subjects'] = subjects
        ctx['has_saved_timetable'] = Timetable.objects.filter(division=div).exists()
        return ctx

# ===== division CRUD =====
class DivisionCreateView(CreateView):
    model = Division
    form_class = DivisionForm
    template_name = 'division_form.html'
    success_url = reverse_lazy('scheduler:course_list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        sem_id = self.request.GET.get('semester') or self.request.POST.get('semester')
        if sem_id:
            kwargs['semester_context'] = Semester.objects.select_related('course').filter(pk=sem_id).first()
        course_id = self.request.GET.get('course') or self.request.POST.get('course')
        if course_id:
            kwargs['course_context'] = Course.objects.filter(pk=course_id).first()
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        sem_id = self.request.GET.get('semester')
        if sem_id:
            initial['semester'] = sem_id
        return initial

    def form_valid(self, form):
        obj = form.save()
        # course field is set in model.save, but redirect based on semester
        sem_id = form.cleaned_data.get('semester').id
        if sem_id:
            return redirect('scheduler:semester_detail', pk=sem_id)
        return redirect('scheduler:course_detail', pk=obj.course_id)

class DivisionUpdateView(UpdateView):
    model = Division
    form_class = DivisionForm
    template_name = 'division_form.html'
    success_url = reverse_lazy('scheduler:course_list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        sem_id = self.request.GET.get('semester') or self.request.POST.get('semester')
        if sem_id:
            kwargs['semester_context'] = Semester.objects.select_related('course').filter(pk=sem_id).first()
        course_id = self.request.GET.get('course') or self.request.POST.get('course')
        if course_id:
            kwargs['course_context'] = Course.objects.filter(pk=course_id).first()
        return kwargs

    def form_valid(self, form):
        obj = form.save()
        sem_id = obj.semester_id or self.request.GET.get('semester')
        if sem_id:
            return redirect('scheduler:semester_detail', pk=sem_id)
        return redirect('scheduler:course_detail', pk=obj.course_id)

class DivisionDeleteView(DeleteView):
    model = Division
    template_name = 'division_confirm_delete.html'
    success_url = reverse_lazy('scheduler:course_list')

    def get(self, request, *args, **kwargs):
        try:
            return super().get(request, *args, **kwargs)
        except Http404:
            sem_id = request.GET.get('semester')
            course_id = request.GET.get('course')
            messages.warning(request, 'Division not found; it may have already been deleted.')
            if sem_id:
                return redirect('scheduler:semester_detail', pk=sem_id)
            if course_id:
                return redirect('scheduler:course_detail', pk=course_id)
            return redirect('scheduler:course_list')

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        # prefer sem_id from instance since divisions now belong to a semester
        sem_id = self.object.semester_id or request.GET.get('semester')
        course_id = self.object.course_id
        response = super().delete(request, *args, **kwargs)
        if sem_id:
            return redirect('scheduler:semester_detail', pk=sem_id)
        if course_id:
            return redirect('scheduler:course_detail', pk=course_id)
        return response


class RoomTypeViewMixin:
    model = Classroom
    form_class = ClassroomForm
    is_lab = False
    room_type_singular = 'Classroom'
    room_type_plural = 'Classrooms'
    list_url_name = 'scheduler:classroom_list'
    add_url_name = 'scheduler:classroom_add'
    edit_url_name = 'scheduler:classroom_edit'
    delete_url_name = 'scheduler:classroom_delete'
    detail_url_name = 'scheduler:classroom_detail'

    def get_queryset(self):
        return (
            Classroom.objects
            .filter(is_lab=self.is_lab)
            .select_related('division__semester__course')
            .order_by('room_number')
        )

    def _context_room_meta(self):
        return {
            'room_type_singular': self.room_type_singular,
            'room_type_plural': self.room_type_plural,
            'list_url_name': self.list_url_name,
            'add_url_name': self.add_url_name,
            'edit_url_name': self.edit_url_name,
            'delete_url_name': self.delete_url_name,
            'detail_url_name': self.detail_url_name,
        }


class BaseRoomListView(RoomTypeViewMixin, ListView):
    template_name = 'room_list.html'
    context_object_name = 'rooms'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(self._context_room_meta())
        return ctx


class BaseRoomDetailView(RoomTypeViewMixin, DetailView):
    template_name = 'room_detail.html'
    context_object_name = 'room'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        room = self.object
        engaged_course_ranges = (
            Timetable.objects
            .filter(classroom=room)
            .annotate(
                day_order=Case(
                    When(day='MON', then=Value(1)),
                    When(day='TUE', then=Value(2)),
                    When(day='WED', then=Value(3)),
                    When(day='THU', then=Value(4)),
                    When(day='FRI', then=Value(5)),
                    When(day='SAT', then=Value(6)),
                    default=Value(99),
                    output_field=IntegerField(),
                )
            )
            .values(
                'semester__course_id',
                'semester__course__name',
                'day',
                'day_order',
            )
            .annotate(
                engaged_start=Min('time_slot__start_time'),
                engaged_end=Max('time_slot__end_time'),
            )
            .order_by('semester__course__name', 'day_order')
        )
        day_labels = dict(Timetable.DAYS)
        engaged_rows = []
        for row in engaged_course_ranges:
            engaged_rows.append({
                'course_name': row['semester__course__name'],
                'day_label': day_labels.get(row['day'], row['day']),
                'engaged_start': row['engaged_start'],
                'engaged_end': row['engaged_end'],
            })
        ctx['engaged_rows'] = engaged_rows
        ctx.update(self._context_room_meta())
        return ctx


class BaseRoomCreateView(RoomTypeViewMixin, CreateView):
    template_name = 'room_form.html'

    def form_valid(self, form):
        self.object = form.save(commit=False)
        self.object.is_lab = self.is_lab
        self.object.save()
        return redirect(self.list_url_name)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(self._context_room_meta())
        return ctx


class BaseRoomUpdateView(RoomTypeViewMixin, UpdateView):
    template_name = 'room_form.html'

    def form_valid(self, form):
        self.object = form.save(commit=False)
        self.object.is_lab = self.is_lab
        self.object.save()
        return redirect(self.list_url_name)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(self._context_room_meta())
        return ctx


class BaseRoomDeleteView(RoomTypeViewMixin, DeleteView):
    template_name = 'room_confirm_delete.html'
    form_class = django_forms.Form

    def get_success_url(self):
        return reverse(self.list_url_name)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(self._context_room_meta())
        return ctx


class ClassroomListView(BaseRoomListView):
    pass


class ClassroomDetailView(BaseRoomDetailView):
    pass


class ClassroomCreateView(BaseRoomCreateView):
    pass


class ClassroomUpdateView(BaseRoomUpdateView):
    pass


class ClassroomDeleteView(BaseRoomDeleteView):
    pass


class LaboratoryListView(BaseRoomListView):
    is_lab = True
    room_type_singular = 'Laboratory'
    room_type_plural = 'Laboratories'
    list_url_name = 'scheduler:laboratory_list'
    add_url_name = 'scheduler:laboratory_add'
    edit_url_name = 'scheduler:laboratory_edit'
    delete_url_name = 'scheduler:laboratory_delete'
    detail_url_name = 'scheduler:laboratory_detail'


class LaboratoryDetailView(BaseRoomDetailView):
    is_lab = True
    room_type_singular = 'Laboratory'
    room_type_plural = 'Laboratories'
    list_url_name = 'scheduler:laboratory_list'
    add_url_name = 'scheduler:laboratory_add'
    edit_url_name = 'scheduler:laboratory_edit'
    delete_url_name = 'scheduler:laboratory_delete'
    detail_url_name = 'scheduler:laboratory_detail'


class LaboratoryCreateView(BaseRoomCreateView):
    is_lab = True
    room_type_singular = 'Laboratory'
    room_type_plural = 'Laboratories'
    list_url_name = 'scheduler:laboratory_list'
    add_url_name = 'scheduler:laboratory_add'
    edit_url_name = 'scheduler:laboratory_edit'
    delete_url_name = 'scheduler:laboratory_delete'


class LaboratoryUpdateView(BaseRoomUpdateView):
    is_lab = True
    room_type_singular = 'Laboratory'
    room_type_plural = 'Laboratories'
    list_url_name = 'scheduler:laboratory_list'
    add_url_name = 'scheduler:laboratory_add'
    edit_url_name = 'scheduler:laboratory_edit'
    delete_url_name = 'scheduler:laboratory_delete'


class LaboratoryDeleteView(BaseRoomDeleteView):
    is_lab = True
    room_type_singular = 'Laboratory'
    room_type_plural = 'Laboratories'
    list_url_name = 'scheduler:laboratory_list'
    add_url_name = 'scheduler:laboratory_add'
    edit_url_name = 'scheduler:laboratory_edit'
    delete_url_name = 'scheduler:laboratory_delete'


# ===== teacher CRUD =====
class TeacherListView(ListView):
    model = Teacher
    template_name = 'teachers.html'
    context_object_name = 'teachers'

    def get_queryset(self):
        queryset = (
            Teacher.objects
            .select_related('department')
            .prefetch_related('additional_departments')
            .order_by('name')
        )
        dept_id = self.request.GET.get('dept')
        if dept_id:
            queryset = queryset.filter(
                Q(department_id=dept_id) | Q(additional_departments__id=dept_id)
            ).distinct()
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        dept_id = self.request.GET.get('dept', '')
        context['departments'] = Department.objects.order_by('name')
        context['selected_department'] = dept_id
        return context


class TeacherDetailView(DetailView):
    model = Teacher
    template_name = 'teacher_detail.html'
    context_object_name = 'teacher'

    def get_queryset(self):
        return (
            Teacher.objects
            .select_related('department')
            .prefetch_related('additional_departments')
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        teacher = self.object
        ctx['teacher_departments'] = teacher.get_departments()
        ctx['known_subject_names'] = teacher.get_known_subject_name_list()
        ctx['division_assignments'] = (
            teacher.division_subjects
            .select_related('division__semester__course', 'subject')
            .order_by(
                'division__semester__course__name',
                'division__semester__number',
                'division__name',
                'subject__name',
            )
        )
        return ctx


class TeacherCreateView(CreateView):
    model = Teacher
    form_class = TeacherForm
    template_name = 'teacher_form.html'
    success_url = reverse_lazy('scheduler:teacher_list')

    def get_initial(self):
        initial = super().get_initial()
        dept_id = self.request.GET.get('dept')
        if dept_id:
            initial['department'] = dept_id
        return initial

    def form_valid(self, form):
        self.object = form.save()
        if self.object.department_id:
            return redirect('scheduler:department_detail', id=self.object.department_id)
        first_additional = self.object.additional_departments.order_by('name').first()
        if first_additional:
            return redirect('scheduler:department_detail', id=first_additional.id)
        return super().form_valid(form)

class TeacherUpdateView(UpdateView):
    model = Teacher
    form_class = TeacherForm
    template_name = 'teacher_form.html'
    success_url = reverse_lazy('scheduler:teacher_list')

    def form_valid(self, form):
        self.object = form.save()
        if self.object.department_id:
            return redirect('scheduler:department_detail', id=self.object.department_id)
        first_additional = self.object.additional_departments.order_by('name').first()
        if first_additional:
            return redirect('scheduler:department_detail', id=first_additional.id)
        return super().form_valid(form)

class TeacherDeleteView(DeleteView):
    model = Teacher
    template_name = 'teacher_confirm_delete.html'
    success_url = reverse_lazy('scheduler:teacher_list')

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        dept_id = self.object.department_id
        response = super().delete(request, *args, **kwargs)
        if dept_id:
            return redirect('scheduler:department_detail', id=dept_id)
        return response




def _build_day_cells(table, timeslot_list, day_order, merge_lab_cells=True, lab_batch_map=None):
    """Return a new dictionary mapping day codes to a list of cell dicts.

    Each cell dictionary represents either a break, an empty slot, or a class
    entry.  Lab entries spanning two contiguous timeslots are merged by
    producing a single cell with ``colspan=2``.
    """
    day_cells = {}
    for day in day_order:
        cells = []
        idx = 0
        entries_for_day = table.get(day, [])
        while idx < len(timeslot_list):
            slot = timeslot_list[idx]
            if getattr(slot, 'is_break', False):
                cells.append({'break': True})
                idx += 1
                continue
            slot_entries = [e for e in entries_for_day if e.time_slot_id == slot.id]
            if slot_entries:
                # Keep a stable order so parallel batches render predictably.
                slot_entries.sort(key=lambda item: (item.subject_id, item.classroom_id, item.teacher_id))
                e = slot_entries[0]
                colspan = 1
                if merge_lab_cells and e.subject.is_lab and idx + 1 < len(timeslot_list):
                    next_slot = timeslot_list[idx + 1]
                    next_entries = [ee for ee in entries_for_day if ee.time_slot_id == next_slot.id]
                    next_entries.sort(key=lambda item: (item.subject_id, item.classroom_id, item.teacher_id))

                    current_signature = sorted((ee.subject_id, ee.teacher_id, ee.classroom_id) for ee in slot_entries)
                    next_signature = sorted((ee.subject_id, ee.teacher_id, ee.classroom_id) for ee in next_entries)
                    if next_entries and current_signature == next_signature:
                        colspan = 2
                batch_rows = []
                if e.subject.is_lab and lab_batch_map:
                    batch_rows = lab_batch_map.get(e.subject_id, [])
                cells.append({'entry': e, 'entries': slot_entries, 'batch_rows': batch_rows, 'colspan': colspan})
                idx += colspan
            else:
                cells.append({'empty': True})
                idx += 1
        day_cells[day] = cells
    return day_cells


def _day_order_for_semester(semester):
    if semester.working_days == 'MON-SAT':
        return ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT']
    return ['MON', 'TUE', 'WED', 'THU', 'FRI']


def _table_from_entries(semester, entries, merge_lab_cells=True, division=None):
    """Build ordered day/cell structure and slot list for timetable rendering."""
    table = {}
    for entry in entries:
        table.setdefault(entry.day, []).append(entry)
    for day, lst in table.items():
        lst.sort(key=lambda x: x.time_slot.start_time)

    if division is None:
        if entries:
            candidate_division = getattr(entries[0], 'division', None)
            if candidate_division:
                division = candidate_division

    lab_batch_map = {}
    if division is not None:
        for batch in (
            LabBatchAssignment.objects
            .filter(division=division, subject__semester=semester)
            .select_related('teacher', 'laboratory')
            .order_by('subject_id', 'batch_number')
        ):
            lab_batch_map.setdefault(batch.subject_id, []).append(batch)

    from .timetable_generator import get_timeslots_for_semester
    timeslot_list = get_timeslots_for_semester(semester, include_breaks=True)
    day_order = _day_order_for_semester(semester)
    day_cells = _build_day_cells(
        table,
        timeslot_list,
        day_order,
        merge_lab_cells=merge_lab_cells,
        lab_batch_map=lab_batch_map,
    )
    ordered_table = [(day, day_cells.get(day, [])) for day in day_order]
    return ordered_table, timeslot_list


def _serialize_schedule_entries(entries):
    payload = []
    for e in entries:
        payload.append({
            'subject_id': e.subject_id or e.subject.id,
            'teacher_id': e.teacher_id or e.teacher.id,
            'classroom_id': e.classroom_id or e.classroom.id,
            'day': e.day,
            'time_slot_id': e.time_slot_id or e.time_slot.id,
        })
    return payload


def _deserialize_schedule_entries(serialized_entries, semester, division=None):
    subject_ids = {r['subject_id'] for r in serialized_entries}
    teacher_ids = {r['teacher_id'] for r in serialized_entries}
    classroom_ids = {r['classroom_id'] for r in serialized_entries}
    time_slot_ids = {r['time_slot_id'] for r in serialized_entries}

    subjects = Subject.objects.in_bulk(subject_ids)
    teachers = Teacher.objects.in_bulk(teacher_ids)
    classrooms = Classroom.objects.in_bulk(classroom_ids)
    time_slots = TimeSlot.objects.in_bulk(time_slot_ids)

    entries = []
    for row in serialized_entries:
        subject = subjects.get(row['subject_id'])
        teacher = teachers.get(row['teacher_id'])
        classroom = classrooms.get(row['classroom_id'])
        time_slot = time_slots.get(row['time_slot_id'])
        if not (subject and teacher and classroom and time_slot):
            continue
        entries.append(Timetable(
            semester=semester,
            division=division,
            subject=subject,
            teacher=teacher,
            classroom=classroom,
            day=row['day'],
            time_slot=time_slot,
        ))
    return entries


def _occupied_room_slots_for_scope(exclude_division=None):
    """Return occupied (classroom_id, day, time_slot_id) tuples for other scopes."""
    qs = Timetable.objects.all()
    if exclude_division is not None:
        qs = qs.exclude(division=exclude_division)
    return set(qs.values_list('classroom_id', 'day', 'time_slot_id'))


def _find_room_slot_conflicts(entries, exclude_division=None):
    """Find existing timetable rows that collide with the provided room/day/slot keys."""
    if not entries:
        return []

    requested_keys = {
        (e.classroom_id or e.classroom.id, e.day, e.time_slot_id or e.time_slot.id)
        for e in entries
    }
    classroom_ids = {key[0] for key in requested_keys}
    day_codes = {key[1] for key in requested_keys}
    slot_ids = {key[2] for key in requested_keys}
    day_labels = dict(Timetable.DAYS)

    qs = (
        Timetable.objects
        .filter(
            classroom_id__in=classroom_ids,
            day__in=day_codes,
            time_slot_id__in=slot_ids,
        )
        .select_related('classroom', 'semester__course', 'division__semester__course', 'time_slot')
        .order_by('classroom__room_number', 'day', 'time_slot__start_time')
    )
    if exclude_division is not None:
        qs = qs.exclude(division=exclude_division)

    conflicts = []
    seen = set()
    for row in qs:
        key = (row.classroom_id, row.day, row.time_slot_id)
        if key not in requested_keys or key in seen:
            continue
        seen.add(key)
        course_name = row.division.semester.course.name if row.division_id else row.semester.course.name
        division_name = row.division.name if row.division_id else 'N/A'
        conflicts.append({
            'room': row.classroom.room_number,
            'course': course_name,
            'division': division_name,
            'day': day_labels.get(row.day, row.day),
            'time': (
                f"{row.time_slot.start_time.strftime('%H:%M')}-"
                f"{row.time_slot.end_time.strftime('%H:%M')}"
            ),
        })
    return conflicts


def _room_conflict_message(conflicts, limit=5):
    if not conflicts:
        return ''
    items = [
        (
            f"{row['room']} is already engaged for {row['course']} ({row['division']}) "
            f"on {row['day']} {row['time']}"
        )
        for row in conflicts[:limit]
    ]
    remaining = len(conflicts) - len(items)
    if remaining > 0:
        items.append(f"and {remaining} more conflict(s)")
    return (
        "Cannot allocate this classroom/laboratory schedule because some time slots are already occupied: "
        + '; '.join(items)
        + '.'
    )


def _build_generation_failure_points(error_text):
    """Convert generator failure text into short, user-friendly bullet points."""
    text = (error_text or '').strip()
    if not text:
        return []

    points = []

    attempts_match = re.search(r'after\s+(\d+)\s+attempts?', text, flags=re.IGNORECASE)
    if attempts_match:
        points.append(
            f"Tried {attempts_match.group(1)} scheduling attempts but no valid combination was found."
        )

    frequent_match = re.search(
        r'Most frequent blocker:\s*(.+?)\s*\((\d+)\s+attempts?\)\.',
        text,
        flags=re.IGNORECASE,
    )
    if frequent_match:
        blocker = frequent_match.group(1).strip()
        count = frequent_match.group(2)
        points.append(f"Most frequent blocker: {blocker} ({count} attempts).")

    last_match = re.search(r'Last blocker:\s*(.+)$', text, flags=re.IGNORECASE)
    if last_match:
        points.append(f"Last blocker: {last_match.group(1).strip()}")

    lower_text = text.lower()
    if 'capacity >=' in lower_text or 'division strength' in lower_text:
        points.append("Use rooms/labs with capacity greater than or equal to the selected division strength.")
    if 'could not assign subject' in lower_text:
        points.append("Increase free room/lab slots or reduce weekly load for the listed subjects.")
    if 'teacher conflict' in lower_text:
        points.append("A teacher is colliding across classes in the same slot; adjust teacher mapping or subject load.")
    if 'parallel for all batches' in lower_text:
        points.append("Lab batches require same-time parallel slots and compatible labs/teachers.")

    if not points:
        points.append(text)
    return points[:5]


def _load_image_font(size, bold=False):
    from PIL import ImageFont

    preferred = []
    if bold:
        preferred.extend(['arialbd.ttf', 'DejaVuSans-Bold.ttf'])
    else:
        preferred.extend(['arial.ttf', 'DejaVuSans.ttf'])

    for name in preferred:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _text_width(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _wrap_text(draw, text, font, max_width, max_lines=3):
    text = (text or '').strip()
    if not text:
        return ''

    words = text.split()
    lines = []
    current = ''

    for word in words:
        candidate = word if not current else f"{current} {word}"
        if _text_width(draw, candidate, font) <= max_width:
            current = candidate
            continue

        if current:
            lines.append(current)
            current = word
        else:
            chunk = ''
            for ch in word:
                test = f"{chunk}{ch}"
                if _text_width(draw, test, font) <= max_width:
                    chunk = test
                else:
                    if chunk:
                        lines.append(chunk)
                    chunk = ch
                    if len(lines) >= max_lines:
                        break
            current = chunk

        if len(lines) >= max_lines:
            break

    if current and len(lines) < max_lines:
        lines.append(current)

    if len(lines) > max_lines:
        lines = lines[:max_lines]
    if lines and len(words) > 1 and len(lines) == max_lines:
        last = lines[-1]
        if len(last) > 2:
            lines[-1] = f"{last[:-1]}..."
    return '\n'.join(lines)


def _render_timetable_image_response(semester, ordered_table, timeslot_list, division=None):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return HttpResponse('Image export requires Pillow (PIL).', status=500)

    slot_count = len(timeslot_list)
    if slot_count <= 5:
        slot_width = 210
    elif slot_count <= 7:
        slot_width = 180
    elif slot_count <= 9:
        slot_width = 155
    else:
        slot_width = 140

    day_col_width = 90
    margin = 30
    title_height = 68
    header_height = 58
    row_height = 82

    total_width = day_col_width + (slot_count * slot_width)
    total_height = header_height + (len(ordered_table) * row_height)
    image_width = margin * 2 + total_width
    image_height = margin * 2 + title_height + total_height

    img = Image.new('RGB', (image_width, image_height), '#ffffff')
    draw = ImageDraw.Draw(img)

    title_font = _load_image_font(28, bold=True)
    meta_font = _load_image_font(18, bold=False)
    header_font = _load_image_font(16, bold=True)
    cell_font = _load_image_font(14, bold=False)

    division_name = division.name if division else 'N/A'
    draw.text(
        (margin, margin),
        f"Course: {semester.course.name}",
        fill='#111111',
        font=title_font,
    )
    draw.text(
        (margin, margin + 34),
        f"Semester: {semester.number}   Division: {division_name}",
        fill='#111111',
        font=meta_font,
    )

    x0 = margin
    y0 = margin + title_height

    def draw_cell(x, y, w, h, text='', fill='#ffffff', font=None, center=False):
        draw.rectangle([x, y, x + w, y + h], fill=fill, outline='#333333', width=1)
        if not text:
            return
        pad = 6
        font = font or cell_font
        if center:
            wrapped = _wrap_text(draw, text, font, max_width=max(10, w - (pad * 2)), max_lines=3)
            bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=2)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            tx = x + max(pad, (w - tw) // 2)
            ty = y + max(pad, (h - th) // 2)
            draw.multiline_text((tx, ty), wrapped, fill='#111111', font=font, spacing=2, align='center')
            return

        wrapped = _wrap_text(draw, text, font, max_width=max(10, w - (pad * 2)), max_lines=4)
        draw.multiline_text((x + pad, y + pad), wrapped, fill='#111111', font=font, spacing=2)

    draw_cell(x0, y0, day_col_width, header_height, text='Day', fill='#efefef', font=header_font, center=True)
    current_x = x0 + day_col_width
    for slot in timeslot_list:
        label = f"{slot.start_time.strftime('%I:%M %p')} - {slot.end_time.strftime('%I:%M %p')}"
        if getattr(slot, 'is_break', False):
            label = f"{label}\nBreak"
        fill = '#e4e4e4' if getattr(slot, 'is_break', False) else '#efefef'
        draw_cell(current_x, y0, slot_width, header_height, text=label, fill=fill, font=header_font, center=True)
        current_x += slot_width

    day_y = y0 + header_height
    for day, cells in ordered_table:
        draw_cell(x0, day_y, day_col_width, row_height, text=day, fill='#f7f7f7', font=header_font, center=True)
        cell_x = x0 + day_col_width
        for cell in cells:
            colspan = max(1, cell.get('colspan', 1))
            cell_w = slot_width * colspan
            if cell.get('break'):
                draw_cell(cell_x, day_y, cell_w, row_height, text='Break', fill='#f2f2f2', font=header_font, center=True)
            elif cell.get('empty'):
                draw_cell(cell_x, day_y, cell_w, row_height, fill='#ffffff')
            else:
                batch_rows = cell.get('batch_rows') or []
                entries = cell.get('entries') or [cell['entry']]
                first = entries[0]
                if batch_rows:
                    lines = [first.subject.name]
                    for batch in batch_rows:
                        lines.append(
                            f"B{batch.batch_number} {batch.from_roll_no}-{batch.to_roll_no}: "
                            f"{batch.teacher.name} ({batch.laboratory.room_number})"
                        )
                    text = '\n'.join(lines)
                elif len(entries) == 1:
                    text = f"{first.subject.name}\n{first.teacher.name}\n{first.classroom.room_number}"
                else:
                    lines = [first.subject.name]
                    for row in entries:
                        lines.append(f"{row.teacher.name} ({row.classroom.room_number})")
                    text = '\n'.join(lines)
                fill = '#fff8d8' if first.subject.is_lab else '#ffffff'
                draw_cell(cell_x, day_y, cell_w, row_height, text=text, fill=fill, font=cell_font)
            cell_x += cell_w
        day_y += row_height

    output = io.BytesIO()
    img.save(output, format='PNG', optimize=True)
    suffix = f"_division_{division.id}" if division else ""
    filename = f"timetable_sem_{semester.id}{suffix}.png"
    response = HttpResponse(output.getvalue(), content_type='image/png')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def division_timetable_view(request, division_id):
    division = get_object_or_404(Division.objects.select_related('semester__course'), pk=division_id)
    semester = division.semester
    entries = list(
        Timetable.objects.filter(division=division)
        .select_related('subject', 'teacher', 'classroom', 'time_slot')
    )
    if not entries:
        messages.warning(
            request,
            'No saved timetable found for this division. Generate and confirm/save from Smart Timetable first.',
        )
        return redirect('scheduler:division_detail', pk=division.id)

    ordered_table, timeslot_list = _table_from_entries(semester, entries, division=division)
    return render(request, 'timetable.html', {
        'semester': semester,
        'division': division,
        'table': ordered_table,
        'timeslots': timeslot_list,
        'start_time': semester.start_time,
        'end_time': semester.end_time,
        'breaks': semester.breaks,
        'working_days': semester.get_working_days_display(),
    })


def export_division_timetable_image(request, division_id):
    division = get_object_or_404(Division.objects.select_related('semester__course'), pk=division_id)
    semester = division.semester
    entries = list(
        Timetable.objects.filter(division=division)
        .select_related('subject', 'teacher', 'classroom', 'time_slot')
    )
    if not entries:
        messages.warning(
            request,
            'No saved timetable found for this division. Generate and confirm/save from Smart Timetable first.',
        )
        return redirect('scheduler:division_detail', pk=division.id)

    ordered_table, timeslot_list = _table_from_entries(
        semester,
        entries,
        merge_lab_cells=False,
        division=division,
    )
    return _render_timetable_image_response(semester, ordered_table, timeslot_list, division=division)


def export_division_timetable_pdf(request, division_id):
    """Backward-compatible alias that now exports timetable as image."""
    return export_division_timetable_image(request, division_id)


def smart_timetable(request):
    """Generate preview, save confirmed version, and export image from smart page."""
    preview_signing_salt = 'scheduler.smart_timetable.preview'
    action = request.POST.get('action', 'generate') if request.method == 'POST' else None

    # department/course/semester may come via GET when fields change or via POST
    dept_id = request.POST.get('department') or request.GET.get('dept')
    course_id = request.POST.get('course') or request.GET.get('course')
    sem_id = request.POST.get('semester') or request.GET.get('sem')
    div_id = request.POST.get('division') or request.GET.get('division')

    timetable_context = {}

    if request.method == 'POST':
        form = SmartTimetableForm(request.POST, dept_id=dept_id, course_id=course_id)
        if action == 'generate':
            if form.is_valid():
                semester = form.cleaned_data['semester']
                division = form.cleaned_data.get('division')
                if not division:
                    form.add_error('division', 'Select a division to generate a division-specific timetable.')
                else:
                    teacher_overrides = {
                        assign.subject_id: assign.teacher
                        for assign in division.assignments.select_related('subject', 'teacher')
                        if assign.teacher_id
                    }
                    try:
                        blocked_room_slots = _occupied_room_slots_for_scope(exclude_division=division)
                        preview_entries = generate_timetable_for_semester(
                            semester,
                            teacher_overrides=teacher_overrides,
                            division=division,
                            blocked_room_slots=blocked_room_slots,
                        )
                        preview_payload = {
                            'semester_id': semester.id,
                            'division_id': division.id,
                            'entries': _serialize_schedule_entries(preview_entries),
                        }
                        preview_token = signing.dumps(
                            preview_payload,
                            salt=preview_signing_salt,
                            compress=True,
                        )
                        ordered_table, timeslot_list = _table_from_entries(
                            semester,
                            preview_entries,
                            division=division,
                        )
                        timetable_context.update({
                            'semester': semester,
                            'division': division,
                            'table': ordered_table,
                            'timeslots': timeslot_list,
                            'working_days': semester.get_working_days_display(),
                            'preview_available': True,
                            'preview_token': preview_token,
                        })
                        messages.success(
                            request,
                            'Timetable preview generated. Confirm and save to make it visible on the division page.',
                        )
                    except Exception as e:
                        failure_text = str(e)
                        timetable_context['failure_details'] = _build_generation_failure_points(failure_text)
                        messages.error(request, failure_text)
        elif action == 'cancel_preview':
            smart_url = reverse('scheduler:smart_timetable')
            params = {}
            if dept_id:
                params['dept'] = dept_id
            if course_id:
                params['course'] = course_id
            if sem_id:
                params['sem'] = sem_id
            if div_id:
                params['division'] = div_id
            redirect_url = f"{smart_url}?{urlencode(params)}" if params else smart_url
            return redirect(redirect_url)
        elif action in ('save_preview', 'export_preview_image', 'export_preview_pdf'):
            preview_token = (request.POST.get('preview_token') or '').strip()
            if not preview_token:
                messages.error(request, 'No generated preview found. Generate timetable first.')
            else:
                try:
                    preview = signing.loads(
                        preview_token,
                        salt=preview_signing_salt,
                        max_age=1800,  # 30 minutes
                    )
                except signing.BadSignature:
                    messages.error(request, 'Preview data is invalid or expired. Generate timetable again.')
                else:
                    semester = Semester.objects.filter(pk=preview.get('semester_id')).select_related('course').first()
                    division = Division.objects.filter(pk=preview.get('division_id')).select_related('semester__course').first()
                    serialized_entries = preview.get('entries') or []
                    if not semester or not division or division.semester_id != semester.id:
                        messages.error(request, 'Preview data is stale. Generate timetable again.')
                    else:
                        preview_entries = _deserialize_schedule_entries(serialized_entries, semester, division=division)
                        if action == 'save_preview':
                            conflicts = _find_room_slot_conflicts(preview_entries, exclude_division=division)
                            if conflicts:
                                messages.error(request, _room_conflict_message(conflicts))
                                ordered_table, timeslot_list = _table_from_entries(
                                    semester,
                                    preview_entries,
                                    division=division,
                                )
                                timetable_context.update({
                                    'semester': semester,
                                    'division': division,
                                    'table': ordered_table,
                                    'timeslots': timeslot_list,
                                    'working_days': semester.get_working_days_display(),
                                    'preview_available': True,
                                    'preview_token': preview_token,
                                })
                            else:
                                Timetable.objects.filter(semester=semester, division=division).delete()
                                Timetable.objects.bulk_create(preview_entries)
                                messages.success(request, f'Saved timetable for {semester} - {division.name}.')
                                smart_url = reverse('scheduler:smart_timetable')
                                redirect_url = (
                                    f"{smart_url}?dept={semester.course.department_id}"
                                    f"&course={semester.course_id}"
                                    f"&sem={semester.id}"
                                    f"&division={division.id}"
                                )
                                return redirect(redirect_url)
                        else:
                            ordered_table, timeslot_list = _table_from_entries(
                                semester,
                                preview_entries,
                                merge_lab_cells=False,
                                division=division,
                            )
                            timetable_context.update({
                                'semester': semester,
                                'division': division,
                                'table': ordered_table,
                                'timeslots': timeslot_list,
                                'working_days': semester.get_working_days_display(),
                                'preview_available': True,
                                'preview_token': preview_token,
                            })
                            return _render_timetable_image_response(
                                semester,
                                ordered_table,
                                timeslot_list,
                                division=division,
                            )
    else:
        initial = {}
        if dept_id:
            initial['department'] = dept_id
        if course_id:
            initial['course'] = course_id
        if sem_id:
            initial['semester'] = sem_id
        if div_id:
            initial['division'] = div_id
        form = SmartTimetableForm(dept_id=dept_id, course_id=course_id, initial=initial)

    context = {'form': form}
    context.update(timetable_context)
    return render(request, 'smart_timetable.html', context)


def export_timetable_image(request, semester_id):
    """Generate an image version of the timetable and return as download."""
    semester = get_object_or_404(Semester, pk=semester_id)
    entries = list(Timetable.objects.filter(semester=semester, division__isnull=True).select_related(
        'subject', 'teacher', 'classroom', 'time_slot'
    ))
    ordered_table, timeslot_list = _table_from_entries(semester, entries, merge_lab_cells=False)
    return _render_timetable_image_response(semester, ordered_table, timeslot_list)


def export_timetable_pdf(request, semester_id):
    """Backward-compatible alias that now exports timetable as image."""
    return export_timetable_image(request, semester_id)
