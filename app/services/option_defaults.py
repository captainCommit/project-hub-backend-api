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
        "values": ("Work Package", "Milestone", "Activity"),
    },
    {
        "entity_type": "RISK",
        "name": "PRIORITY",
        "values": ("Low", "Medium", "High", "Critical"),
    },
    {"entity_type": "RISK", "name": "STATUS", "values": ("Open", "Mitigating", "Closed")},
    {
        "entity_type": "ISSUE",
        "name": "PRIORITY",
        "values": ("Low", "Medium", "High", "Critical"),
    },
    {
        "entity_type": "ISSUE",
        "name": "STATUS",
        "values": ("Open", "In Progress", "Resolved", "Closed"),
    },
    {
        "entity_type": "DECISION",
        "name": "STATUS",
        "values": ("Draft", "Pending", "Approved", "Rejected", "Superseded"),
    },
    {
        "entity_type": "ASSUMPTION",
        "name": "STATUS",
        "values": ("Draft", "Validated", "Invalidated"),
    },
    {
        "entity_type": "SPRINT",
        "name": "STATUS",
        "values": ("Planned", "Active", "Completed", "Cancelled"),
    },
)