PRIORITY_VALUES = ("Low", "Medium", "High", "Critical")

DEFAULT_OPTION_SETS: tuple[dict[str, object], ...] = (
    {
        "entity_type": "PROJECT",
        "name": "STATUS",
        "values": ("Active", "On Hold", "Complete", "Cancelled"),
    },
    {
        "entity_type": "PROGRAM",
        "name": "STATUS",
        "values": ("Active", "On Hold", "Complete", "Cancelled"),
    },
    {
        "entity_type": "PORTFOLIO",
        "name": "STATUS",
        "values": ("Active", "On Hold", "Complete", "Cancelled"),
    },
    {
        "entity_type": "TASK",
        "name": "STATUS",
        "values": ("Not Started", "In Progress", "Blocked", "Complete"),
    },
    {
        "entity_type": "TASK",
        "name": "TYPE",
        "values": ("Work Package", "Milestone", "Activity", "Phase", "Deliverable"),
    },
    {
        "entity_type": "TASK",
        "name": "PRIORITY",
        "values": PRIORITY_VALUES,
    },
    {
        "entity_type": "RISK",
        "name": "PRIORITY",
        "values": PRIORITY_VALUES,
    },
    {
        "entity_type": "RISK",
        "name": "STATUS",
        "values": ("Open", "Mitigating", "Closed", "Resolved", "Blocked", "Escalated", "Cancelled"),
    },
    {
        "entity_type": "ISSUE",
        "name": "PRIORITY",
        "values": PRIORITY_VALUES,
    },
    {
        "entity_type": "ISSUE",
        "name": "STATUS",
        "values": ("Open", "In Progress", "Resolved", "Closed", "Blocked", "Escalated", "Cancelled"),
    },
    {
        "entity_type": "DECISION",
        "name": "PRIORITY",
        "values": PRIORITY_VALUES,
    },
    {
        "entity_type": "DECISION",
        "name": "STATUS",
        # The statuses the app offers. Pending, Rejected and Superseded were retired (migration 202610260400).
        "values": ("Draft", "Open", "In Review", "Approved", "Cancelled", "Deferred"),
    },
    {
        "entity_type": "ASSUMPTION",
        "name": "PRIORITY",
        "values": PRIORITY_VALUES,
    },
    {
        "entity_type": "ASSUMPTION",
        "name": "STATUS",
        "values": ("Draft", "Validated", "Invalidated", "Open", "In Review", "Closed"),
    },
    {
        "entity_type": "SPRINT",
        "name": "STATUS",
        "values": ("Planned", "Active", "Completed", "Cancelled"),
    },
    {
        "entity_type": "DEPENDENCY",
        "name": "PRIORITY",
        "values": PRIORITY_VALUES,
    },
    {
        "entity_type": "DEPENDENCY",
        "name": "STATUS",
        "values": ("Open", "In Review", "Closed", "Escalated"),
    },
    {
        "entity_type": "CHANGE_REQUEST",
        "name": "PRIORITY",
        "values": PRIORITY_VALUES,
    },
    {
        "entity_type": "CHANGE_REQUEST",
        "name": "STATUS",
        "values": ("Draft", "Open", "In Review", "Approved", "Cancelled", "Deferred", "Implemented"),
    },
    {
        "entity_type": "REQUIREMENT",
        "name": "PRIORITY",
        "values": PRIORITY_VALUES,
    },
    {
        "entity_type": "REQUIREMENT",
        "name": "STATUS",
        "values": ("In Review", "Validated", "Deferred", "Cancelled"),
    },
    {
        "entity_type": "TEST_CASE",
        "name": "PRIORITY",
        "values": PRIORITY_VALUES,
    },
)


def default_labels() -> dict[tuple[str, str], dict[str, str]]:
    """(entity type, set name) -> {value key: the label the account started with}."""
    import re

    def key(label: str) -> str:
        return re.sub(r"[^A-Za-z0-9]+", "_", label.strip()).strip("_").upper()

    return {
        (str(definition["entity_type"]), str(definition["name"])): {
            key(str(label)): str(label) for label in definition["values"]  # type: ignore[union-attr]
        }
        for definition in DEFAULT_OPTION_SETS
    }


DEFAULT_LABELS = default_labels()
