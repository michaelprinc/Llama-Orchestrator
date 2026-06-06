# Report: Asynchronni aktualizace profilu a SQL cteni pro GUI odezvu

**Datum:** 2026-06-05  
**Komponenta:** `infra-local/llama-orchestrator`  
**Status:** Recommendation report  
**Autor:** Codex  

---

## 1. Shrnutí

Hypoteza je castecne spravna: presun pomalych aktualizaci profilu mimo hlavni GUI vlakno a cteni z pripravenych SQL snapshotu muze zlepsit odezvu GUI, pokud je realnou brzdou opakovane nacitani a validace mnoha `instances/*/config.json` souboru.

Pro soucasny stav `llama-orchestrator` ale neni vhodne udelat z SQL databaze primarni uloziste profilu jen kvuli rychlosti GUI. Projekt uz ma:

- `state/instance_catalog.sqlite` pro katalog identity profilu, poradova cisla a synchronizaci metadat;
- `state/state.sqlite` pro runtime stav procesu;
- `state/benchmark_history.sqlite` pro benchmark historii;
- autoritativni `instances/*/config.json` pro vlastni konfigurace profilu.

Doporuceni: zachovat `config.json` jako autoritativni zdroj profilu a SQL pouzit jen jako read-through/read-model cache pro rychle seznamy a predvypoctene GUI hodnoty. Nejcistsi prvni krok je jednodussi: rozsirit existujici GUI fast-path a refresh snapshot pattern, ne zavadet novy asynchronni zapis profilu.

---

## 2. Aktuální datový tok

GUI `refresh()` aktualne provadi synchronne:

1. zachyceni vyberu/fokusu v tabulce;
2. `list_instances()` pro runtime stav;
3. `load_all_instances()` pro nacteni vsech profilovych `config.json`;
4. `collect_detected_gpu_inventory(configs.values())`;
5. `latest_benchmark_results()` ze SQLite benchmark historie;
6. sestaveni radku tabulky;
7. plne smazani a vlozeni radku v Tkinter `Treeview`;
8. render GPU panelu, tag filtru, benchmark controls a daemon statusu.

Existujici performance plan z 2026-06-02 uz spravne identifikoval, ze problemem neni jen uloziste. Cast latence muze byt:

- validace a JSON deserializace profilu;
- GPU detekce;
- cteni benchmark historie;
- plny rebuild `Treeview`;
- pomocne renderovani panelu a filtru;
- synchronni prace v hlavnim Tkinter vlakne.

Proto SQL samo o sobe nezaruci rychle GUI, pokud refresh stale dela GPU detekci a plne prekresleni tabulky.

---

## 3. Hodnocení hypotézy

### Kdy návrh pomůže

Asynchronni aktualizace profilu a synchronni cteni ze SQL muze pomoct, pokud:

- GUI potrebuje caste refreshovani 50-100+ profilu;
- jeden profil obsahuje rozsahla `model_metadata`;
- validace Pydantic modelu je meritelne pomala;
- konfigurace jsou casto meneny mimo GUI a katalog muze fungovat jako rychly index;
- SQL read model obsahuje uz predpripravene sloupce pro tabulku, napriklad `display_name`, `port`, `backend`, `tags`, `model_path`, `model_size`, `quantization`, `architecture`, `args_text`.

V takovem pripade muze GUI nacist maly, plochy dataset jednim SQL dotazem a teprve pri detailni akci otevrit plny `config.json`.

### Kdy návrh nepomůže

Navrh nepomuze nebo pomuze malo, pokud:

- nejvetsi cas zabira `collect_detected_gpu_inventory()`;
- nejvetsi cas zabira plny `Treeview` delete/insert render;
- UI akce je lokalni, napriklad queue checkbox, a nepotrebuje vubec cist konfigurace;
- profilova data se po startu GUI meni malo;
- SQL tabulka stale vyzaduje okamzitou synchronizaci z `config.json` pri kazdem refreshi.

V techto pripadech by SQL jen pridalo dalsi vrstvu bez odstraneni skutecneho bloku v GUI vlakne.

---

## 4. Klady

- Rychlejsi seznam profilu: SQL read model muze vratit tabulkova data bez prochazeni filesystemu a Pydantic validace vsech profilu.
- Lepsi oddeleni autoritativnich dat a GUI projekce: `config.json` zustane zdroj pravdy, SQL bude optimalizovany pohled.
- Snadne trideni/filtrovani: tagy, statusy a model metadata mohou byt indexovane nebo ulozene jako normalizovane sloupce.
- Vyssi odolnost GUI pri chybnem profilu: SQL muze drzet posledni validni snapshot a detail chyby zobrazit bez rozbiti cele tabulky.
- Dobry smer pro budouci benchmark grid: vysledky a run metadata uz prirozene patri do SQLite.

---

## 5. Zápory a rizika

