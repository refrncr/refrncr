"""Shared Community installation policy values and transition explanations."""

INSTALLATION_SETTINGS_ID = "00000000-0000-0000-0000-000000000001"
EXPORT_POLICIES = {
    "everyone": "Everyone",
    "team_grants": "Owning team and granted users — team members can grant",
    "admin_grants": "Owning team and granted users — administrators only can grant",
}


def policy_confirmation(previous, policy):
    if previous == policy or (previous, policy) == ("admin_grants", "team_grants"):
        return None
    if policy == "everyone":
        return (
            "Every active user will be able to export every dataset and use the API. "
            "Existing grants will have no effect, but will be kept and apply again "
            "if a stricter policy is selected."
        )
    if previous == "everyone":
        return (
            "Users outside the owning team without a grant will immediately lose export "
            "and API access. Administrators will keep access."
        )
    return (
        "Team members will no longer be able to grant or revoke access. "
        "Existing grants will be kept."
    )
