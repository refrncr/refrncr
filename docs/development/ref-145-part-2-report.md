# REF-145 — onafhankelijke review, deel 2

Status: **technisch gereed voor commit na lokale validatie**. Reviewcorrecties
en alle vereiste lokale controles zijn voltooid en groen.
Niets staged, gecommit of gepusht. GitHub Actions is niet uitgevoerd voor deze
ongecommitteerde wijziging en blijft de onafhankelijke gate vóór merge.

## Reviewbasis en aanpak

Op 10 september 2026 opnieuw gelezen: volledige Jira REF-145 en REF-150–154,
AGENTS.md, het deel-1-verslag, ADR-009–013, de Dataset-context/governance-,
datatype-, import/export-security- en auditdocumentatie, migratieomgeving en
historische fixtures. Confluence Architecture Overview & Domain Model v11 en
Security Architecture v8 zijn opnieuw getoetst aan de concrete implementatie.
De actuele REF-145 bepaalt de scope; de oude algemene formulering over
import/export-formatting in REF-150 breidt die scope niet uit.

`git fetch origin main` is uitgevoerd. `HEAD` en `origin/main` zijn beide
`3be23654be3c2999d06db3d3817d570d838d1dab`. Branch:
`feat/ref-145-canonical-field-datatypes`. De bestaande 20 gewijzigde en acht
nieuwe bestanden waren verklaard deel-1-werk. De volledige diff tegen deze
actuele main en alle nieuwe bestanden zijn beoordeeld; er is niet gewisseld
naar main of over bestaand werk heen geschreven.

Reviewplan: catalogus/alle invoer- en opslagpaden inventariseren; gerichte
precisie-, ordering-, transactie- en migratieregressies toevoegen; fouten in
bestaande componenten herstellen; echte browsercontrole en volledige suites
uitvoeren; documentatie en einddiff controleren. Geen nieuwe API, dependency,
autorisatieregel, tenantcompositie of extra migratie was hiervoor nodig.

## Onafhankelijke inventaris en implementatie

| Onderdeel | Vastgesteld contract en bewijs |
| --- | --- |
| Publieke typen | Precies `string`, `integer`, `number`, `float`, `boolean`, `date`, `time`, `datetime`, `timestamp`; `number` is Decimal. Geen alias `decimal`, UUID-type of case-insensitive type-ID. Catalogus-, formulier- en echte databaseconstrainttests. |
| Centrale architectuur | `datatypes.py` gebruikt uitsluitend standaardbibliotheek: parsers, formatters, Python-typen, stabiele keyprefixen, grenzen, nulls, fouten, ordering en identifiergeschiktheid. ORM CHECK en formulierkeuzes zijn afgeleid. Vaste migratielijsten zijn historische schemasnapshots, geen tweede runtimecatalogus. |
| Application services | Bestaande `canonicalize_record_value` vertaalt `DatatypeError` naar `ValidationError`; Recordfouten blijven per Field. Web, importprofiling en handmatige/confirmed import gebruiken dezelfde contracten. Geen nieuwe Flask/SQLAlchemy-afhankelijkheid in de canonicalizer of Field/Recordregels. Bestaande SQLAlchemy SessionService blijft buiten deze wijziging. |
| Field create/edit | Alle negen typen; onbekende ID's falen ook bij directe calls. Typewijziging gebruikt bestaande `field_has_values` onder Dataset-lock. Alle-null Field mag wijzigen terwijl andere Records bestaan; één waarde blokkeert beide update-entrypoints. Geen conversie of nieuwe algemene Field-revision. |
| Record create/edit | Exact getypeerde waarden, canonieke no-ops, bestaande revisionchecks. Zowel volledige pagina als embedded sidepanel gebruikt canonieke bewerkbare tekst. |
| Identifiers/uniqueness | Float verboden bij create, directe update, gecombineerde update, standalone identifiervervanging en Recordvalidatie. Overige required/membershipregels behouden. Binaire canonieke sleutels voorkomen schijnverschillen; null is afwezige waarde, zonder uniquenesssleutel. Gelijktijdige equivalente Integer/Decimal/Timestamp-identifiers leveren precies één Record en één event. |
| Ordering/pagination | Getypeerde vergelijking, nooit sorteren op numerieke tekst. Recordpagina's en externe exportsort volgen bestaande identifierordening; ontbrekende historische componenten laatst, Record-ID als tie-breaker. 55 incomplete Records bewijzen twee stabiele pagina's zonder dubbele/ontbrekende ID's. |
| Filtering | Er bestaat geen Recordwaarde-filterendpoint of queryport. Geen filterfeature toegevoegd. Tests vergelijken daadwerkelijk uit beide adapters gelezen waarden met dezelfde typed equality/range-semantiek; dat is geen claim over een niet-bestaand filterendpoint. Bestaande audit-/overzichtfilters blijven onderdeel van de volledige suite. |
| Persistence/ports | Nieuwe vijf typen gebruiken bestaande `string_value` plus typeprefix/key; uitlezen reconstrueert int/float/time/datetime en controleert kolom/keyconsistentie. Oude vier kolommen en keys blijven. `RecordValue`-type-union uitgebreid; geen nieuwe portmethode. |
| Import/export | Bestaande CSV/XLSX adapters verwerken canonieke tekst. Bestaande mappingassistent test nu negen canonieke grammars, geen bronformatcatalogus. CSV/JSON canoniek; JSON Integer/Float numeriek en Decimal exact. Nieuwe XLSX-typen als canonieke tekst; oorspronkelijke cel-/formulebeveiliging behouden. |
| Autorisatie/audit | Vertrouwde browseractor uit sessie, CSRF en scoped lookup behouden. Owning-team maintenance of actieve Community-admin vereist. Events bevatten bestaande structurele metadata; Field/Recorddetails blijven `None`, zonder waarden, descriptions, tokens of foutpayloads. Geen nieuw datatype-auditpayload nodig. |

