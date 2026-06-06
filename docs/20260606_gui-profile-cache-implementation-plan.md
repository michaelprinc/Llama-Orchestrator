# Implementation Plan: GUI Profile SQL Cache

**Datum zahajeni:** 2026-06-06  
**Metodologie:** Spec Kit - Plan + Tasks + Analyze  
**Typ zmeny:** performance / internal read-model cache  
**Status:** Draft  
**Navazujici report:** `20260605_gui-profile-sql-async-evaluation-report.md`

---

## 1. Cil

Zrychlit odezvu GUI `llama-orchestrator` pri zobrazovani seznamu profilu pomoci SQLite read-model cache, aniz by se zmenil autoritativni zdroj konfigurace.

Klicove rozhodnuti:

- `instances/*/config.json` zustava primarni zdroj pravdy.
- `state/instance_catalog.sqlite` muze obsahovat pouze cache / read model pro GUI.
- Pred kazdou akci, ktera startuje, stopuje, restartuje, edituje, benchmarkuje, importuje, maze nebo jinak meni instanci, musi kod nacist a validovat aktualni lokalni `config.json`.
- SQL cache nesmi byt pouzita jako jediny zdroj pro vykonani akce.

Uspech znamena, ze rutinni refresh GUI muze nacitat kompaktni tabulkova data rychleji, ale behavior a bezpecnost akci zustanou navazane na overeny lokalni JSON profil.

---

## 2. Scope

### In scope

- Rozsireni existujiciho `instance_catalog.sqlite` o GUI summary read model.
- Hash/mtime sledovani `config.json` pro detekci stale cache.
- Rebuild cache z `instances/*/config.json` pri startu GUI a manualnim refreshi.
- GUI refresh cesta, ktera umi nacist summary radky z cache.
- Fallback na aktualni `load_all_instances()` pri chybe cache nebo stale datech.
- Overeni lokalniho `config.json` pred kazdou akci.
- Timing instrumentation pro srovnani pred/po.
- Unit testy pro cache schema, rebuild, stale detection, fallback a action-time JSON verification.

### Out of scope

- Presun autoritativnich profilu do SQL.
- Asynchronni zapis jako primarni mechanismus persistence profilu.
- Zmena `InstanceConfig` JSON kontraktu mimo nezbytne aditivni metadata, pokud by byla potreba.
- Web UI nebo nahrada Tkinter.
- Zmena runtime/daemon protokolu.
- Optimalizace benchmark grid historie.

---

## 3. Vstupni kontext

Relevantni soubory:

- `src/llama_orchestrator/config/loader.py` - `load_config()`, `load_all_instances()`, `save_config()`, `instance_catalog.sqlite`.
- `src/llama_orchestrator/gui.py` - `refresh()`, `_collect_refresh_snapshot()`, `_build_table_rows()`, action handlers.
- `src/llama_orchestrator/engine/state.py` - `state.sqlite` runtime stav.
- `src/llama_orchestrator/benchmark.py` - `benchmark_history.sqlite`.
- `tests/test_loader.py`, `tests/test_gui.py`, `tests/test_config_migration.py` - existujici testovaci kotvy.
- `docs/20260602_gui-click-response-performance-implementation-plan.md` - souvisejici GUI performance plan.
- `docs/20260605_gui-profile-sql-async-evaluation-report.md` - rozhodnuti o SQL jako cache vrstve.

Soucasny stav:

- `load_all_instances()` stale prochazi `instances/*/config.json` a validuje plne Pydantic profily.
- `save_config()` zapisuje JSON a synchronizuje zakladni identity metadata do `instance_catalog.sqlite`.
- GUI `refresh()` krom profilu nacita runtime stav, GPU inventory a benchmark historii.
- Cast GUI fast-path optimalizaci uz existuje nebo je planovana mimo tuto cache vrstvu.

---

## 4. Clarify a predpoklady

### Potvrzene informace

- SQL vrstva ma byt cache pro GUI, ne zdroj pravdy.
- Pred provedenim akce ma byt overen lokalni `.json` soubor.
- Zachovani kompatibility `instances/*/config.json` je prioritni.

### Predpoklady

