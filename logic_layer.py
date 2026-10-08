REQUIRED_FIELDS = [
    'age', 'gender', 'bmi', 'fitness', 'training_goal',
    'health_injury_history', 'avail_equipment', 'avail_days', 'duration'
]
DISCLAIMER = (
    'Workout recommendations are not a substitute for professional medical '
    'treatment, diagnosis, or advice. Consult a medical professional about '
    'health concerns and exercise restrictions.'
)


def check_missing_information(user_data):
    """Business readiness check; basic input validation belongs to input layer.

    Empty equipment/history lists or strings can explicitly mean none.
    """
    missing_fields = []
    for field in REQUIRED_FIELDS:
        if field not in user_data or user_data[field] is None:
            missing_fields.append(field)
        elif field not in ('health_injury_history', 'avail_equipment', 'avail_days'):
            if user_data[field] == '':
                missing_fields.append(field)
    return missing_fields


def check_conflicting_constraints(user_data):
    conflicts = []
    if not user_data['avail_days']:
        conflicts.append('No available training days provided.')
    # Checking positive duration/type/range is the input manager's job.
    return conflicts


def evaluate_user_input(user_data):
    missing_fields = check_missing_information(user_data)
    if missing_fields:
        return {'status': 'missing_information', 'send_to_ai': False,
                'missing_fields': missing_fields}
    conflicts = check_conflicting_constraints(user_data)
    if conflicts:
        return {'status': 'needs_clarification', 'send_to_ai': False,
                'conflicts': conflicts}
    return {'status': 'valid', 'send_to_ai': True}


def _normalise(values):
    return [value.strip().lower() for value in values]


def check_equipment(user_data, ai_output):
    available_equipment = _normalise(user_data['avail_equipment'])
    unavailable_equipment = []
    for equipment in ai_output['required_equipment']:
        if equipment.strip().lower() not in available_equipment:
            unavailable_equipment.append(equipment)
    return unavailable_equipment


def check_availability(user_data, ai_output):
    issues = []
    if ai_output['day'].strip().lower() not in _normalise(user_data['avail_days']):
        issues.append('Recommended workout day is not available.')
    if ai_output['duration'] > user_data['duration']:
        issues.append("Recommended workout is longer than the user's available time.")
    return issues


def check_health_constraints(user_data, ai_output):
    """Use AI health assessment plus explicit user restrictions.

    Do not infer medical restrictions from diagnosis text using keywords.
    AI manager must require health_conflict, pain_flag, fatigue_flag, intensity,
    activity and target_body_areas in its validated response.
    """
    issues = []
    if ai_output['health_conflict']:
        issues.append('AI reports a conflict with the user\'s health constraints.')
    restricted_activities = _normalise(user_data.get('restricted_activities', []))
    if ai_output['activity'].strip().lower() in restricted_activities:
        issues.append('Recommended activity is explicitly restricted.')
    restricted_areas = _normalise(user_data.get('restricted_body_areas', []))
    for area in ai_output['target_body_areas']:
        if area.strip().lower() in restricted_areas:
            issues.append('Recommended workout targets a restricted area: ' + area + '.')
    # Multi-condition business rule uses multiple fields FROM AI output.
    if ai_output['intensity'] == 'high' and (
        ai_output['pain_flag'] or ai_output['fatigue_flag']
    ):
        issues.append('High intensity conflicts with AI-reported pain or fatigue.')
    return issues


def check_weather(user_data, ai_output):
    # Weather is optional; another module supplies it, if available.
    if user_data.get('weather', 'unknown') == 'unsuitable':
        if ai_output['location'] == 'outdoor':
            return ['Unsuitable weather: ask AI for an indoor alternative.']
    return []


def _workout_issues(user_data, ai_output):
    issues = []
    unavailable = check_equipment(user_data, ai_output)
    if unavailable:
        issues.append('Unavailable equipment: ' + ', '.join(unavailable) + '.')
    issues.extend(check_availability(user_data, ai_output))
    issues.extend(check_health_constraints(user_data, ai_output))
    issues.extend(check_weather(user_data, ai_output))
    return issues


def score(record):
    """0 for blocked plans; otherwise AI goal_match (0-100).

    AI manager validates goal_match's type/range. This ranking reflects AI's
    goal assessment, not a medical risk score.
    """
    user_data = record['user_data']
    if not evaluate_user_input(user_data)['send_to_ai']:
        return 0
    ai_output = record.get('ai_output')
    if ai_output is None or _workout_issues(user_data, ai_output):
        return 0
    return round(ai_output['goal_match'], 2)


def evaluate(record):
    """Evaluate an AI-enriched record; controller performs returned actions.

    record = {'user_data': cleaned_user_data, 'ai_output': validated_ai_output}
    Do not use this as a substitute for the pre-AI evaluate_user_input check.
    """
    user_data = record['user_data']
    readiness = evaluate_user_input(user_data)
    if not readiness['send_to_ai']:
        return {'accepted': False, 'status': readiness['status'],
                'route': 'clarify', 'score': 0,
                'missing_fields': readiness.get('missing_fields', []),
                'issues': readiness.get('conflicts', []), 'disclaimer': DISCLAIMER}
    ai_output = record.get('ai_output')
    if ai_output is None:
        return {'accepted': False, 'status': 'ai_unavailable',
                'route': 'regenerate', 'score': 0,
                'issues': ['No validated AI workout was supplied.'],
                'missing_fields': [], 'disclaimer': DISCLAIMER}
    issues = _workout_issues(user_data, ai_output)
    accepted = not issues
    return {'accepted': accepted,
            'status': 'accepted' if accepted else 'rejected',
            'route': 'display' if accepted else 'regenerate',
            'score': round(ai_output['goal_match'], 2) if accepted else 0,
            'issues': issues, 'missing_fields': [], 'disclaimer': DISCLAIMER}

def route(record):
    return evaluate(record)['route']