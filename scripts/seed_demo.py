"""Seed realistic developer demo data for Project Hub.

This script is intentionally not exposed as an API endpoint. It uses the
application SQLAlchemy models/session directly and is safe to run repeatedly.

Run from the repository root:
    python project-hub-api/scripts/seed_demo.py

Or from project-hub-api:
    python scripts/seed_demo.py
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Account,
    AccountMember,
    AccountMemberRole,
    Assumption,
    Decision,
    Issue,
    OptionSet,
    OptionValue,
    Portfolio,
    Program,
    Project,
    ProjectDeliveryType,
    Resource,
    ResourceAllocation,
    ResourceTimeOff,
    Risk,
    Sprint,
    Task,
    User,
)


T = TypeVar("T")

DEMO_ACCOUNT_NAME = "Demo Project Hub Account"
DEMO_ACCOUNT_SLUG = "demo-project-hub-account"
DEMO_USER_EMAIL = "demo.user@projecthub.local"
DEMO_USER_NAME = "Demo User"


OPTION_DEFINITIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("PORTFOLIO", "STATUS", ("Active", "On Hold", "Complete", "Cancelled")),
    ("PROGRAM", "STATUS", ("Active", "On Hold", "Complete", "Cancelled")),
    ("PROJECT", "STATUS", ("Active", "On Hold", "Complete", "Cancelled")),
    ("TASK", "STATUS", ("Not Started", "In Progress", "Blocked", "Complete")),
    ("TASK", "TYPE", ("Work Package", "Milestone", "Activity")),
    ("TASK", "PRIORITY", ("Low", "Medium", "High", "Critical")),
    ("RISK", "PRIORITY", ("Low", "Medium", "High", "Critical")),
    ("RISK", "STATUS", ("Open", "Mitigating", "Closed")),
    ("ISSUE", "PRIORITY", ("Low", "Medium", "High", "Critical")),
    ("ISSUE", "STATUS", ("Open", "In Progress", "Resolved", "Closed")),
    ("ASSUMPTION", "STATUS", ("Draft", "Validated", "Invalidated")),
    ("DECISION", "STATUS", ("Draft", "Pending", "Approved", "Rejected", "Superseded")),
    ("SPRINT", "STATUS", ("Planned", "Active", "Completed", "Cancelled")),
)


def normalize_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value.strip()).strip("_").upper()


def get_one_or_create(db: Session, model: type[T], where, defaults: dict, counters: Counter[str]) -> T:
    existing = db.scalar(select(model).where(*where))
    if existing is not None:
        counters[f"{model.__name__} reused"] += 1
        return existing
    obj = model(**defaults)
    db.add(obj)
    db.flush()
    counters[f"{model.__name__} created"] += 1
    return obj


def seed_option_value(
    db: Session,
    *,
    account_id,
    entity_type: str,
    name: str,
    label: str,
    sort_order: int,
    counters: Counter[str],
) -> OptionValue:
    option_set = get_one_or_create(
        db,
        OptionSet,
        where=[
            OptionSet.account_id == account_id,
            OptionSet.entity_type == entity_type,
            OptionSet.name == name,
        ],
        defaults={
            "account_id": account_id,
            "entity_type": entity_type,
            "name": name,
            "is_system": True,
        },
        counters=counters,
    )
    value = normalize_key(label)
    return get_one_or_create(
        db,
        OptionValue,
        where=[OptionValue.option_set_id == option_set.id, OptionValue.value == value],
        defaults={
            "option_set_id": option_set.id,
            "label": label,
            "value": value,
            "sort_order": sort_order,
            "is_default": sort_order == 0,
            "is_active": True,
        },
        counters=counters,
    )


def seed_options(db: Session, account_id, counters: Counter[str]) -> dict[tuple[str, str, str], OptionValue]:
    options: dict[tuple[str, str, str], OptionValue] = {}
    for entity_type, name, labels in OPTION_DEFINITIONS:
        for sort_order, label in enumerate(labels):
            option = seed_option_value(
                db,
                account_id=account_id,
                entity_type=entity_type,
                name=name,
                label=label,
                sort_order=sort_order,
                counters=counters,
            )
            options[(entity_type, name, normalize_key(label))] = option
    return options


def option_id(options: dict[tuple[str, str, str], OptionValue], entity_type: str, name: str, label: str):
    return options[(entity_type, name, normalize_key(label))].id


def seed_account_user_membership(db: Session, counters: Counter[str]) -> tuple[Account, User]:
    user = get_one_or_create(
        db,
        User,
        where=[User.email == DEMO_USER_EMAIL],
        defaults={
            "email": DEMO_USER_EMAIL,
            "full_name": DEMO_USER_NAME,
            "cognito_sub": "demo-user-projecthub-local",
            "is_active": True,
        },
        counters=counters,
    )
    account = get_one_or_create(
        db,
        Account,
        where=[Account.slug == DEMO_ACCOUNT_SLUG],
        defaults={"name": DEMO_ACCOUNT_NAME, "slug": DEMO_ACCOUNT_SLUG, "created_by": user.id},
        counters=counters,
    )
    if account.created_by is None:
        account.created_by = user.id
    member = get_one_or_create(
        db,
        AccountMember,
        where=[AccountMember.account_id == account.id, AccountMember.user_id == user.id],
        defaults={"account_id": account.id, "user_id": user.id, "role": AccountMemberRole.OWNER.value},
        counters=counters,
    )
    if member.role not in {AccountMemberRole.OWNER.value, AccountMemberRole.ADMIN.value}:
        member.role = AccountMemberRole.OWNER.value
        counters["AccountMember promoted"] += 1
    return account, user


def seed_hierarchy(db: Session, account: Account, user: User, options, counters: Counter[str]):
    today = date.today()
    portfolio_specs = {
        "Product Development": {
            "color": "#f59e0b",
            "programs": {
                "Cytology": ["Ontario Rollout", "Manitoba Rollout"],
                "Atlas OCR": ["OCR Engine", "API Gateway"],
            },
        },
        "Internal Initiatives": {
            "color": "#0ea5e9",
            "programs": {
                "Project Hub": ["Backend Migration", "Frontend Migration"],
                "Infrastructure": ["AWS Deployment", "Monitoring Setup"],
            },
        },
    }
    portfolios: dict[str, Portfolio] = {}
    programs: dict[str, Program] = {}
    projects: dict[str, Project] = {}

    project_index = 0
    for portfolio_name, portfolio_spec in portfolio_specs.items():
        portfolio = get_one_or_create(
            db,
            Portfolio,
            where=[Portfolio.account_id == account.id, Portfolio.name == portfolio_name],
            defaults={
                "account_id": account.id,
                "name": portfolio_name,
                "description": f"Demo portfolio for {portfolio_name.lower()} workstreams.",
                "status_id": option_id(options, "PORTFOLIO", "STATUS", "Active"),
                "color": portfolio_spec["color"],
                "start_date": today - timedelta(days=30),
                "target_end_date": today + timedelta(days=180),
                "created_by": user.id,
            },
            counters=counters,
        )
        portfolios[portfolio_name] = portfolio
        for program_name, project_names in portfolio_spec["programs"].items():
            program = get_one_or_create(
                db,
                Program,
                where=[Program.account_id == account.id, Program.portfolio_id == portfolio.id, Program.name == program_name],
                defaults={
                    "account_id": account.id,
                    "portfolio_id": portfolio.id,
                    "name": program_name,
                    "description": f"Demo program for {program_name} delivery.",
                    "status_id": option_id(options, "PROGRAM", "STATUS", "Active"),
                    "color": "#8b5cf6",
                    "start_date": today - timedelta(days=20),
                    "target_end_date": today + timedelta(days=150),
                    "created_by": user.id,
                },
                counters=counters,
            )
            programs[program_name] = program
            for project_name in project_names:
                project_index += 1
                project = get_one_or_create(
                    db,
                    Project,
                    where=[Project.account_id == account.id, Project.program_id == program.id, Project.name == project_name],
                    defaults={
                        "account_id": account.id,
                        "portfolio_id": portfolio.id,
                        "program_id": program.id,
                        "name": project_name,
                        "description": f"Demo project for {project_name.lower()}.",
                        "delivery_type": ProjectDeliveryType.AGILE.value if project_name == "Frontend Migration" else ProjectDeliveryType.WATERFALL.value,
                        "status_id": option_id(options, "PROJECT", "STATUS", "Active"),
                        "color": "#6366f1",
                        "start_date": today - timedelta(days=10 + project_index),
                        "target_end_date": today + timedelta(days=45 + project_index * 7),
                        "created_by": user.id,
                    },
                    counters=counters,
                )
                projects[project_name] = project
    return portfolios, programs, projects


def seed_tasks(db: Session, account: Account, user: User, projects: dict[str, Project], options, counters: Counter[str]) -> dict[str, list[Task]]:
    task_names = [
        "Define scope",
        "Confirm requirements",
        "Build API integration",
        "Configure environments",
        "Complete QA testing",
        "Prepare UAT signoff",
    ]
    status_cycle = ["Complete", "In Progress", "In Progress", "Not Started", "Not Started", "Not Started"]
    task_type_id = option_id(options, "TASK", "TYPE", "Activity")
    tasks_by_project: dict[str, list[Task]] = {}
    today = date.today()
    for project_offset, (project_name, project) in enumerate(projects.items()):
        tasks: list[Task] = []
        for index, task_name in enumerate(task_names):
            start = today + timedelta(days=(index * 3) - 12 + project_offset)
            status_label = status_cycle[index]
            task = get_one_or_create(
                db,
                Task,
                where=[Task.account_id == account.id, Task.project_id == project.id, Task.name == task_name, Task.is_deleted.is_(False)],
                defaults={
                    "account_id": account.id,
                    "project_id": project.id,
                    "task_type_id": task_type_id,
                    "status_id": option_id(options, "TASK", "STATUS", status_label),
                    "priority_id": option_id(options, "TASK", "PRIORITY", "Medium"),
                    "name": task_name,
                    "description": f"{task_name} for {project_name}.",
                    "start_date": start,
                    "finish_date": start + timedelta(days=2),
                    "duration_days": Decimal("3.00"),
                    "percent_complete": Decimal("100.00") if status_label == "Complete" else Decimal("50.00") if status_label == "In Progress" else Decimal("0.00"),
                    "sort_order": Decimal(str(index + 1)),
                    "created_by": user.id,
                },
                counters=counters,
            )
            tasks.append(task)
        tasks_by_project[project_name] = tasks
    return tasks_by_project


def seed_raid(db: Session, account: Account, user: User, projects: dict[str, Project], options, counters: Counter[str]) -> None:
    risk_specs = [
        ("Ontario Rollout", "R-DEMO-001", "Integration partner timeline may slip", "External dependency", "Rollout dates could move"),
        ("AWS Deployment", "R-DEMO-002", "Cloud quota approval may be delayed", "Capacity approval", "Deployment environment readiness risk"),
    ]
    for project_name, number, title, cause, effect in risk_specs:
        project = projects[project_name]
        get_one_or_create(
            db,
            Risk,
            where=[Risk.project_id == project.id, Risk.risk_number == number],
            defaults={
                "account_id": account.id,
                "project_id": project.id,
                "program_id": project.program_id,
                "risk_number": number,
                "title": title,
                "cause": cause,
                "effect": effect,
                "priority_id": option_id(options, "RISK", "PRIORITY", "High"),
                "status_id": option_id(options, "RISK", "STATUS", "Open"),
                "assigned_to": user.id,
                "target_resolution_date": date.today() + timedelta(days=30),
                "created_by": user.id,
            },
            counters=counters,
        )

    issue_specs = [
        ("Frontend Migration", "I-DEMO-001", "Legacy field mapping gaps", "Some Base44 export fields do not directly match API fields."),
        ("Monitoring Setup", "I-DEMO-002", "Alert routing not finalized", "Ownership groups need confirmation."),
    ]
    for project_name, number, title, description in issue_specs:
        project = projects[project_name]
        get_one_or_create(
            db,
            Issue,
            where=[Issue.project_id == project.id, Issue.issue_number == number],
            defaults={
                "account_id": account.id,
                "project_id": project.id,
                "program_id": project.program_id,
                "issue_number": number,
                "title": title,
                "description": description,
                "priority_id": option_id(options, "ISSUE", "PRIORITY", "Medium"),
                "status_id": option_id(options, "ISSUE", "STATUS", "In Progress"),
                "assigned_to": user.id,
                "target_resolution_date": date.today() + timedelta(days=14),
                "created_by": user.id,
            },
            counters=counters,
        )

    assumption_specs = [
        ("Backend Migration", "A-DEMO-001", "Backend APIs remain available in the local development environment."),
        ("OCR Engine", "A-DEMO-002", "OCR training sample data can be anonymized for testing."),
    ]
    for project_name, number, description in assumption_specs:
        project = projects[project_name]
        get_one_or_create(
            db,
            Assumption,
            where=[Assumption.project_id == project.id, Assumption.assumption_number == number],
            defaults={
                "account_id": account.id,
                "project_id": project.id,
                "program_id": project.program_id,
                "assumption_number": number,
                "description": description,
                "status_id": option_id(options, "ASSUMPTION", "STATUS", "Validated"),
                "entered_by": user.id,
                "date_entered": date.today(),
            },
            counters=counters,
        )

    decision_specs = [
        ("API Gateway", "D-DEMO-001", "Use API Gateway HTTP API", "Proceed with HTTP API for lower operational overhead."),
        ("Manitoba Rollout", "D-DEMO-002", "Pilot rollout wave approved", "Start with a limited clinic pilot before provincial scale-up."),
    ]
    for project_name, number, title, decision_text in decision_specs:
        project = projects[project_name]
        get_one_or_create(
            db,
            Decision,
            where=[Decision.project_id == project.id, Decision.decision_number == number],
            defaults={
                "account_id": account.id,
                "project_id": project.id,
                "program_id": project.program_id,
                "decision_number": number,
                "title": title,
                "decision_text": decision_text,
                "description": decision_text,
                "impact": "Demo decision for manual testing.",
                "status_id": option_id(options, "DECISION", "STATUS", "Approved"),
                "proposed_date": date.today() - timedelta(days=7),
                "approved_date": date.today(),
                "approved_by": user.id,
                "created_by": user.id,
            },
            counters=counters,
        )


def seed_resources(db: Session, account: Account, user: User, tasks_by_project: dict[str, list[Task]], counters: Counter[str]) -> dict[str, Resource]:
    resource_specs = [
        ("Jane Doe", "Program Manager"),
        ("John Doe", "Backend Engineer"),
        ("Tara Perera", "QA Analyst"),
        ("Alex Morgan", "Cloud Engineer"),
        ("Priya Shah", "Business Analyst"),
    ]
    resources: dict[str, Resource] = {}
    for name, role in resource_specs:
        resources[name] = get_one_or_create(
            db,
            Resource,
            where=[Resource.account_id == account.id, Resource.name == name],
            defaults={
                "account_id": account.id,
                "name": name,
                "role": role,
                "weekly_capacity_hours": Decimal("40.00"),
                "is_active": True,
                "created_by": user.id,
            },
            counters=counters,
        )

    allocation_specs = [
        ("Frontend Migration", 2, "Jane Doe", Decimal("12.00")),
        ("Frontend Migration", 3, "Tara Perera", Decimal("16.00")),
        ("Backend Migration", 2, "John Doe", Decimal("20.00")),
        ("AWS Deployment", 3, "Alex Morgan", Decimal("18.00")),
        ("Ontario Rollout", 1, "Priya Shah", Decimal("10.00")),
    ]
    for project_name, task_index, resource_name, hours in allocation_specs:
        task = tasks_by_project[project_name][task_index]
        resource = resources[resource_name]
        get_one_or_create(
            db,
            ResourceAllocation,
            where=[ResourceAllocation.account_id == account.id, ResourceAllocation.task_id == task.id, ResourceAllocation.resource_id == resource.id],
            defaults={
                "account_id": account.id,
                "task_id": task.id,
                "resource_id": resource.id,
                "allocated_hours": hours,
                "start_date": task.start_date,
                "end_date": task.finish_date,
            },
            counters=counters,
        )

    time_off_specs = [
        ("Jane Doe", date.today() + timedelta(days=21), date.today() + timedelta(days=23), "Conference"),
        ("Alex Morgan", date.today() + timedelta(days=35), date.today() + timedelta(days=36), "Vacation"),
    ]
    for resource_name, start, end, reason in time_off_specs:
        resource = resources[resource_name]
        get_one_or_create(
            db,
            ResourceTimeOff,
            where=[
                ResourceTimeOff.account_id == account.id,
                ResourceTimeOff.resource_id == resource.id,
                ResourceTimeOff.start_date == start,
                ResourceTimeOff.end_date == end,
            ],
            defaults={
                "account_id": account.id,
                "resource_id": resource.id,
                "start_date": start,
                "end_date": end,
                "reason": reason,
                "hours_per_day": Decimal("8.00"),
                "created_by": user.id,
            },
            counters=counters,
        )
    return resources


def seed_sprints(db: Session, account: Account, user: User, projects: dict[str, Project], tasks_by_project: dict[str, list[Task]], options, counters: Counter[str]) -> None:
    project = projects["Frontend Migration"]
    today = date.today()
    sprint_specs = [
        ("Sprint 1", today, today + timedelta(days=13), "Wire shell and navigation"),
        ("Sprint 2", today + timedelta(days=14), today + timedelta(days=27), "Wire project detail tabs"),
    ]
    sprints: list[Sprint] = []
    for name, start, end, goal in sprint_specs:
        sprint = get_one_or_create(
            db,
            Sprint,
            where=[Sprint.account_id == account.id, Sprint.project_id == project.id, Sprint.name == name],
            defaults={
                "account_id": account.id,
                "project_id": project.id,
                "name": name,
                "goal": goal,
                "status_id": option_id(options, "SPRINT", "STATUS", "Planned"),
                "start_date": start,
                "end_date": end,
                "created_by": user.id,
            },
            counters=counters,
        )
        sprints.append(sprint)

    for task, sprint in zip(tasks_by_project["Frontend Migration"][:4], [sprints[0], sprints[0], sprints[1], sprints[1]], strict=False):
        if task.sprint_id != sprint.id:
            task.sprint_id = sprint.id
            counters["Task sprint assignments updated"] += 1


def main() -> None:
    counters: Counter[str] = Counter()
    with SessionLocal() as db:
        account, user = seed_account_user_membership(db, counters)
        options = seed_options(db, account.id, counters)
        _, _, projects = seed_hierarchy(db, account, user, options, counters)
        tasks_by_project = seed_tasks(db, account, user, projects, options, counters)
        seed_raid(db, account, user, projects, options, counters)
        seed_resources(db, account, user, tasks_by_project, counters)
        seed_sprints(db, account, user, projects, tasks_by_project, options, counters)
        db.commit()

    print("Demo seed complete.")
    print(f"Account: {DEMO_ACCOUNT_NAME}")
    print(f"User: {DEMO_USER_EMAIL}")
    print("Summary:")
    for key in sorted(counters):
        print(f"  - {key}: {counters[key]}")


if __name__ == "__main__":
    main()