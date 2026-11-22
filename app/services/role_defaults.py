"""The roles every account starts with (Account > Users & Roles). Owners can change all of them except Owner."""

from app.models.account_role import ACTIONS, AREAS, SCOPE_ALL, SCOPE_ASSIGNED

V, C, E, D, A = ACTIONS
ALL = list(ACTIONS)
VIEW = [V]
WORK = ["REQUIREMENTS", "TESTING", "SPRINTS", "RAID", "GOVERNANCE"]


def grid(**areas: list[str]) -> dict[str, list[str]]:
    return {area: list(areas.get(area, [])) for area in AREAS}


DEFAULT_ROLES: tuple[dict[str, object], ...] = (
    {
        "key": "OWNER",
        "name": "Owner",
        "description": "Full control of the account: everything in every project, users, roles, settings and billing.",
        "scope": SCOPE_ALL,
        "permissions": grid(**{area: ALL for area in AREAS}),
    },
    {
        "key": "PORTFOLIO_MANAGER",
        "name": "Portfolio Manager",
        "description": "Runs assigned portfolios: their programs, projects and all delivery work.",
        "scope": SCOPE_ASSIGNED,
        "permissions": grid(
            PORTFOLIOS=[V, E], PROGRAMS=ALL, PROJECTS=ALL, **{area: ALL for area in WORK},
            RESOURCES=[V, C, E], REPORTING=VIEW,
        ),
    },
    {
        "key": "PROGRAM_MANAGER",
        "name": "Program Manager",
        "description": "Runs assigned programs: their projects and all delivery work.",
        "scope": SCOPE_ASSIGNED,
        "permissions": grid(
            PORTFOLIOS=VIEW, PROGRAMS=[V, E], PROJECTS=ALL, **{area: ALL for area in WORK},
            RESOURCES=[V, C, E], REPORTING=VIEW,
        ),
    },
    {
        "key": "PROJECT_MANAGER",
        "name": "Project Manager",
        "description": "Manages assigned projects, including planning, tasks, resources, and RAID.",
        "scope": SCOPE_ASSIGNED,
        "permissions": grid(
            PORTFOLIOS=VIEW, PROGRAMS=VIEW, PROJECTS=[V, C, E, D], **{area: ALL for area in WORK},
            RESOURCES=[V, E], REPORTING=VIEW,
        ),
    },
    {
        "key": "CONTRIBUTOR",
        "name": "Contributor",
        "description": "Works on assigned projects: updates tasks, RAID items and delivery records.",
        "scope": SCOPE_ASSIGNED,
        "permissions": grid(
            PORTFOLIOS=VIEW, PROGRAMS=VIEW, PROJECTS=[V, E], **{area: [V, C, E] for area in WORK},
            RESOURCES=VIEW, REPORTING=VIEW,
        ),
    },
    {
        "key": "VIEWER",
        "name": "Viewer",
        "description": "Read-only access to assigned projects.",
        "scope": SCOPE_ASSIGNED,
        "permissions": grid(**{area: VIEW for area in AREAS}),
    },
)

DEFAULT_ROLES_BY_KEY = {str(role["key"]): role for role in DEFAULT_ROLES}
