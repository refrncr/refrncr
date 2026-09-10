# REF-39 — objectpresentatiecorrectie, 10 september 2026

De eerste fallback verwarde ontbrekende namen met verwijderde objecten. De daaropvolgende correctie
keek alleen naar resolvers en miste dat een gecommit delete-event zelf verwijdering bewijst.
Daardoor bleef een verwijderd Record als naamloos zichtbaar. De definitieve centrale beslissing
combineert nu gevalideerde catalogusmetadata met resolverresultaten.

## Definitieve beslisvolgorde

1. Bekend succesvol deletecontract: **Deleted object**, zonder objectnaamquery. Het event is zelf
   gezaghebbend bewijs en heeft voorrang, ook als een huidige naam beschikbaar zou zijn.
2. Niet-delete-event met succesvolle resolver en aangetoonde afwezigheid: **Deleted object**.
3. Bestaand object met veilige actuele naam: die naam.
4. Geen veilige/toepasselijke naam of resolver: **No name to display**. Dit bewijst geen verwijdering.
5. Onverwachte resolver/databasefout: veilige foutresponse/logging, nooit een afgeleide delete-status.

Expliciete deletecontracten: `user:deleted`, `team:deleted`, `membership:deleted`, `grant:deleted`,
`dataset_field:deleted`, `dataset_record:deleted` en `dataset_export_access:revoked`. Die laatste
operatie verwijdert de exporttoegangsrij. Alle zeven committen met de succesvolle lifecyclemutatie;
rollback maakt geen succesvol event. Een `dataset:deleted` contract bestaat momenteel niet.
De huidige Record-acties zijn `created`, `updated` en `deleted` met objecttype `dataset_record`.

Alle 29 actionlabels hebben een verplichte expliciete `deletes_object`-beslissing in
`ACTION_PRESENTATIONS`; `ACTION_LABELS` wordt daarvan afgeleid. Er is geen suffixheuristiek of
tweede auditcatalogus. Nieuwe contracten met alleen een label, of met ontbrekende/ongeldige
deletebeslissing, falen de guard. Het bestaande resolverbeleid en de guards blijven behouden.

## Resultaat per type

Onderstaande tabel geldt voor niet-delete-events. De zeven deletecontracten hierboven hebben voorrang.

| Objecttype | Veilige actuele naam | Betrouwbare ontbrekend-status | Weergave zonder veilige naam |
| --- | --- | --- | --- |
| user | display_name van niet-verwijderde user | Succesvolle query vindt geen niet-verwijderde user; inclusief soft delete | Deleted object bij vastgestelde afwezigheid; anders No name to display |
| team | name | Succesvolle query vindt geen team met dat ID | Deleted object bij afwezigheid; anders No name to display |
| dataset | name | Succesvolle query vindt geen Dataset met dat ID | Deleted object bij afwezigheid; anders No name to display |
| dataset_field | name | Succesvolle query vindt geen field met dat ID | Deleted object bij afwezigheid; anders No name to display |
| membership | Bewust geen resolver | Niet onderzocht; geen verwijderingsclaim | No name to display |
| grant | Bewust geen resolver | Niet onderzocht; geen verwijderingsclaim | No name to display |
| dataset_export_access | Bewust geen resolver | Niet onderzocht; geen verwijderingsclaim | No name to display |
| dataset_record | Bewust geen resolver; geen waarden of identifier als naam | Niet onderzocht; geen verwijderingsclaim | No name to display |

Onbekende typen en ongeldige IDs worden niet speculatief opgelost en tonen No name to display.
Dit is geen bestaansclaim. Een onverwachte technische fout krijgt nooit een objectstatus:
de bestaande veilige 503-afhandeling en de vaste logmelding `Audit object resolution failed`
blijven vrij van SQL, namen, IDs, exceptiontekst en payloads. Actorweergave, inclusief Deleted user,
blijft ongewijzigd.

## Implementatie en tests

