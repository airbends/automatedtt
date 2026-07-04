from django.urls import path
from . import views

app_name = 'scheduler'

urlpatterns = [
    path('', views.home, name='home'),
    path('departments/', views.department_list, name='department_list'),
    path('departments/add/', views.DepartmentCreateView.as_view(), name='department_add'),
    path('departments/<int:id>/', views.department_detail, name='department_detail'),
    path('departments/<int:pk>/edit/', views.DepartmentUpdateView.as_view(), name='department_edit'),
    path('departments/<int:pk>/delete/', views.DepartmentDeleteView.as_view(), name='department_delete'),
    # course CRUD
    path('courses/', views.CourseListView.as_view(), name='course_list'),
    path('courses/add/', views.CourseCreateView.as_view(), name='course_add'),
    path('courses/<int:pk>/', views.CourseDetailView.as_view(), name='course_detail'),
    # semester detail view for managing subjects/divisions
    path('semesters/<int:pk>/', views.SemesterDetailView.as_view(), name='semester_detail'),
    path('courses/<int:pk>/edit/', views.CourseUpdateView.as_view(), name='course_edit'),
    path('courses/<int:pk>/delete/', views.CourseDeleteView.as_view(), name='course_delete'),
    # teacher CRUD
    path('teachers/', views.TeacherListView.as_view(), name='teacher_list'),
    path('teachers/add/', views.TeacherCreateView.as_view(), name='teacher_add'),
    path('teachers/<int:pk>/', views.TeacherDetailView.as_view(), name='teacher_detail'),
    path('teachers/<int:pk>/edit/', views.TeacherUpdateView.as_view(), name='teacher_edit'),
    path('teachers/<int:pk>/delete/', views.TeacherDeleteView.as_view(), name='teacher_delete'),
    # classroom CRUD
    path('classrooms/', views.ClassroomListView.as_view(), name='classroom_list'),
    path('classrooms/add/', views.ClassroomCreateView.as_view(), name='classroom_add'),
    path('classrooms/<int:pk>/', views.ClassroomDetailView.as_view(), name='classroom_detail'),
    path('classrooms/<int:pk>/edit/', views.ClassroomUpdateView.as_view(), name='classroom_edit'),
    path('classrooms/<int:pk>/delete/', views.ClassroomDeleteView.as_view(), name='classroom_delete'),
    # laboratory CRUD
    path('laboratories/', views.LaboratoryListView.as_view(), name='laboratory_list'),
    path('laboratories/add/', views.LaboratoryCreateView.as_view(), name='laboratory_add'),
    path('laboratories/<int:pk>/', views.LaboratoryDetailView.as_view(), name='laboratory_detail'),
    path('laboratories/<int:pk>/edit/', views.LaboratoryUpdateView.as_view(), name='laboratory_edit'),
    path('laboratories/<int:pk>/delete/', views.LaboratoryDeleteView.as_view(), name='laboratory_delete'),
    # subject CRUD
    path('subjects/add/', views.SubjectCreateView.as_view(), name='subject_add'),
    # smart timetable generator
    path('smart/', views.smart_timetable, name='smart_timetable'),
    path('subjects/<int:pk>/edit/', views.SubjectUpdateView.as_view(), name='subject_edit'),
    path('subjects/<int:pk>/delete/', views.SubjectDeleteView.as_view(), name='subject_delete'),
    # division CRUD
    path('divisions/add/', views.DivisionCreateView.as_view(), name='division_add'),
    path('divisions/<int:pk>/', views.DivisionDetailView.as_view(), name='division_detail'),
    path('divisions/<int:division_id>/timetable/', views.division_timetable_view, name='division_timetable_view'),
    path('divisions/<int:division_id>/timetable/export-image/', views.export_division_timetable_image, name='division_timetable_export_image'),
    path('divisions/<int:division_id>/timetable/export-pdf/', views.export_division_timetable_pdf, name='division_timetable_export_pdf'),
    path('divisions/<int:pk>/edit/', views.DivisionUpdateView.as_view(), name='division_edit'),
    path('divisions/<int:pk>/delete/', views.DivisionDeleteView.as_view(), name='division_delete'),
    # other existing
    path('semesters/', views.semester_list, name='semester_list'),
    # semester management
    path('semesters/add/', views.SemesterCreateView.as_view(), name='semester_add'),
    path('semesters/<int:pk>/edit/', views.SemesterUpdateView.as_view(), name='semester_edit'),
    path('semesters/<int:pk>/delete/', views.SemesterDeleteView.as_view(), name='semester_delete'),
    path('timetable/<int:semester_id>/', views.timetable_view, name='timetable_view'),
    path('timetable/<int:semester_id>/export-image/', views.export_timetable_image, name='export_timetable_image'),
    path('timetable/<int:semester_id>/export-pdf/', views.export_timetable_pdf, name='export_timetable_pdf'),
]
