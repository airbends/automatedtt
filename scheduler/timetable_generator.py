import random
from datetime import timedelta, datetime, time
from collections import defaultdict
from django.db import transaction


# utility to parse strings to time (support 24â€‘hour and am/pm)
#
# The semester.breaks field is freeâ€‘text; earlier we assumed strict
# "HH:MM" format which rejected entries like "3:30 PM".  Improve
# robustness by attempting several common patterns.

def parse_time(ts_str):
    import re

    s = (ts_str or '').strip()
    if not s:
        raise ValueError("Empty time value")

    # Accept user-friendly suffixes like "10:10mins", "10:10 min", etc.
    s = re.sub(r'(?i)\s*(?:min|mins|minute|minutes)\b', '', s)
    # Handle compact am/pm values like "10:10pm".
    s = re.sub(r'(?i)(\d)(am|pm)$', r'\1 \2', s)
    # Remove trailing punctuation, normalize spaces.
    s = re.sub(r'[^0-9a-zA-Z: ]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()

    for fmt in ('%H:%M', '%I:%M %p', '%I:%M%p'):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue

    # Last resort: basic regex parser.
    m = re.match(r'^(\d{1,2}):(\d{2})(?:\s*([AaPp][Mm]))?$', s)
    if m:
        h = int(m.group(1))
        mnt = int(m.group(2))
        ampm = m.group(3)
        if ampm:
            ampm = ampm.lower()
            if ampm == 'pm' and h != 12:
                h += 12
            elif ampm == 'am' and h == 12:
                h = 0
        return time(h, mnt)
    raise ValueError(f"Invalid time format: {ts_str}")


def _time_to_minutes(t):
    return t.hour * 60 + t.minute


def _has_meridiem_marker(text):
    import re

    return bool(re.search(r'(?i)(?:\b[ap]m\b|[ap]m\s*$)', (text or '').strip()))


def _shift_12_hours(t):
    if t.hour >= 12:
        return t
    return time(t.hour + 12, t.minute, t.second, t.microsecond)


def normalize_break_range_for_semester(start_text, end_text, semester_start=None, semester_end=None):
    """Normalize ambiguous break times against a semester's time window.

    Example: if semester is 12:30-17:40 and user enters 03:30-03:40, this
    maps to 15:30-15:40.
    """
    start = parse_time(start_text)
    end = parse_time(end_text)

    start_options = [(start, 0)]
    end_options = [(end, 0)]
    if not _has_meridiem_marker(start_text) and start.hour < 12:
        start_options.append((_shift_12_hours(start), 1))
    if not _has_meridiem_marker(end_text) and end.hour < 12:
        end_options.append((_shift_12_hours(end), 1))

    candidates = []
    for s, s_cost in start_options:
        for e, e_cost in end_options:
            if s < e:
                candidates.append((s, e, s_cost + e_cost))

    if not candidates:
        return start, end

    if semester_start and semester_end:
        ws = _time_to_minutes(semester_start)
        we = _time_to_minutes(semester_end)
        in_window = [
            c for c in candidates
            if ws <= _time_to_minutes(c[0]) < _time_to_minutes(c[1]) <= we
        ]
        if in_window:
            in_window.sort(key=lambda c: (c[2], _time_to_minutes(c[0]) - ws))
            return in_window[0][0], in_window[0][1]

    candidates.sort(key=lambda c: c[2])
    return candidates[0][0], candidates[0][1]

from .models import Semester, Subject, Classroom, TimeSlot, Timetable, LabBatchAssignment


class TimetableGenerationError(Exception):
    pass


def generate_timetable_for_semester(
    semester,
    working_days=None,
    max_attempts=1000,
    teacher_overrides=None,
    division=None,
    blocked_room_slots=None,
):
    """Generate timetable entries for a given semester.

    - working_days: list of day codes (e.g. ['MON','TUE',...]). If omitted
      the semester's ``working_days`` field is used.
    - max_attempts: maximum iterations while resolving conflicts

    Returns list of Timetable objects (not saved). Raises TimetableGenerationError
    if generation fails after repeated attempts.
    """
    if working_days is None:
        if semester.working_days == 'MON-SAT':
            working_days = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT']
        else:
            working_days = ['MON', 'TUE', 'WED', 'THU', 'FRI']

    raw_subjects = list(semester.subjects.all())
    override_map = teacher_overrides or {}
    blocked_room_slots = set(blocked_room_slots or ())

    if division is not None:
        # Division generation prefers rooms explicitly assigned to that
        # division, but can still use shared (unassigned) rooms.
        division_rooms = list(Classroom.objects.filter(division=division))
        shared_rooms = list(Classroom.objects.filter(division__isnull=True))
    else:
        division_rooms = []
        shared_rooms = list(Classroom.objects.filter(division__isnull=True))
        if not shared_rooms:
            shared_rooms = list(Classroom.objects.all())

    timeslots = get_timeslots_for_semester(semester)

    if not raw_subjects:
        raise TimetableGenerationError("No subjects available for semester")
    if not (division_rooms or shared_rooms):
        raise TimetableGenerationError("No classrooms available")
    if not timeslots:
        raise TimetableGenerationError("No timeslots defined")

    # Optional strict lab-batch configuration (division scope).
    lab_batches_by_subject = {}
    lab_groups_by_subject = {}
    if division is not None:
        batch_qs = (
            LabBatchAssignment.objects
            .filter(division=division, subject__in=raw_subjects)
            .select_related('teacher', 'laboratory', 'subject')
            .order_by('subject_id', 'batch_number')
        )
        for row in batch_qs:
            lab_batches_by_subject.setdefault(row.subject_id, []).append(row)

    for subj in raw_subjects:
        batch_rows = lab_batches_by_subject.get(subj.id, []) if subj.is_lab else []
        if division is not None and subj.is_lab and not batch_rows:
            raise TimetableGenerationError(
                f"Subject '{subj.name}' has no lab batches configured for division '{division.name}'. "
                "Edit the subject and add lab batches."
            )
        if subj.is_lab and batch_rows:
            used_teacher_ids = set()
            lab_groups = {}
            lab_student_totals = {}
            for batch in batch_rows:
                if not batch.teacher_id:
                    raise TimetableGenerationError(
                        f"Subject '{subj.name}' batch {batch.batch_number} has no teacher assigned."
                    )
                if batch.teacher_id in used_teacher_ids:
                    raise TimetableGenerationError(
                        f"Subject '{subj.name}' has duplicate teacher across lab batches; "
                        "all batches must run in parallel with distinct teachers."
                    )
                used_teacher_ids.add(batch.teacher_id)

                if not batch.laboratory_id:
                    raise TimetableGenerationError(
                        f"Subject '{subj.name}' batch {batch.batch_number} has no laboratory assigned."
                    )
                lab = batch.laboratory
                if not lab.is_lab:
                    raise TimetableGenerationError(
                        f"Subject '{subj.name}' batch {batch.batch_number} is assigned to non-lab room {lab.room_number}."
                    )
                if division is not None and lab.division_id and lab.division_id != division.id:
                    raise TimetableGenerationError(
                        f"Subject '{subj.name}' batch {batch.batch_number} uses lab {lab.room_number} "
                        "assigned to a different division."
                    )

                batch_size = batch.to_roll_no - batch.from_roll_no + 1
                if batch_size > lab.capacity:
                    raise TimetableGenerationError(
                        f"Subject '{subj.name}' batch {batch.batch_number} size ({batch_size}) "
                        f"exceeds lab {lab.room_number} capacity ({lab.capacity})."
                    )
                lab_groups.setdefault(lab.id, []).append(batch)
                lab_student_totals[lab.id] = lab_student_totals.get(lab.id, 0) + batch_size

            for lab_id, total_students in lab_student_totals.items():
                group = lab_groups.get(lab_id, [])
                lab = group[0].laboratory if group else None
                if not lab:
                    continue
                required_capacity = total_students
                if required_capacity > lab.capacity:
                    raise TimetableGenerationError(
                        f"Subject '{subj.name}' requires {required_capacity} seats in lab "
                        f"{lab.room_number}, but capacity is {lab.capacity}."
                    )

            # keep deterministic ordering by lab id for stable rendering/tests
            lab_groups_by_subject[subj.id] = [lab_groups[key] for key in sorted(lab_groups.keys())]
            continue

        if override_map.get(subj.id) is None and subj.teacher is None:
            raise TimetableGenerationError(
                f"Subject '{subj.name}' has no teacher assigned; "
                "assign one on the subject or use a division override."
            )

    def _rooms_for_subject(subj):
        # For strict parallel lab-batches, rooms are fixed by assignments.
        batch_rows = lab_batches_by_subject.get(subj.id, []) if subj.is_lab else []
        if batch_rows:
            unique = {}
            for batch in batch_rows:
                unique.setdefault(batch.laboratory_id, batch.laboratory)
            return list(unique.values())

        if division is None:
            if subj.is_lab:
                return [r for r in shared_rooms if r.is_lab]
            regular = [r for r in shared_rooms if not r.is_lab]
            labs = [r for r in shared_rooms if r.is_lab]
            return regular + labs

        if subj.is_lab:
            division_labs = [r for r in division_rooms if r.is_lab]
            shared_labs = [r for r in shared_rooms if r.is_lab]
            ordered_labs = division_labs + [r for r in shared_labs if r.id not in {d.id for d in division_labs}]
            return ordered_labs

        division_regular = [r for r in division_rooms if not r.is_lab]
        shared_regular = [r for r in shared_rooms if not r.is_lab]
        division_labs = [r for r in division_rooms if r.is_lab]
        shared_labs = [r for r in shared_rooms if r.is_lab]
        ordered_rooms = division_regular + shared_regular + division_labs + shared_labs
        unique_rooms = {}
        for room in ordered_rooms:
            unique_rooms.setdefault(room.id, room)
        return list(unique_rooms.values())

    room_options_by_subject = {}
    for subj in raw_subjects:
        options = _rooms_for_subject(subj)
        required_capacity = 0
        if division is not None and (division.strength or 0) > 0:
            required_capacity = division.strength
        batch_rows = lab_batches_by_subject.get(subj.id, []) if subj.is_lab else []
        if required_capacity > 0 and not batch_rows:
            options = [room for room in options if room.capacity >= required_capacity]
        if options:
            room_options_by_subject[subj.id] = options
            continue

        if subj.is_lab:
            if required_capacity > 0 and not batch_rows:
                raise TimetableGenerationError(
                    f"No laboratory with capacity >= division strength ({required_capacity}) "
                    f"is available for subject '{subj.name}'."
                )
            if division is not None:
                raise TimetableGenerationError(
                    f"No laboratory available for division '{division.name}'. "
                    "Assign a lab to this division or keep one unassigned."
                )
            raise TimetableGenerationError("No laboratory available for lab subjects.")

        if division is not None:
            if required_capacity > 0:
                raise TimetableGenerationError(
                    f"No classroom/laboratory with capacity >= division strength ({required_capacity}) "
                    f"is available for subject '{subj.name}'."
                )
            raise TimetableGenerationError(
                f"No classroom available for division '{division.name}'. "
                "Assign a classroom to this division or keep one unassigned."
            )
        raise TimetableGenerationError("No classrooms available.")

    working_day_count = len(working_days)
    max_per_day = max(getattr(semester, 'max_lectures_per_day', 6) or 6, 1)
    daily_capacity = min(max_per_day, len(timeslots))
    weekly_capacity = daily_capacity * working_day_count

    if daily_capacity < 2 and any(subj.is_lab for subj in raw_subjects):
        raise TimetableGenerationError(
            f"max_lectures_per_day ({max_per_day}) is too low for lab subjects. "
            "Lab sessions require two contiguous slots in one day."
        )

    # Build requested units from weekly demand, then auto-normalize to fit
    # semester capacity while preserving at least one appearance per subject.
    unit_counts = {}
    requested_weekly_slots = 0
    minimum_weekly_slots = 0
    for subj in raw_subjects:
        wh = getattr(subj, 'weekly_hours', None) or 0
        if subj.is_lab:
            requested_count = max(wh // 2, 1) if wh > 0 else 1
            slot_cost = 2
        else:
            requested_count = wh if wh > 0 else 1
            slot_cost = 1

        # generator allows at most one slot/session per day for a subject
        count = max(1, min(requested_count, working_day_count))
        unit_counts[subj.id] = count
        requested_weekly_slots += count * slot_cost
        minimum_weekly_slots += slot_cost

    if minimum_weekly_slots > weekly_capacity:
        raise TimetableGenerationError(
            f"Minimum required weekly slots ({minimum_weekly_slots}) exceed "
            f"semester capacity ({weekly_capacity}). Increase daily hours/max "
            "lectures, reduce subjects, or reduce labs."
        )

    normalization_guard = requested_weekly_slots + 1
    while requested_weekly_slots > weekly_capacity:
        normalization_guard -= 1
        if normalization_guard <= 0:
            raise TimetableGenerationError(
                "Unable to normalize weekly lecture demand within semester capacity."
            )
        candidates = [s for s in raw_subjects if unit_counts[s.id] > 1]
        if not candidates:
            break
        gap = requested_weekly_slots - weekly_capacity
        odd_fit = [s for s in candidates if not s.is_lab]
        if gap % 2 == 1 and odd_fit:
            target = max(odd_fit, key=lambda s: unit_counts[s.id])
        else:
            target = max(
                candidates,
                key=lambda s: (
                    unit_counts[s.id] * (2 if s.is_lab else 1),
                    unit_counts[s.id],
                    0 if s.is_lab else 1,
                ),
            )
        unit_counts[target.id] -= 1
        requested_weekly_slots -= 2 if target.is_lab else 1

    subjects = []
    for subj in raw_subjects:
        subjects.extend([subj] * unit_counts[subj.id])

    timeslot_index = {slot: idx for idx, slot in enumerate(timeslots)}
    schedule = []
    additional_teacher_occupancy = set()
    teacher_schedule_by_slot = defaultdict(set)
    room_usage_count = defaultdict(int)

    def teacher_busy(teacher, day, slot):
        if teacher is None:
            return False
        teacher_id = getattr(teacher, 'id', None)
        if teacher_id is None:
            return any(t.teacher == teacher and t.day == day and t.time_slot == slot for t in schedule)
        return (
            teacher_id in teacher_schedule_by_slot[(day, slot.id)]
            or (teacher_id, day, slot.id) in additional_teacher_occupancy
        )

    def classroom_busy(classroom, day, slot):
        return (
            any(t.classroom == classroom and t.day == day and t.time_slot == slot for t in schedule)
            or (classroom.id, day, slot.id) in blocked_room_slots
        )

    def _required_capacity(subject):
        if division is not None and (division.strength or 0) > 0:
            return division.strength
        return 0

    def _pick_best_room(room_choices, subject, day, slot_bundle):
        free_rooms = []
        needed_capacity = _required_capacity(subject)
        for room in room_choices:
            if subject.is_lab and not room.is_lab:
                continue
            if needed_capacity > 0 and room.capacity < needed_capacity:
                continue
            if any(classroom_busy(room, day, s) for s in slot_bundle):
                continue
            free_rooms.append(room)

        if not free_rooms:
            return None

        def room_rank(room):
            # Non-lab subjects prefer regular rooms; labs are acceptable fallback.
            non_lab_in_lab_penalty = 1 if (not subject.is_lab and room.is_lab) else 0
            excess = max(room.capacity - needed_capacity, 0) if needed_capacity > 0 else room.capacity
            return (
                non_lab_in_lab_penalty,
                excess,
                room_usage_count[room.id],
                room.room_number,
            )

        return min(free_rooms, key=room_rank)

    def slot_busy_for_other_subject(day, slot, subject):
        return any(
            t.day == day and t.time_slot == slot and t.subject_id != subject.id
            for t in schedule
        )

    def _slot_duration_minutes(slot):
        start_dt = datetime.combine(datetime.min, slot.start_time)
        end_dt = datetime.combine(datetime.min, slot.end_time)
        return int((end_dt - start_dt).total_seconds() // 60)

    def _is_valid_lab_pair(first_slot, second_slot):
        if first_slot.end_time != second_slot.start_time:
            return False
        total_minutes = _slot_duration_minutes(first_slot) + _slot_duration_minutes(second_slot)
        return total_minutes == 120

    adjacent = {}
    for i, slot in enumerate(timeslots[:-1]):
        next_slot = timeslots[i + 1]
        if _is_valid_lab_pair(slot, next_slot):
            adjacent[slot] = next_slot

    if any(subj.is_lab for subj in raw_subjects) and not adjacent:
        raise TimetableGenerationError(
            "No contiguous time slots available for lab subjects. "
            "Adjust semester hours/breaks."
        )

    attempts = 0
    failure_counts = defaultdict(int)
    last_failure = None
    while attempts < max_attempts:
        attempts += 1
        schedule.clear()
        additional_teacher_occupancy.clear()
        teacher_schedule_by_slot.clear()
        room_usage_count.clear()
        day_slot_usage = {day: set() for day in working_days}
        subject_session_days = {day: set() for day in working_days}
        random.shuffle(subjects)

        try:
            i = 0
            while i < len(subjects):
                subject = subjects[i]
                assigned = False
                batch_rows = lab_batches_by_subject.get(subject.id, []) if subject.is_lab else []
                lab_groups = lab_groups_by_subject.get(subject.id, []) if subject.is_lab else []

                days = working_days.copy()
                random.shuffle(days)
                for day in days:
                    if subject.id in subject_session_days[day]:
                        continue
                    required_daily_slots = 2 if subject.is_lab else 1
                    remaining_daily_slots = max_per_day - len(day_slot_usage[day])
                    if remaining_daily_slots < required_daily_slots:
                        # Skip days already at (or effectively at) lecture limit.
                        continue

                    potential_slots = timeslots.copy()
                    random.shuffle(potential_slots)
                    for slot in potential_slots:
                        idx_slot = timeslot_index.get(slot)
                        if idx_slot is None:
                            continue

                        next_slot = None
                        slot_bundle = [slot]
                        if subject.is_lab:
                            next_slot = adjacent.get(slot)
                            if not next_slot:
                                continue
                            slot_bundle.append(next_slot)

                        # Max lectures/day is counted by unique occupied slots
                        # for the division, not by number of parallel lab batches.
                        slot_ids = {s.id for s in slot_bundle}
                        additional = slot_ids - day_slot_usage[day]
                        if len(day_slot_usage[day]) + len(additional) > max_per_day:
                            continue

                        if any(slot_busy_for_other_subject(day, s, subject) for s in slot_bundle):
                            continue

                        if batch_rows:
                            # Strict parallel batch scheduling: all batches must fit
                            # in this exact slot pair.
                            fits = True
                            for batch in batch_rows:
                                for s in slot_bundle:
                                    if teacher_busy(batch.teacher, day, s) or classroom_busy(batch.laboratory, day, s):
                                        fits = False
                                        break
                                if not fits:
                                    break
                            if fits:
                                for group in lab_groups:
                                    lab = group[0].laboratory
                                    for s in slot_bundle:
                                        if classroom_busy(lab, day, s):
                                            fits = False
                                            break
                                    if not fits:
                                        break
                            if not fits:
                                continue

                            for batch in batch_rows:
                                for s in slot_bundle:
                                    additional_teacher_occupancy.add((batch.teacher_id, day, s.id))
                                    teacher_schedule_by_slot[(day, s.id)].add(batch.teacher_id)

                            for group in lab_groups:
                                # If a single lab carries multiple batches (allowed
                                # when capacity permits), create one physical room
                                # entry using the first batch teacher.
                                representative = group[0]
                                for s in slot_bundle:
                                    schedule.append(Timetable(
                                        semester=semester,
                                        subject=subject,
                                        teacher=representative.teacher,
                                        classroom=representative.laboratory,
                                        day=day,
                                        time_slot=s,
                                    ))
                                    room_usage_count[representative.laboratory.id] += 1
                            day_slot_usage[day].update(slot_ids)
                            subject_session_days[day].add(subject.id)
                            assigned = True
                            break

                        room_choices = room_options_by_subject.get(subject.id, [])
                        sched_teacher = override_map.get(subject.id) or subject.teacher
                        if sched_teacher is None:
                            raise TimetableGenerationError(
                                f"Subject '{subject.name}' has no teacher assigned; "
                                "assign one on the subject or use a division override."
                            )

                        if any(teacher_busy(sched_teacher, day, s) for s in slot_bundle):
                            continue

                        room = _pick_best_room(room_choices, subject, day, slot_bundle)
                        if room is None:
                            continue

                        for s in slot_bundle:
                            schedule.append(Timetable(
                                semester=semester,
                                subject=subject,
                                teacher=sched_teacher,
                                classroom=room,
                                day=day,
                                time_slot=s,
                            ))
                            teacher_schedule_by_slot[(day, s.id)].add(sched_teacher.id)
                            room_usage_count[room.id] += 1
                        day_slot_usage[day].update(slot_ids)
                        subject_session_days[day].add(subject.id)
                        assigned = True
                        break
                        if assigned:
                            break
                    if assigned:
                        break
                if not assigned:
                    if batch_rows:
                        batch_numbers = ', '.join(str(batch.batch_number) for batch in batch_rows)
                        raise TimetableGenerationError(
                            f"Unable to schedule lab subject '{subject.name}' in parallel for all batches. "
                            f"Affected batches: {batch_numbers}."
                        )
                    raise TimetableGenerationError(f"Could not assign subject {subject.name}")
                i += 1

            for day in working_days:
                if len(day_slot_usage[day]) > max_per_day:
                    raise TimetableGenerationError(
                        f"Day {day} exceeds max_lectures_per_day ({max_per_day})."
                    )
                teacher_slot_counts = defaultdict(int)
                for t in schedule:
                    if t.day != day or not t.teacher_id:
                        continue
                    key = (t.teacher_id, t.time_slot_id)
                    teacher_slot_counts[key] += 1
                    if teacher_slot_counts[key] > 1:
                        raise TimetableGenerationError(
                            f"Teacher conflict detected on {day} for slot {t.time_slot.start_time}-{t.time_slot.end_time}."
                        )
                by_subj = {}
                for t in schedule:
                    if t.day != day:
                        continue
                    by_subj.setdefault(t.subject.id, []).append(t)
                for subj_id, entries in by_subj.items():
                    subj = entries[0].subject
                    unique_slots = sorted(
                        {e.time_slot for e in entries},
                        key=lambda x: x.start_time,
                    )
                    if len(unique_slots) > 2:
                        raise TimetableGenerationError(f"Too many slots for {subj.name} on {day}")
                    if subj.is_lab:
                        if len(unique_slots) != 2:
                            raise TimetableGenerationError(f"Lab {subj.name} is not a contiguous double-slot on {day}")
                        if unique_slots[0].end_time != unique_slots[1].start_time:
                            raise TimetableGenerationError(f"Non-contiguous slots for {subj.name} on {day}")
                        if _slot_duration_minutes(unique_slots[0]) + _slot_duration_minutes(unique_slots[1]) != 120:
                            raise TimetableGenerationError(f"Lab {subj.name} is not scheduled as a continuous 2-hour block on {day}")
                    elif len(unique_slots) > 1:
                        raise TimetableGenerationError(f"Non-lab {subj.name} doubled on {day}")

                    batch_rows = lab_batches_by_subject.get(subj_id, [])
                    if subj.is_lab and batch_rows:
                        expected_parallel = len(lab_groups_by_subject.get(subj_id, [])) or 1
                        for slot in unique_slots:
                            slot_entries = [e for e in entries if e.time_slot_id == slot.id]
                            if len(slot_entries) != expected_parallel:
                                raise TimetableGenerationError(
                                    f"Lab {subj.name} does not have all batches scheduled in parallel on {day}."
                                )

            post_counts = {}
            for t in schedule:
                post_counts[t.subject.id] = post_counts.get(t.subject.id, 0) + 1
            for subj in raw_subjects:
                count = unit_counts.get(subj.id, 0)
                if subj.is_lab:
                    parallel_room_count = len(lab_groups_by_subject.get(subj.id, [])) or 1
                    expected = count * 2 * parallel_room_count
                else:
                    expected = count
                actual = post_counts.get(subj.id, 0)
                if actual != expected:
                    raise TimetableGenerationError(
                        f"Subject {subj.name} scheduled {actual} slots, expected {expected}"
                    )
            return schedule
        except TimetableGenerationError as e:
            reason = str(e)
            last_failure = reason
            failure_counts[reason] += 1
            continue

    if failure_counts:
        top_reason, top_count = max(failure_counts.items(), key=lambda item: item[1])
        raise TimetableGenerationError(
            f"Unable to generate valid timetable after {attempts} attempts. "
            f"Most frequent blocker: {top_reason} ({top_count} attempts). "
            f"Last blocker: {last_failure}"
        )
    raise TimetableGenerationError(f"Unable to generate valid timetable after {attempts} attempts")

class BreakSlot:
    """Simple placeholder representing a break interval for display.

    Instances mimic TimeSlot by exposing ``start_time`` and ``end_time`` and
    have an ``is_break`` flag so templates can render them differently.
    """
    def __init__(self, start, end):
        self.start_time = start
        self.end_time = end
        self.is_break = True

    def __str__(self):
        return f"Break {self.start_time}-{self.end_time}"


def get_timeslots_for_semester(semester, include_breaks=False):
    """Return a list of slots covering the semester hours.

    If ``include_breaks`` is False (the default) the behaviour is unchanged:
    a list of ``TimeSlot`` objects is returned, with any break periods
    completely removed.  This is what the generator uses for scheduling.

    When ``include_breaks`` is True the return value is a list containing
    either ``TimeSlot`` instances or ``BreakSlot`` placeholders; slots
    overlapping a declared break period are replaced by a ``BreakSlot``.
    The timetable templates can detect ``slot.is_break`` and render a
    labelled column.
    """
    # Base slot length is one hour for each semester timeline. Break handling
    # can split this dynamically (for example, 09:00-10:00 + 10:00-10:20 break
    # + 10:20-11:20). Keeping the base fixed avoids cross-semester leakage
    # from globally stored TimeSlot rows.
    slot_length = timedelta(hours=1)
    slots = []
    if semester.start_time and semester.end_time:
        # parse all breaks into list for easier lookup
        break_ranges = []
        if semester.breaks:
            import re
            for part in semester.breaks.split(','):
                token = part.strip()
                if not token:
                    continue
                # support separators like "-", "–", "—", or "to"
                token = token.replace('–', '-').replace('—', '-')
                token = re.sub(r'(?i)\s*to\s*', '-', token)
                times = token.split('-', 1)
                if len(times) == 2:
                    try:
                        bstart, bend = normalize_break_range_for_semester(
                            times[0],
                            times[1],
                            semester_start=semester.start_time,
                            semester_end=semester.end_time,
                        )
                        if semester.start_time <= bstart < bend <= semester.end_time:
                            break_ranges.append((bstart, bend))
                    except ValueError:
                        pass
        break_ranges.sort()

        def find_next_break_after(time_point):
            for bstart, bend in break_ranges:
                if bstart >= time_point:
                    return (bstart, bend)
            return None

        current = semester.start_time
        while current < semester.end_time:
            # skip any break intervals that start at or before current
            nb = [b for b in break_ranges if b[0] <= current < b[1]]
            if nb:
                # we are inside a break; optionally emit placeholder and jump past
                if include_breaks:
                    bs = nb[0]
                    slots.append(BreakSlot(bs[0], bs[1]))
                current = nb[0][1]
                continue
            # determine normal slot end
            proposed_end = (datetime.combine(datetime.min, current) + slot_length).time()
            # if proposed_end passes closing time cut short
            if proposed_end > semester.end_time:
                proposed_end = semester.end_time
            # check if a break starts before proposed_end
            next_break = find_next_break_after(current)
            if next_break and next_break[0] < proposed_end:
                # split before the break
                before_end = next_break[0]
                ts, _ = TimeSlot.objects.get_or_create(start_time=current, end_time=before_end)
                slots.append(ts)
                # insert break placeholder if requested later
                if include_breaks:
                    slots.append(BreakSlot(next_break[0], next_break[1]))
                # advance past break
                current = next_break[1]
            else:
                # no break interruption
                if proposed_end <= current:
                    break
                ts, _ = TimeSlot.objects.get_or_create(start_time=current, end_time=proposed_end)
                slots.append(ts)
                current = proposed_end
    else:
        # if semester times are not configured, limit fallback to slots already
        # used by this semester rather than all global slots.
        slots = list(
            TimeSlot.objects.filter(timetable__semester=semester)
            .distinct()
            .order_by('start_time')
        )

    # if include_breaks was False but we inserted BreakSlot objects above, strip them out
    if not include_breaks:
        slots = [s for s in slots if not getattr(s, 'is_break', False)]

    return slots


def run_generator_for_semester(
    semester,
    working_days=None,
    teacher_overrides=None,
    division=None,
    blocked_room_slots=None,
):
    """Utility that clears old timetable and saves newly generated entries.

    Parameters mirror :func:`generate_timetable_for_semester`.  ``teacher_overrides``
    is accepted for convenience when calling from views that already compute an
    override map; older callers that do not pass it are unaffected.
    """
    schedule = generate_timetable_for_semester(
        semester,
        working_days=working_days,
        teacher_overrides=teacher_overrides,
        division=division,
        blocked_room_slots=blocked_room_slots,
    )
    for entry in schedule:
        entry.division = division
    # delete old entries for this scope only
    if division is not None:
        Timetable.objects.filter(semester=semester, division=division).delete()
    else:
        Timetable.objects.filter(semester=semester, division__isnull=True).delete()
    # bulk create
    Timetable.objects.bulk_create(schedule)
    return schedule





