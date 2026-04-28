# ComfyUI Layout Tool

## Ziel

Tool das ComfyUI Workflow-JSONs einliest und Nodes/Verbindungen
so neu anordnet, dass Spaghetti-Verbindungen minimiert werden.

## Technologie

- Python (bevorzugt)
- Kein Framework-Overkill, einfach halten

## ComfyUI Workflow Format

- Workflows sind JSON-Dateien
- Nodes haben `pos: [x, y]`, `size: [w, h]` ODER `size: {width, height}` — beide Varianten kommen vor
- Verbindungen laufen über `links`-Array: `[link_id, src_node, src_slot, dst_node, dst_slot, "TYPE"]`
- `inputs[].link` ist eine einzelne Link-ID (ein Input empfängt genau einen Wert)
- `outputs[].links` ist ein Array (ein Output kann an mehrere Inputs gehen)
- `mode: 0` = aktiv, `mode: 4` = bypassed/deaktiviert
- `order` = numerische Ausführungsreihenfolge (von ComfyUI berechnet, wird nicht verändert)
- Groups: `bounding: [x, y, width, height]` — Zugehörigkeit via geometrische Überlappung, keine explizite Membership-Liste

## Entwicklungsregeln

- Erst verstehen, dann bauen – keine voreiligen Entscheidungen
- Kleine, testbare Schritte
- Vor größeren Umbauten fragen

## ComfyUI Installation

Scan-Daten liegen in: `H:\ComfyUI-Easy-Install\comfyui_scan\`

- 37 Custom Nodes installiert (35 aktiv)
- 76 Modell-Dateien
- Details in `comfyui_summary.json`, `custom_nodes.json`, `models.json`

---

## Installierte Custom Nodes (für Parser relevant)

### Nodes mit Sonderbehandlung im Layout-Algorithmus

#### Dekorative Nodes — keine Datenfluss-Kanten, werden separat platziert
| Node-Typ | Package |
|---|---|
| `Note` | comfy-core |
| `MarkdownNote` | comfyui-itools |
| `Label (rgthree)` | rgthree-comfy |

#### Virtuelle Verbindungen — nicht im `links`-Array, müssen synthetisch erzeugt werden
| Node-Typ | Package | Mechanismus |
|---|---|---|
| `SetNode` | comfyui-kjnodes | Speichert Wert unter `widgets_values[0]` als Name |
| `GetNode` | comfyui-kjnodes | Liest Wert nach `widgets_values[0]` als Name |

Ein `SetNode` mit Name `"VAE"` und ein `GetNode` mit Name `"VAE"` sind virtuell verbunden.
Der Parser muss diese Paare erkennen und synthetische Kanten erzeugen.

#### Reroute — Durchgangspunkt, zählt als echter Graph-Knoten
| Node-Typ | Package |
|---|---|
| `Reroute` | comfy-core |

Hat genau einen Input und einen Output, wird wie ein normaler Node behandelt.

#### Sub-Graphen — UUID als `type`, erscheinen als einzelner Node im Hauptgraphen
In neueren Workflows gibt es Nodes deren `type` eine UUID ist (z.B. `"ce575129-b994-4bea-81b7-07c2b68948a9"`).
Die interne Struktur steht unter `extra.definitions.subgraphs[]`. Für den Layout-Algorithmus
werden diese als normaler Node (Black Box) behandelt — die internen Nodes werden nicht neu angeordnet.

#### Bypasser — steuert Gruppen, hat `OPT_CONNECTION`-Output der zu nichts führt
| Node-Typ | Package |
|---|---|
| `Fast Groups Bypasser (rgthree)` | rgthree-comfy |

#### Switch-Nodes — wählen zwischen zwei Inputs, normale Datenfluss-Behandlung
| Node-Typ | Package |
|---|---|
| `Switch latent [Crystools]` | ComfyUI-Crystools |
| `ComfySwitchNode` | comfy-core |

---

### Alle installierten Packages und ihre in Workflows verwendeten Node-Typen

#### comfy-core (Standard)
`VAELoader` · `VAEDecode` · `VAEEncode` · `VAEDecodeTiled` · `VAEEncodeTiled`
`CLIPTextEncode` · `ConditioningZeroOut` · `FluxGuidance` · `ReferenceLatent`
`KSampler` · `KSamplerSelect` · `SamplerCustomAdvanced` · `CFGGuider` · `RandomNoise`
`EmptySD3LatentImage` · `EmptyFlux2LatentImage` · `EmptyLatentImage`
`Flux2Scheduler` · `SetLatentNoiseMask` · `LatentUpscaleBy`
`LoadImage` · `SaveImage` · `PreviewImage`
`ImageScaleToTotalPixels` · `ImageScaleBy` · `ImageStitch` · `GetImageSize` · `ImageUpscaleWithModel`
`GrowMask` · `UNETLoader` · `CLIPLoader` · `LoraLoaderModelOnly` · `ModelSamplingAuraFlow`
`UpscaleModelLoader` · `ComfySwitchNode` · `TextEncodeQwenImageEdit`
`Note` · `Reroute`

#### ComfyUI-GGUF (city96)
`UnetLoaderGGUF` · `CLIPLoaderGGUF` · `VaeGGUF`

#### rgthree-comfy
`Power Lora Loader (rgthree)` · `Fast Groups Bypasser (rgthree)`
`Image Comparer (rgthree)` · `Label (rgthree)` · `Seed (rgthree)`

#### comfyui-kjnodes (kijai)
`SetNode` · `GetNode` · `VRAM_Debug`

#### ComfyUI-Easy-Use (yolain)
`easy loraStack` · `easy loraStackApply` · `easy seed`
`easy cleanGpuUsed` · `easy clearCacheAll`

#### ComfyUI-Crystools
`Switch latent [Crystools]` · `List of strings [Crystools]` · `Show any [Crystools]`

#### comfyui-easy-sam3 (yolain)
`easy sam3ImageSegmentation` · `easy sam3ModelLoader` · `easy framesEditor`

#### ComfyUI-Qwen3.5-Uncensored / comfyui-rmbg (1038lab)
`AILab_QwenVL_GGUF_Advanced` · `AILab_ImageResize`

#### ComfyUI_essentials (cubiq)
`MaskPreview+`

#### RES4LYF (ClownsharkBatwing)
`ClownsharKSampler_Beta` · `SharkOptions_Beta` · `ClownOptions_DetailBoost_Beta`
`EmptyLatentImageCustom`

#### comfyui-vrgamedevgirl
`FastFilmGrain` · `FastLaplacianSharpen`

#### comfyui-vton-mask-tools
`VTONMaskCleanup`

#### seedvr2_videoupscaler (numz)
`SeedVR2VideoUpscaler` · `SeedVR2LoadDiTModel` · `SeedVR2LoadVAEModel`

#### seedvarianceenhancer
`SeedVarianceEnhancer`

#### wlsh_nodes (wallish77)
`Upscale by Factor with Model (WLSH)`

#### comfyui-itools (MohammadAboulEla)
`MarkdownNote`

#### comfyui-save-image-organized (Delcado19)
`SaveImageClean`
