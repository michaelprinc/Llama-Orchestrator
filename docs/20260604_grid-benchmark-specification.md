# Specification: Grid Benchmark for llama-orchestrator

**Datum:** 2026-06-04  
**Metodologie:** Spec Kit - Phase: Specify + Clarify  
**Status:** Draft / Ready for Review  
**Komponenta:** `infra-local/llama-orchestrator`

---

## 1. Problem statement

Soucasny `llama-orchestrator` umi spustit `Quick benchmark` nad jednou sadou sampling parametru a `Serial benchmark` nad vybranou frontou modelu. To je vhodne pro rychle mereni, ale ne pro systematicke hledani kompromisu mezi rychlosti, latenci, kvalitou odpovedi, pameti a stabilitou.

Pozadovana funkcionalita je rizeny "Grid benchmark": uzivatel vybere model nebo vice modelu, otevre dialog s parametry, zada rozsahy a kroky a spusti serializovany benchmark vsech kombinaci. Vysledky musi byt reprodukovatelne, porovnatelne a bezpecne vuci runtime rizikum llama.cpp.

## 2. Desired outcome

Uzivatel ma z GUI dostupny dialog "Grid benchmark", ktery:

- zobrazi podporovane benchmarkovatelne parametry ve forme tabulky `parametr`, `minimum`, `maximum`, `step`, `enabled`, `default/current`;
- pred spustenim ukaze pocet kombinaci, odhad trvani a upozorneni na rizikove kombinace;
- spousti kombinace serializovane, s moznosti zastaveni po dokonceni aktualni kombinace;
- uklada kazdou kombinaci s jednoznacnym `sweep_id`, `run_id`, parametry, stavem, metrikami a artefaktem;
- oddeluje online sampling parametry HTTP requestu od runtime parametru, ktere vyzaduji restart instance;
- umi pracovat s model-aware parametry, ktere zavisi na architekture modelu, GGUF metadatech a schopnostech konkretniho llama.cpp buildu;
- nepremenuje ani trvale neprepise existujici model config bez explicitniho potvrzeni.

## 3. Scope

### In scope

- GUI akce a dialog pro "Grid benchmark".
- Datovy model pro definici benchmark gridu, validaci rozsahu a enumeraci kombinaci.
- Perzistence grid benchmark nastaveni a vysledku.
- Rozsireni existujici benchmark historie nebo pridani dedikovanych SQLite tabulek pro sweep/run metadata.
- Reuse existujiciho `quick_benchmark_instance()` pro jednu kombinaci, pokud to nebude deformovat metriky.
- Export nebo otevreni vysledku jako Markdown/CSV/JSON.
- Import/aditivni doplneni model metadat z Hugging Face tam, kde zlepsuji defaultni rozsahy, audit a interpretaci vysledku.
- Model-aware grid parametry pro MTP/speculative decoding, architekturu, KV cache, kontext a pametovy model.
- Ochrana pred kombinatorickou explozi a nestabilnimi runtime zmenami.

### Out of scope

- Automaticke vyhodnocovani kvality odpovedi LLM judge.
- Paralelni benchmarkovani vice kombinaci najednou na jednom GPU.
- Automaticke stahovani novych modelu pouze kvuli grid benchmarku.
- Trvale mutace produkcnich/stabilnich instance configu bez potvrzeni.
- Nahrazeni Tkinter GUI.
- Zmeny WordPress, WireGuard, GCP nebo Docker casti repozitare.

## 4. Acceptance criteria