- [ASSUMPTION] Prvni verze cache bude synchronni a deterministicka; asynchronni rebuild se prida pouze pokud mereni ukaze potrebu.
- [ASSUMPTION] Manualni refresh v GUI muze vynutit rebuild cache a znovu nacteni JSON profilu.
- [ASSUMPTION] Auto-refresh muze pouzit posledni validni cache snapshot, pokud hash/mtime ukazuje konzistenci.
- [ASSUMPTION] Pri chybe jednoho profilu cache ulozi `last_error`, ale GUI neshodi cely refresh.
- [ASSUMPTION] Cache summary bude obsahovat jen data potrebna pro tabulku; detail/editace stale nacte plny JSON.

### Otevrene body

- Potvrdit, zda auto-refresh ma stale kontrolovat `mtime` vsech `config.json`, nebo jen pouzit cache do pristiho manualniho refresh.
- Rozhodnout, zda stale cache ma byt v GUI viditelna jako activity log zprava.
- Rozhodnout, zda cache rebuild ma byt dostupny i jako CLI prikaz, napriklad `llama-orch config cache rebuild`.

---

## 5. Navrh architektury

```text
instances/*/config.json
  autoritativni profil
        |
        | load_config(), save_config(), explicit rebuild
        v
state/instance_catalog.sqlite
  identity + GUI summary cache + source hash/mtime + last_error
        |
        | GUI refresh reads compact summary rows when fresh
        v
Tkinter GUI table
        |
        | user action selected
        v
reload and validate local config.json before action execution
```

### Cache pravidla

- Cache radek je platny pouze pokud `source_path`, `config_hash` a/nebo `config_mtime_ns` odpovida aktualnimu souboru.
- Pokud validace JSON selze, cache muze drzet posledni validni summary, ale musi ulozit `last_error` a `cache_status = invalid`.
- GUI smi zobrazit invalid radek s chybou, ale nesmi z nej provest akci bez nove validace JSON.
- `save_config()` musi po uspesnem zapisu synchronne aktualizovat cache pro dany profil.
- Manualni refresh muze rebuildovat celou cache z disku.

### Action-time verification

Vsechny akce, ktere pracuji s profilem, musi pouzit helper:

```text
resolve selected name/uid/no
  -> resolve config path
  -> load_config(path, persist_backfill=False or explicit mode)
  -> verify identity matches selected cache row
  -> execute action with fresh InstanceConfig
```

Pravidlo identity:

- Preferovat `instance_uid`.
- Pokud `instance_uid` chybi u legacy profilu, pouzit stavajici migration/backfill cestu.
- Pokud cache a JSON nesedi v `instance_uid` nebo `name`, akci odmitnout a vyzvat k refreshi.

---

## 6. Navrh implementace

### Workstream A - Measurement and baseline

- Rozsirit timing pro `refresh()` tak, aby oddelil `load_all_instances()` od cache read cesty.
- Zmerit baseline pro 65 profilu:
  - full refresh;
  - config load phase;
  - row build;
  - Treeview render;
  - GPU detection.
- Vytvorit kratky performance report s rozhodnutim, jestli cache prinese smysluplny efekt.

### Workstream B - Cache schema

- Rozsirit `instance_catalog.sqlite` aditivne.
- Navrhovane sloupce nebo tabulka `instance_gui_summary`:
  - `instance_uid TEXT PRIMARY KEY`
  - `instance_no TEXT`
  - `name TEXT`
  - `display_name TEXT`
  - `dir_path TEXT`
  - `source_path TEXT`
  - `config_hash TEXT`
  - `config_mtime_ns INTEGER`
  - `cache_status TEXT`
  - `last_error TEXT`
  - `updated_at TEXT`
  - `port INTEGER`
  - `backend TEXT`
  - `tags_json TEXT`
  - `model_path TEXT`
  - `args_text TEXT`
  - `model_size_gb REAL`
  - `quantization TEXT`
  - `architecture TEXT`
- Pridat schema migration s `PRAGMA user_version` nebo kompatibilni lokalni verzovani.
- Zapnout kratky `busy_timeout` a ponechat kratke transakce.

### Workstream C - Cache builder and validation

- Pridat helpery v `config/loader.py` nebo novem modulu `config/catalog.py`:
  - `build_gui_summary(config, path)`.
  - `upsert_gui_summary(config, path)`.
  - `rebuild_instance_gui_summary()`.
  - `load_gui_summary_rows()`.
  - `is_gui_summary_fresh(row)`.