### Datatypecontracten

| Type | Canonicalisatie en grenzen |
| --- | --- |
| String | Unicode trim + NFC, leeg naar null, maximaal 1000 UTF-8-bytes; interne Cc-controls/surrogaten geweigerd. Geen semantische validatie. |
| Integer | Exacte Python int, maximaal 1000 ASCII-digits; minus toegestaan, leading zeroes/signed zero normaliseren. Plus, decimalen, exponent, grouping, whitespace en trailing content geweigerd. |
| Decimal | Historisch `number`; exact base 10, maximaal 20 integer- en 18 fractiedigits. Geen exponentinvoer, afronding of Field precision/scale. Tests onderscheiden `0.100000000000000001` en `0.100000000000000002`, en behouden 38 digits bij Decimal-contextprecision 2. |
| Float | Eindige binary64; deterministische kortste round-trip `repr`. Maximaal 1000 mantissadigits en exponentmagnitudelimiet 400. NaN/infinities/overflow geweigerd; nonzero-underflow naar nul geweigerd, subnormals behouden; signed zero wordt `0.0`. Grenzen plus 2000 deterministische bitpatronen getest. Geen identifier. |
| Boolean | Alleen `true` en `false`; geen nieuwe CSV/Excel-tokens. Historische keys `b1`/`b0`. |
| Date | Echte Gregoriaanse datum, `YYYY-MM-DD`, jaren 0001–9999, schrikkeljaarcontrole, zonder tijd. |
| Time | `HH:mm:ss`, alleen niet-nul fractie, maximaal zes digits; geen datum/offset/timezone, 24:00 of leap second. |
| DateTime | Date + hoofdletter `T` + Time, zonder timezone/offset. |
| Timestamp | Expliciete RFC 3339 `Z` of numerieke offset tot 23:59; naar exact UTC-instant met `Z`. Maximaal zes fractiedigits; geen Unix-tijd, DST-gok of UTC-jaaroverflow. `-00:00` normaliseert naar `Z` volgens het bekende UTC-instant. |