- [ ] GUI obsahuje jasne pojmenovanou akci `Grid benchmark` nebo `Benchmark grid`, dostupnou pro vybranou instanci a frontu.
- [ ] Dialog podporuje minimalne sampling parametry `max_tokens`, `temperature`, `top_p`, `top_k`, `repeat_penalty`, `seed`, `endpoint`, `ignore_eos`.
- [ ] Dialog podporuje planovanou kategorii runtime parametru, ktere vyzaduji restart, minimalne `model.context_size`, `model.batch_size`, `server.parallel`, `gpu.layers`, `--ubatch-size`, `--cache-type-k`, `--cache-type-v`, `--flash-attn`.
- [ ] Dialog podporuje model-aware kategorii pro architekturu a KV cache: zobrazi `architecture`, `native_context_length`, `n_layers`, `n_embd`, `n_attention_heads`, `n_kv_heads`, `head_dim_k`, `head_dim_v`, `n_experts`, `n_experts_used` a odvozene KV memory scenarios jako read-only metadata.
- [ ] Dialog podporuje MTP/speculative decoding parametry, pokud je model nebo runtime podporuje: minimalne `--spec-type`, `--spec-draft-n-max`, `--spec-draft-n-min`, `--spec-draft-p-min`, `--spec-draft-p-split`, `--cache-type-k-draft`, `--cache-type-v-draft`, `--n-gpu-layers-draft`, `--model-draft`.
- [ ] Dialog podporuje KV/cache runtime parametry pro ladeni pameti a rychlosti: minimalne `--cache-type-k`, `--cache-type-v`, `--kv-offload`, `--kv-unified`, `--cache-ram`, `--cache-idle-slots`, `--ctx-checkpoints`, `--checkpoint-every-n-tokens`, `--swa-full`, `--flash-attn`, `--no-kv-offload`.
- [ ] Model-aware parametry maji eligibility pravidla: MTP parametry jsou aktivni jen pri MTP/draft-capable modelu nebo pri explicitnim override; architekturni metadata jsou read-only a nikdy se negriduji jako zapisovatelne hodnoty.
- [ ] Pred spustenim je videt pocet kombinaci a system odmita nebo vyzaduje potvrzeni pro grid nad konfigurovatelnym limitem.
- [ ] Kazdy beh uklada konkretni hodnoty parametru, `sweep_id`, `run_id`, cas, config hash, prompt hash, metriky, stav a chybu.
- [ ] Zastaveni grid benchmarku neukonci proces nasilne, pokud aktualni kombinace jeste zapisuje vysledek; po stopu zustane historie konzistentni.
- [ ] Runtime parametry jsou aplikovany jen pres docasny plan behu nebo docasnou kopii configu; puvodni config zustava beze zmen, pokud uzivatel nepotvrdi opak.
- [ ] Vysledky lze filtrovat/seradit podle TPS, TTFT, pameti, statusu a hodnot parametru.
- [ ] Pro Hugging Face modely je v artefaktu a vysledku videt zdrojovy repo/file/revision, pokud jsou metadata dostupna.
- [ ] Existujici `Quick benchmark` a `Serial benchmark` zustanou zpetne kompatibilni.

## 5. Constraints

- Grid benchmark musi respektovat aktualni `parameter_mutability`: staticke parametry vyzaduji restart nebo docasnou instanci.
- Tkinter widgety musi byt aktualizovane pouze z hlavniho vlakna.
- Benchmark job ma bezet na pozadi a nesmi blokovat GUI.
- SQLite schema musi byt migrovane aditivne, aby zustala citelna stavajici historie.
- Hugging Face token se nesmi zapisovat do repo souboru; projekt uz pouziva keyring/session storage.
- "Vsechny dostupne parametry" nelze spolehlive udrzovat jako pevny seznam, protoze `llama-server --help` se meni podle verze binarky a buildu.

## 6. Assumptions

- [ASSUMPTION] Prvni verze bude serialni, ne paralelni, protoze jedna GPU instance a jeden server proces by jinak merily vzajemne ruseni.
- [ASSUMPTION] Nazev v UI bude `Grid benchmark`, protoze je kratsi a srozumitelnejsi nez `Benchmark matrix of parameters`.
- [ASSUMPTION] Grid pro sampling parametry muze bezet bez restartu pres HTTP request body.
- [ASSUMPTION] Grid pro runtime parametry bude vyzadovat restart nebo docasnou instanci s izolovanym portem.
- [ASSUMPTION] Kvalita odpovedi bude v prvni verzi hodnocena manualne podle ulozeneho output artefaktu, ne automatickym skorem.
- [ASSUMPTION] Architektura modelu, pocty vrstev/hlav a KV head dimenze budou vstupni metadata pro doporucene rozsahy, ne hodnoty menene gridem.
- [ASSUMPTION] MTP/speculative decoding bude v prvni verzi povolene jen pro modely oznacene v metadatech/tazich jako MTP nebo pri explicitnim uzivatelskem potvrzeni.

## 7. Clarifications

### Pending questions

- Ma byt maximalni pocet kombinaci ve vychozim nastaveni 100, 250 nebo jina hodnota?
- Ma grid benchmark pri runtime parametrech pouzivat docasnou kopii instance, nebo muze po potvrzeni restartovat vybranou instanci s docasne prepsanymi parametry v pameti?
- Maji byt vysledky primarne porovnavane v GUI tabulce, nebo staci prvni verzi export do Markdown/CSV s otevrenim souboru?
- Ma byt MTP/speculative decoding soucasti prvni implementacni faze, nebo az faze po request-only a KV cache gridu?
- Ma grid povolit externi draft model (`--model-draft`) vybrany ze souboru, nebo jen built-in MTP/spec typy bez dalsiho modelu?

### Non-blocking recommendation

Bez dalsich odpovedi lze planovat konzervativne: limit 100 kombinaci bez dalsiho potvrzeni, runtime parametry jen pres docasny run plan bez trvaleho zapisu configu, vysledky v SQLite plus Markdown/CSV export.