- Dve pravdy, pokud neni synchronizace striktne navrzena. Autoritativni musi zustat jen jeden zdroj.
- Komplexnejsi invalidace: profil muze zmenit GUI, CLI, rucni editace souboru, migrace nebo import metadat.
- Race conditions: asynchronni zapis nesmi zpusobit, ze GUI ukaze kombinaci stareho `config.json` a noveho SQL radku.
- Vyssi testovaci narocnost: je potreba testovat rebuild katalogu, stale snapshoty, selhani zapisu, soubezne refreshovani a manualni refresh.
- SQLite write locky: pri castejsich zapisech je nutne hlidat `busy_timeout`, WAL a kratke transakce.
- Neodstrani Tkinter omezeni: widgety se stale musi aktualizovat jen z hlavniho vlakna.

---

## 6. Doporučená jednoduchá praxe

### Doporučení A: měřit a optimalizovat refresh fáze

Nejjednodussi a nejbezpecnejsi praxe je rozsirit existujici timing instrumentation pro `refresh()` a potvrdit, kde je latence. Uz existuje `LLAMA_ORCH_DEBUG_GUI_TIMING=1`; report z 2026-06-02 doporucuje rozpad na faze: state, configs, GPU detection, benchmark, row build, sort, Treeview render, metadata a daemon status.

Acceptance kriterium: pred zmenou SQL vrstvy musi byt jasne, ze `load_all_instances()` je vyznamny podil latence.

### Doporučení B: zachovat lokální fast-path pro UI-only akce

Toto uz je implementacne nejlevnejsi smer:

- queue checkbox menit pres `tree.set(...)`, bez `refresh()`;
- multi-row queue toggle delat stejnym fast-pathem;
- benchmark controls aktualizovat lokalne;
- refresh spoustet jen pro skutecne zmeny dat.

Tento pristup primo zlepsuje klikaci odezvu a nema riziko stale SQL dat.

### Doporučení C: cache GPU inventory s explicitní invalidací

Pokud mereni ukaze, ze GPU detekce brzdi refresh, preferovat kratkodobou cache pred SQL profile migraci:

- cache drzet v instanci GUI;
- invalidovat pri start/stop/restart, zmene GPU mapovani a manualnim refreshi;
- background vlakno nesmi volat Tkinter widgety primo.

### Doporučení D: SQL read model jen jako druhá fáze

Pokud mereni potvrdi, ze cteni profilu je stale hlavni problem, zavest read model nad existujicim `instance_catalog.sqlite`:

1. Pridat tabulku nebo sloupce pro GUI summary, ne menit autoritativni config schema.
2. Pri `save_config()` synchronne aktualizovat `config.json` i katalog v jedne kratke sekvenci.
3. Pri startu nebo manualnim refreshi umet katalog rebuildovat z `instances/*/config.json`.
4. V GUI cist summary radky ze SQL, plny `InstanceConfig` nacitat az pri editaci/detailu.
5. Pri chybe katalogu fallbacknout na soucasne `load_all_instances()`.

Toto je vhodnejsi nez "asynchronni aktualizace profilu do SQL" jako primarni mechanismus, protoze zachova konzistenci a auditovatelnost.

---

## 7. Doporučený návrh architektury

```text
instances/*/config.json
  autoritativni profil, editace, export, kompatibilita
        |
        | save_config(), migration, explicit catalog rebuild
        v
state/instance_catalog.sqlite
  rychly read model: identity + GUI summary + source hash + updated_at
        |
        | GUI refresh reads compact rows
        v
Tkinter Treeview
  renderuje snapshot, lokalni UI-only zmeny bez full refresh
```

Katalog by mel obsahovat minimalne:

- `instance_uid`, `instance_no`, `name`, `display_name`, `dir_path`;
- `config_hash`, `config_mtime`, `catalog_updated_at`;
- `port`, `backend`, `tags_json`, `model_path`, `args_text`;
- `model_size_gb`, `quantization`, `architecture`;
- `last_error`, pokud profil nejde nacist.

Manualni refresh muze vynutit rebuild katalogu a tim opravit nesoulad po rucni editaci souboru.

---

## 8. Závěr

Navrh je technicky vhodny jen jako read-model/cache vrstva, ne jako nahrada souborovych profilu. Pro rychle zlepseni GUI odezvy doporucuji tento postup:

1. Dokoncit mereni refresh fazi.
2. Preferovat UI fast-path a incremental render pred databazovou migraci.
3. Cacheovat GPU inventory, pokud je meritelne pomale.
4. Teprve potom rozsirit `instance_catalog.sqlite` o GUI summary sloupce.

Ocekavany nejlepsi pomer efektu k riziku ma porad tento smer: lokalni aktualizace widgetu pro jednoduche kliky + mene plnych refreshu + lazy/cached drahe operace. SQL read model je dobry follow-up, pokud profiling ukaze, ze nacitani profilu zustava dominantni brzda.
