import type { ComfyNode, ComfyPort } from '../stores/useWorkflowStore'

export interface DisplayRow {
  key: string
  kind: 'input' | 'widget'
  top: number
  height: number
  inputIndex?: number
  input?: ComfyPort
  name: string
  value?: string
  widgetKind?: 'text' | 'select' | 'toggle' | 'number' | 'compact'
  showPort: boolean
}

export const ROW_GAP = 4

const KSamplerValues = ['seed', 'control after generate', 'steps', 'cfg', 'sampler_name', 'scheduler', 'denoise']
const RandomNoiseValues = ['noise_seed', 'control after generate']

const SaveImageCleanValueByInputName: Record<string, number> = {
  path_template: 1,
  model_source: 2,
  clip_source: 3,
  filename_datetime: 5,
  collision_mode: 6,
  detection_info: 7,
  export_workflow_metadata: 8,
  subfolder: 9,
  model_folder: 10,
  clip_folder: 11,
}

const fallbackSchemas: Record<string, string[]> = {
  ksampler: KSamplerValues,
  randomnoise: RandomNoiseValues,
  saveimage: ['filename_prefix'],
  previewimage: [],
  cliptextencode: ['text'],
  ksamplerselect: ['sampler_name'],
  vaeloader: ['vae_name'],
  checkpointloadersimple: ['ckpt_name'],
  unetloader: ['unet_name', 'weight_dtype'],
  unetloadergguf: ['unet_name'],
  cliploader: ['clip_name', 'type', 'device'],
  cliploadergguf: ['clip_name', 'type'],
}

export function getNodeDisplayRows(node: ComfyNode): DisplayRow[] {
  const rows: DisplayRow[] = []
  const inputs = node.inputs ?? []
  const values = Array.isArray(node.widgets_values) ? node.widgets_values : []
  const nodeType = node.type.toLowerCase()
  let valueIndex = 0

  for (let inputIndex = 0; inputIndex < inputs.length; inputIndex += 1) {
    const input = inputs[inputIndex]
    const name = getPortName(input)

    if (!input.widget) {
      rows.push(buildInputRow(input, inputIndex, name))
      continue
    }

    const customValueIndex = getCustomWidgetValueIndex(nodeType, input)
    const currentValueIndex = customValueIndex ?? valueIndex
    const value = values[currentValueIndex]
    rows.push(buildWidgetRow(input, inputIndex, name, value))

    if (customValueIndex === undefined) {
      valueIndex += 1
      if (isSeedWidget(name) && isControlAfterGenerateValue(values[valueIndex])) {
        rows.push(buildSyntheticWidgetRow(node, 'control after generate', values[valueIndex]))
        valueIndex += 1
      }
    }
  }

  if (nodeType === 'saveimageclean') {
    valueIndex = Math.max(valueIndex, 12)
  }

  if (rows.every((row) => row.kind !== 'widget')) {
    for (const row of buildFallbackSchemaRows(node, values)) {
      rows.push(row)
    }
  } else if (valueIndex < values.length) {
    for (let index = valueIndex; index < values.length; index += 1) {
      if (shouldSkipExtraValue(nodeType, index)) continue
      rows.push(buildSyntheticWidgetRow(node, `value_${index}`, values[index]))
    }
  }

  return assignRowOffsets(rows)
}

export function getInputPortCenterY(node: ComfyNode, inputIndex: number, defaultRowHeight: number, topOffset: number): number {
  const row = getNodeDisplayRows(node).find((item) => item.inputIndex === inputIndex)
  if (!row) {
    return topOffset + defaultRowHeight / 2 + inputIndex * defaultRowHeight
  }
  return row.top + Math.min(row.height, defaultRowHeight) / 2
}

export function getVisibleInputPortIndexes(node: ComfyNode): number[] {
  return getNodeDisplayRows(node)
    .filter((row) => row.inputIndex !== undefined && row.showPort)
    .map((row) => row.inputIndex as number)
}

export function getPortName(port: ComfyPort): string {
  return port.localized_name || port.name || ''
}

