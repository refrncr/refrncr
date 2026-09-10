# REF-39 — tussenverslag deel 1

Datum: 9 september 2026. Story: [REF-39](https://cleverhorizons.atlassian.net/browse/REF-39).
Dit verslag betreft lokale implementatie op `feat/ref-39-audit-history`, zonder commit, push,
pull request, merge of release. De gebruiker heeft expliciet **alleen light** gevraagd.

## Inventarisatie en scope

De actuele REF-39, REF-149 en REF-31 zijn geraadpleegd, evenals de repository-instructies,
README, auditcatalogus/inventaris/coverage, auditbeleid, ADR-012, REF-149-verslagen,
architectuur/security/UI-documentatie, migraties en relevante implementaties en tests.
Confluence Architecture Overview & Domain Model v6 en Security Architecture v4 vormden
de bestaande architectuur- en securitycontext. REF-149 is de bron voor productie en payloads;
REF-31 blijft eigenaar van securitylogging. REF-39 hoort bij Community/core, installatiebreed.

De bestaande tabel `audit_events` heeft `id` (UUID-string), timezone-aware `timestamp`,
`actor_user_id` (user-FK, RESTRICT), `object_type`, `object_id`, `action` en JSON `details`.
De catalogus identificeert 29 contracten met **objecttype én actie**; `created` alleen is niet
uniek. Alle contracten vereisen een concrete actor. Er bestaat geen geldige systeemactor;
de eerste beheerder auditeert zijn eigen bootstrap. Users hebben rollen `admin`/`user` en
active/deleted-status; viewer/editor/admin-grants zijn afzonderlijke resourcebevoegdheden.
Verwijderen van users is soft delete. Namen worden actueel gelezen, nooit gesnapshot.

Er bestond geen audit-readport. De bestaande Recordpaginering laadt/sorteert rijen en is
daarom niet gebruikt voor grote auditgeschiedenis. Settings en het gedeelde fetch/side-panel-
contract zijn hergebruikt. Geen tweede auditmodel, broker, repositoryframework of Cloudlaag.

## Contract- en presentatiecoverage

Alle 29 contracten hebben expliciete Engelse actielabels en alle acht objecttypen hebben
een label en resolverbesluit. Contextallowlists en typevalidatie komen rechtstreeks uit
REF-149. De completenesstest faalt op ontbrekende actie-, object-, context- of formattermetadata.

| Objecttype | Technische actie | Engels label | Contextkeys | Resolver |
| --- | --- | --- | --- | --- |
| `user` | `created` | User created | — | user |
| `user` | `email_changed` | User email changed | — | user |
| `user` | `disabled` | User disabled | — | user |
| `user` | `enabled` | User enabled | — | user |
| `user` | `deleted` | User deleted | — | user |
| `team` | `created` | Team created | — | team |
| `team` | `renamed` | Team renamed | — | team |
| `team` | `deleted` | Team deleted | — | team |
| `membership` | `created` | Team membership added | — | Fallback |
| `membership` | `deleted` | Team membership removed | — | Fallback |
| `grant` | `created` | Access granted | — | Fallback |
| `grant` | `permission_changed` | Access permission changed | — | Fallback |
| `grant` | `deleted` | Access revoked | — | Fallback |
| `dataset` | `created` | Dataset created | `owner_team_id`, `creator_user_id` | dataset |
| `dataset` | `owner_changed` | Dataset owning team changed | `previous_owner_team_id`, `owner_team_id` | dataset |
| `dataset` | `metadata_updated` | Dataset metadata updated | — | dataset |
| `dataset` | `business_key_defined` | Record identifier defined | — | dataset |
| `dataset` | `business_key_updated` | Record identifier updated | — | dataset |
| `dataset` | `field_order_updated` | Dataset field order updated | — | dataset |
| `dataset_export_access` | `granted` | Dataset export access granted | `dataset_id`, `subject_type`, `subject_id` | Fallback |
| `dataset_export_access` | `revoked` | Dataset export access revoked | `dataset_id`, `subject_type`, `subject_id` | Fallback |
| `dataset_field` | `created` | Dataset field created | — | dataset_field |
| `dataset_field` | `definition_updated` | Dataset field definition updated | — | dataset_field |
| `dataset_field` | `deleted` | Dataset field deleted | — | dataset_field |
| `dataset_record` | `created` | Record created | — | Fallback |
| `dataset_record` | `updated` | Record updated | — | Fallback |
| `dataset_record` | `deleted` | Record deleted | — | Fallback |
| `dataset` | `dataset.records_imported` | Dataset records imported | `format`, `record_count`, `rejected_row_count` | dataset |
| `dataset` | `dataset.export_requested` | Dataset export requested | `format` | dataset |

Actor: actuele `display_name` van een niet-verwijderde user, anders **Deleted user**.
E-mail en accountstatus worden niet voor auditidentificatie opgehaald. Objectresolvers:
`user.display_name`, `team.name`, `dataset.name`, `dataset_field.name`. `membership`, `grant`,
`dataset_export_access` en `dataset_record` hebben bewust geen naamresolver en tonen
**No name to display** bij niet-delete-events na de objectpresentatiecorrectie van 10 september.
Succesvolle catalogus-delete-events, en gezaghebbend vastgesteld ontbrekende resolveerbare objecten,
tonen **Deleted object**; een lege of onveilige
naam bewijst geen verwijdering. Deze correctie vervangt de gecombineerde fallback uit deel 1.
Stabiele actor- en object-ID's staan in details; Recordwaarden en Dataset descriptions nooit.

## Implementatie en ontwerp

`AuditHistoryService` autoriseert elke lijst/detailread zelfstandig via de bestaande UoW.
De routes controleren daarnaast de actuele sessie en Community-adminrol. Anoniem/inactief
volgt de bestaande veilige loginflow; gewone users met willekeurige Dataset/teamrechten
krijgen 403 onafhankelijk van eventbestaan. Geautoriseerde ontbrekende details geven 404.
Reads creëren geen events en wijzigen geen bestaande audit- of businessrijen.

De nieuwe readport specificeert begrensde projecties en batchresolutie. SQLAlchemy en
database-exceptions blijven in persistence; `AuditReadUnavailable` geeft een neutrale 503.
Templates krijgen alleen een veilig viewmodel. De Community-compositie injecteert dezelfde
service in de webapp. Bestaande sessie-, cookie- en CSRF-grenzen blijven gehandhaafd.

Lijst: 50 events, nieuwste `(timestamp, id)` eerst, 51-rijen-lookahead en getekende keysetcursor.
De cursor bindt actor, canonieke filters, richting, grens, beginmaximum en oorspronkelijke
uitgiftetijd; vervalt na 12 uur. Nieuwe rijen vóór de leesgrens verschuiven bestaande pagina's
niet zoals offsets dat zouden doen. Oudere/nieuwere links bewaren de filterstatus.

Filters: start inclusief, eind exclusief, actor-UUID, contractactie en objecttype; combineerbaar
met AND. Dubbele/onbekende/malformed/te lange parameters en omgekeerde grenzen geven een
toegankelijke fout. Clear filters, refresh en browser back/forward zijn getest. Actoropties
blijven begrensd tot huidige pagina plus geselecteerde actor; details bieden een actorfilterlink.

Tijden: serverrendertijd is exact UTC met zes decimalen. JavaScript toont lokale browsertijd,
behoudt microseconden en toont UTC ook afzonderlijk in details. Native datumfilters gebruiken
milliseconden; ongewijzigde filters bewaren het originele UTC-moment inclusief microseconden
en de tweede wintertijd-occurrence. Nieuw ingevoerde herhaalde uren kiezen de eerste occurrence;
niet-bestaande zomertijden worden afgewezen. Zonder JavaScript blijft ISO-invoer/full-page-detail werken.

Context: alleen bestaande goedgekeurde structurele velden, geen raw JSON. Lijstqueries laden
geen context; details begrenzen historische JSON vóór parsing tot 4096 tekens. Onbekende
keys worden genegeerd en ongeldige toegestane waarden leveren geen context op. Tekst wordt
escaped, namen begrensd en control/bidirectionele formattingtekens alleen in presentatie verwijderd.

Het gedeelde paneel kreeg noodzakelijke toegankelijkheidsverbeteringen: expliciete Tab/Shift+Tab-
omloop voor side panels, behoud van initial-focus bij kopnormalisatie en bescherming tegen
vertraagde close-events na snelle heropening. De auditpagina gebruikt die gedeelde component.

## Migratie en performancebewijs

`0013_ref_39` voegt uitsluitend index `ix_audit_events_timestamp_id(timestamp, id)` toe.
Geen nieuwe kolom of payloadwijziging. Upgrade/downgrade/upgrade bewaart historische rijinhoud
op SQLite en PostgreSQL. De bestaande CLI-schemavalidator en historische testfixtures herkennen
de nieuwe head; dit zijn noodzakelijke aanpassingen voor de indexmigratie.

Gemeten op 10.000 events met de daadwerkelijke lijstprojectie en keysetgrens, `LIMIT 51`:

| Engine | Zonder samengestelde index | Met index |
| --- | --- | --- |
| SQLite | `SCAN audit_events`; `USE TEMP B-TREE FOR ORDER BY` | `SEARCH audit_events USING INDEX ix_audit_events_timestamp_id ((timestamp,id)<(?,?))` |
| PostgreSQL | `Seq Scan` + `Sort`, plannerkosten 363.01 | `Index Scan Backward`, plannerkosten 0.29–6.05 |

Plannerkosten zijn geen gemeten milliseconden. De test gebruikt geen geforceerde queryhints.
Lijstreads gebruiken maximaal zeven queries, details maximaal vier. Ook met veel actors en
alle acht objecttypen blijft dat aantal begrensd. Context komt niet in de lijstprojectie.
Dit bewijst geen constante scantijd voor iedere selectieve filtercombinatie; aanvullende indexen
vereisen een werkelijk workloadprofiel. Gewone index-DDL kan bij grote installaties tijdelijk locks geven.

## Uitgevoerde controles

Alle onderstaande eindcontroles zijn daadwerkelijk uitgevoerd; geen skips in de volledige suite.
De pytestcommando's met databasechecks gebruikten de bestaande lokale PostgreSQL-testcontainer
via `REFRNCR_TEST_POSTGRES_URL` (uitsluitend disposable testcredentials).

| Controle | Exact commando (naast de PostgreSQL-test-URL waar van toepassing) | Resultaat |
| --- | --- | --- |
| REF-39 + guard + gedeeld paneel | `.venv/bin/python -m pytest tests/test_audit_history.py tests/test_audit_history_migration.py tests/test_audit_time_javascript.py tests/test_audit_catalog.py tests/test_side_panel_javascript.py -q --tb=short` | **124 passed**, 16.37 s |
| Volledige suite | `.venv/bin/python -m pytest -q --tb=short` | **1621 passed**, 3 warnings, 182.54 s |
| Login/Settings/importregressies | `.venv/bin/python -m pytest tests/test_authentication_admission.py tests/test_import_wizard_javascript.py tests/test_interfaces.py -q --tb=short` | **114 passed**, 1 warning, 13.14 s |
| Queryplannen | `.venv/bin/python -m pytest tests/test_audit_history.py -k chronology_query_plan -s -q --tb=short` | **2 passed**, 63 deselected, 1.49 s |
| Pre-commit | `.venv/bin/python -m pre_commit run --all-files` | Alle zeven hooks passed |
| Ruff | `.venv/bin/python -m ruff check .` | All checks passed |
| Python syntax | `.venv/bin/python -m compileall -q src tests` | Exit 0 |
| JavaScript syntax | `node --check src/refrncr/static/js/audit-time.js` en `node --check src/refrncr/static/js/refrncr.js` | Exit 0 |
| Whitespace/diff | `git diff --check` | Exit 0 |

De drie volledige-suitewaarschuwingen zijn bestaande deprecations: Starlette/httpx en twee
SQLite-datetime-adapterwaarschuwingen uit de bestaande Record-migratietest. Geen fout of skip.
De nieuwe tijd-JavaScripttest draait via pytest; de bestaande paneel-, import- en navigatietests ook.
De nieuwe, nog untracked bestanden zijn daarnaast expliciet gecontroleerd met
`.venv/bin/python -m pre_commit run --files <alle nieuwe bestanden>`: alle toepasselijke hooks
passed (geen YAML/TOML-bestanden in die selectie). De auditcatalogus/guard is na de laatste
matrixdocumentatie opnieuw gecontroleerd: 55 passed. De tijdelijke browser-testservers zijn gestopt.

Browsercommando:

```sh
node tests/javascript/audit_history_browser.cjs
```

De omgevingsvariabelen `REFRNCR_PLAYWRIGHT_MODULE` en `REFRNCR_BROWSER_EXECUTABLE` verwezen
naar de bestaande externe Playwright-installatie en lokale Chrome executable.
Resultaat: exit 0; **PASS Europe/Amsterdam, America/Los_Angeles, Pacific/Kiritimati** en
**PASS no-JavaScript full-page details and exact UTC fallback**. Gecontroleerd: 50 rijen,
juiste objectdetails per rij, Tab/Shift+Tab, Escape, beide sluitbedieningen, exact focusherstel,
snel sluiten/heropenen, cursor/refresh/back/forward, Clear filters, spring-DST-validatie,
ongewijzigde tweede wintertijd-occurrence met microseconden, drie tijdzones,
invalid-timestamp-fallback, duplicate-controllerguards en geen page errors.

Uitvoer en browserbeelden zijn lokaal bewaard in de tijdelijke directory onder namen
`ref39-*.log` en `ref39-audit-*.png`. Deze tijdelijke bestanden zijn
geen release-artifacts en worden niet gecommit.


De browserfixture is uitsluitend tijdelijk/in-memory op loopback. De ingebouwde browser was
niet beschikbaar; na de verplichte browser-skill/bootstrap is de bestaande externe Playwright-
conventie gebruikt. Geen dependency, browserframework of verplichte CI-browserjob toegevoegd.
Screenshotcontrole is uitgevoerd op de light-lijst, het geopende detailpaneel en een 390px-scherm.
De mobiele tabel behoudt horizontale scroll en echte tabelsemantiek.

De eerste testrondes vonden en corrigeerden: formulier-URL overschreven door veldnaam `action`,
native datumvelden die zes decimalen niet accepteren, focusomloop, snelle paneelheropening,
verouderde migratie-headherkenning en testfixtures voor Settings/service-injectie/DOM-selectors.
Geen bestaande autorisatie- of privacyasserties zijn afgezwakt.

## Bestanden en impact

Git-status: uitsluitend verklaarde wijzigingen voor REF-39; niets staged.
Basiscommit: `5ffc192` (REF-149, PR #42); `main` was bijgewerkt met `git pull --ff-only`.

```text
 M README.md
 M docs/development/audit-coverage.json
 M docs/development/audit-inventory.json
 M docs/development/audit-policy.md
 M docs/development/ui-foundation.md
 M src/refrncr/app.py
 M src/refrncr/audit.py
 M src/refrncr/cli.py
 M src/refrncr/models.py
 M src/refrncr/persistence.py
 M src/refrncr/ports.py
 M src/refrncr/static/css/layouts/settings.css
 M src/refrncr/static/js/refrncr.js
 M src/refrncr/templates/layouts/settings.html
 M src/refrncr/web.py
 M tests/dataset_helpers.py
 M tests/javascript/import_wizard.cjs
 M tests/test_audit_catalog.py
 M tests/test_authentication_admission.py
 M tests/test_interfaces.py
 M tests/test_migrations.py
?? docs/development/audit-history.md
?? docs/development/ref-39-part-1-report.md
?? src/refrncr/audit_history.py
?? src/refrncr/audit_presentation.py
?? src/refrncr/migrations/versions/0013_ref_39_audit_history.py
?? src/refrncr/static/js/audit-time.js
?? src/refrncr/templates/pages/audit/detail.html
?? src/refrncr/templates/pages/audit/error.html
?? src/refrncr/templates/pages/audit/list.html
?? tests/browser_audit_history_app.py
?? tests/javascript/audit_history_browser.cjs
?? tests/javascript/audit_time.cjs
?? tests/test_audit_history.py
?? tests/test_audit_history_migration.py
?? tests/test_audit_time_javascript.py
```


Autorisatie/security: nieuwe installatiebrede adminread, opnieuw gecontroleerd in service en route.
Tenantisolatie: geen tenantmodel of Cloudcompositie. Database: één gemotiveerde indexmigratie.
API: twee server-rendered GET-routes; geen audit-business-API. Dependencies: geen.
Architectuur: bestaande ports/UoW/compositie voortgezet, geen nieuwe ADR nodig.
Documentatie: README, auditbeleid, UI-foundation, historiehandleiding, inventaris/matrix en dit verslag.
Confluence is bijgewerkt met expliciete lokale deel-1-status: [Architecture Overview & Domain Model v7](https://cleverhorizons.atlassian.net/wiki/spaces/RE/pages/41877505) en [Security Architecture v5](https://cleverhorizons.atlassian.net/wiki/spaces/RE/pages/41418774). De bestaande paginainhoud is behouden.

UI-consistentie: geen gewijzigde design tokens; gedeelde side-panelcontroller uitgebreid;
bestaande management-table/button/formvarianten hergebruikt. Bewust paginaspecifiek zijn de
vijf-filtergrid, timestampstijl en audit-detailveldregels. Functionele wijziging is alleen de
geautoriseerde readcapaciteit. Dark mode is op expliciete gebruikersinstructie weggelaten.

## Grenzen, afwijkingen en vervolg

- Keysetpaginering bewaart de filters en bestaande rijvolgorde, maar houdt geen database-MVCC-
  snapshot tussen HTTP-requests vast. Een laat gecommitteerd/backdated event onder het
  beginmaximum kan later zichtbaar worden. Een strikt bevroren set voor zulke inserts vraagt
  een aanvullend product-/datamodelbesluit; er is geen ongevraagde audit-sequence toegevoegd.
- De standaard cursorsleutel leeft per service-instantie. Restart maakt oude links ongeldig.
  Meerdere workers moeten dezelfde geïnjecteerde sleutel gebruiken; zelfstandig aangemaakte
  sleutels maken cursors niet onderling overdraagbaar. Dit staat expliciet in de handleiding.
- De actorselector is geen onbegrensde directory: huidige pagina plus selectie, aangevuld met
  de filterlink in elk detail. Voor een actor die niet in zicht is, moet eerst diens event worden gevonden.
- Native browserinvoer is millisecondeprecies; bestaande microseconden blijven behouden zolang
  het veld niet wordt gewijzigd. Alle eventtijdweergave en server-ISO-grenzen blijven microsecondeprecies.
- Applicatie-append-only blijft dezelfde garantie als REF-149; geen DB-tamperproof claim.
- Light-only is de expliciete scopeaanpassing van de gebruiker. Geen auditexport, nieuwe events,
  securitylogging, retentiebeheer, Record-voor/na-inhoud, Cloudfunctionaliteit of polling toegevoegd.
- GitHub CI is nog niet uitgevoerd: er is op verzoek niets gecommit of gepusht. Dit is het gevraagde
  deel-1-tussenresultaat en geen claim dat de Jira-story gemerged of gereleased is.

Na dit verslag stopt het werk. Er volgt geen commit of push zonder nieuwe expliciete opdracht.
