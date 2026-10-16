"""Seed a large, realistic account: Northlake Fibre & Voice, an internet and VoIP provider building a new
admin portal for its support and billing teams.

25 employees (5 each in senior management, PMO, software, QA and HR/facilities) plus outside contractors,
4 portfolios, 22 projects, about 1,000 tasks with sprints, dependencies and assignments, RAID logs,
requirements and test cases, comments with @mentions, time off, holidays and an activity history. Dates are
relative to today, so the data always looks current.

The employees get user rows but no Cognito login, so they can't sign in and no invite emails are sent.
The existing users in SIGN_IN_USERS are added with the roles listed there, so you can sign in and look around,
or use the owner's session for scripts/loadtest.js. A user belongs to one account only, so they are taken out
of any other account they are in.

Run from project-hub-api (uses DATABASE_URL from .env unless --database-url is given):
    python scripts/seed_northlake.py            # refuses if the account already exists
    python scripts/seed_northlake.py --reset    # deletes the account (and everything in it) first
"""

from __future__ import annotations

import argparse
import random
import sys
import unicodedata
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.database import Base, create_database_engine, engine as app_engine  # noqa: E402
from app.models import (  # noqa: E402
    Account,
    AccountMember,
    AccountMemberRole,
    Assumption,
    ChangeRequest,
    Comment,
    CommentMention,
    Decision,
    DecisionOption,
    Dependency,
    Issue,
    OptionSet,
    OptionValue,
    Portfolio,
    Program,
    Project,
    Requirement,
    Resource,
    ResourceAllocation,
    ResourceTimeOff,
    Risk,
    Sprint,
    Task,
    TaskPredecessor,
    TestCase,
    User,
)
from app.models.access_grant import AccessGrant, ProjectModule  # noqa: E402
from app.models.account_holiday import AccountHoliday  # noqa: E402
from app.models.activity_log import ActivityLog  # noqa: E402
from app.models.resource_skill import ResourceSkill  # noqa: E402
from app.models.skill import Skill  # noqa: E402
from app.models.task_assignment import TaskAssignment  # noqa: E402
from app.models.task_required_skill import TaskRequiredSkill  # noqa: E402
from app.repositories.account_settings import AccountSettingsRepository  # noqa: E402
from app.services.member_resources import ensure_member_resource  # noqa: E402
from app.services.options import OptionService, normalize_key  # noqa: E402

ACCOUNT_NAME = "Northlake Fibre & Voice"
ACCOUNT_SLUG = "northlake-fibre-voice"
EMAIL_DOMAIN = "northlake.example"  # .example is reserved, so these addresses can never receive mail.

# Existing users who can sign in: (email, account role, title, department)
SIGN_IN_USERS = [
    ("owner@projecthub.com", "OWNER", "Project Manager", "PMO"),
    ("demo.user@projecthub.local", "MEMBER", "Accounting Executive", "Finance"),
    ("suparno.karmakar.1729@gmail.com", "VIEWER", "Customer Support", "Customer Operations"),
]
# The account owner also runs this project.
OWNER_PM_PROJECT = "Customer Account Management"

# (first, last, title, department, group, account role, hourly rate)
PEOPLE = [
    ("Catherine", "Moreau", "Chief Executive Officer", "Executive", "exec", "OWNER", 210),
    ("Vikram", "Rao", "Chief Technology Officer", "Executive", "exec", "OWNER", 190),
    ("Olivia", "Bennett", "Chief Operating Officer", "Executive", "exec", "MANAGER", 185),
    ("Samuel", "Adeyemi", "Chief Financial Officer", "Executive", "exec", "VIEWER", 185),
    ("Grace", "Kim", "VP, Customer Operations", "Executive", "exec", "MANAGER", 160),
    ("Rachel", "Goldberg", "Director, PMO", "PMO", "pmo", "MANAGER", 125),
    ("Kwame", "Mensah", "Senior Project Manager", "PMO", "pmo", "MANAGER", 105),
    ("Hannah", "Fischer", "Project Manager", "PMO", "pmo", "MANAGER", 95),
    ("Tomás", "Rivera", "Scrum Master", "PMO", "pmo", "MANAGER", 90),
    ("Aisha", "Siddiqui", "Business Analyst", "PMO", "pmo", "MEMBER", 85),
    ("Daniel", "Okafor", "Lead Software Engineer", "Software Engineering", "dev", "MEMBER", 115),
    ("Mei Lin", "Chen", "Senior Full-Stack Developer", "Software Engineering", "dev", "MEMBER", 105),
    ("Arjun", "Patel", "Backend Developer", "Software Engineering", "dev", "MEMBER", 92),
    ("Sofia", "Marquez", "Frontend Developer", "Software Engineering", "dev", "MEMBER", 90),
    ("Liam", "O'Connell", "DevOps Engineer", "Software Engineering", "dev", "MEMBER", 100),
    ("Nadia", "Kowalski", "QA Lead", "Quality Assurance", "qa", "MEMBER", 90),
    ("Ethan", "Brooks", "Senior QA Analyst", "Quality Assurance", "qa", "MEMBER", 80),
    ("Yuki", "Tanaka", "Test Automation Engineer", "Quality Assurance", "qa", "MEMBER", 85),
    ("Fatima", "Haddad", "QA Analyst", "Quality Assurance", "qa", "MEMBER", 70),
    ("Marcus", "Bell", "Performance Test Engineer", "Quality Assurance", "qa", "MEMBER", 85),
    ("Laura", "Mitchell", "HR Manager", "People & Workplace", "hr", "MANAGER", 80),
    ("Jamal", "Carter", "HR Generalist", "People & Workplace", "hr", "MEMBER", 60),
    ("Elena", "Popescu", "Office & Facilities Manager", "People & Workplace", "hr", "MANAGER", 62),
    ("Ben", "Novak", "IT Support Specialist", "People & Workplace", "hr", "MEMBER", 58),
    ("Priscilla", "Nguyen", "Administrative Coordinator", "People & Workplace", "hr", "MEMBER", 48),
]

# Outside resources with no login: (name, role, department, hourly rate)
CONTRACTORS = [
    ("Fibre Build Crew A", "Contractor - Fibre Installation", "Field Operations", 140),
    ("Fibre Build Crew B", "Contractor - Fibre Installation", "Field Operations", 140),
    ("SIP Carrier Engineering", "Vendor - SIP Trunking", "Vendors", 165),
    ("Core Network Vendor TAC", "Vendor - Core Routing", "Vendors", 180),
    ("Studio Pine UX", "Contract UX Designer", "Vendors", 120),
]

SKILLS = [
    ("Python / FastAPI", "Engineering"), ("React", "Engineering"), ("PostgreSQL", "Engineering"),
    ("AWS", "Engineering"), ("CI/CD", "Engineering"), ("SIP / VoIP", "Telecom"), ("GPON / Fibre", "Telecom"),
    ("IP Routing (BGP/OSPF)", "Telecom"), ("Test Automation (Playwright)", "Quality"),
    ("Performance Testing (k6)", "Quality"), ("Manual & Exploratory Testing", "Quality"),
    ("Project Management", "Delivery"), ("Agile / Scrum", "Delivery"), ("Business Analysis", "Delivery"),
    ("Financial Planning", "Business"), ("HR Administration", "People"), ("Facilities Management", "People"),
    ("Endpoint Management", "IT"), ("Security & Compliance", "IT"), ("UX Design", "Design"),
]

PERSON_SKILLS = {
    "Vikram Rao": [("AWS", "EXPERT"), ("IP Routing (BGP/OSPF)", "ADVANCED"), ("SIP / VoIP", "ADVANCED")],
    "Samuel Adeyemi": [("Financial Planning", "EXPERT")],
    "Rachel Goldberg": [("Project Management", "EXPERT"), ("Agile / Scrum", "ADVANCED")],
    "Kwame Mensah": [("Project Management", "EXPERT"), ("GPON / Fibre", "INTERMEDIATE")],
    "Hannah Fischer": [("Project Management", "ADVANCED"), ("SIP / VoIP", "INTERMEDIATE")],
    "Tomás Rivera": [("Agile / Scrum", "EXPERT"), ("Project Management", "INTERMEDIATE")],
    "Aisha Siddiqui": [("Business Analysis", "EXPERT"), ("UX Design", "INTERMEDIATE")],
    "Daniel Okafor": [("Python / FastAPI", "EXPERT"), ("PostgreSQL", "EXPERT"), ("AWS", "ADVANCED")],
    "Mei Lin Chen": [("React", "EXPERT"), ("Python / FastAPI", "ADVANCED"), ("PostgreSQL", "ADVANCED")],
    "Arjun Patel": [("Python / FastAPI", "ADVANCED"), ("SIP / VoIP", "INTERMEDIATE"), ("PostgreSQL", "INTERMEDIATE")],
    "Sofia Marquez": [("React", "ADVANCED"), ("UX Design", "INTERMEDIATE")],
    "Liam O'Connell": [("AWS", "EXPERT"), ("CI/CD", "EXPERT"), ("Endpoint Management", "INTERMEDIATE")],
    "Nadia Kowalski": [("Test Automation (Playwright)", "ADVANCED"), ("Manual & Exploratory Testing", "EXPERT")],
    "Ethan Brooks": [("Manual & Exploratory Testing", "EXPERT"), ("Test Automation (Playwright)", "INTERMEDIATE")],
    "Yuki Tanaka": [("Test Automation (Playwright)", "EXPERT"), ("CI/CD", "ADVANCED")],
    "Fatima Haddad": [("Manual & Exploratory Testing", "ADVANCED")],
    "Marcus Bell": [("Performance Testing (k6)", "EXPERT"), ("SIP / VoIP", "INTERMEDIATE")],
    "Laura Mitchell": [("HR Administration", "EXPERT")],
    "Jamal Carter": [("HR Administration", "ADVANCED")],
    "Elena Popescu": [("Facilities Management", "EXPERT")],
    "Ben Novak": [("Endpoint Management", "ADVANCED"), ("Security & Compliance", "INTERMEDIATE")],
    "Priscilla Nguyen": [("Facilities Management", "INTERMEDIATE")],
    "Fibre Build Crew A": [("GPON / Fibre", "EXPERT")],
    "Fibre Build Crew B": [("GPON / Fibre", "ADVANCED")],
    "SIP Carrier Engineering": [("SIP / VoIP", "EXPERT")],
    "Core Network Vendor TAC": [("IP Routing (BGP/OSPF)", "EXPERT")],
    "Studio Pine UX": [("UX Design", "EXPERT")],
}