export function formatWidgetValue(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

export function inferWidgetKind(value: unknown, widget: ComfyPort['widget']): DisplayRow['widgetKind'] {
  const widgetType = typeof widget?.type === 'string' ? widget.type.toLowerCase() : ''
  if (widgetType.includes('toggle') || widgetType.includes('checkbox') || typeof value === 'boolean') {
    return 'toggle'
  }
  if (widgetType.includes('combo') || widgetType.includes('select') || widgetType.includes('dropdown')) {
    return 'select'
  }
  if (widgetType.includes('number') || widgetType.includes('slider') || typeof value === 'number') {
    return 'number'
  }
  if (widgetType.includes('text') || widgetType.includes('string')) {
    return isMultilineWidget(value) ? 'text' : 'compact'
  }
  return isMultilineWidget(value) ? 'text' : 'compact'
}

export function inferWidgetHeight(value: unknown, widget: ComfyPort['widget']): number {
  const kind = inferWidgetKind(value, widget)
  if (kind === 'text') {
    const text = formatWidgetValue(value)
    const lines = Math.max(3, text.split(/\r?\n/).length)
    const estimated = Math.ceil(text.length / 54)
    return Math.min(180, Math.max(60, Math.max(lines, estimated) * 18 + 10))
  }
  return 20
}

function buildInputRow(input: ComfyPort, inputIndex: number, name: string): DisplayRow {
  return {
    key: `input-${inputIndex}`,
    kind: 'input',
    top: 0,
    height: 20,
    inputIndex,
    input,
    name,
    showPort: true,
  }
}

function buildWidgetRow(input: ComfyPort, inputIndex: number, name: string, value: unknown): DisplayRow {
  return {
    key: `widget-${inputIndex}`,
    kind: 'widget',
    top: 0,
    height: inferWidgetHeight(value, input.widget),
    inputIndex,
    input,
    name: getWidgetName(input, inputIndex),
    value: formatWidgetValue(value),
    widgetKind: inferWidgetKind(value, input.widget),
    showPort: hasInputLink(input),
  }
}

function buildSyntheticWidgetRow(node: ComfyNode, name: string, value: unknown): DisplayRow {
  return {
    key: `widget-extra-${node.id}-${name}`,
    kind: 'widget',
    top: 0,
    height: inferWidgetHeight(value, undefined),
    name,
    value: formatWidgetValue(value),
    widgetKind: inferWidgetKind(value, undefined),
    showPort: false,
  }
}

function buildFallbackSchemaRows(node: ComfyNode, values: unknown[]): DisplayRow[] {
  const schema = fallbackSchemas[node.type.toLowerCase()] ?? []
  if (schema.length === 0) return []

  return values.map((value, index) => {
    const name = schema[index] ?? `value_${index}`
    return buildSyntheticWidgetRow(node, name, value)
  })
}

function assignRowOffsets(rows: DisplayRow[]): DisplayRow[] {
  let top = 8
  return rows.map((row) => {
    const next = { ...row, top }
    top += row.height + ROW_GAP
    return next
  })
}

function getWidgetName(input: ComfyPort, index: number): string {
  const widgetName = input.widget && typeof input.widget.name === 'string' ? input.widget.name : ''
  return widgetName || getPortName(input) || `value_${index}`
}

function getCustomWidgetValueIndex(nodeType: string, input: ComfyPort): number | undefined {
  if (nodeType !== 'saveimageclean') return undefined
  const name = typeof input.name === 'string' ? input.name : ''
  return SaveImageCleanValueByInputName[name]
}

function shouldSkipExtraValue(nodeType: string, index: number): boolean {
  return nodeType === 'saveimageclean' && (index === 0 || index === 4 || index >= 12)
}

function hasInputLink(input: ComfyPort): boolean {
  return input.link !== null && input.link !== undefined
}

function isSeedWidget(name: string): boolean {
  const normalized = name.toLowerCase()
  return normalized === 'seed' || normalized.endsWith('_seed') || normalized.includes('noise_seed')
}

function isControlAfterGenerateValue(value: unknown): boolean {
  if (typeof value !== 'string') return false
  return ['fixed', 'randomize', 'increment', 'decrement'].includes(value)
}

function isMultilineWidget(value: unknown): boolean {
  const text = formatWidgetValue(value)
  return text.includes('\n') || text.length > 54
}
