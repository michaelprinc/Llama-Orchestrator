# Checklist: GUI Profile SQL Cache

**Datum:** 2026-06-06  
**Metodologie:** Spec Kit - Tasks  
**Navazujici plan:** `20260606_gui-profile-cache-implementation-plan.md`  
**Status:** Draft

---

## Faze A - Measurement and baseline

- [ ] A1 Zapnout/rozsirit timing instrumentation pro `refresh()` faze.
- [ ] A2 Zmerit baseline full refresh pro reprezentativni pocet profilu.
- [ ] A3 Oddelit v mereni config load, GPU detection, benchmark load, row build a Treeview render.
- [ ] A4 Vytvorit kratky baseline report nebo doplnit implementacni report.

**Acceptance checks**

- [ ] Je zrejme, zda `load_all_instances()` je dominantni nebo vyznamny zdroj latence.
- [ ] Mereni je porovnatelne s pozdejsim cache merenim.

## Faze B - Cache schema

- [ ] B1 Navrhnout aditivni schema pro GUI summary cache.
- [ ] B2 Implementovat schema init/migration pro `instance_catalog.sqlite`.
- [ ] B3 Pridat sloupce pro `config_hash`, `config_mtime_ns`, `cache_status`, `last_error`.
- [ ] B4 Pridat summary pole pro GUI tabulku: port, backend, tags, model, args, model size, quantization, architecture.
- [ ] B5 Nastavit kratke SQLite transakce a rozumny timeout.

**Acceptance checks**

- [ ] Existujici katalog identity zustava kompatibilni.
- [ ] Schema je aditivni a neni nutne menit `config.json`.
- [ ] Cache neuklada tajne tokeny ani citlive runtime hodnoty mimo nutny GUI summary rozsah.

## Faze C - Cache builder and validation

- [ ] C1 Implementovat `build_gui_summary(config, path)`.
- [ ] C2 Implementovat `upsert_gui_summary(config, path)`.
- [ ] C3 Implementovat `load_gui_summary_rows()`.
- [ ] C4 Implementovat `is_gui_summary_fresh(row)`.
- [ ] C5 Implementovat `rebuild_instance_gui_summary()`.
- [ ] C6 Pri chybnem profilu ulozit `last_error` a `cache_status = invalid`.

**Acceptance checks**

- [ ] Summary vznikne jen z validovaneho `InstanceConfig`.
- [ ] Rebuild umi zpracovat vice profilu bez padu celeho procesu kvuli jednomu chybnemu JSON.
- [ ] Stale cache je detekovana podle hash/mtime.

## Faze D - Save and migration integration

- [ ] D1 Upravit `save_config()` tak, aby po uspesnem JSON zapisu aktualizoval cache.
- [ ] D2 Osetrit cache update selhani bez rollbacku validne zapsaneho JSON.
- [ ] D3 Zajistit spravne `source_path` a `dir_path` po legacy migracich.
- [ ] D4 Pridat test pro `save_config()` aktualizujici GUI summary cache.

**Acceptance checks**

- [ ] `config.json` zustava primarni a zapisuje se pred cache.
- [ ] Selhani cache neznici ani neprepise profil.
- [ ] Po ulozeni profilu odpovida cache novemu JSON hash/mtime.

## Faze E - GUI read path

- [ ] E1 Pridat lightweight summary objekt pro tabulkove radky.
- [ ] E2 Upravit `_collect_refresh_snapshot()` pro volitelne cteni z cache.
- [ ] E3 Zachovat fallback na `load_all_instances()`.
- [ ] E4 Manualni refresh musi umet vynutit rebuild cache.
- [ ] E5 Auto-refresh nesmi blokovat GUI pri cache chybe.
- [ ] E6 Overit stejny vizualni obsah tabulky pro validni profily.

**Acceptance checks**

- [ ] GUI refresh pouzije cache jen kdyz je platna.
- [ ] Cache chyba nebo stale cache nezpusobi pad GUI.
- [ ] Tag filter, sort, benchmark sloupce a GPU panel zustanou funkcne kompatibilni.

## Faze F - Action-time JSON verification

- [ ] F1 Implementovat helper `_load_fresh_config_for_action(...)` nebo ekvivalent.
- [ ] F2 Helper vzdy nacte lokalni `config.json` pred akci.
- [ ] F3 Helper overi `instance_uid` a `name` proti vybrane instanci.
- [ ] F4 Pri identity mismatch odmitnout akci a vyzvat k refreshi.
- [ ] F5 Napojit helper na start/stop/restart/edit/benchmark/import/export/delete/rename akce.

**Acceptance checks**

- [ ] Zadna profilova akce nepouzije SQL cache jako jediny zdroj pravdy.
- [ ] Chybny nebo nevalidni JSON zastavi akci.
- [ ] Rucne zmeneny JSON pred akci je znovu nacten a validovan.

## Faze G - Tests

- [ ] G1 Test schema init/migration.
- [ ] G2 Test summary extraction.
- [ ] G3 Test stale detection pri zmene `config.json`.
- [ ] G4 Test rebuild s jednim chybnym profilem.
- [ ] G5 Test fallback na full JSON load.
- [ ] G6 Test action helper nacita JSON i po cache refreshi.
- [ ] G7 Test identity mismatch odmita akci.
- [ ] G8 Test manual refresh rebuild behavior.

**Acceptance checks**

- [ ] Testy pokryvaji cache, fallback a action-time verification.
- [ ] Existing GUI/config tests zustanou kompatibilni.

## Faze H - Verification, documentation, rollout

- [ ] H1 Spustit scoped testy pro config loader, GUI helpery a cache.
- [ ] H2 Spustit ruff nebo lokalni lint, pokud je pro projekt nakonfigurovan.
- [ ] H3 Zmerit optimized refresh s cache.
- [ ] H4 Vytvorit implementacni report s pred/po cisly.
- [ ] H5 Aktualizovat README/docs: SQL je GUI cache, JSON je source of truth.
- [ ] H6 Popsat rollback a rebuild cache postup.

**Acceptance checks**

- [ ] Performance report obsahuje baseline i optimized mereni.
- [ ] Dokumentace explicitne rika, ze pred akci se overuje lokalni JSON.
- [ ] Rollback funguje bez ztraty profilu.

## Globalni acceptance

- [ ] `instances/*/config.json` zustava autoritativni zdroj pravdy.
- [ ] SQL cache je pouzita jen pro rychle GUI zobrazeni.
- [ ] Pred kazdou akci je overen lokalni JSON profil.
- [ ] Pri nesouladu cache a JSON akce selze zavrene.
- [ ] Manualni refresh umi cache opravit nebo obejit.
- [ ] Zmena je revertovatelna bez migrace profilu.