DEVS = ["Daniel Okafor", "Mei Lin Chen", "Arjun Patel", "Sofia Marquez", "Liam O'Connell"]
QA = ["Nadia Kowalski", "Ethan Brooks", "Yuki Tanaka", "Fatima Haddad", "Marcus Bell"]
HR = ["Laura Mitchell", "Jamal Carter", "Elena Popescu", "Ben Novak", "Priscilla Nguyen"]

PORTFOLIOS = [
    ("Customer Platforms", "#2563eb", "Customer-facing and internal platforms: the new admin portal and self-service apps."),
    ("Network & Voice Infrastructure", "#059669", "Fibre build-out, VoIP modernization and network operations."),
    ("Compliance & Security", "#dc2626", "SOC 2, endpoint security and regulatory reporting."),
    ("Corporate & People", "#d97706", "Workplace, facilities and people operations."),
]

PROGRAMS = [
    ("Customer Platforms", "Admin Portal Rebuild",
     "Replace the 12-year-old Java admin tool used by support, billing and provisioning with a modern web portal."),
    ("Customer Platforms", "Self-Service Apps", "Customer mobile app and online sign-up."),
    ("Network & Voice Infrastructure", "Fibre Expansion", "Bring GPON fibre to 6,500 new premises."),
    ("Network & Voice Infrastructure", "VoIP Modernization", "Move business voice customers to the new SIP platform."),
    ("Network & Voice Infrastructure", "Network Operations", "Monitoring and core network upgrades."),
    ("Compliance & Security", "Security Program", "SOC 2 Type II and endpoint protection."),
    ("Compliance & Security", "Regulatory", "CRTC reporting and accessibility obligations."),
    ("Corporate & People", "Workplace & Facilities", "Office move and equipment."),
    ("Corporate & People", "People Operations", "HR systems, onboarding and training."),
]

