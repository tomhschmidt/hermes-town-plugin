import { stat, mkdir, readdir, readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

import { build } from 'esbuild'

import { hoistImports, importsAreLeading } from './hoist-imports.mjs'

const root = fileURLToPath(new URL('..', import.meta.url))
const sourceRoot = path.join(root, 'src')
const distRoot = path.join(root, 'dist')
const allowedImports = new Set([
  '@hermes/plugin-sdk',
  'react',
  'react/jsx-runtime'
])

async function findPlugins() {
  const entries = await readdir(sourceRoot, { withFileTypes: true })
  const directories = entries.filter((entry) => entry.isDirectory())

  const plugins = await Promise.all(
    directories.map(async ({ name: id }) => {
      const entryPoint = path.join(sourceRoot, id, 'plugin.tsx')

      try {
        const entryStat = await stat(entryPoint)
        return entryStat.isFile() ? { id, entryPoint } : null
      } catch (error) {
        if (error?.code === 'ENOENT') return null
        throw error
      }
    })
  )

  return plugins.filter(Boolean).sort((a, b) => a.id.localeCompare(b.id))
}

async function readImportSpecifiers(outfile) {
  const result = await build({
    entryPoints: [outfile],
    bundle: true,
    external: ['*'],
    format: 'esm',
    logLevel: 'silent',
    metafile: true,
    platform: 'neutral',
    write: false
  })

  return Object.values(result.metafile.outputs).flatMap((output) =>
    output.imports.map(({ path: specifier }) => specifier)
  )
}

async function main() {
  const plugins = await findPlugins()
  const outputs = []

  for (const { id, entryPoint } of plugins) {
    const outputDirectory = path.join(distRoot, id)
    const outfile = path.join(outputDirectory, 'plugin.js')
    await mkdir(outputDirectory, { recursive: true })

    await build({
      entryPoints: [entryPoint],
      bundle: true,
      external: [...allowedImports],
      format: 'esm',
      jsx: 'automatic',
      outfile,
      platform: 'neutral'
    })
    await writeFile(outfile, hoistImports(await readFile(outfile, 'utf8')))

    outputs.push({ id, outfile })
  }

  const violations = []
  for (const { id, outfile } of outputs) {
    if (!importsAreLeading(await readFile(outfile, 'utf8'))) {
      violations.push(`${id}: import declaration after code (older Hermes Desktop loaders cannot rewrite it)`)
    }
    const specifiers = await readImportSpecifiers(outfile)
    for (const specifier of specifiers) {
      if (!allowedImports.has(specifier)) {
        violations.push(`${id}: ${specifier}`)
      }
    }
  }

  if (violations.length > 0) {
    console.error('Disallowed imports found in bundled plugins:')
    for (const violation of violations) console.error(`- ${violation}`)
    process.exitCode = 1
  }
}

main().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
