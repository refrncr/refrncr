# REF-154 / US-108 — bronformaten in Map columns

Datum: 12 september 2026. Status: herkomst- en kwaliteitsreview afgerond; technisch lokaal gereed na volledige hertest.
Vergelijking met het ontbrekende HTML-referentiebestand en GitHub CI blijven onbevestigd. Alles ongestaged; geen commit,
push, PR of merge uitgevoerd. GitHub Actions blijft de onafhankelijke poort vóór
merge. De wijziging hoort geheel bij Community/core.

## Opdracht, bronnen en aanpak

Gelezen: de volledige geldende AGENTS.md, REF-154, REF-81, REF-134 en REF-145,
import-, datatype-, audit- en securitydocumentatie, ADR-013 en de Confluence-pagina's
Architecture Overview, Security Architecture en Security. Jira blijft de bron
voor productrequirements. De actuele REF-73/83/84/145-contracten en de expliciete
opdracht gaan vóór achterhaalde passages over editor Grants/per-record audit.

- [REF-154](https://cleverhorizons.atlassian.net/browse/REF-154)
- [REF-81](https://cleverhorizons.atlassian.net/browse/REF-81)
- [REF-134](https://cleverhorizons.atlassian.net/browse/REF-134)
- [REF-145](https://cleverhorizons.atlassian.net/browse/REF-145)
- [Architecture Overview](https://cleverhorizons.atlassian.net/wiki/pages/viewpage.action?pageId=41877505)
- [Security Architecture](https://cleverhorizons.atlassian.net/wiki/pages/viewpage.action?pageId=41418774)

`ref-145-import-ux-example.html` is niet aangetroffen in de onderzochte repository,
projectbestanden, bijlagen en tijdelijke bestanden, of als bijlage bij de gelezen
Jira-issues. Er is daarom geen vergelijking met dat ontbrekende bestand geclaimd.

Classificatie vooraf: productfunctionaliteit met een pagina-specifieke presentatie
binnen de bestaande importwizard. Plan: gedeelde bronparser/catalogus toevoegen;
staging en bestaande importservice uitbreiden; voorstellen via de bestaande
DatasetField-port en Unit of Work opslaan; één additieve migratie; gedeelde Jinja
partials en bestaande JavaScript-controller voor disclosure/live voorbeelden;
gedrag, transacties, beide databases en echte Chrome testen; bestaande documentatie
bijwerken. Geen nieuwe wizardstap, architectuurframework of afhankelijkheid.

## Historisch onderzoek: geen herstelbare implementatie gevonden

De aanvankelijke working tree was schoon. Alle lokale en remote branches,
commitgeschiedenis, reflogs, stashes, verwijderde/hernoemde bestanden en unreachable
objecten zijn onderzocht. `fetch --all --prune`, pickaxe (`-S`/`-G`), `fsck` en een
inhoudelijke `cat-file --batch-all-objects`/`--batch`-scan zijn gebruikt. Die scan
omvatte alle 3.701 aanwezige Git-objecten: 1.676 blobs, 1.884 trees en 141 commits.
Er waren geen stashes. De relevante zoektermen omvatten de gevraagde UI-teksten,
`source_format`, REF-154/US-108 en numerieke/temporale parsingpatronen.

Geen object bevatte de gevraagde eerdere Advanced-options-implementatie. De 13
relevante termmatches en vijf aanvullende numeriek/temporale documentmatches zijn
inhoudelijk beoordeeld. Ze verwijzen naar canonieke datatypes of uitgesteld werk.
Concrete positieve aanwijzing: commit
`d11a7037967d3219411f04275b26bb012817df8e` (REF-145 merge), met documentblob
`3069384340010e63dd05f5dde3b4ae9cf765fd1a` voor
`docs/development/ref-145-part-2-report.md`, vermeldt expliciet dat REF-154 niet is
geïmplementeerd. Ook de canonieke datatypewijziging `7d9630e` bevat niet deze UI.

Een exacte oorzaak van 'verdwijnen' kan dus niet worden vastgesteld. Het gevonden
bewijs wijst op uitgestelde implementatie; er is geen verwijderingscommit gevonden.
REF-154 is opnieuw geïmplementeerd volgens de opdracht. PR #49 en
`fix/restore-guided-import-entry` zijn niet toegepast, gebruikt als oplossing of
gemerged; er is niets gecherry-pickt.

Onderzoeksuitvoer van deze sessie staat lokaal in `/tmp/ref154-history.txt`,
`/tmp/ref154-git-object-hits.json` en `/tmp/ref154-numeric-history.json`.

## Geïmplementeerd gedrag

De bestaande Upload → eventuele Select worksheet → Map columns → Preview → Confirm
flow blijft intact. Map columns toont datatype, voorstel en gekoppelde bron-/opslag-
voorbeelden. **Change format** opent één native disclosure; **Advanced options**
opent/sluit alle toepasselijke disclosures. String, Boolean en Float hebben geen
betekenisloze keuzelijst. Ongeldige waarden bij die enkelvoudige profielen leveren
een waardefout op, geen onoplosbare vraag om een ander formaat.

De onveranderlijke, versieerbare allowlist in `source_formats.py` wordt gebruikt
voor detectie, parsing, validatie en presentatie; geparametriseerde tests lopen
onder meer alle DateTime-combinaties uit de catalogus na.

| Type | Bronformaten en grenzen |
| --- | --- |
| Integer / Decimal | Ongegroepeerd, Europees, US, spatie+komma en spatie+punt; strikte duizendgroepen; exacte Decimal; geen exponenten, afronding of accounting negatives |
| Float | Gewone eindige waarden en wetenschappelijke notatie; malformed exponent, overflow, NaN en infinity worden geweigerd |
| Boolean | Case-insensitive true/false, yes/no, y/n, 1/0 en ja/nee; tokenparen mogen mengen |
| Date | De acht gevraagde tekstpatronen en native Excel; echte kalenderdatum/schrikkeljaar |
| Time | 24-uurs HH:mm / HH:mm:ss en 12-uurs h:mm / h:mm:ss AM/PM; 24-uurs seconden mogen maximaal zes fractiecijfers hebben; native Excel |
| DateTime | Alle acht datum- maal vier tijdpatronen, met spatie; ISO-datum kan T gebruiken; native Excel |
| Timestamp | ISO/RFC3339 met Z/offset; lokale DateTime en native Excel alleen met expliciete IANA-zone; UTC-opslag, DST-gap geweigerd en fold vereist earlier/later |

Detectie gebruikt de volledige gemapte kolom. Verschillende geldige canonieke
uitkomsten blijven ambigu; gelijkwaardige uitkomsten kunnen één voorstel opleveren.
Ook Integer `1.234` wordt niet stilzwijgend als duizendtal geïnterpreteerd. Gemengde
of onbekende formaten geven een gerichte fout. Een opgeslagen voorstel krijgt
voorrang uitsluitend als de hele nieuwe kolom eraan voldoet.

Alleen werkelijk lege CSV/Excel-cellen worden null; NULL, N/A en '-' niet. Het
bestaande String-trimcontract blijft gelden. Native Excel-waarden behouden hun
Python-type tijdens staging; een Date verliest geen niet-nul tijdcomponent.
Bestaande datatypecontracten bepalen de uiteindelijke canonieke opslag.

De browser vraagt voorbeelden aan via een CSRF- en tokengebonden POST-route
`/datasets/<key>/records/import/<token>/formats`. Die gebruikt dezelfde serverparser
als Preview/Confirm en schrijft niets. Voorbeelden zijn begrensd tot drie per
kolom, compact weergegeven en geëscapet; de volledige begrensde tekst blijft
beschikbaar. Verouderde asynchrone antwoorden worden genegeerd. Gekozen profielen
zijn diep onveranderlijk in staged state; Preview en Confirm gebruiken die keuzes.

## Persistentie, migratie en veiligheid

`0017_ref_154` na `0016_ref_84` voegt uitsluitend
`dataset_field_source_formats(field_id PK/FK, choice JSON)` toe. Foreign-key deletion
cascadet met Field/Dataset. De opgeslagen keuze bevat een versie-ID en waar nodig
IANA-zone/fold, geen bronwaarden of rechten. Bestaande historische migraties zijn
niet gewijzigd. Startup/preflight-revisiecontroles herkennen de nieuwe head.

De bestaande DatasetField-port leest voorstellen gebatcht en schrijft ze binnen de
bestaande geserialiseerde import-Unit of Work. Records, waarden, voorstellen,
Concept-revisie/timestamp en maximaal één importsamenvatting committen atomisch.
Preview, format review, invalidatie, cancel, expiry, rollback, stale revision en een
verliezende gelijktijdige bevestiging slaan geen voorkeur op. Het aanmaken van een
nieuw Concept behoudt voorstellen voor exact dezelfde Field-identiteiten; dit is
operationele metadata en wordt geen nieuwe versie van de Field-definitie.

Actief Concept, create-only, REF-73 owning-team/Community-admin-autorisatie, CSRF,
opaque token, actor/session/Dataset-binding, TTL, limieten, volledige validatie,
Dataset locking, replaybescherming en bestaande auditcontracten blijven behouden.
Bronformaatkeuze verleent geen Dataset-administratierechten. Legacy editor/admin
Grants blijven inert. Er is geen audit-event per Record toegevoegd.

Upgrade: stop schrijvers, maak een herstelbare backup en gebruik het bestaande
Alembic/startupproces. Downgrade naar `0016_ref_84` verwijdert alleen de nieuwe
voorstellentabel; canonieke Records en audit blijven bestaan. Een volgende upgrade
begint met lege voorstellen. Gebruik een bijpassende backup om ook voorstellen te
herstellen. Upgrade/downgrade met bestaande bedrijfsdata is op beide engines getest.

De historische migratietestfixtures zijn aangepast om de nieuwe migratie werkelijk
te downgraden voordat zij oude schema's simuleren; head-asserties zijn bijgewerkt.
Er zijn geen assertions versoepeld of nieuwe skips toegevoegd. De CLI-aanpassing is
nodig om bestaande schemacontroles niet onbedoeld over te slaan op de nieuwe head.

## Validatie van de teruggevonden eerdere poging

Onderstaande resultaten zijn aangetroffen in het bestaande rapport én de bijbehorende
lokale logs. Ze zijn historische bewijslast; de nieuwe reviewresultaten staan onderaan.

Afgeronde controles van de eerdere poging:

| Controle | Resultaat |
| --- | --- |
| Volledige suite met PostgreSQL 16 | 2.877 passed, 0 skipped, 3 bestaande warnings; 653,22 s |
| Volledige SQLite-suite | 2.059 passed, 818 skipped, 3 warnings; 285,02 s |
| Gerichte import-/datatype-/wizard-JavaScripttests (SQLite) | 544 passed, 99 bestaande PostgreSQL-fixture-skips; 45,87 s |
| Ruff check | Geslaagd |
| Ruff format --check | Geslaagd; 162 Python-bestanden |
| pre-commit --all-files | Alle zeven hooks geslaagd |
| pre-commit voor nieuwe ongestagede bestanden | Alle toepasselijke hooks geslaagd; YAML/TOML hadden geen nieuwe bestanden |
| git diff --check | Geslaagd |
| Echte Chrome | 3/3 viewports geslaagd |

De 818 skips in de standalone SQLite-run zijn bestaande databasefixture-skips bij
ontbrekende PostgreSQL-test-URL; de PostgreSQL-run voert deze gevallen uit. De drie
bestaande waarschuwingen zijn één Starlette/httpx-deprecatie en tweemaal de SQLite
Python-datetime-adapterdeprecatie. Ze zijn niet onderdrukt of als skips omgebouwd.

Reproduceerbare commando's vanuit de repository met de repository-venv:

```sh
env -u REFRNCR_TEST_POSTGRES_URL .venv/bin/python -m pytest -q --tb=short
# Zet REFRNCR_TEST_POSTGRES_URL op een wegwerpbare PostgreSQL 16-testdatabase.
.venv/bin/python -m pytest -q --tb=short
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m pre_commit run --all-files
git diff --check
```

Gerichte run: `tests/test_source_formats.py`, `test_source_format_import.py`,
`test_source_format_transactions.py`, `test_record_import.py`, `test_import_wizard.py`,
`test_import_wizard_javascript.py`, `test_field_datatypes.py`,
`test_field_datatype_services.py`, `test_field_datatype_review.py` en
`test_field_datatype_migration.py` samen met `.venv/bin/python -m pytest -q`.
Log: `/tmp/ref154-targeted-complete.txt`. De volledige PostgreSQL-run omvat ook
alle PostgreSQL-parametrisaties van deze bestanden.

Volledige logs: `/tmp/ref154-complete-sqlite.txt`,
`/tmp/ref154-complete-pg.txt`, `/tmp/ref154-precommit-complete.txt`.
Nieuwe ongestagede bestanden zijn aanvullend met `pre_commit run --files` getest,
omdat `--all-files` alleen tracked bestanden selecteert.

Versies: Python **3.12.2**, SQLite **3.51.0**, PostgreSQL **16.15**
(Debian 16.15-1.pgdg13+2, aarch64), Google Chrome **152.0.7977.84**.
PostgreSQL draaide in de geïsoleerde tijdelijke container `refrncr-ref154-pg16` op
loopbackpoort 55416. De bestaande ontwikkelcontainer op 5432 is niet gewijzigd. De tijdelijke
browserfixture en eigen testcontainer zijn na validatie gestopt.

Echte Chrome-regressie: **3/3 viewports geslaagd: 320, 768 en 1440 px**, elk 1000 px
hoog. Gecontroleerd: Advanced options openen/sluiten, één Change format openen,
gekoppeld label, links/rechts voorbeelden en mobiele verticale paren, gewijzigde
keuze in Preview, uitsluitend ambigu veld openen/focussen, geen horizontale
pagina-overflow, Space/Enter/Tab/Escape en focusherstel, geen JavaScript-fouten.
Screenshots zijn visueel bekeken op 320 en 1440 px. Geen vaste sleeps gebruikt.

De in-app browser was niet beschikbaar (`Browser is not available: iab`). De echte
Chrome-test gebruikt daarom de bestaande externe Playwright-installatie en de
lokale Chrome-binary. Geen browserdependency aan het project toegevoegd. Test:
`tests/javascript/source_formats_browser.cjs`, fixture:
`tests/browser_source_formats_app.py`. Browserlog: `/tmp/ref154-chrome-final.txt`;
screenshots: `/tmp/ref154-{320,768,1440}.png`.

De auditinventaris is bijgewerkt en gecontroleerd: **136 operations, 61 routes**,
waarvan 96 service operations en 40 overige operations; 34 bestaande eventcontracten.
De extra format-reviewoperatie is read-only en veroorzaakt geen business-audit.
Autorisatie-, audit-, security-, datatype- en concurrencytests maken deel uit van
de volledige suites. Nieuwe transactietests bewijzen rollback, replay/concurrent
winner, geen extra autoriteit, migratiebehoud en voorstellen bij een nieuw Concept.

Tijdens ontwikkeling kwamen oude schema/head-aannames in tests en de CLI aan het
licht; die zijn gecorrigeerd. Tussentijdse mislukte of afgebroken runs zijn geen
bewijs voor de definitieve staat. Alleen de afgeronde eindruns hieronder tellen.

Confluence Architecture Overview & Domain Model is bijgewerkt naar **versie 15**
met een REF-154-aanvulling over bronparsing, migratie, ports, transacties en lokale
ongestagede status. De bestaande pagina-inhoud is volledig behouden en na het
schrijven opnieuw gecontroleerd.

## Impact en resterende grenzen

- Designtokens: geen gewijzigd. Globale componenten/varianten: geen gewijzigd.
- Pagina-specifiek: herbruikbare importpartials, native disclosures en voorbeeldgrid;
  bestaande spacingtoken, onder 40rem gestapelde paren, behoud van keyboard/focus.
- Functioneel: gedeelde bronparsing, volledige detectie, live voorbeelden en blijvende
  voorstellen. Geen Simple/Advanced-importmodus of extra wizardstap.
- Autorisatie en tenant isolation: geen nieuwe rechten of Cloud-infrastructuur.
- Database: één additieve migratie. Canonieke datatype-/exportcontracten ongewijzigd.
- API: één interne browserroute; geen nieuwe publieke REST-API of wijziging daarvan.
- Security: bestaande grenzen behouden en getest; geen productiesecrets toegevoegd.
- Dependencies/architectuur: bestaande ports/UoW, geen nieuwe afhankelijkheid;
  ADR-013 en bestaande documentatie uitgebreid, geen nieuw architectuurframework.
- Toegankelijkheid: semantiek/focus/responsive gedrag getest; geen volledige WCAG-audit
  of certificering geclaimd. Geen bekende resterende inconsistentie in deze scope.
- Bestaande tijdelijke uploadopslag blijft procesgebonden en onder bestaande TTL.
  Native Excel-floatprecisie die al in het bronbestand verloren is, is niet terug te
  halen. Tijdzones volgen de geïnstalleerde IANA-database. Meer dan zes fractiecijfers
  en Unix timestamps worden niet toegevoegd. Deze grenzen zijn expliciet.
- GitHub CI is nog niet uitgevoerd voor deze wijziging: zonder commit/push kan er
  geen onafhankelijke PR-run zijn. Niet mergen voordat die groen is.

## Gewijzigde bestanden en aanbevolen Git-handelingen

Branch: `feat/ref-154-source-formats`, gestart vanaf actuele main
`b4a2f812a0ea444e9c62cb61eca922159306971b`.

Git-status: 25 gewijzigde tracked bestanden en 10 nieuwe untracked bestanden;
geen staged diff. De onderstaande expliciete lijst is tegelijk de volledige bestandsinventaris.
Alleen aanbevolen voor later; deze commando's zijn **niet uitgevoerd**.

```sh
git add \
  docs/architecture/adr-013-canonical-field-datatypes.md \
  docs/development/audit-coverage.json \
  docs/development/audit-inventory.json \
  docs/development/audit-policy.md \
  docs/development/field-datatypes.md \
  docs/development/record-import-security.md \
  docs/development/ref-154-report.md \
  src/refrncr/audit.py \
  src/refrncr/cli.py \
  src/refrncr/concept_persistence.py \
  src/refrncr/migrations/versions/0017_ref_154_source_formats.py \
  src/refrncr/models.py \
  src/refrncr/persistence.py \
  src/refrncr/ports.py \
  src/refrncr/record_import.py \
  src/refrncr/source_formats.py \
  src/refrncr/static/css/components/buttons.css \
  src/refrncr/static/css/layouts/import.css \
  src/refrncr/static/js/refrncr.js \
  src/refrncr/templates/pages/datasets/import/mapping.html \
  src/refrncr/templates/pages/datasets/import/source_format.html \
  src/refrncr/templates/pages/datasets/import/source_format_fragments.html \
  src/refrncr/templates/pages/datasets/import/wizard.html \
  src/refrncr/web.py \
  tests/browser_source_formats_app.py \
  tests/dataset_helpers.py \
  tests/javascript/source_formats_browser.cjs \
  tests/test_concept_migration.py \
  tests/test_dataset_deletion.py \
  tests/test_dataset_version_migration.py \
  tests/test_import_wizard.py \
  tests/test_migrations.py \
  tests/test_record_import.py \
  tests/test_source_format_import.py \
  tests/test_source_format_transactions.py \
  tests/test_source_formats.py
git commit -m "feat(import): add source format options (REF-154)"
git push -u origin feat/ref-154-source-formats
```

Aanbevolen PR-titel: `feat(import): add source format options (REF-154)`.
Gebruik de repository-PR-template: beschrijf Map columns-broninterpretatie en
opgeslagen voorstellen, link REF-154, vermeld de definitieve testresultaten,
markeer datamodel en interne API-impact, en vermeld migratie 0017. Autorisatie en
secrets/configuratie veranderen niet. Maak geen PR/merge zolang de gebruiker alleen
een lokale, ongestagede wijziging heeft gevraagd.


## Herkomst- en kwaliteitsreview van de bestaande working tree

Deze vervolgcontrole begon zonder werkbestanden te wijzigen. De gebruiker bevestigde
expliciet dat de bestaande 25 gewijzigde en 10 nieuwe bestanden als mogelijke eerdere
REF-154-poging moesten worden onderzocht en, bij samenhang, behouden en afgemaakt.
Het tussenrapport is vóór corrigerende edits in de conversatie geleverd.
Conclusie: **coherent earlier implementation**.

### Exacte uitgangsstaat en herkomst

- Branch `feat/ref-154-source-formats`; geen staged wijzigingen en geen stashes.
- `HEAD`, `main` en na `git fetch --all --prune` ook `origin/main`:
  `b4a2f812a0ea444e9c62cb61eca922159306971b`.
- Reflog: branch checkout op 2026-09-12 13:50:22 +0200 vanaf main; geen
  implementatiecommit. De merge bevat REF-83/84/145 en de visual refresh.
- Tracked diff bij aanvang: 25 bestanden, 722 toevoegingen en 94 verwijderingen;
  10 aanvullende untracked bestanden. `git diff --check` gaf geen fouten.
- Alle **35 bestanden** waren byte-identiek aan Git-tree
  `df5a6a55f6eefc467d8a25449af4c7340b5ca55b`, onder meer bereikbaar via:
  `refs/codex/turn-diffs/checkpoints/bd59c58b30394bcc00a354142f675ca1543c5025c1dd7dfdedc9f0f72ded75db/4a7b89f67e548e7b02456bb0a1c762e677cee534ad8e1643f1c0f961d3e9bbc3/1789216656442/7e58caae-da98-4501-a3ef-ff546e6b9ffb`.
  Dit is een automatisch working-tree-checkpoint, geen commit op een productbranch.
  Het bewijst inhoudelijke gelijkheid, niet zelfstandig de auteur of ontstaanswijze.
- De herhaalde scan omvatte alle 3.754 Git-objecten: 1.711 blobs, 1.902 trees en
  141 commits. `fsck --full --no-reflogs --unreachable` vond 712 unreachable blobs,
  1.285 trees en 61 commits; alle blobs zijn inhoudelijk op de bronformaattermen
  gescand, inclusief objecten buiten branches/reflogs. De nieuwe bronformaatblobs
  horen bij bovengenoemd checkpoint. Er is geen andere oudere implementatie of
  verwijderingscommit aangetoond. Geen object is toegepast of gecherry-pickt.
- Eerdere testlogs onder `/tmp/ref154-complete-{sqlite,pg}.txt` en
  `/tmp/ref154-chrome-final.txt` bestaan en bevestigen de historische aantallen.
- Exacte Git-commandoutput en SHA-256 per uitgangsbestand zijn apart vastgelegd in
  `/tmp/ref154-review-baseline.json`, buiten de repository. Dit is lokale bewijslast,
  geen release-artifact of bestand voor staging.

De eerdere implementatie is dus nu teruggevonden als ongestagede lokale wijziging
plus checkpoint. Zij is nooit als REF-154-commit op main gekomen. Waarom ooit werd
ervaren dat de opties verdwenen waren, blijft onbekend; de oudere REF-145-documenten
beschrijven ze als uitgesteld werk. PR #49 en de verkeerde herstelbranch zijn niet
gebruikt of gemerged.

### Bestandscontrole

M = gewijzigd tracked bestand bij aanvang; N = nieuw untracked bestand. De volledige
staginglijst hierboven bevat exact dezelfde 35 paden. Alle bestanden sluiten aan op
actuele main en de REF-81/134/145-patronen, met onderstaande concrete rol.

| Groep | Status en bestand | Beoordeling |
| --- | --- | --- |
| Productiecode | M `src/refrncr/audit.py` | Bronformaatreview uitgesloten van business-audit; huidige summary behouden. |
| Productiecode | M `src/refrncr/cli.py` | Nieuwe head in bestaande preflight-/schemacontroles; noodzakelijk migratiegevolg. |
| Productiecode | M `src/refrncr/concept_persistence.py` | Voorstellen behouden voor identieke Fields bij Concept-initialisatie. |
| Productiecode | M `src/refrncr/models.py` | Apart voorstelmodel; geen wijzigingen aan canonieke Record-opslag. |
| Productiecode | M `src/refrncr/persistence.py` | Batch lezen en schrijven via bestaande Field-adapter/UoW. |
| Productiecode | M `src/refrncr/ports.py` | Kleine uitbreiding van bestaande persistence port. |
| Productiecode | M `src/refrncr/record_import.py` | Native waarden, volledige detectie, voorbeelden, gepinde Preview/Confirm-keuzes en atomische opslag. |
| Productiecode | N `src/refrncr/source_formats.py` | Gedeelde onveranderlijke allowlist, strikte parsers, ambiguïteit en IANA/DST. |
| Productiecode | M `src/refrncr/web.py` | Exacte formuliercontrole, CSRF/tokenbinding en shared-partial-response. |
| Templates/CSS/JS | M `src/refrncr/static/css/layouts/import.css` | Paginaspecifieke responsieve vergelijking; bestaande tokens. |
| Templates/CSS/JS | M `src/refrncr/static/js/refrncr.js` | Disclosure, asynchrone voorbeelden en focus; tijdens review race gecorrigeerd. |
| Templates/CSS/JS | M `src/refrncr/templates/pages/datasets/import/mapping.html` | Bronformaatpartial binnen bestaande Map columns. |
| Templates/CSS/JS | M `src/refrncr/templates/pages/datasets/import/wizard.html` | Continue to preview-label; geen nieuwe stap. |
| Templates/CSS/JS | N `src/refrncr/templates/pages/datasets/import/source_format.html` | Label, native details en gekoppelde escaped voorbeelden. |
| Templates/CSS/JS | N `src/refrncr/templates/pages/datasets/import/source_format_fragments.html` | Hergebruikt dezelfde partial voor live responses. |
| Migratie | N `src/refrncr/migrations/versions/0017_ref_154_source_formats.py` | Eén additieve tabel na 0016; nader beoordeeld hieronder. |
| Tests | M `tests/dataset_helpers.py` | Downgrade nieuwe tabel vóór historische fixtures. |
| Tests | M `tests/test_concept_migration.py` | Werkelijke head vóór downgrade; assertions behouden. |
| Tests | M `tests/test_dataset_deletion.py` | Head-assertie volgt migratie 0017. |
| Tests | M `tests/test_dataset_version_migration.py` | Historische baseline en head-assertie bijgewerkt. |
| Tests | M `tests/test_import_wizard.py` | Expliciet profiel bij invalid-input/error-report-tests; caps/escaping behouden. |
| Tests | M `tests/test_migrations.py` | Head-asserties bijgewerkt; geen oude migraties aangepast. |
| Tests | M `tests/test_record_import.py` | Expliciete bronkeuze vóór bestaande error assertions. |
| Tests | N `tests/test_source_formats.py` | Parsercatalogus, precisie, Boolean, temporal, DST en ambiguïteit; review vult grouping/ISO aan. |
| Tests | N `tests/test_source_format_import.py` | Full-file, native staging, Preview/Confirm, cancel/expiry/rollback/stale, escaping en binding. |
| Tests | N `tests/test_source_format_transactions.py` | Beide engines: atomische voorkeur, concurrentie, migratie en Concept; review versterkt rechten/constraints. |
| Tests | N `tests/browser_source_formats_app.py` | Wegwerpfixture, uitgebreid met Timestamp voor de race-regressie. |
| Tests | N `tests/javascript/source_formats_browser.cjs` | Echte Chrome, drie viewports, focus/keyboard/Escape en geselecteerde Preview; race-regressie toegevoegd. |
| Documentatie | M `docs/architecture/adr-013-canonical-field-datatypes.md` | Bronadapter naast ongewijzigd canoniek contract. |
| Documentatie | M `docs/development/audit-coverage.json` | Reviewoperatie en route; dubbel health-item en onnodige volgordewijzigingen gecorrigeerd. |
| Documentatie | M `docs/development/audit-inventory.json` | Nieuwe operatie/route; onnodige hersortering gecorrigeerd. |
| Documentatie | M `docs/development/audit-policy.md` | Summary-invariant, review-exclusie en actuele inventarisaantallen. |
| Documentatie | M `docs/development/field-datatypes.md` | Expliciete catalogus, grenzen, detectie en ambiguïteit. |
| Documentatie | M `docs/development/record-import-security.md` | UX, toegankelijkheid, security, UoW, migratie en rollback. |
| Documentatie | N `docs/development/ref-154-report.md` | Eén bestaand rapport, nu uitgebreid met deze herkomst- en kwaliteitsreview. |

Geen ongerelateerd functioneel gebruikerswerk gevonden. De enige ongerelateerde
ruis was de beperkte JSON-hersortering en dubbele health-verwijzing; deze is
gecorrigeerd met behoud van de noodzakelijke REF-154-inventarisuitbreiding.

### Migratie 0017: beoordeling

`down_revision = "0016_ref_84"` is de actuele enige voorganger. Het model en de
migratie stemmen overeen: `field_id` is een niet-null String(36) primaire sleutel
met FK naar `dataset_fields.id`, `ON DELETE CASCADE`; `choice` is niet-null JSON.
De PK verzorgt unieke opslag en de index voor lookup/cascade; een tweede index op
dezelfde sleutel is niet nodig. Er is geen extra JSON-shape-CHECK: de bestaande
applicatielaag valideert de versieerbare profielkeuze vóór succesvolle bevestiging.

Upgrade maakt alleen een lege tabel; bestaande Fields, Records, versies, identifiers
en audit worden niet gelezen als ander datatype of herschreven. PostgreSQL gebruikt
transactionele DDL; SQLite gebruikt de bestaande expliciete migratietransactie en
foreign-key-hercontrole. Downgrade verwijdert alleen voorkeuren; re-upgrade begint
met lege voorkeuren. Bestaande-data-upgrade/downgrade en FK/PK/NOT NULL/cascade
worden nu expliciet op beide engines getest. Historische migraties blijven intact;
CLI/head-fixtures zijn noodzakelijke integratiewijzigingen.

### Bevindingen en gerichte correcties

1. **Browserfocus-race gereproduceerd.** Een gecontroleerd vertraagd `/formats`-
   antwoord kon focus van de net ingevulde IANA-zone afhalen. De toegevoegde
   Chrome-regressie faalde vóór de fix op de focusassertie. `input` op een tijdzone
   maakt nu oudere antwoorden ongeldig en breekt ze af vóór `change`/blur. Dezelfde
   test slaagt daarna op alle drie viewports, met behoud van waarde én focus.
   Geen vaste sleeps of versoepelde assertions.
2. **Bevoegdhedenbewijs versterkt.** De oorspronkelijke test controleerde vooral de
   gebruikersrol. De test vergelijkt nu onderhoud/eigenaarswissel/exportbeheer vóór
   en na import, controleert concrete verboden beheeraanroepen en vergelijkt alle
   Field-definities. De huidige REF-73 owning-team-bevoegdheid is behouden: legacy
   editor/admin zijn dezelfde onderhoudscapability; er is geen nieuwe scheiding
   ingevoerd die de bestaande autorisatie zou wijzigen.
3. **Migratiebewijs uitgebreid.** Een aanvullende geparametriseerde test controleert
   het gemigreerde PK/FK/NOT NULL-schema, orphan/duplicate/null-afwijzing en echte
   Field-deletion-cascade op beide engines.
4. **Parserdekking aangevuld.** Acht vaste malformed grouping-gevallen voor de overige
   numerieke profielen en een ISO-T/fraction/non-ISO-separator-regressie toegevoegd.
5. **Documentatieruis gecorrigeerd.** Dubbel health-item verwijderd en oorspronkelijke
   volgorde van ongewijzigde inventarisitems behouden.

De volledige REF-154, REF-81, REF-134 en REF-145 zijn geraadpleegd. Achterhaalde REF-81
per-record-audit en legacy editor/admin zijn aantoonbaar vervangen door actuele main,
REF-73/149 en de expliciete opdracht; deze zijn niet opnieuw ingevoerd. De scope is
Community/core. Het normatieve `ref-145-import-ux-example.html` is opnieuw niet lokaal
gevonden, en de projectzoekactie vond alleen de Jira-verwijzing. De gebruiker is
gevraagd naar een vindplaats. Daarom claimen we geen vergelijking met dat bestand;
wel met alle uitgeschreven UX-eisen. Ook de attachment- en commentvelden van de
vier Jira-issues zijn expliciet gecontroleerd: alle vier attachmentlijsten zijn leeg. Geen nieuwe architectuur, rechten, dependency,
Simple/Advanced-modus of wizardstap.

### Nieuwe validatie

- Baseline zonder werkbestandswijzigingen: 138 parsercases passed; 19 import-/transactiecases
  passed en 12 bestaande PostgreSQL-fixture-skips; Ruff/format/diffcheck groen.
- Na gerichte aanvullingen: 147 parsercases plus één wizard-JavaScripttest passed.
- Gerichte import-/transactierun met PostgreSQL ingesteld: **33 passed, 0 skipped**.
- Echte Chrome na fix: **3/3 viewports passed**, 320/768/1440 × 1000 px.
  Screenshots op 320 en 1440 zijn visueel geïnspecteerd. De test bevat ook de
  gecontroleerd vertraagde response tijdens tijdzone-invoer.
- Bredere gerichte suite met PostgreSQL ingesteld: **654 passed, 0 skipped**,
  85,57 seconden. Alle gevraagde import-/datatypebestanden en wizard-JavaScript.
- Pre-commit voor alle tracked bestanden: alle zeven hooks passed.
- De eerste standalone SQLite-run binnen de sandbox eindigde met 2.066 passed,
  819 bestaande PostgreSQL-fixture-skips en drie failures door `Operation not
  permitted`: twee socket-bindtests en de bestaande PostgreSQL-connectiviteitstest.
  Er zijn geen tests aangepast; de volledige suite wordt ongewijzigd met lokale
  netwerktoegang herhaald. Deze eerste run is geen groen eindresultaat.
- Definitieve volledige suites en controle van nieuwe bestanden zijn hieronder vastgelegd.

Nieuwe logs gebruiken `/tmp/ref154-review-*`, zodat historische testlogs behouden
blijven. De geïsoleerde PostgreSQL 16-container heet `refrncr-ref154-review-pg16`,
alleen op 127.0.0.1:55416. Alle muterende PostgreSQL-tests gebruiken uitsluitend deze tijdelijke database.
De standalone SQLite-suite bevat daarnaast een bestaande, onvoorwaardelijke
`SELECT 1`-connectiviteitstest naar de ontwikkelcontainer op 5432; deze is read-only.
De ontwikkelcontainer en zijn data worden niet gewijzigd.
Versies opnieuw uitgelezen: Python 3.12.2, SQLite 3.51.0, PostgreSQL 16.15
(Debian 16.15-1.pgdg13+2, aarch64), Google Chrome 152.0.7977.84.


### Definitieve resultaten van deze review

| Controle | Definitieve uitkomst | Log |
| --- | --- | --- |
| Volledige standalone SQLite-configuratie | **2.069 passed, 819 skipped, 0 failed**, 3 bestaande warnings; 304,80 s | `/tmp/ref154-review-full-sqlite-final.txt` |
| Volledige suite met PostgreSQL 16 | **2.888 passed, 0 skipped, 0 failed**, 3 bestaande warnings; 689,71 s | `/tmp/ref154-review-full-pg.txt` |
| Gerichte import/datatype/wizard-JavaScriptsuite met PostgreSQL 16 | **654 passed, 0 skipped**, 85,57 s | `/tmp/ref154-review-targeted.txt` |
| Import-/transactie-/migratieaanvullingen op beide engines | **33 passed**, 6,61 s; ook opgenomen in bovenstaande suites | `/tmp/ref154-review-transactions.txt` |
| Chrome 152.0.7977.84 | **3/3 viewports passed**: 320, 768, 1440 × 1000 px; inclusief vertraagde response/tijdzonefocus | `/tmp/ref154-review-chrome-final.txt` |
| Ruff check | Passed | Uitvoer in conversatie |
| Ruff format --check | Passed, 162 Python-bestanden | Uitvoer in conversatie |
| pre-commit --all-files | Alle zeven hooks passed | `/tmp/ref154-review-precommit.txt` |
| pre-commit --files voor alle tien nieuwe bestanden | Alle toepasselijke hooks passed; geen nieuwe YAML/TOML-bestanden | `/tmp/ref154-review-precommit-new.txt` |
| git diff --check | Passed | Uitvoer in conversatie |

De 819 skips gebruiken de bestaande PostgreSQL-fixtures bij een ontbrekende URL;
alle betreffende gevallen draaien in de PostgreSQL-run en zijn daar geslaagd.
De warnings zijn één bestaande Starlette/httpx-deprecatie en tweemaal de bestaande
SQLite Python-datetime-adapterdeprecatie. Geen tests afgezwakt, extra skipcodes,
vaste sleeps of onderdrukte warnings toegevoegd. De volledige runs omvatten ook de
bestaande autorisatie-, audit-, concurrency-, security-, migratie- en packaged-CLI-tests.

De aanvankelijke SQLite-failures door sandbox-netwerkblokkades zijn onderzocht en
met ongewijzigde tests opgelost door uitvoering met lokale netwerktoegang. De
vooraf falende Chrome-raceregressie staat in `/tmp/ref154-review-chrome-before.txt`;
het groene eindresultaat vereist de gerichte input/focusfix.

De uitgebreide gerichte run gebruikt exact de tien hierboven genoemde testbestanden.
De full runs gebruiken `.venv/bin/python -m pytest -q --tb=short`, met respectievelijk
`REFRNCR_TEST_POSTGRES_URL` unset en ingesteld op de tijdelijke PostgreSQL 16-database.
De Chrome-run gebruikt het bestaande `tests/javascript/source_formats_browser.cjs`,
`REFRNCR_PLAYWRIGHT_MODULE=/tmp/ref134-browser/node_modules/playwright` en
`REFRNCR_BROWSER_EXECUTABLE=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`.
Er is geen projectdependency geïnstalleerd. De in-app-browser rapporteerde opnieuw
`Browser is not available: iab`; de beschreven echte Chrome-fallback is toegepast.

### Eindstaat en resterende beperkingen

Van de oorspronkelijke 35 bestanden zijn **26 byte voor byte behouden**; de negen
reviewwijzigingen betreffen alleen JavaScript, aanvullende tests/fixture en documentatie.
Er zijn geen extra bestandsnamen toegevoegd ten opzichte van de onderzochte set.
Geen reset, restore, stash, staging, commit, push, PR, merge of release uitgevoerd.
Branch blijft `feat/ref-154-source-formats`, HEAD blijft `b4a2f81`, status blijft
**25 gewijzigde tracked + 10 nieuwe untracked bestanden, niets staged**.
De expliciete `git add`-lijst en aanbevolen commit/push hierboven gelden ongewijzigd.

De parsercatalogus, migratie, Field-port en atomische importimplementatie zijn behouden.
Er is geen bekende resterende codefout binnen de gecontroleerde scope. De ontbrekende
HTML-referentie kon niet rechtstreeks worden vergeleken; de uitgeschreven Jira-UX en
responsieve/keyboard-contracten zijn wel gecontroleerd. Er wordt geen volledige
WCAG-certificering geclaimd. De bestaande beperkingen voor native Excel-precisie,
zes fractiecijfers, IANA-database en procesgebonden upload-TTL blijven gelden.

Design tokens, globale componenten en varianten zijn niet gewijzigd in deze review;
de bestaande pagina-specifieke importregels zijn behouden. De functionele reviewfix
beschermt alleen actuele tijdzone-invoer/focus tegen oude asynchrone antwoorden.
Geen gewijzigde autorisatie, Cloud-isolatie, canoniek datatypecontract, schema bovenop
0017, publieke API, dependency of nieuwe architectuur. De bestaande REF-154-aanvulling
in Confluence hoefde hiervoor niet te worden gewijzigd; deze review schrijft geen
externe documenten of berichten. GitHub CI blijft de onafhankelijke gate vóór merge
en kan zonder de niet-geautoriseerde commit/push niet voor deze wijziging draaien.

De tijdelijke browserfixture en `refrncr-ref154-review-pg16` zijn na de geslaagde
controles gestopt. De container is behouden en niet verwijderd.


## Follow-up: visible but inert Advanced options button

The user reported that Advanced options did not open its controls. A real-Chrome
regression reproduced a concrete uncovered case: with only String/ignored columns,
the controller correctly set `hidden`, but the shared `.button { display: inline-flex }`
rule overrode the browser's native hidden rendering. The action remained visible
with zero expandable format controls. The previous browser suite only exercised
mappings with applicable Decimal/Date controls, so it missed this state.

Classification: **global component**. `.button[hidden] { display: none; }` is added
to `src/refrncr/static/css/components/buttons.css`, at the same reusable level as the
conflicting display rule. No parser, migration, server authorization, persistence,
design token, component variant or separate Advanced mode changes. The existing
per-field progressive disclosure remains the approved behavior.

Chrome now checks String/ignored-only mappings, changing an ignored numeric column
to Decimal, opening the actual labelled format selector, and ignoring that column
again. All existing disclosure, conversion, focus/race and responsive checks remain.
The new visibility assertion failed before the CSS fix and passes after it at
320, 768 and 1440 px. The JavaScript served by the running local app matched the
working-tree controller; no stale-JavaScript explanation was assumed. The in-app
browser remained unavailable, so the existing isolated Chrome fixture was used.

The user was asked which datatypes and local URL were affected; no answer was
available during this reproduction. This establishes and fixes the inert-button
case, without asserting that an unobserved browser session had that exact state.

Logs: `/tmp/ref154-button-chrome-final.txt`, `/tmp/ref154-button-pytest.txt`,
`/tmp/ref154-button-precommit.txt`. Chrome is green at all three viewports; full
pytest and final checks are recorded after completion below.

This follow-up adds one tracked filename to the prior inventory:
`src/refrncr/static/css/components/buttons.css` (shared hidden-button rendering).
The explicit staging command above includes it. Expected final status is now
26 modified tracked files and 10 new files, all unstaged on the same branch.
The previous 35-file counts describe the preserved review baseline.


Follow-up final validation: **2,888 passed, 0 skipped, 0 failed** in 533.36 seconds,
with the same three existing deprecation warnings, using both SQLite and PostgreSQL 16
fixtures in the full suite. Chrome passes all three viewports, including the new
hidden/actionable/dynamically-remapped states. The focused wizard/import run passed
15 cases with seven existing PostgreSQL-fixture skips; those cases are all covered
by the full enabled run. Ruff check, Ruff format --check, all seven pre-commit hooks,
and diff checking pass. The running application on port 8000 was directly verified
to serve the corrected CSS. Reloading the wizard picks up the change.

The standalone Chrome test and updated untracked report were additionally checked
with pre-commit --files. The temporary browser fixture is stopped. No staging,
commit, push or changes to application data were performed. The final staging list
contains 26 modified tracked files and the same ten untracked files. No design tokens,
component variants or format functionality changed; the shared button component now
honors hidden state. There is no known remaining defect in the reproduced case.


## REF-158 follow-up: compact temporal selection

REF-158 replaces the long temporal dropdown with composable Date/Time controls
and an explicit Native Excel alternative. The REF-154 catalogue, canonical
identifiers, parsing/detection, successful-confirmation preferences and migration
0017 are retained. Float ordinary/scientific notation already worked and remains
one profile. Irrelevant timezone/fold submissions now fail closed rather than being
silently removed. Historical test results above are unchanged; current UX and
new validation evidence are recorded in [the REF-158 report](ref-158-report.md).
