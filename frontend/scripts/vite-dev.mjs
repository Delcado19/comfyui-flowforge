import { createServer, mergeConfig } from 'vite'

import config from '../vite.config.mjs'

const server = await createServer(
  mergeConfig(config, {
    configFile: false,
    server: parseServerArgs(process.argv.slice(2)),
  }),
)

await server.listen()
server.printUrls()
server.bindCLIShortcuts({ print: true })

function parseServerArgs(args) {
  const result = {}

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index]
    if (arg === '--host') {
      result.host = args[index + 1] ?? true
      index += 1
    } else if (arg.startsWith('--host=')) {
      result.host = arg.slice('--host='.length) || true
    } else if (arg === '--port') {
      result.port = Number.parseInt(args[index + 1] ?? '', 10)
      index += 1
    } else if (arg.startsWith('--port=')) {
      result.port = Number.parseInt(arg.slice('--port='.length), 10)
    }
  }

  if (!Number.isFinite(result.port)) {
    delete result.port
  }

  return result
}