## 8. Affected components

- `src/llama_orchestrator/benchmark.py` - jedna kombinace benchmarku, settings, historie, artefakty.
- `src/llama_orchestrator/gui.py` - nove tlacitko/menu, dialog, background job, progress, vysledky.
- `src/llama_orchestrator/hf_import.py` - jiz nacita GGUF varianty pres Hugging Face Hub; muze dodat vice metadat.
- `src/llama_orchestrator/model_metadata.py` - jiz vytvari aditivni metadata z GGUF/HF zdroje.
- `src/llama_orchestrator/memory_fit.py` - jiz cte GGUF architekturu a odvozuje KV cache memory scenarios; je prirozeny zdroj pro model-aware defaulty.
- `src/llama_orchestrator/config/schema.py` - obsahuje rozsahy Pydantic poli a `parameter_mutability`.
- `src/llama_orchestrator/engine/command.py` - sestavuje `llama-server` prikaz a extra `args`.
- `state/benchmark_history.sqlite` - soucasna historie quick benchmarku; pravdepodobne bude rozsirena nebo doplnena o grid tabulky.
- `state/benchmark_settings.json` - soucasne globalni quick settings; grid potrebuje vlastni settings soubor.
- `logs/<instance>/benchmarks/` - soucasne Markdown artefakty; grid potrebuje subadresar nebo nazvoslovi podle `sweep_id`.
- `tests/test_benchmark.py`, `tests/test_gui.py`, `tests/test_hf_import.py`, `tests/test_model_metadata.py` - ocekavane testovaci rozsireni.

## 9. External dependencies

- Hugging Face Hub Python API: aktualni dokumentace uvadi `HfApi` jako HTTP klienta, podporuje token, `model_info`, `expand` vlastnosti vcetne `siblings`, `gguf`, `cardData`, `sha`, `tags`, a file metadata pres `files_metadata`. Zdroj: https://huggingface.co/docs/huggingface_hub/main/package_reference/hf_api, pristup 2026-06-04.
- llama.cpp `llama-server`: lokalni build `b9286` ukazuje rozsahle a verzi zavisle CLI parametry pres `llama-server.exe --help`. Pro planovani se ma pouzit runtime introspekce konkretni binarky, ne staticky prepis dokumentace.
- Lokalni `llama-server` build `b9286` obsahuje samostatnou skupinu speculative parametru vcetne `--spec-type none,draft-simple,draft-eagle3,draft-mtp,...`, `--spec-draft-n-max`, `--spec-draft-n-min`, draft KV typy a draft GPU/offload parametry.
- llama.cpp multi-GPU docs potvrzuji parametry jako `--n-gpu-layers`, `--cache-type-k`, `--cache-type-v`, `--ctx-size` a multi-GPU split nastaveni. Zdroj: https://github.com/ggml-org/llama.cpp/blob/master/docs/multi-gpu.md, pristup 2026-06-04.

## 10. Risks

| Risk | Impact | Likelihood |
|------|--------|------------|
| Kombinatoricka exploze rozsahu | high | high |
| Runtime parametry budou restartovat model a zkresli warm/cold metriky | high | medium |
| Nektere llama.cpp parametry nejsou bezpecne gridovat automaticky | high | high |
| MTP/speculative parametry budou bez podpory modelu zpusobovat start/runtime chyby | high | medium |
| KV cache kombinace povedou k OOM nebo sdilene RAM a zkresli vysledky | high | high |
| HF metadata budou neuplna pro lokalni nebo rucne stazene modely | medium | high |
| SQLite migrace narusi existujici quick benchmark historii | high | low |
| GUI bude blokovat pri dlouhem sweepu | high | medium |
| Mereni kvality nebude automaticke, jen performance metriky | medium | high |

## 11. Initial recommendation

Navrh je smysluplny, ale formulaci "vsechny dostupne parametry" je potreba omezit:

1. Prvni verze ma benchmarkovat "supported benchmark parameters", ne kazdy argument `llama-server`.
2. Dialog muze zobrazit katalog vsech detekovanych parametru z `llama-server --help`, ale povolit grid jen pro kuratorovanou a validovanou mnozinu.
3. HF metadata nejsou nutna pro spusteni grid benchmarku. Jsou vhodna pro lepsi defaulty, audit, reporty a bezpecnostni varovani.
4. Import z Hugging Face by se mel rozsirit aditivne, ne prepsat: ukladej vice metadat, ale zachovej soucasny proces importu GGUF variant.
5. Architektura modelu a GGUF metadata maji byt prvotridni vstup do gridu: nepouzivat je jako editovatelne parametry, ale jako zdroj pro doporucene rozsahy KV cache, kontextu, MTP eligibility a varovani pred OOM.
