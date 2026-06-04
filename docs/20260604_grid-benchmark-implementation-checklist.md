# Checklist

## Faze A - Parameter catalog

- [ ] A1 Navrhnout `GridParameterSpec` a typy hodnot.
- [ ] A2 Implementovat enumeraci kombinaci s limity.
- [ ] A3 Pridat katalog request parametru.
- [ ] A4 Pridat katalog runtime-static parametru.
- [ ] A5 Pridat informacni introspekci `llama-server --help`.

**Acceptance checks**

- [ ] Numeric parametry umi `min/max/step`.
- [ ] Enum/bool parametry umi explicitni seznam hodnot.
- [ ] Katalog oznacuje restart-required parametry.

## Faze M - Model-aware tuning

- [ ] M1 Vytvorit read-only model capability profile z GGUF/metadat.
- [ ] M2 Pridat KV/cache tuning katalog a memory warnings.
- [ ] M3 Pridat MTP/speculative tuning katalog a eligibility.
- [ ] M4 Pridat mapovani MTP/KV parametru na runtime args.

**Acceptance checks**

- [ ] Architekturni metadata se zobrazuji jako read-only.
- [ ] KV/cache kombinace maji odhad pametoveho dopadu.
- [ ] MTP/speculative parametry jsou dostupne jen pri podpore modelu/binary nebo explicitnim override.

## Faze B - Storage and artifacts

- [ ] B1 Navrhnout SQLite grid schema.
- [ ] B2 Implementovat sweep/run insert/update helpers.
- [ ] B3 Implementovat grid artifact path a writer.

**Acceptance checks**

- [ ] Stavajici `benchmarks` historie je citelna.
- [ ] Kazdy grid run ma ulozene `parameters_json`.
- [ ] Failed run zustane dohledatelny.

## Faze C - Request-only runner

- [ ] C1 Implementovat request-only runner.
- [ ] C2 Doplnit stop request a progress callbacks.
- [ ] C3 Propojit quick benchmark result s grid runem.

**Acceptance checks**

- [ ] Stop ukonci sweep mezi kombinacemi.
- [ ] Request body odpovida hodnotam kombinace.
- [ ] GUI quick benchmark zustava kompatibilni.

## Faze D - Runtime/model-static runner

- [ ] D1 Navrhnout temporary runtime config aplikaci.
- [ ] D2 Implementovat mapovani runtime/model parametru na config/args.
- [ ] D3 Implementovat restart/start strategii pro runtime grid.

**Acceptance checks**

- [ ] `config.json` se bez potvrzeni nezmeni.
- [ ] Runtime/model run ceka na healthy stav.
- [ ] Chyba startu se zapise jako failed run.

## Faze E - GUI dialog and UX

- [ ] E1 Pridat GUI akci `Grid benchmark`.
- [ ] E2 Implementovat dialog s tabulkou parametru.
- [ ] E3 Pridat preview kombinaci a potvrzeni limitu.
- [ ] E4 Napojit background runner, progress a stop.
- [ ] E5 Pridat zobrazeni/export vysledku.

**Acceptance checks**

- [ ] Dialog se otevre bez spusteni benchmarku.
- [ ] Nevalidni rozsahy se odmitnou pred startem.
- [ ] GUI zustane responzivni behem sweepu.

## Faze F - Hugging Face metadata

- [ ] F1 Rozsirit HF variant metadata bez zmeny token storage.
- [ ] F2 Dopsat metadata do `ModelMetadata` aditivne.
- [ ] F3 Pouzit metadata pro defaultni rozsahy a report.

**Acceptance checks**

- [ ] HF token se neulozi do repo souboru.
- [ ] Lokalni model bez HF metadat lze benchmarkovat.
- [ ] Artefakt ukazuje repo/file/revision, pokud jsou dostupne.

## Faze G - Verification and documentation

- [ ] G1 Aktualizovat README benchmark sekci.
- [ ] G2 Vytvorit implementacni report.
- [ ] G3 Spustit scoped testy a ruff.

**Acceptance checks**

- [ ] Plan a checklist pouzivaji stejna task ID.
- [ ] Kazde acceptance kriterium ma provadeci krok.
- [ ] Rizikove kroky maji rollback.
- [ ] Jsou uvedene verification kroky.