# Agile projects list features; each becomes a work package with design, API, UI and test stories.
# Waterfall/hybrid projects list phases of tasks. Offsets are days from today.
PROJECTS = [
    dict(program="Admin Portal Rebuild", name="Portal Foundation & Single Sign-On", kind="portal", delivery="AGILE",
         pm="Tomás Rivera", start=-170, end=-20, status="Complete", budget=180000,
         features=["Okta SSO login for staff", "Role-based access control", "Audit log of admin actions",
                   "Global customer search", "Navigation shell & theming", "MFA enforcement & session timeout",
                   "Feature flag service", "Error monitoring & alerting"]),
    dict(program="Admin Portal Rebuild", name="Customer Account Management", kind="portal", delivery="AGILE",
         pm="Tomás Rivera", start=-120, end=45, status="Active", budget=240000,
         features=["Customer profile view", "Service address validation (Canada Post)", "Authorized contacts",
                   "Account notes & history timeline", "Merge duplicate accounts", "Suspend & restore service",
                   "Consent & marketing preferences (CASL)", "Bulk account updates from CSV",
                   "Customer communication log"]),
    dict(program="Admin Portal Rebuild", name="Billing & Invoicing Console", kind="portal", delivery="AGILE",
         pm="Kwame Mensah", start=-95, end=75, status="Active", budget=310000,
         features=["Invoice search & PDF view", "Payment history", "Refunds & account credits",
                   "GST/HST/PST rules by province", "Proration on plan change", "Dunning & collections workflow",
                   "Tokenized payment methods (PCI)", "Export to NetSuite", "Billing dispute tracking",
                   "Promotional pricing & bundles"]),
    dict(program="Admin Portal Rebuild", name="VoIP Service Provisioning", kind="portal", delivery="AGILE",
         pm="Hannah Fischer", start=-70, end=100, status="Active", budget=260000,
         features=["DID number inventory", "Number porting requests", "Hosted PBX extension setup",
                   "Voicemail & call forwarding settings", "E911 address registration", "SIP credential reset",
                   "Call detail records viewer", "Auto-attendant builder", "Fax-to-email (T.38) settings"]),
    dict(program="Admin Portal Rebuild", name="Internet Service Provisioning", kind="portal", delivery="AGILE",
         pm="Kwame Mensah", start=-45, end=130, status="Active", budget=255000,
         features=["Plan catalogue management", "ONT & modem activation", "Speed tier changes",
                   "Static IP assignment", "Outage & maintenance notices", "Line diagnostics (ping & traceroute)",
                   "Installation appointment booking", "Equipment returns tracking"]),
    dict(program="Admin Portal Rebuild", name="Support Ticketing Integration", kind="portal", delivery="AGILE",
         pm="Tomás Rivera", start=-20, end=120, status="Active", budget=140000,
         features=["Two-way ticket sync with Zendesk", "Create ticket from customer view", "SLA timers & escalations",
                   "Field technician dispatch", "Knowledge base suggestions", "CSAT survey results"]),
    dict(program="Admin Portal Rebuild", name="Reporting & Analytics Dashboards", kind="portal", delivery="AGILE",
         pm="Hannah Fischer", start=30, end=170, status="Active", budget=120000,
         features=["Subscriber growth dashboard", "Churn & retention report", "ARPU by product line",
                   "Outage impact report", "Support volume & SLA report", "Scheduled report emails"]),
    dict(program="Admin Portal Rebuild", name="Legacy Admin Tool Migration & Cutover", kind="cutover",
         delivery="HYBRID", pm="Rachel Goldberg", start=-30, end=190, status="Active", budget=160000,
         phases=[
             ("Planning", ["Inventory legacy admin tool screens & reports", "Data mapping: legacy CRM to new schema",
                           "Cutover strategy & rollback plan", "Communication plan for support & billing teams"]),
             ("Migration build", [f"Migration script: {entity}" for entity in (
                 "customers & contacts", "service addresses", "subscriptions & plans", "invoices & payments",
                 "phone numbers & PBX config", "equipment records", "ticket history")]),
             ("Rehearsal", ["Dry run 1 (10% sample)", "Dry run 2 (full copy)", "Data reconciliation report",
                            "Dry run 3 & sign-off", "Cutover rehearsal (timed)"]),
             ("Training", [f"Agent training session {n}" for n in range(1, 7)] + ["Train-the-trainer for team leads"]),
             ("Go-live", ["Freeze legacy tool changes", "Production cutover weekend", "Go-live", "Hypercare week 1",
                          "Hypercare week 2", "Decommission legacy admin tool"]),
         ]),
    dict(program="Self-Service Apps", name="Customer Mobile App v3", kind="app", delivery="AGILE",
         pm="Tomás Rivera", start=-60, end=110, status="Active", budget=150000,
         features=["Biometric login", "Data usage meter", "Pay my bill", "Push alerts for outages",
                   "Plan upgrade flow", "Chat with support"]),
    dict(program="Self-Service Apps", name="Online Sign-Up Redesign", kind="app", delivery="AGILE",
         pm="Hannah Fischer", start=-150, end=-35, status="Complete", budget=90000,
         features=["Address serviceability check", "Plan comparison page", "Soft credit check (Equifax)",
                   "Installation scheduling", "Order confirmation emails"]),
    dict(program="Fibre Expansion", name="North District Fibre Build", kind="network", delivery="WATERFALL",
         pm="Kwame Mensah", start=-200, end=140, status="Active", budget=2400000,
         phases=[
             ("Initiation", ["Project charter approved", "Municipal stakeholder engagement plan"]),
             ("Planning", ["Route survey & GIS design", "Municipal road-occupancy permits", "Hydro pole attachment applications",
                           "Bill of materials", "Splice & cabinet locations"]),
             ("Procurement", ["Order 96F & 288F fibre cable", "Order OLT chassis & ONTs", "Order splice enclosures",
                              "Contract civil works crews"]),
             ("Build", [f"Trenching & conduit - Segment {n}" for n in range(1, 9)]
              + [f"Fibre pull & splice - Segment {n}" for n in range(1, 9)]
              + ["Install street cabinets", "OLT install & commissioning"]),
             ("Testing", ["OTDR testing - all segments", "Light-level acceptance", "Service activation trials (50 homes)"]),
             ("Closeout", ["As-built drawings", "Handover to NOC", "Marketing launch to new premises", "Lessons learned"]),
         ]),
    dict(program="Fibre Expansion", name="Rural Fixed Wireless Pilot", kind="network", delivery="WATERFALL",
         pm="Kwame Mensah", start=-90, end=120, status="On Hold", budget=480000,
         phases=[
             ("Planning", ["Tower site survey (3 sites)", "Spectrum licence application (ISED)", "Propagation modelling"]),
             ("Procurement", ["Order radios & CPE kits", "Tower lease agreements"]),
             ("Build", ["Install radios - Site 1", "Install radios - Site 2", "Install radios - Site 3",
                        "Backhaul link commissioning"]),
             ("Pilot", ["Recruit 40 pilot households", "CPE installs", "Pilot performance review"]),
         ]),
    dict(program="VoIP Modernization", name="SIP Trunk Migration", kind="voip", delivery="WATERFALL",
         pm="Hannah Fischer", start=-140, end=60, status="Active", budget=520000,
         phases=[
             ("Discovery", ["Inventory business SIP trunks", "Select new carrier", "Carrier contract & SLAs"]),
             ("Design", ["Number porting plan", "SBC high-availability design", "Codec & transcoding policy"]),
             ("Build", ["Deploy SBC pair (Toronto & Montreal)", "Provision trunks on carrier", "Billing feed for new CDRs"]),
             ("Migration", [f"Port numbers - Wave {n}" for n in range(1, 8)]),
             ("Testing", ["Call quality testing (MOS > 4.0)", "Failover testing", "E911 routing verification"]),
             ("Closeout", ["Decommission legacy trunks", "Handover to NOC"]),
         ]),
    dict(program="VoIP Modernization", name="Hosted PBX Feature Pack", kind="voip", delivery="HYBRID",
         pm="Hannah Fischer", start=-40, end=110, status="Active", budget=210000,
         phases=[
             ("Discovery", ["Customer feature survey", "Prioritize feature backlog"]),
             ("Build", ["Call recording", "Hunt groups & ring strategies", "Microsoft Teams direct routing",
                        "Visual voicemail", "Call queue wallboards", "Mobile softphone app"]),
             ("Testing", ["Feature regression suite", "Load test 2,000 concurrent calls"]),
             ("Launch", ["Pricing & packaging", "Sales enablement", "Launch to business customers"]),
         ]),
    dict(program="VoIP Modernization", name="E911 & NG911 Readiness", kind="voip", delivery="WATERFALL",
         pm="Kwame Mensah", start=-10, end=160, status="Active", budget=175000,
         phases=[
             ("Assessment", ["CRTC NG911 obligations review", "Gap analysis of current E911 flow"]),
             ("Design", ["Dynamic location (Ray Baum-style) design", "ESInet connectivity design"]),
             ("Build", ["Location validation service", "PSAP routing updates", "Customer address audit"]),
             ("Testing", ["Test calls with 911 operator", "Compliance filing"]),
         ]),
    dict(program="Network Operations", name="NOC Monitoring Upgrade", kind="network", delivery="HYBRID",
         pm="Hannah Fischer", start=-100, end=50, status="Active", budget=230000,
         phases=[
             ("Planning", ["Monitoring requirements workshop", "Tool selection (Grafana stack)"]),
             ("Build", ["Prometheus exporters for OLTs", "SNMP collection for core routers", "SBC call metrics",
                        "Alert routing to on-call", "Customer-impact correlation", "NOC wall dashboards"]),
             ("Rollout", ["Run old and new monitoring in parallel", "NOC staff training", "Retire legacy Nagios"]),
         ]),
    dict(program="Network Operations", name="Core Router Refresh", kind="network", delivery="WATERFALL",
         pm="Kwame Mensah", start=-260, end=-60, status="Complete", budget=890000,
         phases=[
             ("Planning", ["Capacity forecast", "Vendor RFP", "Migration runbook"]),
             ("Execution", ["Install routers - Toronto POP", "Install routers - Montreal POP", "BGP migration",
                            "Traffic cutover", "Decommission old routers"]),
             ("Closeout", ["Final acceptance", "Lessons learned"]),
         ]),
    dict(program="Security Program", name="SOC 2 Type II Readiness", kind="security", delivery="WATERFALL",
         pm="Rachel Goldberg", start=-130, end=150, status="Active", budget=195000,
         phases=[
             ("Assessment", ["Select audit firm", "Readiness gap assessment", "Define system boundary"]),
             ("Policies", [f"Policy: {name}" for name in (
                 "Information Security", "Access Control", "Change Management", "Incident Response",
                 "Vendor Management", "Business Continuity", "Data Retention", "Acceptable Use")]),
             ("Controls", ["Quarterly access reviews", "Centralized logging (CloudTrail & SIEM)",
                           "Vulnerability scanning", "Background check process", "Security awareness training"]),
             ("Audit", ["Observation window starts", "Evidence collection", "Auditor fieldwork", "SOC 2 report issued"]),
         ]),
    dict(program="Security Program", name="Endpoint Security Rollout", kind="security", delivery="WATERFALL",
         pm="Rachel Goldberg", start=-50, end=70, status="Active", budget=60000,
         phases=[
             ("Planning", ["EDR vendor selection", "Device inventory"]),
             ("Rollout", [f"Deploy EDR & disk encryption - {group}" for group in (
                 "IT pilot", "Engineering", "PMO & QA", "Executive", "People & Workplace", "Field laptops")]),
             ("Closeout", ["Compliance dashboard", "Retire old antivirus"]),
         ]),
    dict(program="Regulatory", name="CRTC Reporting Automation", kind="security", delivery="HYBRID",
         pm="Kwame Mensah", start=-15, end=130, status="Active", budget=85000,
         phases=[
             ("Discovery", ["Map CRTC data collection requirements", "Identify source systems"]),
             ("Build", ["Subscriber counts by speed tier", "Broadband availability by hexagon", "Accessibility report",
                        "Annual telecom fees calculation"]),
             ("Validation", ["Finance reconciliation", "Submit annual return"]),
         ]),
    dict(program="Workplace & Facilities", name="Head Office Move - Phase 2", kind="corporate", delivery="WATERFALL",
         pm="Elena Popescu", start=-80, end=40, status="Active", budget=340000,
         phases=[
             ("Planning", ["Floor plan & seating chart", "Lease amendment signed", "Movers RFQ"]),
             ("Fit-out", ["Network cabling & Wi-Fi survey", "Meeting room AV install", "Furniture delivery",
                          "Security badge system"]),
             ("Move", ["Pack & label (by department)", "Move weekend", "IT desk-side support day",
                       "Old office handback"]),
         ]),
    dict(program="Workplace & Facilities", name="Hybrid Work Equipment Refresh", kind="corporate", delivery="WATERFALL",
         pm="Elena Popescu", start=-25, end=55, status="Active", budget=75000,
         phases=[
             ("Planning", ["Equipment standards", "Order laptops & docks", "Order headsets for support agents"]),
             ("Rollout", [f"Equipment handout - {group}" for group in ("Support team", "Engineering", "Everyone else")]
              + ["Recycle old equipment"]),
         ]),
    dict(program="People Operations", name="HRIS Implementation", kind="corporate", delivery="WATERFALL",
         pm="Laura Mitchell", start=-110, end=80, status="Active", budget=120000,
         phases=[
             ("Selection", ["HRIS requirements", "Vendor demos", "Contract signed"]),
             ("Configuration", ["Org structure & job codes", "Time-off policies", "Payroll integration",
                                "Benefits enrolment setup", "Employee data import"]),
             ("Launch", ["Manager training", "Employee self-service launch", "Retire spreadsheets"]),
         ]),
    dict(program="People Operations", name="Onboarding & Annual Compliance Training", kind="corporate",
         delivery="WATERFALL", pm="Laura Mitchell", start=-35, end=95, status="Active", budget=30000,
         phases=[
             ("Design", ["New-hire onboarding checklist", "Choose LMS", "Accessibility (AODA) training module"]),
             ("Rollout", ["Privacy & PIPEDA training", "Workplace harassment training", "Security awareness module",
                          "Track completion & reminders"]),
         ]),
]

# Who works on what, by project kind: PMs come from the project, these are the doers.
TEAMS = {
    "portal": dict(api=["Daniel Okafor", "Arjun Patel", "Mei Lin Chen"], ui=["Sofia Marquez", "Mei Lin Chen"],
                   design=["Aisha Siddiqui", "Studio Pine UX"], test=["Ethan Brooks", "Yuki Tanaka", "Fatima Haddad"]),
    "app": dict(api=["Arjun Patel", "Daniel Okafor"], ui=["Sofia Marquez", "Mei Lin Chen"],
                design=["Studio Pine UX", "Aisha Siddiqui"], test=["Yuki Tanaka", "Fatima Haddad"]),
    "cutover": dict(default=["Aisha Siddiqui", "Daniel Okafor", "Mei Lin Chen", "Liam O'Connell", "Nadia Kowalski",
                             "Grace Kim"]),
    "network": dict(default=["Fibre Build Crew A", "Fibre Build Crew B", "Core Network Vendor TAC", "Liam O'Connell",
                             "Vikram Rao"]),
    "voip": dict(default=["Arjun Patel", "Daniel Okafor", "SIP Carrier Engineering", "Marcus Bell", "Liam O'Connell"]),
    "security": dict(default=["Liam O'Connell", "Ben Novak", "Nadia Kowalski", "Samuel Adeyemi", "Laura Mitchell"]),
    "corporate": dict(default=["Elena Popescu", "Priscilla Nguyen", "Ben Novak", "Jamal Carter", "Laura Mitchell"]),
}

