"""ArchPilot — From Idea to Building. Product identity, single source of truth."""
APP_NAME = "ArchPilot"
TAGLINE = "From Idea to Building."
DEFAULT_PROJECT_SUBDIR = "ArchPilot"
DB_FILENAME = "archpilot.db"
LOG_FILENAME = "archpilot.log"
WORK_SUBDIR = "work"

# Engineering honesty strings — reused across UI, reports and warnings.
PRELIMINARY_STAMP = (
    "PRELIMINARY ESTIMATE — NOT FOR CONSTRUCTION. "
    "Requires professional engineering review."
)
FREECAD_ATTRIBUTION = (
    "ArchPilot uses an independent installation of FreeCAD for 3D modelling. "
    "ArchPilot is not affiliated with or endorsed by the FreeCAD project."
)
STRUCTURAL_CAVEAT = (
    "ArchPilot is a design and estimating assistant, not a structural or code-compliance "
    "check. Storey additions and structural changes may require re-running structural "
    "checks, site investigation and professional review."
)
ESTIMATE_SCOPE_NOTE = (
    "This estimate covers only items measured from the current model and priced with "
    "rates you have supplied. Categories with no measured data are excluded, not assumed."
)


def report_footer() -> str:
    return f"{APP_NAME} — {TAGLINE} | {PRELIMINARY_STAMP}"
