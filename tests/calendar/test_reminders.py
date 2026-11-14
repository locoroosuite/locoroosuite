"""Unit tests for calendar reminder normalization (U15.57, issues #10/#11)."""

from app.modules.calendar.services.reminders import (
    alarms_to_reminders,
    reminders_to_alarms,
)


class TestRemindersToAlarms:
    def test_lowercase_display_normalized(self):
        alarms, error = reminders_to_alarms([{"type": "display", "trigger_minutes": 15}])
        assert error is None
        assert alarms == [{"action": "DISPLAY", "trigger": "-PT15M", "description": ""}]

    def test_integer_trigger_converted_to_duration(self):
        alarms, error = reminders_to_alarms([{"type": "DISPLAY", "trigger_minutes": 60}])
        assert error is None
        assert alarms[0]["trigger"] == "-PT1H"

    def test_day_scale_trigger(self):
        alarms, error = reminders_to_alarms([{"type": "DISPLAY", "trigger_minutes": 1440}])
        assert error is None
        assert alarms[0]["trigger"] == "-P1D"

    def test_zero_minutes_at_start(self):
        alarms, error = reminders_to_alarms([{"type": "DISPLAY", "trigger_minutes": 0}])
        assert error is None
        assert alarms[0]["trigger"] == "-PT0M"

    def test_invalid_type_rejected(self):
        for bad in ("notification", "popup", "email2"):
            _alarms, error = reminders_to_alarms([{"type": bad, "trigger_minutes": 15}])
            assert error is not None, bad

    def test_missing_type_defaults_to_display(self):
        alarms, error = reminders_to_alarms([{"trigger_minutes": 15}])
        assert error is None
        assert alarms[0]["action"] == "DISPLAY"

    def test_non_integer_trigger_rejected(self):
        _alarms, error = reminders_to_alarms([{"type": "DISPLAY", "trigger_minutes": "soon"}])
        assert error is not None

    def test_negative_and_huge_triggers_rejected(self):
        assert reminders_to_alarms([{"type": "DISPLAY", "trigger_minutes": -5}])[1] is not None
        assert reminders_to_alarms([{"type": "DISPLAY", "trigger_minutes": 10**9}])[1] is not None

    def test_duplicates_rejected(self):
        _alarms, error = reminders_to_alarms(
            [
                {"type": "DISPLAY", "trigger_minutes": 15},
                {"type": "display", "trigger_minutes": 15},
            ]
        )
        assert error is not None

    def test_none_and_empty_accepted(self):
        assert reminders_to_alarms(None) == ([], None)
        assert reminders_to_alarms([]) == ([], None)
        assert reminders_to_alarms("") == ([], None)

    def test_summary_used_as_description(self):
        alarms, _error = reminders_to_alarms(
            [{"type": "DISPLAY", "trigger_minutes": 15}], summary="Standup"
        )
        assert alarms[0]["description"] == "Standup"


class TestAlarmsToReminders:
    def test_round_trip(self):
        alarms, _error = reminders_to_alarms(
            [
                {"type": "DISPLAY", "trigger_minutes": 30},
                {"type": "EMAIL", "trigger_minutes": 90},
            ],
            summary="x",
        )
        reminders = alarms_to_reminders(alarms)
        assert {"type": "DISPLAY", "trigger_minutes": 30} in reminders
        assert {"type": "EMAIL", "trigger_minutes": 90} in reminders

    def test_unparseable_trigger_keeps_raw_value(self):
        reminders = alarms_to_reminders([{"action": "DISPLAY", "trigger": "-19960401T000000Z"}])
        assert reminders[0]["trigger"] == "-19960401T000000Z"

    def test_empty(self):
        assert alarms_to_reminders(None) == []
        assert alarms_to_reminders([]) == []

    def test_unknown_action_normalized_to_display(self):
        reminders = alarms_to_reminders([{"action": "audio", "trigger": "-PT5M"}])
        assert reminders[0]["type"] == "DISPLAY"
