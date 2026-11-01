"""Institute sample data as pure Python (no DB, no dates). Dates are expressed as day offsets from an `as_of`
date so the demo never goes stale and tests can pin the date.

EVERYTHING HERE IS SYNTHETIC SAMPLE DATA. The names, batches, attendance and scores are invented to exercise the
tools; they are not records of any real institute or student. Batch ids, centre names and course names are
placeholders, not QSpiders / PySpiders' real catalogue.

Two tenants on purpose:
  qspiders_sample - the demo tenant (config from tenants/qspiders_sample/config.yaml)
  other_institute - a TEST FIXTURE that deliberately reuses the demo's student id "stu_asha" and batch id
                    "PYF-101", to prove one tenant can never read another's rows. Seeded only on request.

Scenarios the demo student set covers (each one is asserted by tests):
  stu_asha    active, 80% attendance (above the 75% threshold), one graded / one submitted / one upcoming assessment
  stu_rahul   active, 60% attendance (below), a missing assignment
  stu_sneha   active, exactly 75% (the boundary: at the threshold is NOT below it)
  stu_karthik enrolment on hold, 50% attendance
  stu_meera   two active enrolments in different batches
  stu_neha    withdrawn: the tools must behave as if she has no access to that batch
"""

from dataclasses import dataclass

from app.tenants.loader import load_tenant_config
from app.tenants.schema import TenantConfig


@dataclass(frozen=True)
class SeedCourse:
    id: str
    brand: str
    name: str


@dataclass(frozen=True)
class SeedBatch:
    id: str
    course_id: str
    mode: str                       # online | offline
    centre: str | None
    timing: str
    started_days_ago: int
    past_sessions: int              # held on as_of-1 ... as_of-N
    upcoming_sessions: int          # scheduled on as_of+1 ... as_of+M
    topics: tuple[str, ...]         # cycled over the sessions in date order


@dataclass(frozen=True)
class SeedEnrollment:
    id: str
    student_id: str
    batch_id: str
    status: str
    enrolled_days_ago: int
    attendance: str                 # one char per PAST session, oldest first: P present, A absent


@dataclass(frozen=True)
class SeedAssessment:
    id: str
    batch_id: str
    title: str
    kind: str
    due_in_days: int                # negative = already due
    max_score: int


@dataclass(frozen=True)
class SeedResult:
    assessment_id: str
    enrollment_id: str
    status: str                     # submitted | graded | missing
    score: int | None = None


@dataclass(frozen=True)
class SeedTenant:
    config: TenantConfig
    students: tuple[tuple[str, str, str], ...]  # (id, name, email)
    courses: tuple[SeedCourse, ...]
    batches: tuple[SeedBatch, ...]
    enrollments: tuple[SeedEnrollment, ...]
    assessments: tuple[SeedAssessment, ...]
    results: tuple[SeedResult, ...]