Ruwe invoer is vóór parsing begrensd op 4096 codepoints; niet-Stringtypen
weigeren Unicode-whitespace, controls en trailing content. Unknown-ID-,
value-, limit- en storagefouten zijn begrensd en bevatten geen invoer.
Getypeerde formatters controleren nu ook vóór tekstallocatie Integer-magnitude
en Decimal-coefficient/exponent/scale. Dit voorkomt bijvoorbeeld formatting
van `Decimal('1e1000000000')`. UTC-overflow wordt een veilige datatypefout.

Decimalparsing, canonical keys, reads, vergelijking, identifiers en CSV/JSON
rendering gaan niet via Float. De **bestaande SQLite Numeric-projectie** kan
intern wel binary floating point gebruiken; die kolom is niet gezaghebbend
voor deze semantiek. Die historische opslagbeperking is expliciet behouden en
gedocumenteerd, niet als exacte SQLite Numeric-opslag gepresenteerd. Exactheid
komt uit de ongewijzigde canonieke key; PostgreSQL gebruikt daarnaast Numeric.

### Migratie en compatibility

Een schemaswijziging is noodzakelijk: de oude Field CHECK accepteert vier IDs.
`0014_ref_145` breidt alleen die constraint uit na preflight van type-ID's,
canonieke keys, kolomvorm, eindige Numeric-projectie, unique keys en
identifierdefinities. Geen nieuwe tabel/kolom, Recordbackfill of auditherschrijving.
Historische migraties 0001–0013 zijn byte-ongewijzigd tegen origin/main.

Extra tests upgraden elk van de dertien historische lege schema's naar head
op beide engines. De bestaande populated upgradefixtures voor Fields,
identifiers, Records/revisions, Fieldordering, context, governance en audit
blijven in de volledige suite. De specifieke 0013→0014→0013→0014-test bewaart
oude String/Decimal/Boolean/Date-inhoud, identifier/unique keys en audit exact.
Malformed keys/kolommen/unique keys/typen falen vóór DDL met niet-onthullende
melding en behoud van revision/data. Geïnjecteerde DDL-failure rolt terug.
Downgrade faalt zodra een nieuw type is geconfigureerd, ook zonder waarden.
Writers stoppen en een restorable backup blijven de operatorvoorwaarden.

## Reviewbevindingen en correcties

| Bevinding | Correctie en regressie |
| --- | --- |
| Embedded Record-sidepanel gebruikte nog `str(value)`: DateTime met spatie en kleine Decimal met exponent konden niet ongewijzigd terug naar de service. Detail/title toonden ook Python-representaties. | Gedeeld Recordformulier en panel gebruiken catalogusformatter. Flask-test controleert alle negen canonical forms in dit afzonderlijke pad; echte Chrome-test maakt alle typen, controleert prepopulation inclusief `0.000000000000000001`, DateTime/Timestamp en bewaart revision 1 bij ongewijzigd opslaan. |
| Getypeerde formatter bouwde Decimal/int-tekst vóór grenscontrole; extreme exponents konden enorme allocatie veroorzaken. UTC-overflow lekte een technische exception uit de formatter. | Centrale `validate_typed` vóór formatting; veilige exceptionvertaling. Tests met huge Integer, ±miljard Decimalexponent, scaleoverschrijding, sNaN/infinity en beide UTC-jaargrenzen. |
| Export had een aparte validator voor de oude vier typen, en de ORM hardcodeerde een tweede actuele typenlijst. | Exporttype-/resourcevalidatie naar catalogus; ORM CHECK afgeleid. Serializerquoting voor String blijft bewust adapterverantwoordelijkheid. Oude exportregressies behouden zonder afgezwakte assertions. |
| Failure-test van ongeldige Decimal in XLSX vond een open write-only XML-generator/tijdelijk bestand; alleen Workbook.close was onvoldoende. | Generator expliciet sluiten en writerbestand opruimen, ook vóór save. Afzonderlijke regressie controleert gesloten sheet en afwezig tijdelijk bestand. De aanvankelijke unraisable-warning is opgelost. |
| Deel 1 bewees niet expliciet PG16, gelijktijdige nieuwe identifiers, Field-type/write-race of iedere mutation-failurefase. | PostgreSQL 16.15 apart gestart; nieuwe dual-engine race-, precision-, pagination- en volledige snapshot-failuretests. Bestaande echte auditconstraintfailures blijven actief. |
| Deel 1 controleerde geen echte browser en miste het embedded editorpad. | Herhaalbaar loopback-fixture en Chrome-script volgens bestaande externe Playwright-conventie toegevoegd. Geen nieuwe dependency of verplichte CI-browsertool. |

