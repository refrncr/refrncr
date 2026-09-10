# REF-39 — onafhankelijke review en oplevering

Dit is het historische verslag van 9 september. De [objectpresentatiecorrectie](ref-39-object-presentation-report.md)
van 10 september onderscheidt No name to display en Deleted object en bevat het actuele testbewijs.
Bij aanvang daarvan bestond de eerdere implementatie al als commit `49e8321` op dezelfde branch.

Branch: `feat/ref-39-audit-history`. Basis: actuele `origin/main`, commit `5ffc192`
(REF-149, PR #42). Geen commit, push, PR of merge uitgevoerd. Lokale review van 9 september 2026;
GitHub Actions blijft de onafhankelijke gate vóór merge.

## Functionele oplevering en inventaris

Settings → Audit log (`GET /settings/audit-log`) toont installatiebrede historie in een semantische
tabel met timestamp, actor, leesbare actie/objecttype, actuele objectnaam en unieke View details.
Er zijn 50 rijen per pagina, newest-first op timestamp/ID, vijf combineerbare URL-filters,
Clear filters, lege/geen-resultaattoestanden en veilige 400/403/404/503-afhandeling.
`GET /settings/audit-log/<event_id>` levert het gedeelde detailzijpaneel of een zelfstandige pagina
zonder JavaScript. Details bevatten ook exacte UTC en technische namen/actor- en object-IDs.

Opnieuw gelezen: volledige Jira-story, AGENTS, deel-1-verslag, catalogus, inventaris en matrix,
auditbeleid, ADR-009/010/011/012, REF-149-rapporten, UI/session/import/export-documentatie,
Confluence Architecture Overview en Security Architecture, en de wijziging tegenover actuele main.
De grote JSON-documenten zijn inclusief alle waarden en relaties beoordeeld via een verliesvrije
geïnterneerde weergave; geen operationele rijen zijn weggelaten.

Er zijn precies 29 bestaande eventcontracten, acht objecttypen, negen contextlabels en vijf
contextformatters. De catalogus is niet vervangen of gekopieerd: `audit.EVENTS` blijft de bron
voor technische contracten en veilige context. De inventaris bevat nu 116 operaties, waaronder
twee expliciet niet-geaudite readoperaties. Er bestaat geen geldige systeemactor in REF-149;
bootstrap gebruikt de echte initiële administrator. Er is dus geen fictieve systeemfixture nodig.

## Autorisatie, privacy en append-only

Route én service eisen een actuele actieve, niet-verwijderde Community administrator.
Onbevoegde serviceaanroepen falen vóór cursor/filter/eventlookup. De tests bewijzen weigering
voor anonieme/ongeldige/onbekende principals, gewone gebruikers, inactive/deleted administrators,
een gedegradeerde administrator met bestaande sessie en cursors, gefingeerde sessie-JSON en
legacy viewer/editor/admin-Datasetgrants via een team. Geldige/missende/malformed detail-IDs
verlenen geen toegang. Anoniem/inactief volgt de bestaande veilige loginredirect; niet-admin
ontvangt 403. Auditresponses hebben `no-store`; filterwaarden verdwijnen uit loginredirects.

Community kent alleen User-rollen `admin` en `user`. Een afzonderlijke teamadministratorrol
bestaat niet; teamadministratie vereist Community-admin. REF-73 heeft Dataset-admin/editorrollen
vervangen. Legacy grants worden expliciet als escalatiepoging getest, zonder fictieve rollen
of nieuwe bevoegdheden te introduceren.

Alleen huidige veilige namen worden gebatcht opgehaald: user, team, Dataset en field. Membership,
grant, export access en Record hebben expliciet geen naamresolver. Tests geven ieder objecttype
een ID uit een andere tabel. Geen e-mail/status, beschrijving of Recordinhoud dient als fallback.
Hernoemen/verwijderen verandert geen historisch event. Namen worden begrensd, controls/bidi
verwijderd en Jinja-escaped; IDs blijven in details beschikbaar. Approved context gebruikt de
productievalidator, onbekende keys worden genegeerd, ongeldige context wordt niet weergegeven.
Geen raw JSON, secretachtige extra payload of HTML/event-handler wordt uitvoerbare markup.

Statevergelijkingen bewijzen dat reads geen domein- of auditwijzigingen maken. Failure-injection
op lijst/detail/actor/object-SELECTs levert een neutrale fout zonder SQL/token/trace en zonder
commit of partial mutation. De bestaande session/CSRF/IDOR-tests blijven onderdeel van de suite.
Dit voegt geen database-enforced immutability of cryptografisch ledgerbewijs toe.

## Reviewbevindingen en correcties

| Bevinding uit deel 1 | Correctie en regressiebewijs |
| --- | --- |
| Gelijke action/timestamp gaf identieke toegankelijke detailnamen | Event-ID toegevoegd aan verborgen linknaam; Chrome controleert 50 unieke namen met echte ties. |
| Ontbrekende `Intl` kon het filterformulier half ombouwen en de submitconversie overslaan | Capabilitycheck vóór enhancement; expliciete ISO-input blijft werken. VM- en Chrome-test zonder DateTimeFormat. |
| JavaScript normaliseerde 30 februari stilzwijgend naar maart | Exacte ISO-roundtripcontrole; onmogelijke kalenderdatum houdt de UTC-fallback. Test reproduceerde dit vóór de fix. |
| Completenessguard accepteerde verkeerde resolvers en stale mappings | Guard weigert cross-type resolver en stale action/type; runtime resolveert eveneens uitsluitend het eigen type. Sabotagefixtures voor ontbrekende action/type/context, hernoemd contract, verkeerde resolver, stale entry en geldige uitbreiding. |
| Concurrencybewijs was alleen inserts tussen opeenvolgende aanroepen | Aparte writer thread commit tijdens een gepauzeerde page-SELECT, op beide engines; volledige oorspronkelijke set blijft exact en uniek. Backdated-inserttest toont expliciet de grens van de garantie. |
| Grenzen 100/101 en verschillende browser/foutscenario's ontbraken | 100/101 toegevoegd; zes tijdzones, 403/503, sessieverlies, lange namen, ties, light bij dark preference en zoom/reflow toegevoegd. |
| Documentatie bevatte onduidelijke snapshotclaim en verouderde REF-149-status | Cursorsemantiek en Jira AC28 verduidelijkt; light-only in AC32; ADR-012 vermeldt de echte REF-149-merge. |

Er is geen nieuw autorisatielek, payloadlek of N+1 gevonden. De fixes introduceren geen dependency,
nieuw event, gewijzigde technische actionnaam, extra migration of Cloudlogica.

## Pagination, filters, tijd en performance

De cursor is HMAC-gevalideerd, gebonden aan administrator, normalized filters, richting, grens,
eerste-pagina-maximum en 12 uur geldigheid. Gemodificeerde richting, afgekorte/lange/onjuiste
tokens, gewijzigde filters/actor en expiry falen gesloten. Beide navigatierichtingen en alle
grenzen 0/1/49/50/51/100/101/127/10.000 worden getest.

De exacte garantie is behoud van iedere oorspronkelijk aanwezige match, precies één keer bij
vooruit bladeren. Er blijft geen MVCC-snapshot open tussen requests. Nieuwere posities vallen
buiten het eerste maximum; latere commits onder dat maximum kunnen op nog niet gepasseerde
posities verschijnen. Achter een gepasseerde grens wordt niets retroactief ingevoegd. Daardoor
kan reverse navigation na backdated inserts andere paginagrenzen tonen. Refresh begint opnieuw.
De backdated-test verbergt dit niet: zij verwacht de oorspronkelijke set plus het nieuwe oudere
event. De bestaande rijvolgorde blijft gelijk, zonder hiaten of duplicaten.

Start inclusief, end exclusief; gelijke grenzen geven nul matches. Expliciete UTC/positieve en
negatieve offsets worden naar hetzelfde moment genormaliseerd. Dubbele/onbekende parameters,
ongeldige kalenderwaarden, omgekeerde grenzen, Unicode/injectionachtige en extreem lange input
leveren 400 zonder rijen. URL-state, refresh, back/forward en Clear filters zijn in Chrome getest.
Lokale tijd behoudt zes fractiecijfers; exacte UTC blijft machineleesbaar en zichtbaar in detail.
Ongewijzigde native controls behouden oorspronkelijke microseconden en de tweede DST-occurrence;
nieuwe invoer gebruikt milliseconden en de eerste herhaalde occurrence. DST-gaten geven feedback.

Maximaal zeven SQL-queries per lijst en vier per detail, geen count, geen N+1, geen context in de
lijstprojectie, maximaal 4096 contexttekens bij detail en batches van maximaal 50 objecten/51 actors.
De bestaande nieuwe migration `0013_ref_39` is uitsluitend de timestamp/ID-index. Upgrade vanaf
0012, downgrade en herupgrade behouden historische velden/payloads. Eerdere historische migrations
blijven door de volledige suite gedekt. 10.000-event-plannen vergelijken vóór/na index op beide
engines. SQLite gebruikt indexed search zonder tijdelijke sortering; PostgreSQL backward index
scan zonder sort. Sparse filters kunnen meer databasewerk vragen: geen constant-time claim.

## Browser en toegankelijkheid

Echte Chrome **152.0.7977.83**, externe bestaande Playwright, disposable in-memory loopbackfixture.
Zes tijdzones: UTC, Europe/Amsterdam, America/New_York, Asia/Tokyo, America/Los_Angeles en
Pacific/Kiritimati. Native controls, DST winter/zomer, datumgrens, pointer/Enter, beginfocus,
Tab/Shift+Tab-trap, Escape, beide sluitbedieningen, openerherstel, juiste rij/objectkoppeling,
snel sluiten/heropenen, duplicate controllerinitialisatie en JavaScript-uit zijn geslaagd.
DOM-controles verifiëren caption, dialognaam en de benoemde contextsectie. 403/503 zijn
geïnjecteerde HTTP-browserresponses; daadwerkelijke databasefouten zijn afzonderlijk server-side
getest. Sessieverlies is met echte cookieverwijdering getest en stuurt naar login.

Screenshots van lijst, detail, 390px-scherm, 200% CSS-zoom en dark browser preference zijn bekeken
en staan uitsluitend in de OS-tempdirectory. Lange namen blijven volledig beschikbaar, de tabel
heeft horizontale scroll en focus is zichtbaar. Light-only is de expliciete productownerkeuze;
een donkere thema-implementatie is niet geleverd. De zoomtest gebruikt CSS-zoom met reflow, niet
de browsermenubediening. Er is geen volledige handmatige screenreader/WCAG-certificering geclaimd.
De ingebouwde browserverbinding was na skill-bootstrap niet beschikbaar; daarom de bestaande
Chrome-fallback. Er is geen nieuwe CI-browserdependency geïntroduceerd.

## Exact testbewijs

Repository `.venv`: Python **3.12.2**, SQLite **3.51.0**, PostgreSQL **16.15** (aarch64 Debian),
Node **24.9.0**. PostgreSQL `refrncr-postgres` draait image `postgres:16`; serverversie is met
`docker exec refrncr-postgres psql -U refrncr -d refrncr_test -Atc 'select version()'` gecontroleerd.
Database-URL is de bestaande disposable CI/testconfiguratie, geen productiecredential.

```sh
export REFRNCR_TEST_POSTGRES_URL=postgresql+psycopg://refrncr:refrncr@localhost:5432/refrncr_test
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest tests/test_audit_history.py tests/test_audit_history_review.py tests/test_audit_history_migration.py tests/test_audit_time_javascript.py tests/test_audit_catalog.py -q
.venv/bin/python -m pytest tests/test_audit_history.py -k chronology_query_plan -s -q
env -u REFRNCR_TEST_POSTGRES_URL .venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m pre_commit run --all-files
.venv/bin/python -m compileall -q src tests
node --check src/refrncr/static/js/audit-time.js
node --check src/refrncr/static/js/refrncr.js
node --check tests/javascript/audit_time.cjs
node --check tests/javascript/audit_history_browser.cjs
git diff --check
```

Pre-commit controleert ook de nieuwe, nog ongetrackte bestanden expliciet:

```python
import subprocess

files = (
    subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"])
    .decode()
    .split("\0")
)
subprocess.run(
    [".venv/bin/python", "-m", "pre_commit", "run", "--files", *filter(None, files)],
    check=True,
)
```

Browser: `.venv/bin/python tests/browser_audit_history_app.py`, gevolgd door
`node tests/javascript/audit_history_browser.cjs`. `REFRNCR_PLAYWRIGHT_MODULE` en
`REFRNCR_BROWSER_EXECUTABLE` wijzen naar de bestaande externe installaties; persoonlijke absolute
paden zijn niet opgenomen. De browserfixture wordt na de controle gestopt.

| Controle | Resultaat |
| --- | --- |
| Volledige suite, SQLite én PostgreSQL 16 ingeschakeld | **1682 passed, 0 failed, 0 skipped**, 3 warnings, 211.12 s |
| Volledige aparte SQLite-configuratie | **1271 passed, 0 failed, 411 skipped**, 3 warnings, 128.01 s |
| Gerichte audit history/review/migration/tijd/catalogus | **184 passed, 0 failed, 0 skipped**, 21.72 s |
| REF-149-guard na laatste matrixwijziging | **55 passed**, 0 failed/skipped, 7.66 s |
| Nieuwe reviewtests afzonderlijk | **57 passed, 0 failed, 0 skipped**, 2.64 s |
| Queryplannen 10.000 events, beide engines | **2 passed**, 67 deselected door expliciete gerichte selectie, 1.65 s |
| Echte Chrome | Zes tijdzones en alle beschreven browsercontroles passed, exit 0 |
| Ruff lint en format-check | Passed; 109 Pythonbestanden geformatteerd |
| Pre-commit, tracked en expliciet nieuwe bestanden | Alle toepasselijke hooks passed |
| Python compileall, vier JavaScript syntaxchecks, diff-check | Exit 0 |

De 411 skips bestaan alleen in de run zonder PostgreSQL-omgevingsvariabele en zijn de bestaande
PostgreSQL-fixturevoorwaarden. Alle 411 zijn daadwerkelijk geslaagd in de ingeschakelde volledige
run. Er zijn geen skips of uitzonderingslijsten toegevoegd om fouten te verhullen. Die aparte
SQLite-configuratie bevat bovendien een bestaande PostgreSQL-connectiesmoketest die haar lokale
default-URL gebruikt; het is dus geen letterlijk PostgreSQL-vrije suite.

De eerste standalone run in de netwerksandbox eindigde met 1268 passed, 3 failed, 411 skipped,
3 warnings (156.83 s): PostgreSQL-connectie en twee echte socket/CLI-tests kregen Operation not
permitted. Met de benodigde lokale netwerktoegang slaagde de ongewijzigde connectietest (1 passed,
0.25 s) en vervolgens de volledige run hierboven. Geen productcode of assertion is hiervoor
gewijzigd. De drie resterende waarschuwingen zijn bestaande deprecations: Starlette/httpx éénmaal
en de SQLite datetime-adapter tweemaal. Geen dependencywijziging buiten scope.

De initiële kalenderregressie faalde op de ongewenste normalisatie van 30 februari naar 2 maart;
na de fix slagen alle zes zones. De eerste sabotage-testrun (6 failed, 1 passed) vroeg tevens de
nog niet bestaande injecteerbare contextlabelparameter; na uitbreiding van de guard zijn alle
sabotages onderdeel van de groene review- en volledige suites. Deze initiële failures zijn geen
resterende failures.

## Documentatie, impact en restpunten

Gewijzigd: audit-historyhandleiding, coverage/inventory, auditbeleid, UI-foundation, README,
ADR-012-status, deel-1-verslag (persoonlijke paden verwijderd) en dit deel-2-verslag. Jira AC28
en AC32 beschrijven de werkelijke cursor- en light-only-afspraken. Confluence Architecture Overview (versie 8) en Security Architecture (versie 6) bevatten dezelfde
reviewstatus, met lokale validatie onderscheiden van CI.

Nieuwe events gaan eerst via REF-149-contract/allowlist en operationele inventaris; vervolgens
expliciete action/type/contextlabels, een veilig gebatchte eigen-type-resolver of bewuste fallback,
completeness/privacy/autorisatie- en beide database-engine-tests. Geen tweede catalogus.

Impact: autorisatie en privacy zijn expliciet getest; geen tenantmodel, dependency of gewijzigd
bestaand API-contract. Twee GET-routes zijn nieuw, geen publieke audit-API. De indexmigration is
geen online/concurrent DDL en kan op grote installaties tijdelijk locken. Actorselectie blijft
begrensd tot pagina plus huidige selectie. Default signing keys leven per service-instance;
meerdere workers moeten dezelfde veilig geïnjecteerde key gebruiken. Restart invalideert cursors.
Auditexport, retention, security-eventlogging (REF-31), auditorrollen en Cloudisolatie blijven
buiten REF-39. Bestaande deprecationwarnings worden niet buiten scope aangepast.

UI-classificatie: page-specific detail-linknaam en auditdatumcontroller; geen nieuwe tokens of
componentvarianten. Deel 1 past het globale gedeelde side-panel aan voor focus/trap/loadgedrag.
De bewuste auditpagina-CSS gebruikt bestaande tokens; er zijn geen nieuwe visuele inconsistenties
gevonden die deze oplevering blokkeren.

## Git en aanbevolen vervolgstap

**Technisch gereed voor commit na lokale validatie.** Niets staged, gecommit of gepusht. Aanbevolen na review van `git diff` en `git status`:

```sh
git add -- README.md docs/architecture/adr-012-audit-export-boundary.md docs/development/audit-coverage.json docs/development/audit-inventory.json docs/development/audit-policy.md docs/development/audit-history.md docs/development/ui-foundation.md docs/development/ref-39-part-1-report.md docs/development/ref-39-part-2-report.md src/refrncr tests
git commit -m "feat(audit): add administrator audit history (REF-39)"
git push -u origin feat/ref-39-audit-history
```

Deze commando's zijn uitsluitend voorgesteld. Gebruik de bestaande PR-template en link REF-39;
merge pas na groene GitHub Actions. Geen CI- of mergestatus voor deze ongeschreven commit geclaimd.

Volledige gewijzigde/nieuwe bestandslijst (39 bestanden):

- `README.md`
- `docs/architecture/adr-012-audit-export-boundary.md`
- `docs/development/audit-coverage.json`
- `docs/development/audit-history.md`
- `docs/development/audit-inventory.json`
- `docs/development/audit-policy.md`
- `docs/development/ref-39-part-1-report.md`
- `docs/development/ref-39-part-2-report.md`
- `docs/development/ui-foundation.md`
- `src/refrncr/app.py`
- `src/refrncr/audit.py`
- `src/refrncr/audit_history.py`
- `src/refrncr/audit_presentation.py`
- `src/refrncr/cli.py`
- `src/refrncr/migrations/versions/0013_ref_39_audit_history.py`
- `src/refrncr/models.py`
- `src/refrncr/persistence.py`
- `src/refrncr/ports.py`
- `src/refrncr/static/css/layouts/settings.css`
- `src/refrncr/static/js/audit-time.js`
- `src/refrncr/static/js/refrncr.js`
- `src/refrncr/templates/layouts/settings.html`
- `src/refrncr/templates/pages/audit/detail.html`
- `src/refrncr/templates/pages/audit/error.html`
- `src/refrncr/templates/pages/audit/list.html`
- `src/refrncr/web.py`
- `tests/browser_audit_history_app.py`
- `tests/dataset_helpers.py`
- `tests/javascript/audit_history_browser.cjs`
- `tests/javascript/audit_time.cjs`
- `tests/javascript/import_wizard.cjs`
- `tests/test_audit_catalog.py`
- `tests/test_audit_history.py`
- `tests/test_audit_history_migration.py`
- `tests/test_audit_history_review.py`
- `tests/test_audit_time_javascript.py`
- `tests/test_authentication_admission.py`
- `tests/test_interfaces.py`
- `tests/test_migrations.py`