RAID = {
    "portal": dict(
        risks=[
            ("Legacy CRM data quality is worse than sampled", "Duplicate and incomplete records in the legacy CRM",
             "Migration rework and delayed cutover"),
            ("Key backend knowledge concentrated in one developer", "Daniel holds most of the legacy integration knowledge",
             "Delivery slows if he is unavailable"),
            ("PCI scope creep from new payment screens", "Card data could touch portal servers",
             "Audit cost and timeline increase"),
            ("Zendesk API rate limits under peak load", "Monday morning ticket spikes", "Sync delays for agents"),
            ("Support agents resist the new workflow", "Muscle memory from the old tool", "Lower adoption and longer calls"),
            ("Okta licence costs exceed budget", "More admin users than planned", "Budget overrun"),
            ("Third-party tax engine changes pricing", "Vendor renewal in Q1", "Unplanned cost"),
        ],
        issues=[
            ("Staging environment unstable after DB upgrade", "Intermittent 502s on staging since Postgres upgrade."),
            ("Province tax rounding differs from legacy invoices", "QC and NS invoices differ by 1 cent on some lines."),
            ("Address validation API rejects rural route addresses", "Canada Post API returns no match for RR addresses."),
            ("Design system tokens out of sync with Figma", "Spacing and colour tokens drifted."),
            ("Flaky end-to-end tests in CI", "About 6% of Playwright runs fail on the porting flow."),
            ("Legacy tool exports truncate notes over 4,000 chars", "Long account notes are cut off in the export."),
        ],
        assumptions=["Legacy admin tool stays read-only during migration",
                     "Support team leads are available for weekly UAT sessions",
                     "Okta remains the identity provider for staff",
                     "NetSuite API limits are sufficient for nightly export"],
        decisions=[
            ("Build the portal as a React SPA on the existing FastAPI backend",
             [("React SPA + FastAPI", "Team knows the stack", "SPA SEO not needed", "Medium"),
              ("Buy a CRM admin suite", "Faster start", "Licence cost; poor fit for provisioning", "Low")]),
            ("Use Zendesk as the system of record for tickets",
             [("Zendesk stays master", "No agent retraining", "Two-way sync complexity", "Medium"),
              ("Build ticketing into the portal", "One tool", "Large scope", "High")]),
            ("Release behind feature flags, team by team", []),
        ],
        dependencies=[("Okta tenant configured by IT", "IT Support"),
                      ("NetSuite sandbox access from Finance", "Finance"),
                      ("Carrier porting API credentials", "SIP Carrier Engineering")],
        change_requests=["Add French-language UI for Quebec agents", "Bulk credit tool for outage compensation",
                         "Show network outage banner on customer view"],
    ),
    "network": dict(
        risks=[("Permit approvals delayed by the municipality", "Road occupancy permits backlog", "Build start slips"),
               ("Frost-heave season limits trenching", "Ground freeze from December", "Work pauses for 3 months"),
               ("Fibre cable lead times increase", "Global supply constraints", "Segments 6-8 delayed"),
               ("Contractor crew availability", "Competing builds in the region", "Slower build rate"),
               ("Hydro pole make-ready costs higher than estimated", "Old poles need replacement", "Budget overrun")],
        issues=[("Segment 3 hit undocumented gas line", "Work stopped pending locate and redesign."),
                ("OLT firmware bug drops ONTs after 30 days", "Vendor ticket open; workaround is a scheduled reboot."),
                ("Splice loss above spec on Segment 2", "Re-splicing required at two enclosures.")],
        assumptions=["Take-rate of 35% within 12 months", "Municipality approves permits within 6 weeks",
                     "Existing conduit on Main St is reusable"],
        decisions=[("Use aerial fibre where poles allow", [("Aerial where possible", "Cheaper, faster", "Storm exposure", "Low"),
                                                           ("Underground everywhere", "Resilient", "2x cost", "High")])],
        dependencies=[("Hydro pole attachment approvals", "Local Hydro"), ("ISED spectrum licence", "ISED")],
        change_requests=["Extend build to Lakeview subdivision", "Add 20 business premises on Industrial Rd"],
    ),
    "voip": dict(
        risks=[("Number porting rejections from losing carriers", "Mismatched account data", "Customers stuck on old trunks"),
               ("Call quality issues over customer broadband", "Jitter on low-tier plans", "Complaints and churn"),
               ("E911 address data incomplete", "Legacy records missing suite numbers", "Compliance exposure")],
        issues=[("One-way audio on some Polycom handsets", "NAT keep-alive setting needed on firmware 7.x."),
                ("Carrier CDR feed missing call duration", "Billing feed fails validation for Wave 3 numbers.")],
        assumptions=["New carrier honours porting SLAs of 5 business days", "Customers keep existing handsets"],
        decisions=[("Port numbers in waves of 300", [("Waves of 300", "Manageable support load", "Longer overall", "Medium"),
                                                     ("Big-bang weekend", "Fast", "High risk", "High")])],
        dependencies=[("Carrier SBC interconnect", "SIP Carrier Engineering"), ("Billing CDR mapping", "Finance")],
        change_requests=["Add Teams direct routing to scope", "Offer porting on weekends"],
    ),
    "security": dict(
        risks=[("Evidence collection effort underestimated", "Manual screenshots for many controls", "Audit delay"),
               ("Vendors without SOC reports", "Small vendors lack attestations", "Exceptions in report")],
        issues=[("Access reviews overdue for two systems", "Legacy admin tool and NetSuite reviews not done."),
                ("EDR agent conflicts with VPN client", "Engineering laptops lose VPN after EDR install.")],
        assumptions=["Audit firm available for fieldwork in the planned window"],
        decisions=[("Use a compliance automation platform", [("Vanta-style platform", "Automated evidence", "Annual cost", "Low"),
                                                            ("Spreadsheets", "Free", "Manual effort", "High")])],
        dependencies=[("HRIS background check integration", "People Operations")],
        change_requests=["Include the new admin portal in SOC 2 scope"],
    ),
    "corporate": dict(
        risks=[("Furniture delivery delayed", "Supplier backlog", "Move date slips"),
               ("Low adoption of HRIS self-service", "Staff used to emailing HR", "Manual work continues")],
        issues=[("Wi-Fi dead zone in north meeting rooms", "Survey found weak coverage; extra AP needed."),
                ("Payroll integration field mismatch", "Vacation accrual codes differ between systems.")],
        assumptions=["Landlord completes base building work on time", "All staff complete training within 30 days"],
        decisions=[("Hot-desking for hybrid staff", [("Hot-desking", "Less space", "Less personal", "Low"),
                                                     ("Assigned desks", "Familiar", "Higher lease cost", "Medium")])],
        dependencies=[("Landlord base building works", "Landlord"), ("Payroll provider API access", "Payroll vendor")],
        change_requests=["Add a wellness room to the floor plan", "Extend HRIS to contractors"],
    ),
}
RAID["app"] = RAID["portal"]
RAID["cutover"] = RAID["portal"]

COMMENT_TEMPLATES = [
    "Picked this up today, should have a first pass by end of week.",
    "Blocked on the staging environment again - {mention} can you take a look?",
    "PR is up for review. {mention} could you review the API changes?",
    "Demoed this to the support team leads, they want the search results to show the service address too.",
    "Moved the finish date by two days, waiting on the vendor's answer.",
    "Testing found two edge cases around provincial tax - logged as issues.",
    "{mention} FYI this changes the cutover runbook.",
    "Done on my side. {mention} over to you for QA.",
    "Raised this in stand-up; we agreed to split it into two stories.",
    "Vendor confirmed the fix ships in next week's firmware.",
    "Customer feedback from the pilot is positive so far.",
    "{mention} do we have budget approval for this?",
    "Updated the acceptance criteria based on the workshop.",
    "Can we get this into the next sprint? It blocks the billing work.",
    "Retested on the latest build - passing now.",
]

HOLIDAYS = [
    ("New Year's Day", (1, 1)), ("Family Day", None), ("Good Friday", None), ("Victoria Day", None),
    ("Canada Day", (7, 1)), ("Civic Holiday", None), ("Labour Day", None), ("Thanksgiving", None),
    ("Remembrance Day", (11, 11)), ("Christmas Day", (12, 25)), ("Boxing Day", (12, 26)),
]


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def easter(year: int) -> date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def holidays_for(year: int) -> list[tuple[str, date]]:
    may_25 = date(year, 5, 25)
    victoria = may_25 - timedelta(days=(may_25.weekday() - 0) % 7 or 7) if may_25.weekday() != 0 else date(year, 5, 18)
    computed = {
        "Family Day": nth_weekday(year, 2, 0, 3),
        "Good Friday": easter(year) - timedelta(days=2),
        "Victoria Day": victoria,
        "Civic Holiday": nth_weekday(year, 8, 0, 1),
        "Labour Day": nth_weekday(year, 9, 0, 1),
        "Thanksgiving": nth_weekday(year, 10, 0, 2),
    }
    return [(name, date(year, *fixed) if fixed else computed[name]) for name, fixed in HOLIDAYS]