Voor niet-delete-events levert `AuditReadOperations.resolve_objects` per aangevraagd ID een expliciet
`AuditObjectRecord(exists, name)`. De adapter baseert exists op het resultaat van de typegebonden
query, niet op de naam. De centrale presentatielaag maakt `ObjectResolution` met resolved,
unnamed of deleted en een label; `AuditView` draagt dezelfde status/label naar lijst en detail.
Een bestaand object met lege, te lange of onveilige naam is unnamed, nooit deleted.
Ontbrekende batchresultaten en onjuiste resolverconfiguraties zijn technische fouten.

De guard controleert ondersteunde eigen-type-resolvers. Geen N+1: maximaal zeven SQL-queries per
lijst, vier per detail en geen extra bestaansqueries voor bewust naamloze typen. Bestaande querycount-,
autorisatie-, privacy-, escaping-, delete/rename-, pagination- en failure-injectiontests blijven groen.
Templates gebruiken al hetzelfde centrale label en hebben geen extra beslislogica nodig.
Object-ID blijft in details beschikbaar; de nieuwe HTML-test controleert dat het niet in de lijst
terechtkomt. Er zijn geen wijzigingen aan auditdata, eventproductie, technische actionnamen,
objecttypen, schema, migrations, CSS/tokens, bevoegdheden of Cloudgedrag.

`test_audit_object_presentation.py` bevat 58 nieuwe cases ten opzichte van commit `49e8321`: echte namen, lege/afgekeurde namen,
confirmed absence voor alle vier resolvers, vijf typen zonder resolver inclusief onbekend,
list/detail-pariteit, HTML-escaping, ID-niet-in-lijst, onverwachte exceptions/onvolledige resultaten
met veilige logs, guard zonder adapterondersteuning en een echte import/handmatige-creatieflow.
Bestaande tests controleren tevens soft-deleted users en daadwerkelijk verwijderde fields.
De laatste uitbreiding controleert de zeven expliciete deletebeslissingen op beide engines,
het overslaan van naamresolvers bij delete-events, onbekende delete-achtige namen zonder heuristiek,
een nieuw contract zonder metadata en dezelfde status in tabel en zijpaneel na echte Recordverwijdering.

De nieuwe integratietest maakt een echt Record en echt export-accessobject en bevestigt voor beide
No name to display. Een nieuwe CSV-import van twee Records produceert exact één
`dataset:dataset.records_imported` met format CSV, record_count 2 en rejected_row_count 0;
er ontstaat geen per-Record-event voor de geïmporteerde rijen. Daarna produceert handmatig
aanmaken exact één `dataset_record:created`. Alle eerder aanwezige auditrijen, inclusief een
historisch per-Record-importevent, blijven byte-equivalent in de statevergelijking. De bestaande
REF-149 CSV/Excel-summarytests zijn ook opnieuw uitgevoerd. Importproductiecode is niet aangepast.

De Chrome-test controleert expliciet No name to display bij niet-delete-events en Deleted object
bij `dataset_record:deleted`, beide in Recordrij én detail, met behoud van de juiste
object-ID, escaping/privacy en keyboard/focusgedrag. Een eerste run onthulde een timingprobleem in
de test: de volgende focusactie kon vóór het vertraagde native close-event plaatsvinden. De test
wacht nu op dat echte event; alle bestaande ID- en focusassertions zijn behouden. Geen product-JavaScript
gewijzigd. De nieuwe CSV-fixture is tijdens ontwikkeling aangepast aan het al bestaande ondersteunde
CSV-formaat met scheidingsteken; er is geen importgedrag gewijzigd om de test te laten slagen.

## Validatie

Python 3.12.2, SQLite 3.51.0, PostgreSQL 16.15 (serverversie gecontroleerd), Chrome 152.0.7977.83.
De PostgreSQL-URL is uitsluitend de bestaande disposable lokale/CI-testconfiguratie.

```sh
export REFRNCR_TEST_POSTGRES_URL=postgresql+psycopg://refrncr:refrncr@localhost:5432/refrncr_test
.venv/bin/python -m pytest tests/test_audit_object_presentation.py tests/test_audit_history.py tests/test_audit_history_review.py tests/test_audit_catalog.py tests/test_side_panel_javascript.py -q
.venv/bin/python -m pytest tests/test_audit_coverage.py -k 'bulk_import_exactly_one_summary or declared_event_actor_target_and_safe_context' -q
.venv/bin/python -m pytest -q
env -u REFRNCR_TEST_POSTGRES_URL .venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m pre_commit run --all-files
.venv/bin/python -m pre_commit run --files tests/test_audit_object_presentation.py docs/development/ref-39-object-presentation-report.md
node --check tests/javascript/audit_history_browser.cjs
git diff --check
```

