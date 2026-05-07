import { build, mergeConfig } from 'vite'

import config from '../vite.config.mjs'

await build(
  mergeConfig(config, {
    configFile: false,
  }),
)