def ascii_slug(value: str) -> str:
    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return "".join(ch for ch in plain.lower() if ch.isalnum())


def add_workdays(start: date, days: int) -> date:
    current = start
    while current.weekday() >= 5:
        current += timedelta(days=1)
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def workdays_between(start: date, end: date) -> int:
    return sum(1 for n in range((end - start).days + 1) if (start + timedelta(days=n)).weekday() < 5)


def at(day: date, hour: int = 10) -> datetime:
    return datetime.combine(day, time(hour, 0), tzinfo=UTC)


class Seeder:
    def __init__(self, db: Session, rng: random.Random) -> None:
        self.db = db
        self.rng = rng
        self.today = date.today()
        self.now = datetime.now(UTC)
        self.counts: dict[str, int] = defaultdict(int)
        self.users: dict[str, User] = {}
        self.resources: dict[str, Resource] = {}
        self.skills: dict[str, Skill] = {}
        self.portfolios: dict[str, Portfolio] = {}
        self.programs: dict[str, Program] = {}
        self.projects: list[tuple[dict, Project]] = []
        self.tasks_by_project: dict[UUID, list[Task]] = defaultdict(list)
        self.assignees: dict[UUID, str] = {}
        self.project_team: dict[UUID, set[str]] = defaultdict(set)
        self.user_projects: dict[str, set[UUID]] = defaultdict(set)
        self.activity: list[ActivityLog] = []
        self.owner_names: list[str] = []
        self.sign_in_names: dict[str, str] = {}  # email -> name, for the SIGN_IN_USERS that exist
        self.pending: list[object] = []
        self.table_order = {table: index for index, table in enumerate(Base.metadata.sorted_tables)}

    # Helpers

    def add(self, obj, kind: str | None = None):
        self.pending.append(obj)
        self.counts[kind or type(obj).__name__] += 1
        return obj

    def flush(self) -> None:
        """Insert pending rows one table at a time, parents first.

        The models declare few relationships, so a single flush can't order inserts by foreign key.
        """
        by_table: dict[object, list[object]] = defaultdict(list)
        for obj in self.pending:
            by_table[obj.__table__].append(obj)
        for table in sorted(by_table, key=self.table_order.__getitem__):
            self.db.add_all(by_table[table])
            self.db.flush()
        self.pending.clear()

    def opt(self, entity_type: str, name: str, label: str) -> UUID:
        return self.options[(entity_type, name, normalize_key(label))]

    def created_at(self, start: date) -> datetime:
        """When a record was created: a little before it starts, never in the future."""
        moment = at(start - timedelta(days=self.rng.randint(3, 20)), self.rng.randint(8, 17))
        return min(moment, self.now - timedelta(hours=1))

    def log(self, entity_type: str, entity_id: UUID, action: str, when: datetime, actor: str | None,
            old: dict | None = None, new: dict | None = None) -> None:
        if when > self.now:
            return
        user = self.users.get(actor or "")
        self.activity.append(ActivityLog(
            id=uuid4(), account_id=self.account.id, entity_type=entity_type, entity_id=entity_id, action=action,
            old_values=old, new_values=new, created_by=user.id if user else None, created_at=when,
        ))

    def person_id(self, name: str | None) -> UUID | None:
        user = self.users.get(name or "")
        return user.id if user else None

    # Steps

    def run(self) -> Account:
        self.seed_account()
        self.seed_people()
        self.seed_skills()
        self.seed_hierarchy()
        self.flush()
        for spec, project in self.projects:
            if "features" in spec:
                self.seed_agile_project(spec, project)
            else:
                self.seed_phased_project(spec, project)
        self.seed_owner_work()
        self.flush()
        self.seed_raid()
        self.seed_comments()
        self.seed_time_off_and_holidays()
        self.seed_access_grants()
        for entry in self.activity:
            self.add(entry)
        self.flush()
        return self.account

    def seed_account(self) -> None:
        self.account = self.add(Account(id=uuid4(), name=ACCOUNT_NAME, slug=ACCOUNT_SLUG))
        self.flush()
        AccountSettingsRepository(self.db).create_default(self.account.id)
        OptionService(self.db).seed_defaults_for_account(self.account.id)
        self.db.flush()
        rows = self.db.execute(
            select(OptionSet.entity_type, OptionSet.name, OptionValue.value, OptionValue.id)
            .join(OptionValue, OptionValue.option_set_id == OptionSet.id)
            .where(OptionSet.account_id == self.account.id)
        ).all()
        self.options = {(entity, name, value): option_id for entity, name, value, option_id in rows}

    def seed_people(self) -> None:
        for first, last, title, department, group, role, rate in PEOPLE:
            full_name = f"{first} {last}"
            email = f"{ascii_slug(first)}.{ascii_slug(last)}@{EMAIL_DOMAIN}"
            user = self.db.scalar(select(User).where(User.email == email))
            if user is None:
                user = self.add(User(id=uuid4(), email=email, full_name=full_name, first_name=first, last_name=last,
                                     title=title, department=department, company=ACCOUNT_NAME, is_active=True))
            self.users[full_name] = user
            self.add(AccountMember(account_id=self.account.id, user_id=user.id, role=role))
            self.flush()
            resource = ensure_member_resource(self.db, account_id=self.account.id, user=user)
            resource.role, resource.department = title, department
            resource.hourly_rate = Decimal(rate)
            resource.weekly_capacity_hours = Decimal("40.00")
            resource.non_project_hours = Decimal("10.00" if group in ("exec", "hr") else "4.00")
            self.resources[full_name] = resource
            self.counts["Resource"] += 1

        for email, role, title, department in SIGN_IN_USERS:
            user = self.db.scalar(select(User).where(User.email == email.lower()))
            if user is None:
                print(f"  ! {email} has no user yet (sign in once first); not added.")
                continue
            # A user belongs to one account only.
            self.db.execute(delete(AccessGrant).where(AccessGrant.user_id == user.id))
            self.db.execute(delete(AccountMember).where(AccountMember.user_id == user.id))
            self.add(AccountMember(account_id=self.account.id, user_id=user.id, role=role))
            self.flush()
            resource = ensure_member_resource(self.db, account_id=self.account.id, user=user)
            resource.role, resource.department = title, department
            name = resource.name
            self.users[name], self.resources[name] = user, resource
            self.sign_in_names[email] = name
            if role == AccountMemberRole.OWNER.value:
                self.owner_names.append(name)

        for name, role, department, rate in CONTRACTORS:
            self.resources[name] = self.add(Resource(
                id=uuid4(), account_id=self.account.id, name=name, role=role, department=department,
                hourly_rate=Decimal(rate), weekly_capacity_hours=Decimal("40.00"), non_project_hours=Decimal("0"),
            ))

    def seed_skills(self) -> None:
        for name, category in SKILLS:
            self.skills[name] = self.add(Skill(id=uuid4(), account_id=self.account.id, name=name, category=category))
        for person, skills in PERSON_SKILLS.items():
            for skill, proficiency in skills:
                self.add(ResourceSkill(account_id=self.account.id, resource_id=self.resources[person].id,
                                       skill_id=self.skills[skill].id, proficiency=proficiency))

    def seed_hierarchy(self) -> None:
        sponsor = self.users["Catherine Moreau"].id
        for name, color, description in PORTFOLIOS:
            self.portfolios[name] = self.add(Portfolio(
                id=uuid4(), account_id=self.account.id, name=name, description=description, color=color,
                status_id=self.opt("PORTFOLIO", "STATUS", "Active"), start_date=self.today - timedelta(days=270),
                target_end_date=self.today + timedelta(days=200), created_by=sponsor,
            ))
        for portfolio, name, description in PROGRAMS:
            specs = [spec for spec in PROJECTS if spec["program"] == name]
            self.programs[name] = self.add(Program(
                id=uuid4(), account_id=self.account.id, portfolio_id=self.portfolios[portfolio].id, name=name,
                description=description, status_id=self.opt("PROGRAM", "STATUS", "Active"),
                start_date=self.today + timedelta(days=min(s["start"] for s in specs)),
                target_end_date=self.today + timedelta(days=max(s["end"] for s in specs)), created_by=sponsor,
            ))
        for spec in PROJECTS:
            program = self.programs[spec["program"]]
            start = self.today + timedelta(days=spec["start"])
            project = self.add(Project(
                id=uuid4(), account_id=self.account.id, portfolio_id=program.portfolio_id, program_id=program.id,
                name=spec["name"], description=f"{spec['name']} - part of the {spec['program']} program.",
                delivery_type=spec["delivery"], status_id=self.opt("PROJECT", "STATUS", spec["status"]),
                start_date=start, target_end_date=self.today + timedelta(days=spec["end"]),
                budget=Decimal(spec["budget"]), project_manager=spec["pm"], created_by=self.person_id(spec["pm"]),
                created_at=self.created_at(start),
            ))
            if spec["name"] == OWNER_PM_PROJECT and self.owner_names:
                project.project_manager = self.owner_names[0]
            self.projects.append((spec, project))
            self.project_team[project.id].add(spec["pm"])
            self.log("PROJECT", project.id, "CREATED", project.created_at, spec["pm"], new={"name": project.name})

    # Tasks

    def task_status(self, spec: dict, start: date, finish: date) -> tuple[str, Decimal]:
        if spec["status"] == "Complete" or finish < self.today:
            roll = self.rng.random()
            if spec["status"] != "Complete" and roll > 0.9:
                return ("Blocked" if roll > 0.97 else "In Progress"), Decimal(self.rng.choice([60, 75, 90]))
            return "Complete", Decimal(100)
        if start <= self.today:
            if spec["status"] == "On Hold" or self.rng.random() < 0.12:
                return "Blocked", Decimal(self.rng.choice([10, 25, 40]))
            done = workdays_between(start, self.today) / max(workdays_between(start, finish), 1)
            return "In Progress", Decimal(min(95, max(5, round(done * 20) * 5)))
        return "Not Started", Decimal(0)

    def make_task(self, spec: dict, project: Project, name: str, start: date, finish: date, *, kind: str,
                  sort: int, parent: Task | None = None, assignee: str | None = None, sprint: Sprint | None = None,
                  points: int | None = None, description: str | None = None) -> Task:
        status, percent = self.task_status(spec, start, finish)
        if kind == "Milestone":
            percent = Decimal(100) if status == "Complete" else Decimal(0)
        task = self.add(Task(
            id=uuid4(), account_id=self.account.id, project_id=project.id, parent_task_id=parent.id if parent else None,
            sprint_id=sprint.id if sprint else None, task_type_id=self.opt("TASK", "TYPE", kind),
            status_id=self.opt("TASK", "STATUS", status),
            priority_id=self.opt("TASK", "PRIORITY", self.rng.choices(["Low", "Medium", "High", "Critical"], [2, 6, 3, 1])[0]),
            name=name, description=description, start_date=start, finish_date=finish,
            duration_days=Decimal(max(workdays_between(start, finish), 1) if kind != "Milestone" else 0),
            story_points=points, percent_complete=percent, sort_order=Decimal(sort),
            created_by=self.person_id(spec["pm"]), created_at=self.created_at(start),
        ))
        self.tasks_by_project[project.id].append(task)
        self.log("TASK", task.id, "CREATED", task.created_at, spec["pm"], new={"name": name})
        # Work package dates and status are rolled up from their children later, so they get no history.
        if status != "Not Started" and kind != "Work Package":
            self.log("TASK", task.id, "STATUS_CHANGED", at(start, 9), assignee or spec["pm"],
                     old={"status_id": str(self.opt("TASK", "STATUS", "Not Started"))},
                     new={"status_id": str(self.opt("TASK", "STATUS", "In Progress"))})
        if status in ("Complete", "Blocked") and kind != "Work Package":
            when = at(finish, 16) if status == "Complete" else at(min(self.today, finish), 11)
            self.log("TASK", task.id, "STATUS_CHANGED", when, assignee or spec["pm"],
                     old={"status_id": str(self.opt("TASK", "STATUS", "In Progress"))},
                     new={"status_id": str(task.status_id)})
        if assignee and kind != "Milestone":
            self.assign(project, task, assignee, start, finish)
        return task

    def assign(self, project: Project, task: Task, name: str, start: date, finish: date) -> None:
        resource = self.resources[name]
        user = self.users.get(name)
        hours = Decimal(max(workdays_between(start, finish), 1) * self.rng.choice([4, 5, 6, 6, 8]))
        self.add(TaskAssignment(account_id=self.account.id, task_id=task.id, user_id=user.id if user else None,
                                resource_name=None if user else name, hours=hours))
        self.add(ResourceAllocation(account_id=self.account.id, task_id=task.id, resource_id=resource.id,
                                    allocated_hours=hours, start_date=start, end_date=finish))
        self.assignees.setdefault(task.id, name)
        self.project_team[project.id].add(name)
        if user:
            self.user_projects[name].add(project.id)

    def link(self, task: Task, predecessor: Task) -> None:
        self.add(TaskPredecessor(account_id=self.account.id, task_id=task.id, predecessor_task_id=predecessor.id,
                                 dependency_type="FS", lag_days=0))

    def roll_up(self, spec: dict, parent: Task, children: list[Task]) -> None:
        parent.start_date = min(child.start_date for child in children)
        parent.finish_date = max(child.finish_date for child in children)
        parent.duration_days = Decimal(max(workdays_between(parent.start_date, parent.finish_date), 1))
        parent.percent_complete = Decimal(round(sum(c.percent_complete for c in children) / len(children)))
        statuses = {child.status_id for child in children}
        complete = self.opt("TASK", "STATUS", "Complete")
        not_started = self.opt("TASK", "STATUS", "Not Started")
        parent.status_id = (complete if statuses == {complete} else not_started if statuses == {not_started}
                            else self.opt("TASK", "STATUS", "In Progress"))

    def seed_agile_project(self, spec: dict, project: Project) -> None:
        team = TEAMS[spec["kind"]]
        start, end = project.start_date, project.target_end_date
        sprints: list[Sprint] = []
        sprint_start, number = start, 1
        while sprint_start < end:
            sprint_end = min(sprint_start + timedelta(days=13), end)
            state = "Completed" if sprint_end < self.today else "Active" if sprint_start <= self.today else "Planned"
            if spec["status"] == "Complete":
                state = "Completed"
            sprints.append(self.add(Sprint(
                id=uuid4(), account_id=self.account.id, project_id=project.id, name=f"Sprint {number}",
                goal=None, status_id=self.opt("SPRINT", "STATUS", state), start_date=sprint_start, end_date=sprint_end,
                created_by=self.person_id(spec["pm"]),
            )))
            sprint_start, number = sprint_end + timedelta(days=1), number + 1

        def sprint_for(day: date) -> Sprint | None:
            return next((s for s in sprints if s.start_date <= day <= s.end_date), sprints[-1] if sprints else None)

        features = spec["features"]
        span = workdays_between(start, end)
        step = max(int(span * 0.78 / len(features)), 4)
        sort = 0
        last_ui: list[Task] = []
        for index, feature in enumerate(features):
            sort += 1
            parent = self.make_task(spec, project, feature, start, start, kind="Work Package", sort=sort * 100)
            feature_start = add_workdays(start, index * step)
            plan = [
                (f"Design & acceptance criteria: {feature}", "design", self.rng.randint(2, 4)),
                (f"API: {feature}", "api", self.rng.randint(3, 7)),
                (f"UI: {feature}", "ui", self.rng.randint(3, 6)),
                (f"Automated tests: {feature}", "test", self.rng.randint(2, 4)),
            ]
            children, previous, cursor = [], None, feature_start
            for offset, (name, role, days) in enumerate(plan, start=1):
                finish = add_workdays(cursor, days - 1)
                child = self.make_task(
                    spec, project, name, cursor, finish, kind="Activity", sort=sort * 100 + offset, parent=parent,
                    assignee=self.rng.choice(team[role]), sprint=sprint_for(cursor),
                    points=self.rng.choice([1, 2, 3, 5, 5, 8]),
                    description=f"Part of '{feature}' in {project.name}.",
                )
                if previous is not None:
                    self.link(child, previous)
                if role in ("api", "ui"):
                    self.require_skill(child, "Python / FastAPI" if role == "api" else "React")
                children.append(child)
                previous, cursor = child, add_workdays(finish, 1)
            parent.sprint_id = None
            self.roll_up(spec, parent, children)
            last_ui.append(children[2])
            self.seed_requirement_and_tests(spec, project, feature, index + 1, children, sprint_for(children[-1].start_date))

        milestones = [("MVP demo to stakeholders", 0.45), ("UAT sign-off", 0.85), ("Production release", 1.0)]
        for offset, (name, fraction) in enumerate(milestones, start=1):
            day = add_workdays(start, int(span * fraction) - 1) if fraction < 1 else end
            milestone = self.make_task(spec, project, name, day, day, kind="Milestone", sort=(len(features) + offset) * 100)
            for ui in last_ui:
                if ui.finish_date <= day and offset == 1:
                    self.link(milestone, ui)

    def seed_phased_project(self, spec: dict, project: Project) -> None:
        team = TEAMS[spec["kind"]]["default"]
        phases = spec["phases"]
        start, end = project.start_date, project.target_end_date
        total = sum(len(tasks) for _, tasks in phases)
        span = workdays_between(start, end)
        per_task = max(span // max(total, 1), 2)
        cursor, previous, sort = start, None, 0
        for phase, names in phases:
            sort += 1
            parent = self.make_task(spec, project, phase, start, start, kind="Work Package", sort=sort * 100)
            children = []
            for offset, name in enumerate(names, start=1):
                is_milestone = name in ("Go-live", "Project charter approved", "SOC 2 report issued", "Move weekend",
                                        "Contract signed", "Launch to business customers", "Submit annual return")
                days = 0 if is_milestone else max(1, int(per_task * self.rng.uniform(0.8, 1.6)))
                finish = cursor if is_milestone else add_workdays(cursor, days - 1)
                finish = min(finish, end)
                child = self.make_task(
                    spec, project, name, min(cursor, finish), finish, kind="Milestone" if is_milestone else "Activity",
                    sort=sort * 100 + offset, parent=parent,
                    assignee=None if is_milestone else self.rng.choice(team),
                )
                if previous is not None and (offset == 1 or self.rng.random() < 0.6):
                    self.link(child, previous)
                children.append(child)
                previous = child
                # Some tasks run in parallel with the next one.
                cursor = finish if self.rng.random() < 0.3 else add_workdays(finish, 1)
                cursor = min(cursor, end)
            self.roll_up(spec, parent, children)

    def require_skill(self, task: Task, skill: str) -> None:
        if self.rng.random() < 0.5:
            self.add(TaskRequiredSkill(account_id=self.account.id, task_id=task.id, skill_id=self.skills[skill].id,
                                       required_proficiency="INTERMEDIATE"))

    def seed_requirement_and_tests(self, spec: dict, project: Project, feature: str, number: int,
                                   stories: list[Task], sprint: Sprint | None) -> None:
        design, _api, ui, tests = stories
        requirement = self.add(Requirement(
            id=uuid4(), account_id=self.account.id, project_id=project.id, program_id=project.program_id,
            requirement_number=f"REQ-{number:03d}", title=feature,
            description=(f"As a support or billing agent, I need {feature[0].lower() + feature[1:]} in the admin "
                         f"portal so that I no longer have to use the legacy tool." if spec["kind"] == "portal" else
                         f"As a customer, I want {feature[0].lower() + feature[1:]} in {project.name}."),
            priority_id=self.opt("REQUIREMENT", "PRIORITY", self.rng.choice(["Medium", "High", "High", "Critical"])),
            status_id=self.opt("REQUIREMENT", "STATUS", "Validated" if design.finish_date < self.today else "In Review"),
            entered_by_name="Aisha Siddiqui", entered_date=design.start_date, created_by=self.person_id("Aisha Siddiqui"),
        ))
        tester = self.assignees.get(tests.id, "Ethan Brooks")
        for index, (suffix, steps) in enumerate((
            ("happy path", "1. Sign in as an agent\n2. Open a customer\n3. Complete the flow"),
            ("validation errors", "1. Enter invalid data\n2. Submit\n3. Check the error messages"),
            ("permissions", "1. Sign in as a read-only agent\n2. Try the action\n3. Confirm it is blocked"),
        )[: self.rng.choice([2, 3, 3])], start=1):
            executed = ui.finish_date < self.today
            result = ("PASS" if self.rng.random() < 0.85 else "FAIL") if executed else "NOT_EXECUTED"
            self.add(TestCase(
                id=uuid4(), account_id=self.account.id, project_id=project.id, program_id=project.program_id,
                test_case_number=f"TC-{number:03d}-{index}", title=f"{feature} - {suffix}",
                priority_id=self.opt("TEST_CASE", "PRIORITY", "High" if index == 1 else "Medium"),
                sprint_id=sprint.id if sprint else None, requirement_id=requirement.id, designed_by_name=tester,
                executed_by_name=tester if executed else None,
                execution_date=min(add_workdays(ui.finish_date, 1), self.today) if executed else None,
                result=result, test_steps=steps, expected_results="The portal behaves as described in the requirement.",
                actual_results=("As expected." if result == "PASS" else "Error toast shown; see linked issue.")
                if executed else None,
                created_by=self.person_id(tester),
            ))

    def seed_owner_work(self) -> None:
        """Give the signed-in owners some work of their own, so their Home page isn't empty."""
        complete = self.opt("TASK", "STATUS", "Complete")
        for name in self.owner_names:
            open_tasks = [task for _, project in self.projects for task in self.tasks_by_project[project.id]
                          if task.status_id != complete and task.parent_task_id is not None
                          and abs((task.finish_date - self.today).days) <= 30]
            for task in self.rng.sample(open_tasks, min(8, len(open_tasks))):
                project = next(p for _, p in self.projects if p.id == task.project_id)
                self.assign(project, task, name, task.start_date, task.finish_date)

    # RAID, comments, people

    def seed_raid(self) -> None:
        for spec, project in self.projects:
            pools = RAID[spec["kind"]]
            team = sorted(self.project_team[project.id])
            people = [name for name in team if name in self.users] or [spec["pm"]]
            finished = spec["status"] == "Complete"
            base = dict(account_id=self.account.id, project_id=project.id, program_id=project.program_id)

            for number, (title, cause, effect) in enumerate(self.rng.sample(pools["risks"], min(len(pools["risks"]), self.rng.randint(3, 6))), 1):
                owner = self.rng.choice(people + self.owner_names[:1])
                status = "Closed" if finished else self.rng.choices(["Open", "Mitigating", "Closed", "Escalated"], [5, 3, 2, 1])[0]
                risk = self.add(Risk(id=uuid4(), risk_number=f"R-{number:03d}", title=title, cause=cause, effect=effect,
                                     priority_id=self.opt("RISK", "PRIORITY", self.rng.choice(["Medium", "High", "High", "Critical"])),
                                     status_id=self.opt("RISK", "STATUS", status), assigned_to=self.person_id(owner),
                                     target_resolution_date=self.today + timedelta(days=self.rng.randint(-10, 45)),
                                     created_by=self.person_id(spec["pm"]), created_at=self.created_at(project.start_date + timedelta(days=20)), **base))
                self.log("RISK", risk.id, "CREATED", risk.created_at, spec["pm"], new={"title": title})

            for number, (title, description) in enumerate(self.rng.sample(pools["issues"], min(len(pools["issues"]), self.rng.randint(2, 4))), 1):
                owner = self.rng.choice(people)
                status = "Closed" if finished else self.rng.choices(["Open", "In Progress", "Resolved", "Blocked"], [4, 4, 2, 1])[0]
                issue = self.add(Issue(id=uuid4(), issue_number=f"I-{number:03d}", title=title, description=description,
                                       priority_id=self.opt("ISSUE", "PRIORITY", self.rng.choice(["Low", "Medium", "High", "Critical"])),
                                       status_id=self.opt("ISSUE", "STATUS", status), assigned_to=self.person_id(owner),
                                       target_resolution_date=self.today + timedelta(days=self.rng.randint(-5, 21)),
                                       created_by=self.person_id(owner), created_at=self.created_at(min(self.today, project.start_date + timedelta(days=40))), **base))
                self.log("ISSUE", issue.id, "CREATED", issue.created_at, owner, new={"title": title})

            for number, description in enumerate(pools["assumptions"][: self.rng.randint(2, 4)], 1):
                self.add(Assumption(id=uuid4(), assumption_number=f"A-{number:03d}", description=description,
                                    status_id=self.opt("ASSUMPTION", "STATUS", self.rng.choice(["Open", "Validated", "In Review"])),
                                    entered_by=self.person_id(spec["pm"]), entered_by_name=spec["pm"], date_entered=project.start_date,
                                    assigned_to_name=self.rng.choice(people), target_date=self.today + timedelta(days=self.rng.randint(5, 60)), **base))

            for number, (title, options) in enumerate(pools["decisions"][: self.rng.randint(1, 3)], 1):
                started = project.start_date + timedelta(days=18) <= self.today
                status = ("Approved" if finished or (started and self.rng.random() < 0.6)
                          else self.rng.choice(["Pending", "In Review", "Open"]))
                decision = self.add(Decision(
                    id=uuid4(), decision_number=f"D-{number:03d}", title=title, decision_text=title,
                    description=f"Decision needed for {project.name}.", impact="Affects scope and timeline.",
                    priority_id=self.opt("DECISION", "PRIORITY", "High"), status_id=self.opt("DECISION", "STATUS", status),
                    proposed_date=project.start_date + timedelta(days=10), proposed_by_name=spec["pm"],
                    approved_date=project.start_date + timedelta(days=18) if status == "Approved" else None,
                    approved_by=self.person_id("Catherine Moreau") if status == "Approved" else None,
                    created_by=self.person_id(spec["pm"]), **base))
                for order, (option_title, pros, cons, effort) in enumerate(options):
                    self.add(DecisionOption(account_id=self.account.id, decision_id=decision.id, title=option_title,
                                            pros=pros, cons=cons, work_effort=effort, sort_order=order,
                                            created_by=self.person_id(spec["pm"])))

            for number, (title, owner) in enumerate(pools["dependencies"][: self.rng.randint(1, 3)], 1):
                self.add(Dependency(id=uuid4(), dependency_number=f"DEP-{number:03d}", title=title,
                                    description=f"{project.name} needs this from {owner}.",
                                    priority_id=self.opt("DEPENDENCY", "PRIORITY", "High"),
                                    status_id=self.opt("DEPENDENCY", "STATUS", "Closed" if finished else self.rng.choice(["Open", "In Review", "Closed"])),
                                    impact_of_delay="Downstream tasks slip day for day.", fallback_plan="Escalate to the program sponsor.",
                                    assigned_to_name=self.rng.choice(people), entered_by_name=spec["pm"],
                                    date_entered=project.start_date, target_date=self.today + timedelta(days=self.rng.randint(-5, 40)),
                                    created_by=self.person_id(spec["pm"]), **base))

            for number, title in enumerate(pools["change_requests"][: self.rng.randint(0, 2)], 1):
                status = "Implemented" if finished else self.rng.choice(["Open", "In Review", "Approved", "Draft"])
                proposer = self.rng.choice(["Grace Kim", "Olivia Bennett", spec["pm"]])
                self.add(ChangeRequest(id=uuid4(), change_request_number=f"CR-{number:03d}", title=title,
                                       description=f"Requested by {proposer}.",
                                       priority_id=self.opt("CHANGE_REQUEST", "PRIORITY", "Medium"),
                                       status_id=self.opt("CHANGE_REQUEST", "STATUS", status), proposed_by_name=proposer,
                                       proposed_date=self.today - timedelta(days=self.rng.randint(2, 30)),
                                       created_by=self.person_id(proposer), **base))

    def seed_comments(self) -> None:
        complete = self.opt("TASK", "STATUS", "Complete")
        candidates = [task for _, project in self.projects for task in self.tasks_by_project[project.id]
                      if task.parent_task_id is not None and task.start_date <= self.today
                      and (task.status_id != complete or task.finish_date > self.today - timedelta(days=30))]
        for task in self.rng.sample(candidates, min(len(candidates), 320)):
            team = [name for name in self.project_team[task.project_id] if name in self.users]
            if not team:
                continue
            parent_id = None
            for _ in range(self.rng.choice([1, 1, 2, 3])):
                author = self.assignees.get(task.id) if task.id in self.assignees and self.assignees[task.id] in self.users else self.rng.choice(team)
                others = [name for name in team + self.owner_names if name != author]
                mention = self.rng.choice(others) if others else None
                template = self.rng.choice(COMMENT_TEMPLATES)
                if "{mention}" in template and mention is None:
                    continue
                body = template.format(mention=f"@{mention}")
                start = at(task.start_date, 9)
                when = start + (min(self.now, at(task.finish_date, 17)) - start) * self.rng.random()
                comment = self.add(Comment(id=uuid4(), account_id=self.account.id, entity_type="TASK", entity_id=task.id,
                                           body=body, parent_comment_id=parent_id, created_by=self.users[author].id,
                                           created_at=min(when, self.now)))
                if "{mention}" in template:
                    self.add(CommentMention(account_id=self.account.id, comment_id=comment.id,
                                            mentioned_user_id=self.users[mention].id, created_at=comment.created_at))
                self.log("TASK", task.id, "COMMENTED", comment.created_at, author, new={"comment_id": str(comment.id)})
                parent_id = parent_id or comment.id

    def seed_time_off_and_holidays(self) -> None:
        reasons = ["Vacation", "Vacation", "Personal day", "Conference", "Medical appointment", "Family event"]
        for name in [f"{first} {last}" for first, last, *_ in PEOPLE]:
            for _ in range(self.rng.randint(1, 3)):
                start = add_workdays(self.today, self.rng.randint(-60, 120))
                days = self.rng.choice([1, 1, 2, 3, 5, 10])
                self.add(ResourceTimeOff(account_id=self.account.id, resource_id=self.resources[name].id, start_date=start,
                                         end_date=add_workdays(start, days - 1), reason=self.rng.choice(reasons),
                                         hours_per_day=Decimal("8.00"), created_by=self.users[name].id))
        for year in (self.today.year, self.today.year + 1):
            for name, day in holidays_for(year):
                self.add(AccountHoliday(account_id=self.account.id, holiday_date=day, name=name))

    def seed_access_grants(self) -> None:
        """Owners see everything; everyone else gets grants like a real admin would set up."""
        engineering = [ProjectModule.TASKS, ProjectModule.SPRINTS, ProjectModule.ISSUES, ProjectModule.RISKS,
                       ProjectModule.DEPENDENCIES, ProjectModule.REQUIREMENTS]
        testing = [ProjectModule.TESTING, ProjectModule.ISSUES, ProjectModule.TASKS, ProjectModule.REQUIREMENTS]
        analysis = [ProjectModule.REQUIREMENTS, ProjectModule.TASKS, ProjectModule.ASSUMPTIONS, ProjectModule.DECISIONS,
                    ProjectModule.CHANGE_REQUESTS]
        delivery = [ProjectModule.TASKS, ProjectModule.ISSUES, ProjectModule.RISKS, ProjectModule.DEPENDENCIES]
        grants: list[tuple[str, str, object, list[ProjectModule] | None]] = []

        def portfolio(person, role, name, modules=None):
            grants.append((person, role, ("portfolio", self.portfolios[name].id), modules))

        def program(person, role, name, modules=None):
            grants.append((person, role, ("program", self.programs[name].id), modules))

        portfolio("Olivia Bennett", "MANAGER", "Network & Voice Infrastructure")
        portfolio("Olivia Bennett", "MANAGER", "Corporate & People")
        portfolio("Olivia Bennett", "VIEWER", "Customer Platforms")
        portfolio("Olivia Bennett", "VIEWER", "Compliance & Security")
        for name in self.portfolios:
            portfolio("Samuel Adeyemi", "VIEWER", name)
            portfolio("Rachel Goldberg", "MANAGER", name)
        portfolio("Grace Kim", "MANAGER", "Customer Platforms")
        portfolio("Grace Kim", "VIEWER", "Network & Voice Infrastructure")
        program("Aisha Siddiqui", "MEMBER", "Admin Portal Rebuild", analysis)
        program("Aisha Siddiqui", "MEMBER", "Self-Service Apps", analysis)
        for dev in DEVS:
            program(dev, "MEMBER", "Admin Portal Rebuild", engineering)
            program(dev, "MEMBER", "Self-Service Apps", engineering)
        for tester in QA:
            portfolio(tester, "MEMBER", "Customer Platforms", testing)
        program("Marcus Bell", "MEMBER", "VoIP Modernization", testing)
        for person in HR:
            portfolio(person, "MEMBER", "Corporate & People", delivery)

        # The sign-in users who aren't owners.
        if accountant := self.sign_in_names.get("demo.user@projecthub.local"):
            billing = next(project for spec, project in self.projects if spec["name"] == "Billing & Invoicing Console")
            grants.append((accountant, "MEMBER", ("project", billing.id),
                           [ProjectModule.TASKS, ProjectModule.ISSUES, ProjectModule.RISKS, ProjectModule.REQUIREMENTS,
                            ProjectModule.CHANGE_REQUESTS]))
        if support := self.sign_in_names.get("suparno.karmakar.1729@gmail.com"):
            portfolio(support, "VIEWER", "Customer Platforms")

        # Project managers manage their projects (unless a wider grant already covers them).
        covered = {(person, scope) for person, _, scope, _ in grants}
        for spec, project in self.projects:
            if spec["pm"] != "Rachel Goldberg" and (spec["pm"], ("project", project.id)) not in covered:
                grants.append((spec["pm"], "MANAGER", ("project", project.id), None))

        # Anyone assigned work must be able to see it.
        def reaches(person: str, project: Project) -> bool:
            return any(p == person and scope in (("portfolio", project.portfolio_id), ("program", project.program_id),
                                                 ("project", project.id)) for p, _, scope, _ in grants)

        for person, project_ids in self.user_projects.items():
            if person in self.owner_names or person in ("Catherine Moreau", "Vikram Rao"):
                continue
            for _, project in self.projects:
                if project.id in project_ids and not reaches(person, project):
                    grants.append((person, "MEMBER", ("project", project.id), delivery))

        for person, role, (level, scope_id), modules in grants:
            self.add(AccessGrant(
                account_id=self.account.id, user_id=self.users[person].id, role=role,
                portfolio_id=scope_id if level == "portfolio" else None,
                program_id=scope_id if level == "program" else None,
                project_id=scope_id if level == "project" else None,
                modules=[module.value for module in modules] if role == "MEMBER" and modules else None,
                created_by=self.users["Catherine Moreau"].id,
            ))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", help="defaults to DATABASE_URL from the API settings")
    parser.add_argument("--reset", action="store_true", help="delete the account and everything in it first")
    parser.add_argument("--seed", type=int, default=2026, help="random seed, for repeatable data")
    args = parser.parse_args(argv)

    engine = create_database_engine(args.database_url) if args.database_url else app_engine
    with Session(engine) as db:
        existing = db.scalar(select(Account).where(Account.slug == ACCOUNT_SLUG))
        if existing is not None:
            if not args.reset:
                raise SystemExit(f"{ACCOUNT_NAME} already exists ({existing.id}). Use --reset to recreate it.")
            db.delete(existing)
            db.commit()
            print(f"Deleted the existing {ACCOUNT_NAME} account.")

        seeder = Seeder(db, random.Random(args.seed))
        account = seeder.run()
        db.commit()
        account_id = account.id

    print(f"Seeded {ACCOUNT_NAME} ({account_id})")
    for kind in sorted(seeder.counts):
        print(f"  {kind:22} {seeder.counts[kind]}")
    if seeder.sign_in_names:
        print(f"Users who can sign in: {', '.join(seeder.sign_in_names.values())}")


if __name__ == "__main__":
    main()