Failure-injection: vóór SQL-mutatie, na business flush vóór audit, tijdens
auditinsert en bij commit na flush, telkens voor Field create/update en Record
create/update op beide engines. Snapshot omvat **alle tabellen**, niet alleen
Recordaantallen. Iedere injectie wordt aantoonbaar bereikt; mutation, waarden,
revisions, timestamps en events blijven gelijk. Stale en canonical no-op,
validation/authorization denial, echte audit-FK-failure en uniquenessconflict
blijven afzonderlijk getest. Geen nieuwe successful-eventcontext of logging.

## Testbewijs

Omgeving: Python 3.12.2; SQLite 3.51.0; PostgreSQL **16.15**, Debian
16.15-1.pgdg13+2, aarch64; Chrome 152.0.7977.83. PostgreSQL draait uitsluitend
in een disposable `postgres:16` container op loopbackpoort 55416, met
geïsoleerde testschema's. De standaard lokale PostgreSQL 15-installatie is
niet gewijzigd. Versies zijn rechtstreeks opgevraagd met `sqlite3.sqlite_version`
en `SELECT version()` op de test-URL.

```sh
docker run --detach --rm --name refrncr-ref145-pg16 \
  --publish 127.0.0.1:55416:5432 \
  --env POSTGRES_USER=refrncr --env POSTGRES_PASSWORD=refrncr \
  --env POSTGRES_DB=refrncr_test postgres:16
export REFRNCR_TEST_POSTGRES_URL=postgresql+psycopg://refrncr:refrncr@127.0.0.1:55416/refrncr_test
.venv/bin/python -m pytest tests/test_field_datatypes.py tests/test_field_datatype_services.py tests/test_dataset_export.py -q --tb=short
.venv/bin/python -m pytest tests/test_field_datatype_review.py tests/test_field_datatype_migration.py -q --tb=short
.venv/bin/python -m pytest tests/test_field_datatypes.py tests/test_field_datatype_services.py tests/test_field_datatype_review.py tests/test_field_datatype_migration.py tests/test_dataset_browser.py tests/test_dataset_export.py -q --tb=short
.venv/bin/python -m pytest -q --tb=short
env -u REFRNCR_TEST_POSTGRES_URL .venv/bin/python -m pytest -q --tb=short
.venv/bin/python -m pre_commit run --all-files
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
PYTHONPYCACHEPREFIX=/tmp/ref145-part2-pycache .venv/bin/python -m compileall -q src tests
node --check src/refrncr/static/js/refrncr.js
node --check tests/javascript/side_panels.cjs
node --check tests/javascript/field_datatypes_browser.cjs
git diff --check
```

Pre-commit controleert ook expliciet alle nieuwe, nog ongetrackte bestanden:

```sh
.venv/bin/python - <<'PY'
import subprocess
files = subprocess.check_output(
    ["git", "ls-files", "--others", "--exclude-standard", "-z"], text=True
).split("\0")
subprocess.run(
    [".venv/bin/python", "-m", "pre_commit", "run", "--files", *filter(None, files)],
    check=True,
)
PY
```

