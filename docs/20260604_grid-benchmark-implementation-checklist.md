# Checklist

## Faze A - Parameter catalog

- [x] A1 Navrhnout `GridParameterSpec` a typy hodnot.
- [x] A2 Implementovat enumeraci kombinaci s limity.
- [x] A3 Pridat katalog request parametru.
- [x] A4 Pridat katalog runtime-static parametru.
- [ ] A5 Pridat informacni introspekci `llama-server --help`.

**Acceptance checks**

- [x] Numeric parametry umi `min/max/step`.
- [x] Enum/bool parametry umi explicitni seznam hodnot.
- [x] Katalog oznacuje restart-required parametry.

## Faze M - Model-aware tuning

- [x] M1 Vytvorit read-only model capability profile z GGUF/metadat.
- [ ] M2 Pridat KV/cache tuning katalog a memory warnings.
- [ ] M3 Pridat MTP/speculative tuning katalog a eligibility.
- [ ] M4 Pridat mapovani MTP/KV parametru na runtime args.

**Acceptance checks**

- [x] Architekturni metadata se zobrazuji jako read-only.
- [ ] KV/cache kombinace maji odhad pametoveho dopadu.
- [ ] MTP/speculative parametry jsou dostupne jen pri podpore modelu/binary nebo explicitnim override.

## Faze B - Storage and artifacts

- [x] B1 Navrhnout SQLite grid schema.
- [x] B2 Implementovat sweep/run insert/update helpers.
- [x] B3 Implementovat grid artifact path a writer.

**Acceptance checks**

- [x] Stavajici `benchmarks` historie je citelna.
- [x] Kazdy grid run ma ulozene `parameters_json`.
- [x] Failed run zustane dohledatelny.

## Faze C - Request-only runner

- [x] C1 Implementovat request-only runner.
- [x] C2 Doplnit stop request a progress callbacks.
- [x] C3 Propojit quick benchmark result s grid runem.

**Acceptance checks**

- [x] Stop ukonci sweep mezi kombinacemi.
- [x] Request body odpovida hodnotam kombinace.
- [x] GUI quick benchmark zustava kompatibilni.

## Faze D - Runtime/model-static runner

- [ ] D1 Navrhnout temporary runtime config aplikaci.
- [ ] D2 Implementovat mapovani runtime/model parametru na config/args.
- [ ] D3 Implementovat restart/start strategii pro runtime grid.

**Acceptance checks**

- [ ] `config.json` se bez potvrzeni nezmeni.
- [ ] Runtime/model run ceka na healthy stav.
- [ ] Chyba startu se zapise jako failed run.

## Faze E - GUI dialog and UX

- [x] E1 Pridat GUI akci `Grid benchmark`.
- [x] E2 Implementovat dialog s tabulkou parametru.
- [x] E3 Pridat preview kombinaci a potvrzeni limitu.
- [x] E4 Napojit background runner, progress a stop.
- [x] E5 Pridat zobrazeni/export vysledku.

**Acceptance checks**

- [x] Dialog se otevre bez spusteni benchmarku.
- [x] Nevalidni rozsahy se odmitnou pred startem.
- [x] GUI zustane responzivni behem sweepu.

## Faze F - Hugging Face metadata

- [ ] F1 Rozsirit HF variant metadata bez zmeny token storage.
- [ ] F2 Dopsat metadata do `ModelMetadata` aditivne.
- [ ] F3 Pouzit metadata pro defaultni rozsahy a report.

**Acceptance checks**

- [ ] HF token se neulozi do repo souboru.
- [ ] Lokalni model bez HF metadat lze benchmarkovat.
- [ ] Artefakt ukazuje repo/file/revision, pokud jsou dostupne.

## Faze G - Verification and documentation

- [x] G1 Aktualizovat README benchmark sekci.
- [x] G2 Vytvorit implementacni report.
- [x] G3 Spustit scoped testy a ruff.

**Acceptance checks**

- [x] Plan a checklist pouzivaji stejna task ID.
- [x] Kazde acceptance kriterium ma provadeci krok.
- [x] Rizikove kroky maji rollback.
- [x] Jsou uvedene verification kroky.