Browser: `.venv/bin/python tests/browser_audit_history_app.py` en
`node tests/javascript/audit_history_browser.cjs`, met de bestaande externe Playwright/Chrome
via `REFRNCR_PLAYWRIGHT_MODULE` en `REFRNCR_BROWSER_EXECUTABLE`. De in-app browser was na de
bestaande skill-bootstrap niet beschikbaar. De disposable loopbackfixture is na validatie gestopt;
er zijn geen screenshots/logs/browserprofielen aan de repository toegevoegd.

| Controle | Resultaat |
| --- | --- |
| Gerichte objectpresentatie/history/guard/side-panel | 240 passed, 0 failed/skipped, 19.97 s |
| Bestaande REF-149-import/eventcontracten | 62 passed, 451 deselected, 20.43 s |
| Aparte SQLite-configuratie | 1304 passed, 436 PostgreSQL-fixture skips, 3 warnings, 192.98 s |
| Volledige suite met PostgreSQL 16, na cursieve tabelweergave | 1740 passed, 0 failed/skipped, 3 warnings, 264.19 s |
| Chrome | Zes tijdzones inclusief native dialog/UTC/no-JS en objectlabelpariteit: passed |
| Ruff, format, pre-commit, JS-syntax, diff-check | Alle toepasselijke checks passed, exit 0 |

De SQLite-configuratie slaat zoals gebruikelijk PostgreSQL-fixtures over wanneer de URL ontbreekt;
ze bevat wel een bestaande PostgreSQL-connectiesmoketest met lokale default-URL. Alle PostgreSQL-cases
worden daarom ook in de ingeschakelde volledige run uitgevoerd. Er zijn geen skips toegevoegd.
De drie bestaande warnings betreffen Starlette/httpx en tweemaal de SQLite datetime-adapter.
De eerste pre-commit-run formatteerde één bestaande assertion; de volledige hookset is daarna herhaald.

## Documentatie en Git

Jira REF-39 objectpresentatie, labelmetadata en AC14/19 zijn aangepast; de REF-149-import/append-onlygrens is expliciet
bevestigd. Confluence Architecture Overview (versie 10) en Security Architecture (versie 8) bevatten dezelfde
zeven deletecontracten, beslisvolgorde, verplichte metadata, per-type-regels, foutafhandeling en importgarantie.
Beide volledige paginabodies en de Jira-description
zijn na de update teruggelezen en vergeleken met de verzonden inhoud: gelijk (afgezien van buitenste
witruimte). De gewijzigde secties zijn ook inhoudelijk gecontroleerd. Repositoryhandleiding en auditbeleid zijn bijgewerkt;
de historische deel-1/deel-2-verslagen verwijzen naar deze correctie. De twee verouderde fallbackteksten
komen niet meer voor in de repository, actuele Jira-description of beide actuele Confluencebodies.
Jira blijft To Do; er is geen Done- of CI-claim gemaakt.

Gewijzigde bestanden:

- `src/refrncr/audit_presentation.py`
- `src/refrncr/audit_history.py`
- `src/refrncr/ports.py`
- `src/refrncr/persistence.py`
- `src/refrncr/templates/pages/audit/list.html`
- `tests/test_audit_object_presentation.py` (nieuw)
- `tests/test_audit_history.py`
- `tests/test_audit_history_review.py`
- `tests/javascript/audit_history_browser.cjs`
- `tests/browser_audit_history_app.py`
- `docs/development/audit-history.md`
- `docs/development/audit-policy.md`
- `docs/development/ref-39-part-1-report.md`
- `docs/development/ref-39-part-2-report.md`
- `docs/development/ref-39-object-presentation-report.md` (nieuw)

