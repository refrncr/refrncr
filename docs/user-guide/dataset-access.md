# Dataset access

Administrators choose **Settings → Dataset access → Who can export and use the API**.
The setting applies to every dataset in the installation. New installations and existing
installations upgraded to this version start with **Everyone**.

- **Everyone:** every active user can export datasets. Explicit grants are retained but
  have no effect, and nobody can add or remove grants in this mode.
- **Owning team and granted users — team members can grant:** administrators, owning-team
  members and explicitly granted users/teams can export. Administrators and owning-team
  members can grant and revoke access on the dataset's **Access** tab.
- **Owning team and granted users — administrators only can grant:** the same users can
  export, but only administrators manage grants. Owning-team members see the grants and
  user email addresses as a read-only list.

Reading in the app remains available to every active user. Export rights do not grant
access to another team's draft or permit exporting Scheduled or Cancelled versions.
Only administrators can change the owning team.

Saving shows a confirmation before opening access to everyone, restricting access from
Everyone, or removing team members' ability to manage grants. Delegating administration
from administrators to team members takes effect without confirmation. Existing grants
are always kept and apply again when a stricter mode is selected. If someone changes the
setting while your form is open, reload the page before trying again.

Release note: this setting and API enforcement (REF-306) are released together in rc1.
REF-294 alone changes file export; it must not be released without REF-306.