| Controle | Resultaat |
| --- | --- |
| Eerste gerichte catalogus/service/export-run | 313 passed, 0 failed/skipped, 0 warnings |
| Uitgebreide review/migraties na cleanupcorrectie | 104 passed, 0 failed/skipped, 0 warnings |
| Laatste gerichte datatype/service/review/migration/Flask/export-run | 554 passed, 0 failed/skipped, 0 warnings |
| Volledige pytest met SQLite + PostgreSQL 16.15 | **2126 passed, 0 failed, 0 skipped**, 3 bestaande warnings; 342.75 s |
| Volledige zelfstandige SQLite-configuratie | **1605 passed, 0 failed, 521 bestaande PostgreSQL-skips**, 3 bestaande warnings; 199.23 s |
| Ruff, formatter, pre-commit, syntax/compilation en diff | Passed; Ruff format-check: 122 bestanden. Alle toepasselijke pre-commit-hooks passed, inclusief expliciet nieuwe bestanden. YAML/TOML hebben bij die laatste selectie geen bestanden. |
| Echte Chrome | Alle negen Field create/edit; Float disabled/cleared en geforgede POST geweigerd; validation focus; Escape/focusreturn; 1440/320px; alle Recordwaarden canoniek voorgeladen en no-op revision 1; alle negen Field edits zonder JavaScript; 0 page errors |

De zelfstandige SQLite-configuratie laat de **bestaande** optionele
PostgreSQL-fixtures overslaan wanneer de URL ontbreekt; al die gevallen
draaien zonder skips in de PG16-enabled run. Er zijn geen skips toegevoegd.
De bestaande connection-smoketest gebruikt in de standalone run nog zijn
default localhost:5432 (PostgreSQL 15.18); die `SELECT 1` is geen PG16-bewijs.
Het PG16-bewijs komt uitsluitend uit de expliciet ingestelde 55416-run.
De drie warnings zijn één bestaande Starlette/httpx-deprecation en tweemaal
de bestaande Python 3.12 SQLite-datetime-adapterdeprecation in de populated
REF-75-fixture. Geen nieuwe warning blijft open. Dependencies zijn niet gewijzigd.

Browsercommando's, met verse fixture per run:

```sh
.venv/bin/python tests/browser_field_datatypes_app.py
# In een tweede terminal, met bestaande externe Playwright/Chrome-locaties:
REFRNCR_PLAYWRIGHT_MODULE="$REFRNCR_PLAYWRIGHT_MODULE" \
REFRNCR_BROWSER_EXECUTABLE="$REFRNCR_BROWSER_EXECUTABLE" \
REFRNCR_SCREENSHOT_DIR=/tmp node tests/javascript/field_datatypes_browser.cjs
```

De ingebouwde browser-skill is eerst gelezen en geprobeerd; `iab` was niet
beschikbaar. De bestaande externe browserconventie is daarom gebruikt.
Twee initiële browserharness-locators zijn gecorrigeerd (wrapped selectlabel
en Record-rowbutton); de definitieve run hierboven is volledig groen.
Screenshots op 1440/320px zijn bekeken en staan buiten de repository.
Geen screenreader-certificering of algemene WCAG-audit geclaimd.

De volledige suites omvatten Field/Record/business-key-, uniqueness/ordering-,
autorisatie/CSRF/IDOR-, audit/rollback-, CSV/XLSX import-, alle export-, CLI-,
migratie/preflight- en Node-controllerregressies. De repository heeft geen
verplichte typechecker, SAST, SBOM of CI-end-to-endframework; die zijn niet
als uitgevoerde controles opgevoerd.

## Scope en impact

REF-150 (semantische regels), REF-151 (outputtimezone), REF-152
(outputformats/typed Excel-configuratie), REF-153 (JSON-import) en REF-154
(bronformats, formatdetectie, remembered proposals en nieuwe import-UX) zijn
**niet geïmplementeerd**. Evenmin Unix-tijd, populated conversie, per-Field
Decimalprecision/scale of Cloud-only infrastructuur. Het enige herbruikbare
extensiepunt is de kleine centrale catalogus en de bestaande service/portgrens.

