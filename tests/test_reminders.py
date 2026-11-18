"""Tests for reminder system."""

import json
import pytest
from pathlib import Path

from applyloop.config import ProjectPaths
from applyloop.reminders import (
    add_reminder,
    complete_reminder,
    check_due_reminders,
    list_reminders,
    auto_generate_follow_ups,
)


@pytest.fixture
def reminder_project(tmp_path):
    (tmp_path / "data").mkdir(parents=True)
    return ProjectPaths(root=tmp_path)


class TestAddReminder:
    def test_creates_reminder(self, reminder_project):
        r = add_reminder(
            reminder_project,
            application_num=1,
            company="Acme",
            role="Engineer",
            remind_date="2099-01-01",
            reminder_type="follow_up",
            message="Check status",
        )
        assert r.application_num == 1
        assert r.company == "Acme"

        rfile = reminder_project.reminders_file
        assert rfile.exists()
        data = json.loads(rfile.read_text())
        assert len(data) == 1

    def test_appends_to_existing(self, reminder_project):
        add_reminder(reminder_project, 1, "A", "E1", "2099-01-01")
        add_reminder(reminder_project, 2, "B", "E2", "2099-01-02")

        data = json.loads(reminder_project.reminders_file.read_text())
        assert len(data) == 2


class TestCompleteReminder:
    def test_marks_complete(self, reminder_project):
        add_reminder(reminder_project, 1, "Acme", "Eng", "2099-01-01")
        result = complete_reminder(reminder_project, 1)
        assert result is True

        data = json.loads(reminder_project.reminders_file.read_text())
        assert data[0]["completed"] is True

    def test_not_found(self, reminder_project):
        result = complete_reminder(reminder_project, 999)
        assert result is False


class TestCheckDueReminders:
    def test_no_reminders(self, reminder_project):
        due = check_due_reminders(reminder_project)
        assert len(due) == 0

    def test_overdue_reminder(self, reminder_project):
        add_reminder(reminder_project, 1, "Acme", "Eng", "2020-01-01")
        due = check_due_reminders(reminder_project)
        assert len(due) == 1

    def test_future_reminder_not_due(self, reminder_project):
        add_reminder(reminder_project, 1, "Acme", "Eng", "2099-12-31")
        due = check_due_reminders(reminder_project)
        assert len(due) == 0

    def test_completed_not_due(self, reminder_project):
        add_reminder(reminder_project, 1, "Acme", "Eng", "2020-01-01")
        complete_reminder(reminder_project, 1)
        due = check_due_reminders(reminder_project)
        assert len(due) == 0


class TestListReminders:
    def test_empty_list(self, reminder_project):
        result = list_reminders(reminder_project)
        assert len(result) == 0

    def test_filters_completed(self, reminder_project):
        add_reminder(reminder_project, 1, "Acme", "Eng", "2099-01-01")
        add_reminder(reminder_project, 2, "Beta", "Dev", "2099-01-02")
        complete_reminder(reminder_project, 1)

        active = list_reminders(reminder_project, show_completed=False)
        assert len(active) == 1

        all_reminders = list_reminders(reminder_project, show_completed=True)
        assert len(all_reminders) == 2


class TestAutoGenerateFollowUps:
    def test_generates_for_applied(self, reminder_project):
        apps = reminder_project.data_dir / "applications.md"
        apps.write_text(
            "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 1 | 2024-01-15 | Acme | Eng | 3/5 | Applied | x | y | |\n"
        )
        created = auto_generate_follow_ups(reminder_project)
        assert created == 1

    def test_skips_evaluated(self, reminder_project):
        apps = reminder_project.data_dir / "applications.md"
        apps.write_text(
            "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 1 | 2024-01-15 | Acme | Eng | 3/5 | Evaluated | x | y | |\n"
        )
        created = auto_generate_follow_ups(reminder_project)
        assert created == 0

    def test_skips_existing_reminder(self, reminder_project):
        apps = reminder_project.data_dir / "applications.md"
        apps.write_text(
            "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 1 | 2024-01-15 | Acme | Eng | 3/5 | Applied | x | y | |\n"
        )
        add_reminder(reminder_project, 1, "Acme", "Eng", "2099-01-01")
        created = auto_generate_follow_ups(reminder_project)
        assert created == 0