Branch `feat/ref-39-audit-history` was bij aanvang van de eerste objectcorrectie schoon met bestaande
commit `49e8321`. Deze vervolgstap bouwt voort op de 13 verklaarde ongecommitte bestanden daarvan;
de browserfixture en cursieve tabelweergave brengen het totaal op 15 gewijzigde/nieuwe bestanden.
Deze correctie is ongestaged en ongecommit; er is niets gecommit, geamendeerd of gepusht tijdens
deze opdracht. Die bestaande commit is niet herschreven. GitHub CI blijft vereist vóór merge.

## Herbeoordeling stap 2 na de laatste UI-aanpassing

De bestaande deel-2-review is vergeleken met de latere correcties en het beschikbare testbewijs.
Dit is een gerichte herbeoordeling, geen claim dat de volledige oorspronkelijke onafhankelijke
review opnieuw vanaf nul is uitgevoerd. `git ls-remote origin refs/heads/main` bevestigt opnieuw
`5ffc192dbdd927ed5c2c3962c490641bcf68abac`; de reviewbasis is niet veranderd.

| Onderdeel van stap 2 | Opnieuw nodig? | Bewijs/besluit |
| --- | --- | --- |
| Objectresolutie, alle 29 mappings, deletebeslissingen, privacy, querycounts, import en append-only | Al herhaald na de functionele correctie | 240 gerichte tests, 62 import/eventtests en beide volledige databaseconfiguraties hierboven |
| Autorisatie, IDOR, sessions/CSRF, filters, concurrency, migrations, tijd-JavaScript | Al opnieuw uitgevoerd in volledige suite na laatste templatewijziging | 1740 passed; geen latere productcodewijziging |
| Aparte SQLite-configuratie | Geen nieuwe functionele aanleiding | 1304 passed na service/port/adaptercorrectie; de latere 1740-run bevat ook alle SQLite-cases na de templatewijziging |
| Echte Chrome en screenshots | Ja, na de cursieve tabelweergave | Opnieuw zes tijdzones, panel/focus, filters/history, no-JS, errors/sessionverlies, light/dark preference, klein scherm en 200% CSS-zoom: exit 0 |
| Gerenderde tekststijl | Ja | Berekende stijl is italic voor beide lijstfallbacks, normal voor een actuele usernaam en voor beide detailfallbacks; screenshots bekeken |
| Lint, format, pre-commit, syntax, diff | Korte eindcontrole | Opnieuw uitgevoerd bij afsluiten van deze herbeoordeling |
| Jira/Confluence en architectuur | Geen nieuwe functionele afspraak | Laatste inhoud exact teruggelezen bij de objectcorrectie; deze herbeoordeling wijzigt geen productregels of architectuur |
| Eindverslag en handleiding | Ja | Actuele tests, 15 bestanden en cursieve kolomweergave aangevuld |

De oude gecombineerde fallback en dark-mode-eis uit de oorspronkelijke reviewopdracht zijn
vervangen door de latere expliciete productownerafspraken: drie objectstatussen en light-only.
Geen herintroductie van die verouderde eisen.

Browsercommando's bij deze hercontrole:

```sh
.venv/bin/python tests/browser_audit_history_app.py
node tests/javascript/audit_history_browser.cjs
git ls-remote origin refs/heads/main
```

Dezelfde bestaande externe Playwright/Chrome-omgevingsvariabelen zijn gebruikt. Een aanvullende
eenmalige Node/Playwright-controle las `getComputedStyle(...).fontStyle` bij de beide gefilterde
Recordlijsten, hun details en de userlijst. Alle drie controles slaagden; geen nieuw testbestand
of screenshot is aan de repository toegevoegd. De in-app browser was na de voorgeschreven
bootstrap opnieuw niet beschikbaar. Chrome-fallback en fixture zijn na afloop afgesloten.

UI-impact: componentvariant voor fallbacklabels in de objectkolom, via `<i>` rond de bestaande
escaped `<bdi>` op basis van `object_status`. Geen gewijzigde tokens, globale componenten, CSS,
detailstijl of functionaliteit; geen nieuwe visuele inconsistentie aangetroffen. De bestaande
beperkingen rond screenreader-certificering en CSS-zoom uit deel 2 blijven expliciet gelden.