Autorisatie, tenantgrens, API, dependencies en auditcontracten veranderen niet.
Database-impact blijft alleen migratie 0014; er is geen extra reviewmigratie.
Architectuur blijft ADR-009/010/011/012 volgen. ADR-013 en de datatypegids
beschrijven de centrale keuze en toekomstige-typeprocedure. Het deel-1-verslag
is als historisch gemarkeerd en verwijst naar deze correcties.
Confluence Architecture Overview & Domain Model is bijgewerkt en opnieuw
geverifieerd op versie 12 met reviewcorrecties, PG16/browserbewijs en expliciet
ongecommitteerde status. Jira en Security Architecture zijn niet gewijzigd.

UI-classificatie: productfunctionaliteit via bestaande gedeelde Field- en
Recordcomponenten. Geen nieuwe design tokens, CSS, page-specifieke regels of
frameworks. De bestaande numeric-columnvariant geldt ook voor Integer/Float.
De consistentiecontrole vond en herstelde het tweede Record-renderpad;
Booleanselectors, bestaande focus/keyboardcontrollers en escaped markup blijven.

## Git, gewijzigde bestanden en aanbevolen vervolgstap

Eindstatus: 22 gewijzigde tracked en 12 nieuwe bestanden, 34 in totaal;
geen onverwachte bestanden, persoonlijke absolute paden, debugcode of bekende
secrets in de wijziging. Geen dependency-/CI-/historische migratiewijzigingen.
Niets staged/gecommit/gepusht;
geen PR, merge, release of Jira-statuswijziging. Hieronder staat de complete
focusset als **aanbevolen**, niet uitgevoerd `git add`-commando:

```sh
git add -- \
  src/refrncr/datatypes.py src/refrncr/services.py src/refrncr/ports.py \
  src/refrncr/models.py src/refrncr/persistence.py src/refrncr/record_import.py \
  src/refrncr/dataset_export.py src/refrncr/web.py src/refrncr/cli.py \
  src/refrncr/migrations/versions/0014_ref_145_field_datatypes.py \
  src/refrncr/static/js/refrncr.js \
  src/refrncr/templates/pages/datasets/fields/_form_fields.html \
  src/refrncr/templates/pages/datasets/detail.html \
  src/refrncr/templates/pages/datasets/_record_table.html \
  src/refrncr/templates/pages/datasets/_record_form_fields.html \
  src/refrncr/templates/pages/datasets/_record_panels.html \
  tests/test_field_datatypes.py tests/test_field_datatype_services.py \
  tests/test_field_datatype_migration.py tests/test_field_datatype_review.py \
  tests/test_dataset_fields.py tests/test_dataset_records.py \
  tests/test_dataset_browser.py tests/test_migrations.py tests/dataset_helpers.py \
  tests/javascript/side_panels.cjs tests/javascript/field_datatypes_browser.cjs \
  tests/browser_field_datatypes_app.py \
  docs/architecture/adr-013-canonical-field-datatypes.md \
  docs/development/field-datatypes.md docs/development/record-import-security.md \
  docs/development/dataset-export-security.md \
  docs/development/ref-145-part-1-report.md docs/development/ref-145-part-2-report.md
git commit -m "feat(fields): expand canonical datatypes (REF-145)"
git push -u origin feat/ref-145-canonical-field-datatypes
```

Daarna een gerichte PR volgens de repositorytemplate, met Jira-link en dit
testbewijs; merge uitsluitend na groene GitHub Actions. Deze opdrachten zijn
alleen aanbevelingen en niet uitgevoerd.

## Restpunten

Geen algemene Field-revision, SQL-filterindex, nieuwe per-kolomsortering of
Recordfilterendpoint toegevoegd. Bestaande pagina's sorteren in application
memory; exports gebruiken begrensde externe sortering. Zes fractiedigits en
numerieke/String-resourcegrenzen zijn bewuste contracten. De SQLite
Numeric-projectie is niet exact en blijft niet-gezaghebbend. XLSX gebruikt
canonieke tekst voor de vijf nieuwe typen tot de aparte outputformatstory.

De browserfixture is gestopt en de disposable PG16-container is gestopt en
automatisch verwijderd na de controles. GitHub CI
kan zonder de niet-geautoriseerde commit/push niet voor deze wijziging lopen.
Dat blijft een pre-mergevoorwaarde, geen lokaal behaalde controle.