- Summary builder musi pouzivat plne validovany `InstanceConfig`.
- Cache builder nesmi menit obsah profilu, pouze cist a zapisovat summary.
- Pri chybnem profilu ulozit error metadata bez prepisu poslednich validnich sloupcu, pokud existuji.

### Workstream D - Save and migration integration

- Po `save_config()` aktualizovat summary cache pro zapisovany profil.
- Pri lazy backfill/migration chranit poradek:
  - nejdrive uspesne zapsat `config.json`;
  - potom aktualizovat cache;
  - pri selhani cache neznehodnotit zapsany JSON, jen zalogovat/fallbacknout.
- Pri presunu/renamovani legacy adresaru zajistit, ze cache drzi nove `source_path` a `dir_path`.

### Workstream E - GUI read path

- Upravit `_collect_refresh_snapshot()` tak, aby umela pouzit cache summary pro tabulkove config hodnoty.
- Zachovat fallback na `load_all_instances()`:
  - cache DB neni dostupna;
  - schema je neplatne;
  - cache je stale;
  - explicitni manual refresh vynutil full load.
- Runtime state, benchmark results a GPU inventory zustanou oddelene zdroje.
- Row builder by mel prijmout bud plny `InstanceConfig`, nebo lightweight summary objekt.
- GUI musi viditelne zachovat stejny obsah tabulky jako pred zmenou pro validni profily.

### Workstream F - Action-time JSON verification

- Pridat helper pro akce, napriklad `_load_fresh_config_for_action(name)`.
- Pouzit ho ve vsech relevantnich GUI akcich:
  - start;
  - stop/restart, pokud vyzaduje config;
  - edit args;
  - open/export config;
  - clone/import metadata;
  - quick/serial/grid benchmark;
  - delete/rename/display-name update;
  - GPU mapping changes.
- Helper musi porovnat `instance_uid` a `name` proti vybranemu radku/cache.
- Pri nesouladu zobrazit message/activity log a neprovadet akci.

### Workstream G - Tests

- Unit testy pro schema init/migration.
- Unit testy pro summary extraction z `InstanceConfig`.
- Test stale detection pri zmene `config.json`.
- Test rebuild s jednim chybnym profilem.
- Test fallback na `load_all_instances()`.
- Test, ze action helper nacte JSON i kdyz GUI refresh pouzil cache.
- Test identity mismatch odmita akci.
- Regression test pro `save_config()` aktualizujici cache.

### Workstream H - Documentation and rollout

- Aktualizovat README nebo relevantni docs sekci o tom, ze SQL je cache.
- Dopsat implementacni report s merenim pred/po.
- Dokumentovat rollback:
  - smazat nebo ignorovat `state/instance_catalog.sqlite`;
  - vratit GUI na `load_all_instances()` path;
  - zachovat `config.json` beze zmen.

---

## 7. Rollback a bezpecnost

Rollback musi byt jednoduchy:

1. Vypnout cache read path feature flagem nebo fallback podminkou.
2. Smazat `state/instance_catalog.sqlite`, pokud je potreba rebuild.
3. Pokracovat pres stavajici `load_all_instances()` bez ztraty profilu.

Bezpecnostni guardrails:

- SQL cache nikdy nesmi obsahovat tajne tokeny.
- Cache nesmi zapisovat do `config.json`.
- Akce musi selhat zavrene, pokud JSON validace selze.
- Nesoulad cache vs JSON je duvod k odmítnuti akce, ne k automaticke oprave bez potvrzeni.

---

## 8. Acceptance criteria

- GUI refresh umi nacist validni profile summary z SQL cache.
- `config.json` zustava autoritativni a kompatibilni.
- Manualni refresh umi rebuildovat cache z lokalnich JSON souboru.
- Pri chybe cache existuje fallback na aktualni JSON load path.
- Pred kazdou akci se nacte a validuje lokalni `config.json`.
- Identity mismatch mezi cache a JSON zastavi akci.
- Testy pokryvaji cache schema, rebuild, stale detection, fallback a action-time verification.
- Performance report ukazuje pred/po mereni nebo vysvetluje, proc cache nebyla aktivovana.
