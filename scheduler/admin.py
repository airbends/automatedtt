from django.contrib import admin
from .models import (
    Department, Course, Semester, Teacher,
    Subject, Division, DivisionSubject, Classroom, TimeSlot, Timetable
)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('name', 'department', 'duration_years')
    list_filter = ('department',)
    search_fields = ('name',)


@admin.register(Semester)
class SemesterAdmin(admin.ModelAdmin):
    list_display = ('course', 'number')
    list_filter = ('course',)
    search_fields = ('course__name',)


@admin.register(Teacher)
class TeacherAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'department')
    list_filter = ('department',)
    search_fields = ('name', 'email')


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'semester', 'teacher', 'is_lab')
    list_filter = ('semester', 'is_lab')
    search_fields = ('name',)


@admin.register(Division)
class DivisionAdmin(admin.ModelAdmin):
    list_display = ('name', 'course', 'strength')
    list_filter = ('course',)
    search_fields = ('name',)


@admin.register(DivisionSubject)
class DivisionSubjectAdmin(admin.ModelAdmin):
    list_display = ('division', 'subject', 'teacher')
    list_filter = ('division', 'teacher')
    search_fields = ('subject__name',)


@admin.register(Classroom)
class ClassroomAdmin(admin.ModelAdmin):
    list_display = ('room_number', 'capacity', 'is_lab', 'division')
    list_filter = ('is_lab',)
    search_fields = ('room_number', 'division__name', 'division__semester__course__name')


@admin.register(TimeSlot)
class TimeSlotAdmin(admin.ModelAdmin):
    list_display = ('start_time', 'end_time')
    search_fields = ('start_time', 'end_time')


@admin.register(Timetable)
class TimetableAdmin(admin.ModelAdmin):
    list_display = ('semester', 'division', 'day', 'time_slot', 'subject', 'teacher', 'classroom')
    list_filter = ('semester', 'division', 'day', 'time_slot')
    search_fields = ('subject__name', 'teacher__name')
    actions = ['generate_timetable']

    def generate_timetable(self, request, queryset):
        """Admin action: generate timetable for selected semesters."""
        from django.contrib import messages
        from .timetable_generator import run_generator_for_semester, TimetableGenerationError

        success = 0
        for semester in queryset:
            try:
                run_generator_for_semester(semester)
                success += 1
            except TimetableGenerationError as e:
                self.message_user(request, f"Failed for {semester}: {e}", level=messages.ERROR)
        if success:
            self.message_user(request, f"Timetable generated for {success} semester(s)", level=messages.SUCCESS)
    generate_timetable.short_description = "Generate Timetable"
