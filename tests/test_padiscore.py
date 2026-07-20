from app.engine.padiscore import compute_padiscore, MemberStats


def test_perfect_member_scores_high():
    stats = MemberStats(
        on_time_streak=12,
        average_delay_hours=0.0,
        missed_payment_count=0,
        completed_cycles_all_pots=20,
        unresolved_defaults=0,
        resolved_defaults=0,
    )
    score = compute_padiscore(stats)
    assert score > 95.0


def test_brand_new_member_with_no_history_scores_moderately():
    """A new member isn't punished for having no history yet — no defaults means full recovery marks,
    but zero streak/tenure keeps the score mid-range rather than either extreme."""
    stats = MemberStats(
        on_time_streak=0,
        average_delay_hours=0.0,
        missed_payment_count=0,
        completed_cycles_all_pots=0,
        unresolved_defaults=0,
        resolved_defaults=0,
    )
    score = compute_padiscore(stats)
    assert 30.0 < score < 70.0


def test_missed_payments_hurt_hard():
    baseline = MemberStats(
        on_time_streak=5, average_delay_hours=2.0, missed_payment_count=0,
        completed_cycles_all_pots=5, unresolved_defaults=0, resolved_defaults=0,
    )
    with_misses = MemberStats(
        on_time_streak=5, average_delay_hours=2.0, missed_payment_count=3,
        completed_cycles_all_pots=5, unresolved_defaults=0, resolved_defaults=0,
    )
    assert compute_padiscore(with_misses) < compute_padiscore(baseline) - 15


def test_unresolved_default_scores_lower_than_resolved_default():
    unresolved = MemberStats(
        on_time_streak=5, average_delay_hours=2.0, missed_payment_count=0,
        completed_cycles_all_pots=5, unresolved_defaults=1, resolved_defaults=0,
    )
    resolved = MemberStats(
        on_time_streak=5, average_delay_hours=2.0, missed_payment_count=0,
        completed_cycles_all_pots=5, unresolved_defaults=0, resolved_defaults=1,
    )
    assert compute_padiscore(unresolved) < compute_padiscore(resolved)


def test_score_always_within_bounds():
    extreme = MemberStats(
        on_time_streak=999, average_delay_hours=0.0, missed_payment_count=0,
        completed_cycles_all_pots=999, unresolved_defaults=0, resolved_defaults=0,
    )
    assert 0.0 <= compute_padiscore(extreme) <= 100.0

    worst = MemberStats(
        on_time_streak=0, average_delay_hours=500.0, missed_payment_count=99,
        completed_cycles_all_pots=0, unresolved_defaults=10, resolved_defaults=0,
    )
    assert 0.0 <= compute_padiscore(worst) <= 100.0