def qspiders_sample() -> SeedTenant:
    return SeedTenant(
        config=load_tenant_config("qspiders_sample"),
        students=(
            ("stu_asha", "Asha Rao", "asha@example.com"),
            ("stu_rahul", "Rahul Menon", "rahul@example.com"),
            ("stu_sneha", "Sneha Iyer", "sneha@example.com"),
            ("stu_karthik", "Karthik Reddy", "karthik@example.com"),
            ("stu_meera", "Meera Nair", "meera@example.com"),
            ("stu_neha", "Neha Kulkarni", "neha@example.com"),
        ),
        courses=(
            SeedCourse("PY-FS", "PySpiders", "Python Full Stack (sample curriculum)"),
            SeedCourse("QS-TEST", "QSpiders", "Software Testing (sample curriculum)"),
            SeedCourse("PY-DS", "PySpiders", "Data Science with Python (sample curriculum)"),
        ),
        batches=(
            SeedBatch("PYF-101", "PY-FS", "online", None, "Mon-Fri 10:00-12:00 IST", 14, 10, 5,
                      ("Python basics", "Data types and control flow", "Functions", "OOP", "File handling",
                       "Flask routing", "REST APIs", "SQL basics", "ORM", "Testing with pytest", "Django intro",
                       "Deployment basics", "Git and GitHub", "Capstone planning", "Interview preparation")),
            SeedBatch("QAT-201", "QS-TEST", "offline", "Sample Centre A", "Mon-Sat 18:00-20:00 IST", 10, 8, 4,
                      ("Testing fundamentals", "Test design techniques", "Test case writing", "Defect life cycle",
                       "SQL for testers", "API testing basics", "Automation basics", "Selenium locators",
                       "Selenium waits", "Test frameworks", "Bug tracking tools", "Mock interview prep")),
            SeedBatch("PYD-301", "PY-DS", "online", None, "Tue-Sat 19:00-21:00 IST", 7, 6, 4,
                      ("Python refresher", "NumPy", "Pandas basics", "Data cleaning", "Visualisation", "Statistics basics", "Intro to ML", "Model evaluation",
                       "Project work", "Portfolio review")),
        ),
        enrollments=(
            SeedEnrollment("E-1001", "stu_asha", "PYF-101", "active", 14, "PPPPAPPPAP"),     # 8/10 = 80%
            SeedEnrollment("E-1002", "stu_rahul", "PYF-101", "active", 14, "PAPAPPAAPP"),    # 6/10 = 60%
            SeedEnrollment("E-1003", "stu_sneha", "QAT-201", "active", 10, "PPAPPAPP"),      # 6/8  = 75% boundary
            SeedEnrollment("E-1004", "stu_karthik", "QAT-201", "on_hold", 10, "PPAPPAAA"),   # 4/8  = 50%
            SeedEnrollment("E-1005", "stu_meera", "PYF-101", "active", 14, "PPPPPPPPPP"),    # 100%
            SeedEnrollment("E-1006", "stu_meera", "PYD-301", "active", 7, "PPAPPP"),         # 5/6
            SeedEnrollment("E-1007", "stu_neha", "PYD-301", "withdrawn", 7, "PAAPAA"),
        ),
        assessments=(
            SeedAssessment("ASM-1", "PYF-101", "Python Basics Quiz", "quiz", -7, 20),
            SeedAssessment("ASM-2", "PYF-101", "Flask REST API Assignment", "assignment", -2, 50),
            SeedAssessment("ASM-3", "PYF-101", "Capstone Project Milestone 1", "project", 6, 100),
            SeedAssessment("ASM-4", "QAT-201", "Test Design Techniques Quiz", "quiz", -5, 25),
            SeedAssessment("ASM-5", "QAT-201", "Bug Report Assignment", "assignment", 3, 40),
            SeedAssessment("ASM-6", "PYD-301", "Pandas Exercise", "assignment", -1, 30),
        ),
        results=(
            SeedResult("ASM-1", "E-1001", "graded", 17),
            SeedResult("ASM-2", "E-1001", "submitted"),
            SeedResult("ASM-1", "E-1002", "graded", 11),
            SeedResult("ASM-2", "E-1002", "missing"),
            SeedResult("ASM-1", "E-1005", "graded", 19),
            SeedResult("ASM-2", "E-1005", "graded", 44),
            SeedResult("ASM-4", "E-1003", "graded", 20),
            SeedResult("ASM-4", "E-1004", "missing"),
            SeedResult("ASM-6", "E-1006", "submitted"),
        ),
    )


def other_institute() -> SeedTenant:
    return SeedTenant(
        config=TenantConfig(tenant_id="other_institute", name="Other Institute (test fixture)",
                            timezone="Asia/Kolkata", attendance_threshold_pct=90),
        students=(
            ("stu_asha", "Aisha Other", "aisha@other.example"),  # same student id as the demo tenant's Asha
            ("stu_zed", "Zed Other", "zed@other.example"),
        ),
        courses=(SeedCourse("OT-1", "OtherBrand", "SECRET OTHER-TENANT COURSE"),),
        batches=(
            SeedBatch("PYF-101", "OT-1", "online", None, "Sun 09:00-11:00 IST", 14, 4, 2,
                      ("SECRET OTHER-TENANT TOPIC",)),                  # same batch id as the demo tenant's
            SeedBatch("OTH-900", "OT-1", "online", None, "Sat 09:00-11:00 IST", 14, 4, 2, ("Other topic",)),
        ),
        enrollments=(
            SeedEnrollment("E-1001", "stu_asha", "PYF-101", "active", 14, "AAAA"),   # same enrolment id too
            SeedEnrollment("E-9001", "stu_zed", "OTH-900", "active", 14, "PPPP"),
        ),
        assessments=(SeedAssessment("ASM-1", "PYF-101", "SECRET OTHER-TENANT QUIZ", "quiz", -3, 10),),
        results=(SeedResult("ASM-1", "E-1001", "graded", 1),),
    )


def build_seed(include_isolation_fixture: bool = False) -> list[SeedTenant]:
    tenants = [qspiders_sample()]
    if include_isolation_fixture:
        tenants.append(other_institute())
    return tenants
