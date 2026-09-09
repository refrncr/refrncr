# REF-149 — tussenverslag deel 1

Deel 1 is geïmplementeerd op `feat/ref-149-audit-coverage`, vanaf bijgewerkt `main`
(`5d69ee3`, REF-73 / PR #41). De working tree was bij aanvang schoon. Er zijn geen commits,
pushes, PR's of merges uitgevoerd. De wijzigingen staan lokaal, met nieuwe bestanden nog
ongetrackt. De onafhankelijke volledige validatie van deel 2 en GitHub CI blijven open.

## Inventaris en matrix

De volledige actuele REF-149-story en de scopegrenzen van REF-31, REF-39 en de afgeronde REF-73
zijn gelezen. De bestaande services, audit/UoW/ports/adapters, migraties 0001–0012,
governance-ADR en relevante lokale/Confluence-architectuur- en securitydocumentatie vormen de
implementatiecontext. REF-149 is Community/core; er is geen Cloud-compositie toegevoegd.

- [Functionele inventaris](audit-inventory.json): 78 publieke application-service-operaties,
  ondersteunende security/transient operaties, administratieve preflight/upgrade/startup en de
  synthetische Argon2-benchmark. Interfaces verwijzen naar hun gezaghebbende operatie.
- [Gezaghebbende coverage matrix](audit-coverage.json): 114 expliciete beslissingen, inclusief
  actor/target, context, succes, validatie/autorisatie/stale/no-op/rollback/retry,
  security-handoff, implementatiestatus en stabiele testsymbolen.
- 44 expliciete routes: 43 Flask-routes en één health-route. HEAD/OPTIONS, statische assets en
  framework-documentatie zijn apart verantwoord. Er zijn zes CLI-flows, inclusief benchmark/help.
- 31 domain-audited, 19 security logging required, 11 technical logging only en
  53 intentionally excluded. Geen geïnventariseerde operatie blijft ongeclassificeerd.

Eén serviceoperatie wordt niet opnieuw geteld voor een andere interface. Samengestelde
metadata/ownership- en field/identifier-wijzigingen hebben expliciete conditionele eventcontracten.
`move_field` delegeert zonder extra event. Legacy Grantbeheer blijft programmatic-only waar er
geen huidige HTTP/CLI-entrypoint is.

## Catalogus en correcties

[refrncr.audit](../../src/refrncr/audit.py) bevat 29 ondersteunde object/action-contracten en de
expliciete operatieregistratie. Services gebruiken `record_audit` vóór de persistence-port.
Bestaande historische actionnamen blijven behouden. UUIDs zijn canoniek; enums worden vóór
normalisatie begrensd en daarna allowlisted; importaantallen zijn begrensde integers.
Ontbrekende/onbekende contextvelden worden geweigerd. Er komen geen bedrijfswaarden, descriptions,
secrets, tokens, bestandsnamen, paden, SQL of traces in auditcontext.

Gecorrigeerd gedrag:

1. Een gelijk genormaliseerd e-mailadres veroorzaakt geen extra event. De target-lock en
   geserialiseerde SQLite-write voorkomen ook dubbele events bij gelijktijdige gelijke wijzigingen.
2. Herhaald enable/disable/delete met dezelfde userstatus veroorzaakt geen event of timestampwijziging.
   Bestaande lifecyclebeveiliging, waaronder bescherming van de laatste admin, blijft gelden.
3. Een gelijk genormaliseerde teamnaam en ongewijzigde Grantpermission veroorzaken geen event.
4. Ongewijzigde Datasetmetadata veroorzaakt ook zonder ownership-parameter geen event.
5. Gelijke canonieke Recordwaarden behouden revisie, timestamp en auditstatus. Een stale revision
   wordt vóór de no-op gecheckt; stale dezelfde-waarde-input wordt dus nog steeds afgewezen.
6. Import produceert één summary in plaats van een `created`-event voor ieder Record.
7. Geautoriseerde en volledig voorbereide Datasetexport produceert het vereiste requestevent.

Elke correctie heeft regressiedekking. De gerichte suite controleert iedere auditplichtige mutatie
met onafhankelijke verwachtingen voor action/object/actor, ongeldige input of lifecycle,
onbevoegde actor, herhaling en rollback vóór/na auditflush en vóór commit op beide engines.
Bestaande historische cascade-events bij teamverwijdering blijven behouden; dit is geen nieuwe
bulkselectiefunctionaliteit.

## Export en bulk

De product owner heeft de eerste voorgelegde exportoplossing expliciet goedgekeurd. Het bestand
wordt volledig gegenereerd en op leesbaarheid gecontroleerd binnen de bestaande coherente snapshot.
Daarna sluit de read-UoW en commit de service een aparte audit-write-UoW vóór teruggave van het artifact.
`dataset.export_requested` bevat uitsluitend actor, Dataset-UUID, timestamp en formaat CSV/Excel/JSON.
Een preparation/auditfailure sluit de spool en geeft geen download terug. Een post-stream-failure
behoudt het accurate requestevent en geeft vaste technische logging zonder payload of tweede event.
Het event beweert geen volledige ontvangst door de client.

SQLite expliciete reads en PostgreSQL repeatable-read blijven intact. De concurrencytest laat
committed writes tijdens export toe met een coherente oude snapshot; SQLite WAL wordt in die test
expliciet ingesteld. Normale SQLite-tests gebruiken de bestaande standaardmodus. Bounded-memory
sortering/spooling is behouden. Er wordt geen writer vastgehouden tijdens bestandsopbouw.

`dataset.records_imported` is één Dataset-summary in dezelfde UoW als alle geïmporteerde Records.
Details zijn formaat, committed `record_count` en gezaghebbend `rejected_row_count` (nul bij de
huidige all-or-nothing-import). Failed, cancelled, stale en rolled-back imports maken geen summary;
een geconsumeerde token kan niet opnieuw importeren. Historische auditrows worden niet herschreven.

## CI-guard en documentatie

[refrncr.audit_guard](../../src/refrncr/audit_guard.py) ontdekt publieke servicemethoden onafhankelijk
van de registratie, inclusief nieuwe serviceklassen en klassen met een geïnjecteerde UoW. Alle reads
worden expliciet geclassificeerd; er is geen heuristiek die methoden op grond van hun naam mutaties
noemt. De guard controleert ook routes/methoden, CLI-subcommando's/module-CLIs, bestaande symbolen,
vereiste matrixvelden en testverwijzingen. Een echte geïnjecteerde nieuwe service laat de guard met
de ontbrekende operatienaam falen. Een runtime-Flask-test vergelijkt daarnaast de geregistreerde routes.

De bestaande pytest-stap in GitHub Actions voert deze guard automatisch uit. Er is geen extra
CI-tool vereist. [Auditbeleid](audit-policy.md) en [ADR-012](../architecture/adr-012-audit-export-boundary.md)
documenteren contracten, de goedgekeurde transactiegrens, toevoegingsprocedure en scope.
Export-, import-, Dataset-contextdocumentatie en README zijn bijgewerkt. ADR-011 vermeldt nu de
reeds uitgevoerde REF-73-merge.

Confluence is bijgewerkt met expliciete pending-validatiestatus:

- [Architecture Overview & Domain Model](https://cleverhorizons.atlassian.net/wiki/spaces/RE/pages/41877505/Architecture+Overview+Domain+Model), versie 5.
- [Security Architecture](https://cleverhorizons.atlassian.net/wiki/spaces/RE/pages/41418774/Security+Architecture), versie 3.

## Exact uitgevoerde gerichte checks

Interpreter: repository `.venv`, Python 3.12. Database: geïsoleerde PostgreSQL **16.15**-testcontainer,
naast SQLite. `REFRNCR_TEST_POSTGRES_URL` wees naar de tijdelijke testdatabase op poort 55499.
De testfixtures maken eigen schema's en ruimen die op; een bestaande lokale database op poort 5432
is niet gebruikt voor de testmutaties. Die bleek PostgreSQL 15 en was alleen read-only gecontroleerd.

| Check | Resultaat |
| --- | --- |
| `python -m pytest tests/test_audit_catalog.py tests/test_audit_coverage.py -q` | **488 passed**, 68.57 s, exit 0 |
| Bestaande gerichte regressiegroep hieronder | **776 passed**, 3 warnings, 79.44 s, exit 0 |
| Aangescherpte read/preview-test: `python -m pytest tests/test_audit_coverage.py -k ordinary_service_reads -q` | **2 passed**, 442 deselected, 1.07 s, exit 0 |
| Catalogus/guard na laatste matrixverfijning: `python -m pytest tests/test_audit_catalog.py -q` | **44 passed**, 2.87 s, exit 0 |
| `python -m ruff check .` | Geslaagd |
| `python -m compileall -q src/refrncr tests/test_audit_catalog.py tests/test_audit_coverage.py` | Geslaagd |
| `python -m pre_commit run --all-files` | Alle geconfigureerde hooks geslaagd |
| Pre-commit expliciet op nieuwe, nog ongetrackte bestanden | Toepasselijke hooks geslaagd; YAML/TOML niet van toepassing |
| `git diff --check` | Geslaagd |

De regressiegroep is exact uitgevoerd met:

```shell
.venv/bin/python -m pytest \
  tests/test_dataset_export.py tests/test_record_import.py \
  tests/test_user_management.py tests/test_team_management.py \
  tests/test_membership_management.py tests/test_grant_management.py \
  tests/test_dataset_foundation.py tests/test_dataset_access.py \
  tests/test_dataset_fields.py tests/test_dataset_business_keys.py \
  tests/test_dataset_records.py tests/test_dataset_governance.py \
  tests/test_dataset_governance_review.py tests/test_dataset_context.py \
  tests/test_import_wizard.py tests/test_authorization_boundaries.py \
  tests/test_session_security.py tests/test_interfaces.py tests/test_dataset_browser.py -q
```

De warnings betreffen de bestaande Starlette/httpx- en Python sqlite3-datetime-deprecations.
De eerste tussentijdse run had één oude no-op-revisieverwachting die is aangepast aan REF-149;
de daaropvolgende gerichte regressiegroep is volledig groen. Er wordt niet beweerd dat de
volledige pytest-suite of GitHub CI in deel 1 is uitgevoerd.

## Gewijzigde bestanden

Nieuwe bestanden:

- `src/refrncr/audit.py`, `src/refrncr/audit_guard.py`;
- `tests/test_audit_catalog.py`, `tests/test_audit_coverage.py`;
- `docs/development/audit-inventory.json`, `audit-coverage.json`, `audit-policy.md`;
- `docs/architecture/adr-012-audit-export-boundary.md`;
- dit tussenverslag.

Gewijzigde bestaande bestanden:

- `src/refrncr/services.py`, `record_import.py`, `dataset_export.py`, `web.py`;
- `src/refrncr/models.py`, `ports.py` (JSON-contextannotatie ondersteunt integercounts);
- `tests/test_dataset_export.py`, `test_dataset_records.py`, `test_record_import.py`;
- `README.md`, `docs/architecture/adr-011-dataset-team-governance.md`;
- `docs/development/dataset-context.md`, `dataset-export-security.md`, `record-import-security.md`.

## Impact, beperkingen en vervolg

Geen nieuwe schema/migratie, dependency, API-contract, UI, autorisatiemodel, tenantstructuur of
Cloudfunctionaliteit. Bestaande CSRF-, session-, IDOR/non-disclosing- en bevoegdheidsregels zijn
met de gerichte suites beschermd. Auditcontext krijgt gecontroleerde structuur; Record-no-ops
verhogen bewust niet langer hun revisie. De exporttransactiegrens is expliciet goedgekeurd en in
ADR-012 vastgelegd.

De guard is een onderhoudbare completeness-check en geen bewijs dat willekeurige Pythoncode geen
verborgen side-effects heeft. Nieuwe dynamische interfaces of top-level adapterworkflows moeten
bij review expliciet in registratie/discovery worden opgenomen. Een nieuw geldige exportaanvraag
is steeds een afzonderlijk request; ontvangstbewijs of precies-eenmaal netwerkdelivery wordt niet
geclaimd. De bestaande process-local importclaim en enkelvoudige Community-workergrens blijven gelden.

Er zijn geen bekende implementatieblokkades na de gerichte checks. REF-31-securitylogging en
REF-39-history-UI blijven bewust onuitgevoerd. Deel 2 voert de onafhankelijke volledige validatie uit;
GitHub CI moet groen zijn vóór een eventuele merge. Commit en push vereisen een volgende expliciete
opdracht. De tijdelijke REF-149-testcontainer wordt na de checks verwijderd.
