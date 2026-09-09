# REF-149 — eindverslag deel 2

## Resultaat en Git

Deel 2 beoordeelt de implementatie van deel 1 opnieuw tegen de actuele
[REF-149-story](https://cleverhorizons.atlassian.net/browse/REF-149), de oorspronkelijke instructie,
AGENTS.md, de code, databasegedrag en documentatie. De eerdere groene gerichte tests zijn niet als
bewijs voor de volledige wijziging overgenomen.

Branch: `feat/ref-149-audit-coverage`, gebaseerd op `5d69ee3` (REF-73, PR #41).
De lokale wijzigingen aan het begin kwamen overeen met deel 1. Geen branchwissel, nieuwe pull,
commit, staging, push, PR, merge of release uitgevoerd. Dit verslag betreft de lokale werkboom;
GitHub Actions is nog niet uitgevoerd voor deze ongecommitteerde wijzigingen.

**Lokale validatie geslaagd: 1553 tests met PostgreSQL 16 ingeschakeld, zonder skips.**
Standalone SQLite: 1192 geslaagd, 361 PostgreSQL-cases bewust overgeslagen en gedekt door de
eerste run. Geen bekende open implementatieblokkade; GitHub CI blijft vereist vóór merge.

## Inventaris en classificaties

De [functionele inventaris](audit-inventory.json) en de
[audit coverage matrix](audit-coverage.json) bevatten dezelfde **114 unieke operaties**:
78 publieke application-service-operaties en 36 ondersteunende, beveiligings-, transient- en
administratieve operaties. De interface-inventaris omvat 44 expliciete HTTP-routes
(43 Flask en één health-route), zes CLI-flows, module-entrypoints en impliciete frameworkroutes.
Een serviceoperatie wordt niet opnieuw geteld voor iedere aanroepende interface.

| Classificatie | Aantal | Beslissing |
| --- | ---: | --- |
| Domain-audited | 31 | Succesvolle businesswijziging of voorbereid geautoriseerd exportverzoek |
| Security logging required | 19 | Authenticatie-, sessie- en beveiligingsmechanismen; implementatie bij REF-31 |
| Technical logging only | 11 | Setup, migratie/preflight, onderhoud, benchmark en streamdiagnostiek |
| Intentionally excluded | 53 | Gewone reads, capabilityberekeningen en tijdelijke importstappen met expliciete motivatie |

De herinventarisatie gebruikte naast de bestaande guard een inspectie van alle publieke
functies/klassen in de applicatiemodules, de daadwerkelijke servicecompositie, CLI-parser,
Flask-registratie en routecalls via gedeelde lokale helpers. Pure validators, serializers,
datatypen en CRUD-adapters zijn mechanismen onder hun geclassificeerde operatie; zij vormen geen
extra onafhankelijke use-cases. Er is geen huidige authenticated business-API, background
businessworker, publicatieflow, API-keybeheer of Dataset-delete-use-case.

Het register van 114 was als operatieset correct. Twee interfacekoppelingen naar de bestaande
`DatasetAuthorizationService.creation_teams` ontbraken bij de GET- en POST-flow voor Datasetcreatie.
Die zijn hersteld zonder extra operaties of gewijzigde classificaties.

Iedere matrixrij bevat capability, service, interfaces, read/extraction/mutation, domein-, security-
en technische beslissing, event/object/actor/context, success/failure/stale/no-op/rollback/retry,
implementatiestatus, uitsluitingsmotief en testverwijzingen. Dit JSON-bestand blijft de gezaghebbende
matrix; dit verslag is bewijs en toelichting, geen tweede catalogus.

## Catalogus en contextminimalisatie

De [centrale catalogus](../../src/refrncr/audit.py) bevat **29 object/action-contracten**:

| Objecttype | Actions |
| --- | --- |
| `user` | `created`, `email_changed`, `disabled`, `enabled`, `deleted` |
| `team` | `created`, `renamed`, `deleted` |
| `membership` | `created`, `deleted` |
| `grant` | `created`, `permission_changed`, `deleted` |
| `dataset` | `created`, `owner_changed`, `metadata_updated`, `business_key_defined`, `business_key_updated`, `field_order_updated`, `dataset.records_imported`, `dataset.export_requested` |
| `dataset_export_access` | `granted`, `revoked` |
| `dataset_field` | `created`, `definition_updated`, `deleted` |
| `dataset_record` | `created`, `updated`, `deleted` |

Historische object/action-paren blijven behouden. De twee nieuwe namen hebben een capabilityprefix;
er is geen migratie of hernoeming van historische events. Initial setup gebruikt bewust de nieuw
gecreëerde administrator als actor. Overige services ontvangen een vertrouwd vastgestelde principal;
de browser levert de actor vanuit de serversessie. Form/queryvelden kunnen de auditactor niet kiezen.

De contractvalidatie gebeurt vóór de bestaande `UnitOfWork.add_audit`-port. Actor/object en structurele
ID-context zijn canonieke UUIDs van 36 tekens. Contextvelden moeten exact met het contract overeenkomen;
extra of ontbrekende velden worden geweigerd. Formaat/subject-enums zijn vóór normalisatie begrensd
op 16 tekens; alleen allowlisted waarden worden opgeslagen. Importcounts zijn integers van 0 tot
10.000; booleans, floats en strings worden afgewezen. Errors kopiëren geen afgewezen inhoud.

Audit bevat geen Recordwaarden, Datasetdescriptions, namen, bestanden, uploads, lokale paden,
passwords, credentials, session-/CSRF-tokens, SQL of stacktraces. Tests controleren zowel contract-
rejecties vóór de adapter als werkelijk opgeslagen events. Bestaande ORM-rows worden niet achteraf
tegen nieuwe contracten gevalideerd; historische compatibiliteit blijft bestaan.

## Bevindingen en correcties

| Bevinding | Correctie | Regressiebewijs |
| --- | --- | --- |
| Deel 1 controleerde niet alle verplichte matrixbeslissingen en interfacekoppelingen | Guard controleert exact de operatieverzameling, boolean audit/securityvelden, geldige kind, verplichte object-/loggingvelden en tweezijdige interfacereferenties | `test_guard_rejects_inconsistent_or_missing_matrix_decisions`, `test_guard_requires_exact_inventory_registration` |
| Twee routecalls naar `creation_teams` ontbraken | Inventaris en matrix aangevuld | `test_http_inventory_traces_current_service_calls_and_shared_helpers` |
| Een exception uit `stream.close()` viel buiten de veilige technische logging | Iteratie en cleanup vallen onder dezelfde afhandeling; één vaste melding zonder exceptionpayload | `test_stream_read_and_cleanup_failures_log_once_without_payload` |
| Snapshotafsluiting, responseconstructie en HEAD/OPTIONS waren onvoldoende expliciet bewezen | Gerichte failure- en HTTP-methodetests toegevoegd | `test_export_preparation_or_audit_failure_closes_artifact_without_event`, `test_response_construction_failure_keeps_request_and_closes_artifact`, `test_export_head_and_options_have_explicit_audit_semantics` |
| Rollbacktests gebruikten vooral geïnjecteerde Pythonexceptions | Alle 29 mutatiecases krijgen daarnaast een echte audit-primary-keyconstraintfailure; importretry wordt daarna uitgevoerd | `test_every_mutation_rolls_back_business_and_audit` |
| Volledige run faalde op vullen van een REF-36-schema via huidige services met `details` | Historische fixture gebruikt gereflecteerde oude tabellen; upgrade moet alle oude auditvelden en null-context behouden | `test_populated_ref_36_sqlite_database_upgrades_to_ref_72` |
| Importdocumentatie zei nog dat geen importhistorie wordt opgeslagen | Expliciet onderscheid tussen tijdelijke upload/wizarddata en duurzame structurele samenvatting | Review van `record-import-security.md` tegen services, policy en Confluence |

De wijziging aan de historische fixture is noodzakelijk om de bestaande upgradecheck geldig te houden;
productie ondersteunt starten uitsluitend na de bestaande migratievoorbereiding. Er is geen schema-
compatibiliteitsworkaround aan de runtime toegevoegd en geen testassertie voor auditbehoud verwijderd.

De review bevestigt ook de correcties uit deel 1: genormaliseerde user/team/permission/Dataset/Record-
no-ops maken geen event en behouden timestamps/revisies; lifecycle-no-ops blijven stil; stale Record-
revisies worden vóór same-value-acceptatie afgewezen; import geeft één summary in plaats van per-Record-
events; export wordt na succesvolle voorbereiding en auditcommit teruggegeven.

## Transacties, bulk en export

Alle auditplichtige businessmutaties gebruiken dezelfde write-UoW voor businessdata, cleanup,
timestamps en audit. De tests vergelijken alle gepersisteerde tabellen vóór/na een fout, inclusief
sessies, waarden en audit. Getest zijn failures vóór auditopslag, ná flush van audit/businessdata,
vóór commit en door een echte databaseconstraint. Autorisatiefouten, invalid input/lifecycle,
stale input, no-ops en duplicate submissions mogen geen extra succesvol event opleveren.

Composities behouden hun expliciete historische contract: metadata/ownership en field/identifier
emitteren alleen hun gewijzigde onderdelen. `move_field` voegt geen wrapper-event toe. Teamdeletion
behoudt één team-event plus bestaande per-Grant-cleanup-events; membership/export-access-cascades
vallen onder de teamlifecycle. Dat is geen nieuwe bevestigde bulkselectie.

Een bevestigde niet-lege import maakt één `dataset.records_imported` op de Dataset, met formaat,
gecommitteerde Recordcount en autoritatieve rejected-rowcount (nul bij de huidige all-or-nothing-
import). CSV én Excel zijn getest. Cancel, invalid/stale preview, geweigerde actor, replay en rollback
maken geen succesvol summary-event. De claim wordt na een technische rollback vrijgegeven voor een
expliciete retry; een succesvolle claim kan niet opnieuw uitvoeren. Historische per-Record-events
worden niet verwijderd.

De door de product owner goedgekeurde [ADR-012](../architecture/adr-012-audit-export-boundary.md)
blijft intact: autorisatie, generatie en volledige begrensde leespreflight delen een coherent
read-snapshot; daarna sluit de read-UoW. Een aparte audit-only write-UoW commit precies één
`dataset.export_requested` vóór service-return. Een voorbereiding/snapshot-close/auditfailure levert
geen artifact terug. Een responseconstructie- of latere streamfailure behoudt het accurate requestevent.

De export genereert geen download-completion-event. Revocation na het autorisatiesnapshot geldt voor
volgende exports. Bestaande concurrente revoke- en coherentiescenario's blijven getest. SQLite gebruikt
expliciete readtransacties (WAL voor gelijktijdige writer-scenario's); PostgreSQL repeatable read.
De standaard SQLite-journalmodus is eveneens gedekt door de gewone export- en auditcommittests.

Bounded memory blijft behouden: een 1 MiB spool, sortchunks van 1.000 Records, maximaal 64 merge-runs,
64 KiB readabilityblokken en bestaande streaming serializers. De service houdt geen writer tijdens
generatie vast. HEAD voert de exporthandler uit en maakt één voorbereidings-event; automatische
OPTIONS maakt geen event. Beide methoden hebben nu expliciete regressietests.

## Security, technische logging en scope

REF-31 blijft eigenaar van structured security logging. De matrix noemt per relevante flow de handoff:
login/sessie/transport/CSRF/throttling, geweigerde toegang, lifecycle/permissionwijzigingen,
import/exportuitkomsten en verdachte tokens/replays. Een securityverplichting in de matrix betekent
niet dat deze logger in REF-149 is geïmplementeerd. Gewone reads zijn geen domeinevents.

De exportwrapper logt waargenomen lees-/cleanupfouten eenmaal als `Dataset export stream failed`,
zonder exceptioninhoud. Socketfouten buiten de iterator behoren tot de serving-stackdiagnostiek;
de applicatie kan niet ieder netwerkverlies waarnemen of volledige clientontvangst bewijzen.

Geen nieuwe UI, API, dependency, migratie, tenantkolom, eventbus of extern loggingplatform.
Autorisatieregels, CSRF, non-disclosing/IDOR-afhandeling en sessiebeveiliging zijn via bestaande
regressies gecontroleerd. Er is geen Cloud-tenantisolatie toegevoegd of als getest geclaimd.
REF-39 blijft verantwoordelijk voor historypresentatie; Recordinhoud blijft uitgesloten.

## Documentatie en Confluence

Inhoudelijke vergelijking vond plaats met de actuele pagina's, niet uitsluitend met lokale concepten:

| Onderwerp | Repository | Confluence |
| --- | --- | --- |
| Service/UoW-boundary, behouden events, composities | `audit-policy.md`, ADR-010, ADR-012 | Architecture Overview & Domain Model, REF-149-sectie |
| Snapshot → sluiten → auditcommit → return; revoke en failures | ADR-011, ADR-012, `dataset-export-security.md` | Architecture Overview & Domain Model en Security Architecture |
| Eén importsamenvatting en structurele counts | `record-import-security.md`, `audit-policy.md`, matrix | Beide REF-149-secties |
| Contextallowlist, bootstrapactor en REF-31/39-scope | `audit.py`, matrix en policy | Security Architecture, REF-149-sectie |
| Guard, interfaces, validatiestatus | `audit_guard.py`, catalogtests, dit verslag | Architecture Overview & Domain Model, REF-149-sectie |

[Architecture Overview & Domain Model](https://cleverhorizons.atlassian.net/wiki/spaces/RE/pages/41877505/Architecture+Overview+Domain+Model)
en [Security Architecture](https://cleverhorizons.atlassian.net/wiki/spaces/RE/pages/41418774/Security+Architecture)
zijn bijgewerkt naar respectievelijk **versie 6** en **versie 4**. Beide pagina's zijn daarna
teruggelezen: de volledige opgeslagen Markdownbody kwam exact overeen met de verzonden inhoud.
De REF-149-beslissingen en aantallen zijn inhoudelijk gelijk aan de repositorydocumentatie;
de pagina's verwijzen voor de volledige operationele matrix naar de repository.
De bredere toekomstige architectuur/securityprincipes op die pagina's worden niet als reeds
geïmplementeerde Community-controls gepresenteerd; de REF-149-secties beschrijven de huidige wijziging.

## Validatiebewijs

Alle Pythonchecks gebruiken `.venv` (Python 3.12.2). Databaseversie is rechtstreeks gecontroleerd:
PostgreSQL **16.15**, tijdelijke container `refrncr-ref149-part2`, loopbackpoort 55499, geïsoleerde
UUID-testschema's. Een bestaande lokale database is niet als testdatabase gebruikt.
Toolversies: pytest 8.4.2, SQLAlchemy 2.0.52, psycopg 3.3.4, Ruff 0.16.5, pre-commit 4.6.2,
Flask 3.1.3 en openpyxl 3.1.5.

De eerste volledige PostgreSQL-enabled run gaf **1551 passed, 1 failed, 3 warnings** in 207,40 s.
De failure was de hierboven beschreven historische fixture; dit is geen geslaagde eindrun.
De uitgebreide auditset vóór die fixturecorrectie gaf **567 passed** in 88,04 s. De extra
route-traceregressie gaf daarna **55 catalog-/guardtests passed** in 9,47 s.

| Definitieve controle | Resultaat |
| --- | --- |
| Volledige pytest met PostgreSQL 16.15 ingeschakeld | **1553 passed, 0 skipped**, 3 warnings; 235,20 s; exit 0 |
| Volledige pytest zonder PostgreSQL-configuratie | **1192 passed, 361 skipped**, 3 warnings; 150,56 s; exit 0 |
| Catalogus/guard na de laatste documentatieaanpassing | **55 passed**; zie commandolog |
| Ruff | **All checks passed** |
| pre-commit `--all-files` | **Alle zeven hooks passed** |
| pre-commit voor nieuwe, ongetrackte bestanden | **Alle toepasselijke hooks passed**; YAML/TOML skipped omdat deze bestanden ontbreken in die selectie |
| Python compileall en `git diff --check` | **Geslaagd** |

De 361 skips in de standalone run zijn uitsluitend de niet-geconfigureerde PostgreSQL-cases;
ze zijn daadwerkelijk uitgevoerd in de PostgreSQL-enabled run. De drie warnings zijn de bestaande
Starlette/httpx-deprecation en tweemaal de SQLite datetime-adapterdeprecation. Geen test of warning
is onderdrukt om een groene uitkomst te krijgen.

Exacte volledige testcommando's, vanuit de repositoryroot (credentials zijn disposable test-only):

```sh
REFRNCR_TEST_POSTGRES_URL='postgresql+psycopg://refrncr:refrncr@127.0.0.1:55499/refrncr_test' .venv/bin/python -m pytest -ra
env -u REFRNCR_TEST_POSTGRES_URL .venv/bin/python -m pytest -ra
.venv/bin/python -m pytest tests/test_audit_catalog.py -q
.venv/bin/python -m ruff check .
.venv/bin/python -m pre_commit run --all-files
.venv/bin/python -m compileall -q src/refrncr tests
git diff --check
```

Nieuwe bestanden zijn daarnaast via de NUL-gescheiden uitvoer van
`git ls-files --others --exclude-standard -z` expliciet aan
`.venv/bin/python -m pre_commit run --files` meegegeven; `--all-files` omvat ongetrackte bestanden
immers niet. De laatste guardrun toetst ook de definitieve matrixstatus en nieuwe testverwijzingen.

Lokale commandologs:

- `/tmp/ref149-part2-full-postgres-final.txt`
- `/tmp/ref149-part2-full-sqlite.txt`
- `/tmp/ref149-part2-guard-documentation-final.txt`
- `/tmp/ref149-part2-precommit-final.txt`
- `/tmp/ref149-part2-precommit-new-final.txt`
- `/tmp/ref149-part2-browser-access.txt`, `-browser-governance.txt`, `-browser-download.txt`

Browseromgeving: Node v24.9.0, externe Playwright 1.63.0, Chrome 152.0.7977.83.
Per script is eerst `PYTHONPATH=src .venv/bin/python tests/browser_table_rows_app.py` gestart.
Het browsercommando gebruikte vervolgens:

```sh
REFRNCR_PLAYWRIGHT_MODULE=/private/tmp/ref134-browser/node_modules/playwright REFRNCR_BROWSER_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' node tests/javascript/audit_export_browser.cjs
```

Voor de twee bestaande scripts was alleen de scriptnaam anders. Geen externe site of werkelijke
gebruikersdata is voor deze tests gebruikt.

De volledige suite bevat de bestaande SQLite/PostgreSQL persistence- en concurrencytests,
Alembic-upgrades/downgrades, CLI/bootstrap, geïsoleerde wheel/sdist-installatie en echte lokale
serverstart, Flask/FastAPI-interfaces, import/export, auth/CSRF/IDOR/sessies en Node-JavaScriptchecks.
De tijdelijke package-artifacts zijn uitsluitend testfixtures, geen gemaakte release.

Echte Chrome-browsertests gebruiken de bestaande externe Playwright-installatie, zonder dependency-
wijziging. Iedere script kreeg een nieuwe disposable in-memory loopbackfixture:

- `export_access_browser.cjs`: PASS op 1440/768/320 px, keyboard/focus, duplicates/revoke,
  lange namen, 200% tekst, no-JS-servervalidatie en geen scripterrors.
- `dataset_governance_browser.cjs`: PASS op desktop/smal scherm, create/owner/exportpanels,
  persistence/revoke, stale errorfocus, Escape-focusherstel en geen overflow/scripterrors.
- Nieuwe `audit_export_browser.cjs`: PASS voor echte CSV/Excel/JSON-downloads, inhoud/formaten,
  verborgen export voor gewone readers en server-side 404 bij een directe export-URL.

Deze optionele browserchecks zijn uitgevoerd; zij zijn geen nieuwe verplichte CI-tooling.

## Resterende grenzen en afronding

De guard controleert geregistreerde/ontdekte services, interfaces en contractconsistentie; hij is geen
bewijs dat willekeurige Pythoncode geen verborgen writes bevat. Nieuwe dynamische interfaces,
receiver-aliases en top-level adapterworkflows vereisen expliciete registratie/review. De huidige
operaties hebben expliciete beslissingen en concrete behavior-tests.

De bestaande process-local importclaim biedt geen nieuwe cross-process exactly-once-garantie.
Een geautoriseerd, nieuw exportverzoek is opnieuw auditable; audit betekent voorbereiding, geen
leveringsbewijs. Geen algemene metadatarevisie, securitylogger (REF-31), history-UI (REF-39),
tamper-evident ledger of retentieproduct is toegevoegd.

Alle drie tijdelijke browserservers zijn gestopt; de eigen PostgreSQL-container is gestopt en via
`--rm` verwijderd. Bestaande services/databases zijn niet opgeruimd of aangepast. De testuitvoer in
`/tmp` blijft als lokaal bewijs beschikbaar; die bestanden maken geen deel uit van de wijziging.

### Gewijzigde bestanden

Deze lijst omvat het totale resultaat van deel 1 en deel 2. De specifieke correcties uit deel 2 staan
in de bevindingentabel; overige wijzigingen zijn tijdens deze review opnieuw beoordeeld.

Commit en push blijven achterwege. GitHub Actions CI blijft de onafhankelijke gate vóór merge.

```text
 M README.md
 M docs/architecture/adr-011-dataset-team-governance.md
 M docs/development/dataset-context.md
 M docs/development/dataset-export-security.md
 M docs/development/record-import-security.md
 M src/refrncr/dataset_export.py
 M src/refrncr/models.py
 M src/refrncr/ports.py
 M src/refrncr/record_import.py
 M src/refrncr/services.py
 M src/refrncr/web.py
 M tests/test_dataset_export.py
 M tests/test_dataset_records.py
 M tests/test_migrations.py
 M tests/test_record_import.py
?? docs/architecture/adr-012-audit-export-boundary.md
?? docs/development/audit-coverage.json
?? docs/development/audit-inventory.json
?? docs/development/audit-policy.md
?? docs/development/ref-149-part-1-report.md
?? docs/development/ref-149-part-2-report.md
?? src/refrncr/audit.py
?? src/refrncr/audit_guard.py
?? tests/javascript/audit_export_browser.cjs
?? tests/test_audit_catalog.py
?? tests/test_audit_coverage.py
```
