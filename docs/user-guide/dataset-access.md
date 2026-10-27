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

API keys use their owner's current dataset export rights. In either stricter mode, the
API keys page explains this below Create API key. Lists include only permitted datasets;
a direct request for another dataset returns 404. Changes to grants, team membership,
ownership or this setting apply to the next API request. A read already in progress may
finish. The API still supports Scheduled publications selected by version or future date;
file export keeps its existing version restrictions.

Access to a mapping includes the source identifiers and context values copied into it.
It does not grant access to the source dataset itself.

Release note: this setting and API enforcement (REF-306) are released together in rc1.
